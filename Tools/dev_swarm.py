#!/usr/bin/env python
"""Infinity Code dev-swarm — a re-runnable Kimi-powered feature builder.

Pipeline: PLAN (K3, staff-tier reasoning) -> BUILD (k2.7-code) -> REVIEW (k2.6).
It writes a plan + per-file proposals + a review to Tools/proposals/<slug>/ for a
human to inspect and apply. It never edits the repo directly — proposals only —
so it is safe to run repeatedly and hard while iterating.

Usage:
  python Tools/dev_swarm.py "Add a keyboard shortcut Ctrl+/ that focuses search"
  python Tools/dev_swarm.py --spec path/to/feature.md
  python Tools/dev_swarm.py "..." --context src/App.tsx src/hooks/useMissions.ts

Models are reached through the shared client at D:/genesis/infra/llm_client.py
(K3 via OpenRouter today; MOONSHOT direct when the balance is topped up).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROPOSALS = REPO / "Tools" / "proposals"
sys.path.insert(0, r"D:/genesis/infra")

try:
    import llm_client  # type: ignore
except Exception as exc:  # noqa: BLE001
    print(f"FATAL: cannot import shared llm_client: {exc}")
    sys.exit(1)

PLANNER = "kimi-k3"        # decompose + design; thinking-only, 1M ctx
BUILDER = "kimi-k2.7-code" # write the actual code
REVIEWER = "kimi-k2.6"     # critique the proposal


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48] or "feature").rstrip("-")


def read_context(paths: list[str]) -> str:
    chunks: list[str] = []
    for rel in paths:
        p = (REPO / rel) if not Path(rel).is_absolute() else Path(rel)
        try:
            body = p.read_text(encoding="utf-8", errors="ignore")
            if len(body) > 16000:
                body = body[:16000] + "\n… (truncated)"
            chunks.append(f"=== FILE: {rel} ===\n{body}")
        except OSError as exc:
            chunks.append(f"=== FILE: {rel} (unreadable: {exc}) ===")
    return "\n\n".join(chunks)


def call(model: str, system: str, user: str, max_tokens: int = 20000) -> str:
    print(f"  → {model} …", flush=True)
    out = llm_client.chat(model, system=system, user=user, max_tokens=max_tokens)
    if isinstance(out, str) and out.startswith("ERR("):
        raise RuntimeError(out)
    return out if isinstance(out, str) else json.dumps(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("feature", nargs="?", help="feature description")
    ap.add_argument("--spec", help="path to a markdown spec file")
    ap.add_argument("--context", nargs="*", default=[], help="repo files to feed as context")
    args = ap.parse_args()

    if args.spec:
        feature = Path(args.spec).read_text(encoding="utf-8")
    elif args.feature:
        feature = args.feature
    else:
        ap.error("provide a feature description or --spec")

    slug = slugify(args.feature or Path(args.spec).stem)
    out_dir = PROPOSALS / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = read_context(args.context) if args.context else "(no files provided)"

    print(f"\n=== dev-swarm: {slug} ===")
    print(f"proposals → {out_dir}")

    # 1. PLAN (K3)
    plan = call(
        PLANNER,
        system=(
            "You are the tech lead for Infinity Code, a React+Tauri+FastAPI desktop "
            "agent-swarm app. Produce a precise, minimal implementation plan: which "
            "files to add/change, the approach, and any risks. Match existing "
            "conventions. Be concrete and terse — a coder will implement your plan."
        ),
        user=f"FEATURE:\n{feature}\n\nRELEVANT CODE:\n{ctx}",
    )
    (out_dir / "PLAN.md").write_text(plan, encoding="utf-8")

    # 2. BUILD (k2.7-code)
    build = call(
        BUILDER,
        system=(
            "You are a senior engineer. Implement the plan as complete, paste-ready "
            "code. For each file, output a fenced block preceded by a line "
            "'FILE: <relative/path>'. Full file contents or a clearly-marked unified "
            "diff. TypeScript strict + React function components; Python 3.12 + FastAPI "
            "conventions. No placeholders, no TODOs."
        ),
        user=f"PLAN:\n{plan}\n\nEXISTING CODE:\n{ctx}",
        max_tokens=28000,
    )
    (out_dir / "BUILD.md").write_text(build, encoding="utf-8")

    # 3. REVIEW (k2.6)
    review = call(
        REVIEWER,
        system=(
            "You are a harsh reviewer. Given a plan and its implementation, list "
            "concrete defects (correctness, edge cases, convention mismatches, "
            "missing wiring) ranked by severity, then a short verdict: SHIP / FIX / "
            "REDO. Be specific with file references."
        ),
        user=f"PLAN:\n{plan}\n\nIMPLEMENTATION:\n{build}",
    )
    (out_dir / "REVIEW.md").write_text(review, encoding="utf-8")

    print("\n=== REVIEW (summary) ===")
    print(review[-1200:])
    print(f"\nAll artifacts in: {out_dir}")
    print("Inspect PLAN.md / BUILD.md / REVIEW.md, then apply the good parts.")


if __name__ == "__main__":
    main()
