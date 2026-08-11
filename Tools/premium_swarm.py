#!/usr/bin/env python
"""Infinity Code premium-polish swarm harness.

Model-swappable autonomous loop that refines the frontend toward the standard
in docs/PREMIUM_MOTION_BRIEF.md:

  capture (playwright) -> vision judge (qwen3-vl-plus, 0-100 rubric)
    -> 2 harsh critics -> top-K fixes -> 2 coders (FILE blocks)
    -> tsc+vite gate -> constraint reviewer veto -> re-capture -> re-judge
    -> keep only iterations that raise the score -> progression oracle

Roster policy (matches the app's own Ascension doctrine):
  * Kimi K2.6 drafts + critiques when Moonshot direct has balance.
  * Qwen 3.8 Max is oracle/reviewer/progression ONLY — it never drafts.
  * When Kimi is unfunded, drafting falls back to a DashScope coder model;
    critics fall back to Qwen 3.8 Max (reviewing is role-legal).
  * qwen3-vl-plus is the eyes (screenshots in, scores out).

Safety:
  * Write allow-list: src/index.css, src/App.tsx, src/components/*.tsx,
    src/lib/*.ts — nothing else is ever written.
  * Backups of every touched file per iteration; restore on build failure,
    reviewer veto, or score drop. No git commits, ever.
  * Backend for screenshots runs with a THROWAWAY INFINITY_DATA_DIR so the
    real mission/longtask DBs are never touched.

Usage:
  .venv\\Scripts\\python.exe Tools\\premium_swarm.py --dry-run        # pipeline proof, zero API calls
  .venv\\Scripts\\python.exe Tools\\premium_swarm.py --iterations 2   # live loop
  .venv\\Scripts\\python.exe Tools\\premium_swarm.py --provider qwen  # skip Kimi probe
"""
from __future__ import annotations

import argparse
import base64
import difflib
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:  # Windows consoles default to cp1252 — never crash on unicode output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "docs" / "PREMIUM_MOTION_BRIEF.md"
RUNS = REPO / "Tools" / "premium_runs"
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
NODE = "node"
FRONT_PORT = 4173
BACKEND_PORT = 8000
ALLOW_RE = re.compile(
    r"^src/(index\.css|App\.tsx|components/[A-Za-z0-9_.\-]+\.tsx|lib/[A-Za-z0-9_.\-]+\.ts)$"
)
FILE_BLOCK_RE = re.compile(
    r"^FILE:\s*(\S+)\s*$\r?\n```[a-zA-Z]*\r?\n(.*?)^```\s*$",
    re.MULTILINE | re.DOTALL,
)
CONTEXT_FILES = [
    "src/index.css",
    "src/App.tsx",
    "src/components/TabBar.tsx",
    "src/components/ChatView.tsx",
    "src/components/SwarmCouncilPanel.tsx",
    "src/components/AscensionDial.tsx",
    "src/lib/appearance.ts",
]

CALLS = {"n": 0}
MAX_CALLS = 60
# Owner asked for slower quota consumption (2026-08-07). Seconds to sleep
# before every LLM call; override via PREMIUM_PACE_S env (0 = no pacing).
PACE_S = float(os.environ.get("PREMIUM_PACE_S", "2"))


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def log(run_dir: Path | None, event: str, **fields) -> None:
    entry = {"ts": time.time(), "event": event, **fields}
    line = json.dumps(entry, ensure_ascii=False, default=str)
    print(f"[premium-swarm] {event} " + (
        " ".join(f"{k}={v}" for k, v in fields.items() if k in (
            "model", "ok", "score", "before", "after", "applied", "reverted",
            "blocked", "shot", "file"))), flush=True)
    if run_dir is not None:
        with (run_dir / "swarm.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def load_home_env() -> dict:
    env = {}
    for p in (Path.home() / ".env", REPO / ".env"):
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def moonshot_key() -> str:
    kf = REPO / "backend" / "moonshot.key"
    return kf.read_text(encoding="utf-8").strip() if kf.is_file() else ""


def load_infra_env() -> dict:
    env = {}
    p = Path("D:/genesis/infra/.env")
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def deepseek_chat(model: str, system: str, user: str, *, max_tokens: int = 8000,
                  temperature: float = 0.3, timeout: int = 600,
                  run_dir: Path | None = None) -> str:
    """Direct api.deepseek.com — verified live 2026-08-07 (v4-flash + v4-pro)."""
    key = load_infra_env().get("DEEPSEEK_API_KEY", "")
    if not key:
        return "ERR: no DEEPSEEK_API_KEY in D:/genesis/infra/.env"
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    # deepseek-v4-* are reasoning models: without this, reasoning eats the
    # whole budget and content comes back empty (flash = speed role).
    if model.endswith("-flash"):
        body["thinking"] = {"type": "disabled"}
    try:
        d = _post_json("https://api.deepseek.com/chat/completions", key, body,
                       timeout)
        msg = d["choices"][0]["message"]
        out = msg.get("content") or msg.get("reasoning_content") or ""
        if run_dir:
            log(run_dir, "llm_call", model=model, provider="deepseek",
                tokens=d.get("usage", {}).get("total_tokens"))
        return out
    except urllib.error.HTTPError as e:
        return f"ERR: HTTP {e.code} {e.read().decode()[:200]}"
    except Exception as e:
        return f"ERR: {str(e)[:200]}"


def dispatch_chat(ref: str, system: str, user: str, **kw) -> str:
    """Unified call by provider ref: 'deepseek/v4-flash', 'dashscope/qwen3-max',
    'moonshot/kimi-k2.6'. Unknown refs fall back to dashscope."""
    if ref.startswith("deepseek/"):
        slug = ref.split("/", 1)[1]
        model = slug if slug.startswith("deepseek-") else f"deepseek-{slug}"
        return deepseek_chat(model, system, user, **kw)
    if ref.startswith("moonshot/"):
        return kimi_chat(ref.split("/", 1)[1], system, user, **kw)
    if ref.startswith("dashscope/"):
        return qwen_chat(ref.split("/", 1)[1], system, user, **kw)
    return qwen_chat(ref, system, user, **kw)


def entry(ref: str) -> dict:
    # fn accepts the legacy leading model-name arg and ignores it
    return {"name": ref,
            "fn": lambda _m, *a, **k: dispatch_chat(ref, *a, **k)}


def _post_json(url: str, key: str, body: dict, timeout: int) -> dict:
    CALLS["n"] += 1
    if CALLS["n"] > MAX_CALLS:
        raise RuntimeError(f"call budget exceeded ({MAX_CALLS})")
    if PACE_S > 0:
        time.sleep(PACE_S)
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def qwen_chat(model: str, system: str, user: str, *, images: list[Path] | None = None,
              max_tokens: int = 4000, temperature: float = 0.3,
              timeout: int = 600, run_dir: Path | None = None) -> str:
    env = load_home_env()
    key = env.get("DASHSCOPE_API_KEY", "")
    base = (env.get("DASHSCOPE_BASE_URL", "")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    if not key:
        return "ERR: no DASHSCOPE_API_KEY"
    if images:
        content: list[dict] = [{"type": "text", "text": user}]
        for img in images:
            b64 = base64.b64encode(img.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        user_msg: dict = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": user}
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, user_msg]}
    try:
        d = _post_json(base + "/chat/completions", key, body, timeout)
        out = d["choices"][0]["message"].get("content") or ""
        if run_dir:
            log(run_dir, "llm_call", model=model, provider="dashscope",
                tokens=d.get("usage", {}).get("total_tokens"))
        return out
    except urllib.error.HTTPError as e:
        return f"ERR: HTTP {e.code} {e.read().decode()[:200]}"
    except Exception as e:
        return f"ERR: {str(e)[:200]}"


def kimi_chat(model: str, system: str, user: str, *, max_tokens: int = 8000,
              timeout: int = 600, run_dir: Path | None = None) -> str:
    key = moonshot_key()
    if not key:
        return "ERR: no backend/moonshot.key"
    body = {"model": model, "temperature": 1.0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    try:
        d = _post_json("https://api.moonshot.ai/v1/chat/completions", key, body, timeout)
        out = d["choices"][0]["message"].get("content") or ""
        if run_dir:
            log(run_dir, "llm_call", model=model, provider="moonshot",
                tokens=d.get("usage", {}).get("total_tokens"))
        return out
    except urllib.error.HTTPError as e:
        return f"ERR: HTTP {e.code} {e.read().decode()[:200]}"
    except Exception as e:
        return f"ERR: {str(e)[:200]}"


def kimi_alive(timeout: int = 45) -> bool:
    """Cheap balance probe: a suspended account 429s before spending tokens."""
    key = moonshot_key()
    if not key:
        return False
    body = {"model": "kimi-k2.6", "temperature": 1.0, "max_tokens": 16,
            "messages": [{"role": "user", "content": "ping"}]}
    try:
        _post_json("https://api.moonshot.ai/v1/chat/completions", key, body, timeout)
        return True
    except urllib.error.HTTPError as e:
        return e.code not in (401, 402, 403, 429)
    except Exception:
        return False


def qwen_coder_model() -> str:
    """Pick the best live DashScope drafting model.

    Verified 2026-08-07: qwen3-coder-plus / qwen3.8-max / qwen3.7-max /
    qwen3-vl-plus are 403 (free quota exhausted); qwen3-coder-flash,
    qwen3-max, qwen-vl-max, qwen3-vl-flash, qwen-plus are live.
    """
    env = load_home_env()
    key = env.get("DASHSCOPE_API_KEY", "")
    base = (env.get("DASHSCOPE_BASE_URL", "")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    try:
        req = urllib.request.Request(base + "/models",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ids = [m.get("id", "") for m in json.loads(r.read())["data"]]
    except Exception:
        return "qwen3-coder-flash"
    for want in ("qwen3-coder-flash", "qwen3-max", "qwen3-coder-plus"):
        if want in ids:
            return want
    return "qwen3-coder-flash"


def resolve_roster(provider: str, run_dir: Path | None,
                   drafter_ref: str | None = None) -> dict:
    use_kimi = False
    if provider in ("auto", "kimi"):
        print("[premium-swarm] probing Kimi K2.6 balance …", flush=True)
        use_kimi = kimi_alive()
        print(f"[premium-swarm] kimi-k2.6 {'AVAILABLE' if use_kimi else 'UNAVAILABLE'}")
    if drafter_ref:
        drafter = entry(drafter_ref)
    elif use_kimi:
        drafter = entry("moonshot/kimi-k2.6")
    else:
        drafter = entry("deepseek/v4-flash")  # cheap coder, verified live
    # two harsh critics from different lineages (deepseek + qwen)
    roster = {
        "drafter": drafter,
        "critic_a": entry("deepseek/v4-pro") if not use_kimi
        else entry("moonshot/kimi-k2.6"),
        "critic_b": entry("dashscope/qwen3-max") if not use_kimi
        else entry("moonshot/kimi-k2.6"),
        "reviewer": entry("dashscope/qwen3-max"),
        "progression": entry("deepseek/v4-pro"),
        "judge": entry("dashscope/qwen-vl-max"),
    }
    if run_dir:
        log(run_dir, "roster", **{k: v["name"] for k, v in roster.items()})
    return roster


def parse_json_reply(text: str) -> dict | list | None:
    if not text or text.startswith("ERR"):
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"[\[{].*[\]}]", t, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# --------------------------------------------------------------------------
# gates & files
# --------------------------------------------------------------------------

def run_cmd(cmd: list[str], timeout: int = 420,
            extra_env: dict | None = None) -> tuple[int, str]:
    try:
        env = {**os.environ, **(extra_env or {})}
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace",
                           env=env)
        return r.returncode, (r.stdout or "")[-4000:] + (r.stderr or "")[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:
        return 1, str(e)[:400]


def typecheck() -> tuple[bool, str]:
    rc, out = run_cmd([NODE, r"node_modules\typescript\bin\tsc", "--noEmit"], 300)
    return rc == 0, out


# The sandbox backend port is chosen once per run (owner's own backend usually
# holds 8000); the dist used for screenshots is built with VITE_API_PORT
# pointing at it.
SANDBOX_API_PORT = 8001


def build_frontend() -> tuple[bool, str]:
    rc, out = run_cmd([NODE, r"node_modules\vite\bin\vite.js", "build"], 420,
                      extra_env={"VITE_API_PORT": str(SANDBOX_API_PORT)})
    return rc == 0, out


def read_context_files() -> dict[str, str]:
    files = {}
    for rel in CONTEXT_FILES:
        p = REPO / rel
        if p.is_file():
            files[rel] = p.read_text(encoding="utf-8", errors="replace")
    return files


def files_as_prompt(files: dict[str, str], cap_each: int | None = 14000) -> str:
    parts = []
    for rel, body in files.items():
        if cap_each and len(body) > cap_each:
            body = body[:cap_each] + "\n… (truncated)"
        parts.append(f"=== FILE: {rel} ===\n{body}")
    return "\n\n".join(parts)


def parse_file_blocks(text: str) -> dict[str, str]:
    out = {}
    if not text or text.startswith("ERR"):
        return out
    for m in FILE_BLOCK_RE.finditer(text):
        path = m.group(1).strip().strip("`").replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        if ALLOW_RE.match(path):
            out[path] = m.group(2)
        else:
            print(f"[premium-swarm] REJECTED out-of-allowlist FILE block: {path}")
    return out


def make_diff(old: dict[str, str], new: dict[str, str]) -> str:
    parts = []
    for rel, body in new.items():
        parts.append("\n".join(difflib.unified_diff(
            old.get(rel, "").splitlines(), body.splitlines(),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=3)))
    return "\n".join(p for p in parts if p.strip())


# --------------------------------------------------------------------------
# servers & capture
# --------------------------------------------------------------------------

def free_port(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass


def start_servers(run_dir: Path):
    """Returns (backend_proc, httpd). Throwaway data dir protects the real DB."""
    if not free_port(SANDBOX_API_PORT) or not free_port(FRONT_PORT):
        raise RuntimeError(
            f"port {SANDBOX_API_PORT}/{FRONT_PORT} busy — stop other servers first")
    data_dir = run_dir / "sandbox_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "INFINITY_DATA_DIR": str(data_dir)}
    backend = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "main:app", "--port",
         str(SANDBOX_API_PORT), "--app-dir", "backend"],
        cwd=REPO, env=env,
        stdout=(run_dir / "backend.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT)
    # wait for backend
    up = False
    for _ in range(60):
        time.sleep(1)
        if backend.poll() is not None:
            raise RuntimeError("backend exited early — see backend.log")
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{SANDBOX_API_PORT}/api/v1/ascension/state",
                    timeout=2) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            continue
    if not up:
        backend.terminate()
        raise RuntimeError("backend never answered /api/v1/ascension/state")

    dist = REPO / "dist"
    handler = lambda *a, **kw: _QuietHandler(*a, directory=str(dist), **kw)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", FRONT_PORT), handler)
    httpd.allow_reuse_address = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return backend, httpd


SHOTS = [
    # (name, settle_ms, description-for-judge)
    ("01_intro", 900, "app shell mid entrance-animation"),
    ("02_settled", 3500, "app shell fully settled"),
]


def capture(run_dir: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright
    shots_dir = run_dir / "shots"
    shots_dir.mkdir(exist_ok=True)
    out: list[Path] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900},
                          device_scale_factor=1)
        for name, settle_ms, _ in SHOTS:
            try:
                page.goto(f"http://localhost:{FRONT_PORT}/",
                          wait_until="domcontentloaded", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(settle_ms)
                f = shots_dir / f"{name}.png"
                page.screenshot(path=str(f))
                out.append(f)
            except Exception as e:
                print(f"[premium-swarm] shot {name} failed: {str(e)[:120]}")
        # council panel reveal (best effort: find a toggle mentioning council/swarm)
        try:
            page.goto(f"http://localhost:{FRONT_PORT}/",
                      wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            btn = page.locator("button, [role='button']",
                               has_text=re.compile(r"council|swarm", re.I)).first
            if btn.count():
                btn.click(timeout=3000)
                page.wait_for_timeout(1400)
                f = shots_dir / "03_council.png"
                page.screenshot(path=str(f))
                out.append(f)
        except Exception as e:
            print(f"[premium-swarm] council shot skipped: {str(e)[:120]}")
        b.close()
    return out


# --------------------------------------------------------------------------
# swarm roles
# --------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You are the visual oracle of Infinity Code X. You score UI screenshots "
    "against a premium desktop-app standard (Qoder/Qwen/Kimi-class finish). "
    "Be strict and concrete; average AI-generated UI scores in the 40s.")

JUDGE_PROMPT = """Score this screenshot of the Infinity Code X desktop app.

Rubric (0-100 each):
- motion: evidence of well-eased entrances/transitions (or tasteful static hierarchy if mid-motion absent)
- depth_glow: layered depth, soft glows/shadows, no flat dead surfaces
- typography: clear hierarchy, weights, letter-spacing, readable sizes
- spacing: consistent rhythm, padding, alignment; nothing cramped or lost
- microinteractions: hover/active affordances, status indicators, polish details
- cohesion: one consistent design language, no clashing colors/styles

Reply with STRICT JSON only:
{{"scores": {{"motion": n, "depth_glow": n, "typography": n, "spacing": n, "microinteractions": n, "cohesion": n}}, "total": n, "notes": ["..."], "top_opportunities": ["..."]}}
(total = weighted mean: motion .25, depth_glow .2, typography .15, spacing .15, microinteractions .15, cohesion .10)

View: {view}
Extra context from prior rounds:
{focus}"""


def judge_shot(roster: dict, shot: Path, focus: str, run_dir: Path) -> dict:
    view = {"01_intro": "app shell during entrance animation (~0.9s)",
            "02_settled": "app shell fully settled",
            "03_council": "Swarm Council panel open"}.get(shot.stem, shot.stem)
    out = roster["judge"]["fn"](
        roster["judge"]["name"], JUDGE_SYSTEM,
        JUDGE_PROMPT.format(view=view, focus=focus or "(first pass)"),
        images=[shot], max_tokens=1200, temperature=0.2, run_dir=run_dir)
    data = parse_json_reply(out)
    if isinstance(data, dict) and "scores" in data:
        data.setdefault("total", round(sum(data["scores"].values()) / 6, 1))
        return data
    return {"scores": {}, "total": 0.0, "notes": [f"judge parse failure: {out[:160]}"],
            "top_opportunities": []}


CRITIC_SYSTEM = (
    "You are one of two harsh critics of Infinity Code X, a desktop AI app "
    "that must look and move like Qoder/Qwen/Kimi-class premium software. "
    "No mercy, no compliments. Every defect must come with a concrete fix "
    "(exact CSS/TSX approach), ranked by user-visible impact.")


def critic_pass(roster: dict, idx: int, judge_notes: str, files_prompt: str,
                focus: str, run_dir: Path) -> list[dict]:
    brief = BRIEF.read_text(encoding="utf-8") if BRIEF.is_file() else ""
    prompt = f"""MOTION BRIEF (the standard):
{brief[:6000]}

VISION ORACLE REPORT:
{judge_notes}

CURRENT CODE:
{files_prompt}

FOCUS FROM PROGRESSION ORACLE:
{focus or '(none yet)'}

List up to 6 concrete defects that most cheaply raise the premium feel —
animations, easing, hover/active states, entrance choreography, glow/depth,
typography hierarchy, spacing. Respect the brief's hard constraints.
Reply STRICT JSON only:
[{{"file": "src/...", "where": "selector or region", "defect": "...", "fix": "...", "impact": 1-10}}]
"""
    out = roster["critic_a" if idx == 0 else "critic_b"]["fn"](
        CRITIC_SYSTEM, prompt, max_tokens=4000,
        temperature=(0.4 if idx else 0.7), run_dir=run_dir)
    data = parse_json_reply(out)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict) and d.get("file")][:6]
    print(f"[premium-swarm] critic {idx} parse failure: {out[:160]}")
    return []


CODER_SYSTEM = (
    "You are a coder in the Infinity Code X premium swarm. You ONLY code. "
    "Implement the assigned fixes as complete, paste-ready files. The file "
    "contents below are COMPLETE and AUTHORITATIVE: every component's prop "
    "contract is defined there — never pass a prop that is not declared, and "
    "when you re-render an existing component you must supply ALL of its "
    "required props. Never animate layout properties; only "
    "transform/opacity/filter. Keep existing behavior and exports intact. "
    "TypeScript strict must stay green.")


def coder_pass(roster: dict, fixes: list[dict], files: dict[str, str],
               feedback: str, run_dir: Path,
               cap_each: int | None = None) -> dict[str, str]:
    feedback_block = ""
    if feedback:
        feedback_block = ("PREVIOUS ATTEMPT FAILED THE BUILD GATE. ERRORS:\n"
                          + feedback + "\nFIX ONLY THESE ERRORS.")
    prompt = f"""Implement ONLY these fixes (from the critics):
{json.dumps(fixes, indent=1, ensure_ascii=False)}

HARD CONSTRAINTS: the premium motion brief — animate transform/opacity/filter
only, easing cubic-bezier(0.22,1,0.36,1) entrances, micro 120-200ms,
panel reveals 240-400ms, stagger 40-60ms, respect prefers-reduced-motion,
no new dependencies, use existing CSS tokens.

CURRENT FILES:
{files_as_prompt(files, cap_each=cap_each)}
{feedback_block}

Output format — one 'FILE: <path>' line then a fenced block with the FULL new
file content, for every file you change. Nothing else.
"""
    out = roster["drafter"]["fn"](roster["drafter"]["name"], CODER_SYSTEM, prompt,
                                   max_tokens=20000, run_dir=run_dir)
    return parse_file_blocks(out)


REVIEWER_SYSTEM = (
    "You are the strict constraint reviewer of Infinity Code X (Qwen 3.8 Max "
    "oracle role — you review, you never draft). You veto anything that "
    "violates the constraints, regardless of how good it looks.")

CONSTRAINTS = """- animates ONLY transform/opacity/filter (shadows via pseudo-element opacity)
- no new dependencies, no new imports beyond existing packages
- no hardcoded colors outside CSS token definitions in src/index.css
- prefers-reduced-motion handled for any new animation
- existing component props/exports/behavior preserved
- TypeScript strict-safe (no implicit any, no unchecked possibly-undefined)
- no changes outside the declared files"""


def reviewer_pass(roster: dict, diff: str, run_dir: Path) -> dict:
    prompt = f"""Diff about to be applied to the Infinity Code X frontend:

{diff[:30000]}

Constraints (veto on ANY violation):
{CONSTRAINTS}

Reply STRICT JSON only:
{{"verdict": "SHIP" | "VETO", "veto_files": ["src/..."], "reasons": ["..."]}}
"""
    out = roster["reviewer"]["fn"](roster["reviewer"]["name"], REVIEWER_SYSTEM,
                                    prompt, max_tokens=2500, temperature=0.1,
                                    run_dir=run_dir)
    data = parse_json_reply(out)
    if isinstance(data, dict) and "verdict" in data:
        return data
    return {"verdict": "VETO", "veto_files": [], "reasons": [f"parse failure: {out[:160]}"]}


def progression_pass(roster: dict, summary: str, run_dir: Path) -> str:
    out = roster["progression"]["fn"](
        roster["progression"]["name"],
        "You are the progression oracle of Infinity Code X: you answer 'how "
        "can this be better next?'. Advise, never draft. Be specific and "
        "prioritized; max 12 lines.",
        f"State of the premium polish run:\n{summary}\n\nWhat should the next "
        "iteration focus on, and why?",
        max_tokens=1200, temperature=0.4, run_dir=run_dir)
    return out if not out.startswith("ERR") else ""


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------

def apply_with_gate(new_files: dict[str, str], run_dir: Path,
                    backup_dir: Path, roster: dict,
                    files: dict[str, str],
                    fixes: list[dict] | None = None) -> tuple[dict[str, str], str]:
    """Apply candidate files; build-gate with one coder retry; restore on fail.
    Returns (kept_new_files, status)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    # drop no-op drafts (model returned the file unchanged) before touching disk
    new_files = {
        rel: body for rel, body in new_files.items()
        if not (REPO / rel).is_file()
        or (REPO / rel).read_text(encoding="utf-8") != body
    }
    if not new_files:
        return {}, "no_op"
    old = {rel: (REPO / rel).read_text(encoding="utf-8") for rel in new_files
           if (REPO / rel).is_file()}
    for rel, body in old.items():
        (backup_dir / rel.replace("/", "__")).write_text(
            body, encoding="utf-8", newline="")

    def write_all(cand: dict[str, str]) -> None:
        for rel, body in cand.items():
            p = REPO / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8", newline="")

    write_all(new_files)
    ok, out = typecheck()
    if ok:
        ok, out = build_frontend()
    if not ok:
        # one feedback retry to the same drafter, same fix list — the error
        # text alone let the coder re-hallucinate; keep the assignment visible
        print("[premium-swarm] build gate FAILED — feeding errors back to coder")
        retry = coder_pass(roster, fixes or [], {**files, **new_files},
                           out[-2500:], run_dir)
        retry = {k: v for k, v in retry.items() if k in new_files}
        if retry:
            write_all(retry)
            ok, out = typecheck()
            if ok:
                ok, out = build_frontend()
            if ok:
                new_files = {**new_files, **retry}
    if not ok:
        for rel, body in old.items():
            (REPO / rel).write_text(body, encoding="utf-8", newline="")
        print(f"[premium-swarm] reverted — gate output tail: {out[-300:]}")
        return {}, "gate_failed"
    return new_files, "applied"


def one_iteration(roster: dict, run_dir: Path, it: int, focus: str,
                  top_fixes: int) -> dict:
    it_dir = run_dir / f"iter{it}"
    it_dir.mkdir(exist_ok=True)

    # 1. judge current state
    shots = capture(run_dir) if it == 0 else None
    if shots is None:
        shots = sorted((run_dir / "shots").glob("*.png"))
    before = [judge_shot(roster, s, focus, run_dir) for s in shots]
    before_total = round(sum(j["total"] for j in before) / max(1, len(before)), 2)
    for s, j in zip(shots, before):
        shutil.copy(s, it_dir / f"before_{s.name}")
    log(run_dir, "judge_before", iter=it, score=before_total,
        notes=[n for j in before for n in j.get("notes", [])][:8])

    # 2. critics (parallel)
    judge_notes = json.dumps(
        [{"view": s.name, **j} for s, j in zip(shots, before)],
        ensure_ascii=False, indent=1)[:6000]
    files = read_context_files()
    with ThreadPoolExecutor(max_workers=2) as ex:
        crits = list(ex.map(
            lambda i: critic_pass(roster, i, judge_notes,
                                  files_as_prompt(files), focus, run_dir),
            range(2)))
    defects = crits[0] + crits[1]
    # dedupe by (file, where), keep highest impact
    seen: dict[tuple, dict] = {}
    for d in defects:
        k = (d.get("file"), str(d.get("where", ""))[:60])
        if k not in seen or int(d.get("impact", 0)) > int(seen[k].get("impact", 0)):
            seen[k] = d
    fixes = sorted(seen.values(), key=lambda d: -int(d.get("impact", 0)))[:top_fixes]
    fixes = [f for f in fixes if ALLOW_RE.match(str(f.get("file", "")))]
    (it_dir / "fixes.json").write_text(json.dumps(fixes, indent=1, ensure_ascii=False),
                                       encoding="utf-8")
    log(run_dir, "fixes_selected", iter=it, n=len(fixes),
        files=sorted({f["file"] for f in fixes}))
    if not fixes:
        return {"iter": it, "status": "no_fixes", "before": before_total}

    # 3. coders (parallel, split fixes; each gets the FULL files it touches so
    #    prop contracts are never truncated into hallucinations)
    half = max(1, len(fixes) // 2)
    chunks = [fixes[:half], fixes[half:]] if len(fixes) > 1 else [fixes]

    def chunk_files(chunk: list[dict]) -> dict[str, str]:
        wanted = {str(f.get("file", "")) for f in chunk}
        return {rel: files[rel] for rel in wanted if rel in files} or files

    with ThreadPoolExecutor(max_workers=2) as ex:
        drafts = list(ex.map(
            lambda c: coder_pass(roster, c, chunk_files(c), "", run_dir),
            chunks))
    new_files: dict[str, str] = {}
    for d in drafts:
        new_files.update(d)
    if not new_files:
        return {"iter": it, "status": "no_drafts", "before": before_total}

    # 4. gate
    kept, status = apply_with_gate(new_files, run_dir, it_dir / "backups",
                                    roster, files, fixes=fixes)
    if status != "applied":
        return {"iter": it, "status": status, "before": before_total}

    # 5. reviewer veto
    diff = make_diff(files, kept)
    (it_dir / "changes.diff").write_text(diff, encoding="utf-8")
    verdict = reviewer_pass(roster, diff, run_dir)
    vetoed = set(verdict.get("veto_files", []))
    if verdict.get("verdict") == "VETO" and vetoed:
        for rel in vetoed:
            if rel in kept and (it_dir / "backups" / rel.replace("/", "__")).is_file():
                (REPO / rel).write_text(
                    (it_dir / "backups" / rel.replace("/", "__")).read_text(encoding="utf-8"),
                    encoding="utf-8", newline="")
                kept.pop(rel)
        log(run_dir, "reviewer_veto", iter=it, veto_files=sorted(vetoed),
            reasons=verdict.get("reasons", [])[:4])
        if not kept:
            build_frontend()
            return {"iter": it, "status": "vetoed", "before": before_total}
        build_frontend()

    # 6. re-judge
    shots2 = capture(run_dir)
    after = [judge_shot(roster, s, focus, run_dir) for s in shots2]
    after_total = round(sum(j["total"] for j in after) / max(1, len(after)), 2)
    for s, j in zip(shots2, after):
        shutil.copy(s, it_dir / f"after_{s.name}")
    log(run_dir, "judge_after", iter=it, score=after_total)

    # 7. keep or roll back whole iteration
    if after_total < before_total - 1.0:
        for rel in kept:
            bak = it_dir / "backups" / rel.replace("/", "__")
            if bak.is_file():
                (REPO / rel).write_text(bak.read_text(encoding="utf-8"),
                                         encoding="utf-8", newline="")
        build_frontend()
        log(run_dir, "iteration_reverted", iter=it, before=before_total,
            after=after_total)
        status = "score_dropped"
    else:
        status = "kept"

    return {"iter": it, "status": status, "before": before_total,
            "after": after_total, "applied": sorted(kept.keys())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=2)
    ap.add_argument("--top-fixes", type=int, default=4)
    ap.add_argument("--provider", choices=("auto", "kimi", "qwen"), default="auto")
    ap.add_argument("--backend-port", type=int, default=0,
                    help="sandbox backend port (default: first free from 8001)")
    ap.add_argument("--drafter", default=None,
                    help="provider ref override for the coder role, e.g. "
                         "deepseek/v4-flash | dashscope/qwen3-coder-flash | "
                         "moonshot/kimi-k2.6")
    ap.add_argument("--dry-run", action="store_true",
                    help="build + serve + capture only; zero API calls")
    args = ap.parse_args()

    global SANDBOX_API_PORT
    if args.backend_port:
        SANDBOX_API_PORT = args.backend_port
    elif not free_port(SANDBOX_API_PORT):
        for cand in range(8002, 8020):
            if free_port(cand):
                SANDBOX_API_PORT = cand
                break

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[premium-swarm] run dir: {run_dir}")

    # gate 0: repo must start green
    print("[premium-swarm] gate 0: tsc --noEmit …")
    ok, out = typecheck()
    if not ok:
        print(f"[premium-swarm] ABORT — repo is red before we touch it:\n{out[-1500:]}")
        sys.exit(1)
    print("[premium-swarm] gate 0: vite build …")
    ok, out = build_frontend()
    if not ok:
        print(f"[premium-swarm] ABORT — vite build red:\n{out[-1500:]}")
        sys.exit(1)
    print("[premium-swarm] gate 0 GREEN")

    if args.dry_run:
        print("[premium-swarm] DRY RUN: serving + capturing, no API calls")
        backend, httpd = start_servers(run_dir)
        try:
            shots = capture(run_dir)
            print(f"[premium-swarm] captured {len(shots)} shots:")
            for s in shots:
                print(f"  - {s}")
        finally:
            backend.terminate()
            httpd.shutdown()
        print("[premium-swarm] dry run complete — pipeline works")
        return

    roster = resolve_roster(args.provider, run_dir, drafter_ref=args.drafter)
    backend, httpd = start_servers(run_dir)
    focus = ""
    results = []
    try:
        for it in range(args.iterations):
            res = one_iteration(roster, run_dir, it, focus, args.top_fixes)
            results.append(res)
            log(run_dir, "iteration_done", **{k: v for k, v in res.items()
                                               if k != "applied"},
                applied=res.get("applied"))
            if res.get("status") in ("kept", "score_dropped"):
                focus = progression_pass(
                    roster,
                    f"iterations so far: {json.dumps(results, ensure_ascii=False)}",
                    run_dir)
                (run_dir / f"focus_iter{it}.md").write_text(focus, encoding="utf-8")
    finally:
        backend.terminate()
        httpd.shutdown()

    print("\n[premium-swarm] === SUMMARY ===")
    for r in results:
        print(f"  iter {r['iter']}: {r.get('status')} "
              f"{r.get('before', '?')} -> {r.get('after', '?')} "
              f"files={r.get('applied', [])}")
    print(f"[premium-swarm] api calls: {CALLS['n']} | run dir: {run_dir}")


if __name__ == "__main__":
    main()
