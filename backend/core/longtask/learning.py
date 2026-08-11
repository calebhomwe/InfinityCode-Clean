"""Self-teaching flywheel for Long Tasks.

Three jobs, all cheap and failure-proof:
- extract_lessons: after a run, one capped LLM call distils <=3 reusable
  one-line lessons from what happened;
- recall_lessons: before a run, embed the goal and cosine-recall the most
  relevant past lessons so the builder starts smarter;
- frontend_related: detect UI work so the frontend playbook / vision review
  only ever spend on tasks that need them;
- maybe_distill_playbook: nightly, collapse enough frontend lessons into a
  persistent SkillEngine playbook so quality compounds without spend.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_FRONT_RE = re.compile(
    r"\b(ui|frontend|front-end|component|components|page|pages|style|styling|"
    r"css|design|layout|landing|dashboard|button|animation|vibe)\b", re.I)
_FRONT_EXTS = (".tsx", ".jsx", ".css", ".scss", ".html", ".vue", ".svelte")

# Distil once we have enough un-distilled frontend lessons to be worth a call.
DISTILL_THRESHOLD = 8
PLAYBOOK_NAME = "frontend-vibe-learned"

_EXTRACT_PROMPT = (
    "A coding agent just finished a long task. From its goal, outcome and any "
    "errors, write AT MOST 3 reusable lessons for future runs. Each lesson is "
    "one short line starting with '- ', phrased as a rule (e.g. '- always run "
    "the test suite before finishing'). No preamble, no headings.\n\n"
    "GOAL: {goal}\nSTATUS: {status}\nRESULT: {result}\nERRORS:\n{errors}"
)

_DISTILL_PROMPT = (
    "Merge these frontend lessons learned from past coding runs into ONE "
    "tight playbook of at most 12 concrete rules for building beautiful, "
    "distinctive frontend UI. One rule per line starting with '- '. "
    "Drop duplicates. No preamble.\n\nLESSONS:\n{lessons}"
)


def frontend_related(task: Optional[Dict], steps: Optional[List[Dict]]) -> bool:
    """True when the goal or the touched files say this was UI work."""
    goal = str((task or {}).get("goal", ""))
    if _FRONT_RE.search(goal):
        return True
    for s in steps or []:
        args = s.get("args_json") or "{}"
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001 - unparseable args: skip
                args = {}
        if str(args.get("path", "")).lower().endswith(_FRONT_EXTS):
            return True
    return False


def extract_lessons(task: Dict, steps: List[Dict],
                  chat_fn: Callable) -> List[str]:
    """Distil <=3 lessons from a finished run. Never raises, never blocks."""
    try:
        errors = [str(s.get("result_json") or "")[:150]
                  for s in (steps or []) if s.get("kind") == "tool_error"][:4]
        prompt = _EXTRACT_PROMPT.format(
            goal=str(task.get("goal", ""))[:400],
            status=str(task.get("status", "")),
            result=str(task.get("result", ""))[:400],
            errors="\n".join(errors) or "(none)")
        reply = chat_fn([{"role": "user", "content": prompt}], max_tokens=600)
        text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
        lessons: List[str] = []
        for line in text.splitlines():
            line = line.strip().lstrip("-0123456789. \t")
            if 10 <= len(line) <= 300:
                lessons.append(line)
            if len(lessons) >= 3:
                break
        return lessons
    except Exception as exc:  # noqa: BLE001 - learning must never break runs
        logger.info("lesson extraction skipped: %s", exc)
        return []


def recall_lessons(goal: str, embed_fn: Callable, journal: Any,
                   top_k: int = 3, min_score: float = 0.25) -> List[Dict]:
    """Embed the goal and cosine-recall the most relevant past lessons."""
    try:
        vecs = embed_fn([goal])
        if not vecs:
            return []
        return journal.search_lessons(vecs[0], top_k=top_k, min_score=min_score)
    except Exception as exc:  # noqa: BLE001
        logger.info("lesson recall skipped: %s", exc)
        return []


def maybe_distill_playbook(journal: Any, chat_fn: Callable,
                           skills_dir: Path) -> Optional[Path]:
    """Collapse >=DISTILL_THRESHOLD un-distilled frontend lessons into a
    SkillEngine-shaped playbook JSON. Returns the written path, else None."""
    try:
        pending = journal.lessons(kind="frontend", undistilled_only=True)
        if len(pending) < DISTILL_THRESHOLD:
            return None
        block = "\n".join("- " + str(l["lesson"]) for l in pending[:40])
        reply = chat_fn([{"role": "user",
                          "content": _DISTILL_PROMPT.format(lessons=block)}],
                        max_tokens=900)
        text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
        rules = [ln.strip().lstrip("- \t") for ln in text.splitlines()
                 if 10 <= len(ln.strip().lstrip("- \t")) <= 300][:12]
        if not rules:
            return None
        skills_dir = Path(skills_dir)
        skills_dir.mkdir(parents=True, exist_ok=True)
        path = skills_dir / f"{PLAYBOOK_NAME}.json"
        path.write_text(json.dumps({
            "name": PLAYBOOK_NAME,
            "topic": "frontend design playbook (self-distilled)",
            "source_type": "self-training",
            "steps": [{"order": i + 1, "instruction": r}
                      for i, r in enumerate(rules)],
            "verified": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        }, indent=2), encoding="utf-8")
        journal.mark_lessons_distilled([l["id"] for l in pending])
        logger.info("Distilled %d frontend lessons into %s", len(pending), path)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("playbook distillation skipped: %s", exc)
        return None


__all__ = ["frontend_related", "extract_lessons", "recall_lessons",
           "maybe_distill_playbook", "PLAYBOOK_NAME", "DISTILL_THRESHOLD"]
