"""DeepSeek vision loop: screenshot -> Qwen-MM critique -> fix -> re-check.

The loop gives text-only coding models (DeepSeek, local, MiniMax, ...) a
vision feedback channel for frontend work:

    for i in range(max_iters):
        shot = screenshot_fn(i)                 # caller-provided (Playwright etc.)
        critique = critique_image(shot, criteria)  # Qwen-MM, strict JSON
        score = parse score
        stop when: score >= target, or max_iters reached, or the score
                   improvement between iterations < delta (converged).

The fix step is pluggable (apply_fix_fn) so the app can wire it to the
swarm/workspace-edit pipeline; the CLI demo prints fixes.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("infinity.vision_loop")

DEFAULT_MAX_ITERS = 3
DEFAULT_DELTA = 0.05
DEFAULT_TARGET = 9.0

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+(.+)$", re.MULTILINE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_SCORE_RE = re.compile(r"(?:score|rating|verdict)[^\d]{0,40}(\d{1,2}(?:\.\d)?)", re.IGNORECASE)
_FIX_VERB_RE = re.compile(r"\b(change|replace|add|use|increase|decrease|move|remove|fix|adjust|set|make)\b", re.IGNORECASE)


def _strip_think(reply: str) -> str:
    return _THINK_RE.sub(" ", reply or "")


def _prose_fallback(reply: str) -> Dict[str, Any]:
    """Weak-model tolerance: parse a prose critique when strict JSON is absent.

    Score: the number near "score/rating", else the first 0-10 number in the
    text. Issues/fixes: bullet lines; lines with fix-verbs count as fixes.
    """
    text = _strip_think(reply)
    score = 0.0
    m = _SCORE_RE.search(text)
    if not m:
        for num in re.findall(r"(?:\b|\s)(10|\d(?:\.\d)?)\b", text):
            val = float(num)
            if 0.0 <= val <= 10.0:
                score = val
                break
    else:
        score = max(0.0, min(10.0, float(m.group(1))))

    bullets = [b.strip() for b in _BULLET_RE.findall(text)][:10]
    issues = [b for b in bullets if not _FIX_VERB_RE.search(b)]
    fixes = [b for b in bullets if _FIX_VERB_RE.search(b)]
    if not issues and bullets:
        issues = bullets
    return {"score": score, "issues": issues[:8], "fixes": fixes[:8]}


def parse_critique(reply: Optional[str]) -> Dict[str, Any]:
    """Extract {score, issues, fixes} from a critique reply.

    Strict JSON first (cloud models); a prose fallback covers weak local
    models that answer in bullets instead of JSON. Conservative: any total
    failure yields score 0.0 with an explanatory issue so the loop does not
    claim success on garbage.
    """
    if not reply:
        return {"score": 0.0, "issues": ["no critique returned"], "fixes": []}
    m = _JSON_RE.search(reply)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                score = float(data.get("score", 0.0) or 0.0)
                bad_score = not math.isfinite(score)
                if bad_score:
                    score = 0.0
                score = max(0.0, min(10.0, score))
                issues = [str(x) for x in data.get("issues", [])[:8]]
                fixes = [str(x) for x in data.get("fixes", [])[:8]]
                if bad_score:
                    issues.insert(0, "critique score was not a finite number")
                return {"score": score, "issues": issues, "fixes": fixes}
        except json.JSONDecodeError:
            pass
    fallback = _prose_fallback(reply)
    if fallback["issues"] or fallback["score"] > 0:
        return fallback
    return {"score": 0.0, "issues": ["critique was not JSON"], "fixes": []}


def run_vision_loop(
    screenshot_fn: Callable[[int], str],
    apply_fix_fn: Optional[Callable[[List[str], int], Any]] = None,
    criteria: str = "clean, premium, aligned, no generic AI look",
    max_iters: int = DEFAULT_MAX_ITERS,
    delta: float = DEFAULT_DELTA,
    target: float = DEFAULT_TARGET,
    critique_fn: Optional[Callable[[str, str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Run the critique-fix loop; returns the report dict.

    screenshot_fn(i) -> path of the screenshot for iteration i (0-based).
    apply_fix_fn(fixes, i) -> applies the suggested fixes (optional in CLI
    demo mode). critique_fn is pluggable for tests; defaults to the real
    Qwen-MM critique.
    """
    if critique_fn is None:
        try:
            from backend.core import vision_assist
        except ImportError:  # running with backend/ as the working directory
            from core import vision_assist
        critique_fn = vision_assist.critique_image

    iterations: List[Dict[str, Any]] = []
    prev_score: Optional[float] = None
    verdict = "not_started"

    for i in range(max_iters):
        try:
            shot = screenshot_fn(i)
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            logger.warning("vision_loop screenshot failed at iter %d: %s", i, exc)
            verdict = "screenshot_failed"
            break
        reply = critique_fn(shot, criteria)
        parsed = parse_critique(reply)
        score = parsed["score"]
        iterations.append({"iteration": i + 1, "screenshot": shot, **parsed})

        if score >= target:
            verdict = "passed"
            break
        if prev_score is not None and (score - prev_score) < delta and score > 0:
            # No meaningful improvement -> converged; stop before burning iters.
            verdict = "converged"
            break
        prev_score = score
        if i < max_iters - 1 and parsed["fixes"] and apply_fix_fn is not None:
            try:
                apply_fix_fn(parsed["fixes"], i)
            except Exception as exc:  # noqa: BLE001
                logger.warning("vision_loop apply_fix failed at iter %d: %s", i, exc)
    else:
        verdict = "max_iters"

    scores = [it["score"] for it in iterations]
    return {
        "verdict": verdict,
        "iterations": iterations,
        "scores": scores,
        "best_score": max(scores) if scores else 0.0,
        "criteria": criteria,
    }


def demo_cli() -> None:
    """Standalone demo: screenshot a local file/URL via Playwright, critique it."""
    import argparse
    import os
    import sys
    import tempfile

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Vision loop demo (screenshot -> Qwen-MM critique)")
    parser.add_argument("target", help="file:// path or http(s) URL to screenshot")
    parser.add_argument("--criteria", default="clean, premium, aligned, no generic AI look")
    parser.add_argument("--max-iters", type=int, default=DEFAULT_MAX_ITERS)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    out_dir = os.path.join(tempfile.gettempdir(), "vision_loop_demo")
    os.makedirs(out_dir, exist_ok=True)

    def screenshot_fn(i: int) -> str:
        shot_path = os.path.join(out_dir, f"shot_{i}.png")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(args.target, wait_until="networkidle")
            page.screenshot(path=shot_path, full_page=False)
            browser.close()
        return shot_path

    def apply_fix_fn(fixes: List[str], i: int) -> None:
        # CLI demo: fixes are printed, not applied. The app wires its swarm
        # here for real auto-fix passes.
        print(f"  [iter {i + 1}] suggested fixes:")
        for f in fixes:
            print(f"    - {f}")

    report = run_vision_loop(screenshot_fn, apply_fix_fn, args.criteria, args.max_iters)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    demo_cli()
