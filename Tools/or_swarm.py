"""OpenRouter parallel swarm — fan tasks out to N workers concurrently.

Reusable harness (systematize-what-works). Reads OPENROUTER_API_KEY, runs a
task list across a thread pool on a chosen model (default
deepseek/deepseek-v4-flash-0731, 5 workers), auto-continues truncated replies,
and writes each task's output to a file.

Gotchas baked in (from prior projects):
  - OpenRouter wants a custom User-Agent + HTTP-Referer/X-Title or some routes 4xx.
  - finish_reason=="length" -> append + "continue exactly" up to a cap.
  - retry 429/5xx with backoff.

Drive it from another script:
    from Tools.or_swarm import run_swarm
    run_swarm([{ "id": "x", "prompt": "...", "out": "path.md" }], workers=2)
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"


def _key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not k:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    return k


# Reasoning models (kimi-k3) FORCE reasoning that consumes the max_tokens budget,
# so too-small a cap yields empty `content`; and the provider 400s above ~6000.
# 6000 leaves room for reasoning + a full reply while staying under the ceiling.
MAX_OUT_CAP = 6000
SAFE_OUT = 4096


def _post(model: str, convo: List[Dict[str, str]], max_tokens: int) -> Dict[str, Any]:
    key = _key()
    # deepseek-v4-flash is a reasoning model; on a code/general call it spends the
    # whole max_tokens budget on hidden reasoning and returns EMPTY `content`.
    # Disable reasoning outright for this deterministic path (see memory
    # deepseek-v4-flash-reasoning-content-trap). (Was a 2500-token cap for kimi-k3.)
    body = json.dumps({"model": model, "messages": convo,
                       "max_tokens": max_tokens, "temperature": 0.4,
                       "reasoning": {"enabled": False}}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://infinitycode.app",
        "X-Title": "Infinity Code Swarm",
        "User-Agent": "InfinityCode-Swarm/1.0",
    })
    with urllib.request.urlopen(req, timeout=200) as r:
        return json.load(r)


def _call(model: str, messages: List[Dict[str, str]], max_tokens: int = SAFE_OUT,
          max_cont: int = 3) -> str:
    convo = list(messages)
    full = ""
    cap = min(max_tokens, MAX_OUT_CAP)
    for _ in range(max_cont + 1):
        data: Optional[Dict[str, Any]] = None
        for attempt in range(4):
            try:
                data = _post(model, convo, cap)
                break
            except urllib.error.HTTPError as e:
                # A 400 above the provider ceiling: back off to a safe cap once.
                if e.code == 400 and cap > SAFE_OUT:
                    cap = SAFE_OUT
                    continue
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    time.sleep(2 * (2 ** attempt))
                    continue
                raise
        if data is None:
            break
        ch = data["choices"][0]
        piece = ch["message"].get("content") or ""
        finish = ch.get("finish_reason")
        full += piece
        # Only continue when there is real content AND we were truncated — never
        # loop on empty reasoning-only pieces (that was the 0-char bug).
        if finish != "length" or not piece.strip():
            break
        convo = convo + [
            {"role": "assistant", "content": piece},
            {"role": "user", "content": "Continue exactly from where you stopped, no repetition."},
        ]
    return full


def run_swarm(
    tasks: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    workers: int = 5,
    system: str = "",
    on_done: Optional[Callable[[Dict[str, Any], str], None]] = None,
) -> List[Dict[str, Any]]:
    """tasks: [{id, prompt, out(optional path)}]. Returns [{id, ok, chars, out}]."""
    def work(task: Dict[str, Any]) -> Dict[str, Any]:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": task["prompt"]}
        ]
        t0 = time.time()
        try:
            text = _call(model, msgs, task.get("max_tokens", MAX_OUT_CAP))
            if not text.strip():
                # Empty content is a failure, not a silent OK (was the false-OK bug).
                return {"id": task["id"], "ok": False, "chars": 0,
                        "error": "empty content (reasoning consumed budget)",
                        "secs": round(time.time() - t0, 1)}
            if task.get("out"):
                Path(task["out"]).parent.mkdir(parents=True, exist_ok=True)
                Path(task["out"]).write_text(text, encoding="utf-8")
            if on_done:
                on_done(task, text)
            return {"id": task["id"], "ok": True, "chars": len(text),
                    "out": task.get("out"), "secs": round(time.time() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"id": task["id"], "ok": False, "error": str(exc)[:160],
                    "secs": round(time.time() - t0, 1)}

    results: List[Dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(work, tasks):
            results.append(res)
            print(f"  [{'OK ' if res['ok'] else 'ERR'}] {res['id']:22} "
                  f"{res.get('chars', 0)} chars · {res['secs']}s"
                  + (f" -> {res.get('error')}" if not res["ok"] else ""))
    return results


if __name__ == "__main__":
    import sys
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    r = run_swarm(spec.get("tasks", []), model=spec.get("model", DEFAULT_MODEL),
                  workers=spec.get("workers", 5), system=spec.get("system", ""))
    ok = sum(1 for x in r if x["ok"])
    print(f"\n{ok}/{len(r)} tasks ok")
