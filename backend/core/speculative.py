"""Speculative code generation for Infinity Code.

K3-style speculative decoding for *code*: a fast, cheap draft model
generates the initial script; a strong target model verifies (and if
necessary rewrites) it.  This cuts latency on easy problems while keeping
quality high on hard ones.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

try:
    from backend.core.exec_utils import run_python
except ImportError:  # running with backend/ as the working directory
    from core.exec_utils import run_python  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


class SpeculativeCoder:
    """Draft with a fast model, verify with a strong one."""

    def __init__(
        self,
        draft_call: Callable[[str], Tuple[str, float]],
        target_call: Callable[[str], Tuple[str, float]],
        work_root: Path,
        accept_threshold: float = 0.7,
    ) -> None:
        """
        Args:
            draft_call:  (prompt) -> (code_text, cost_aud)  — fast/cheap model
            target_call: (prompt) -> (code_text, cost_aud)  — strong model
            work_root: Directory for temporary runs.
            accept_threshold: If the draft executes and a lightweight heuristic
                (exit-0 + non-empty output) passes, skip the target verification.
                Set to 1.0 to *always* verify with the target model.
        """
        self.draft_call = draft_call
        self.target_call = target_call
        self.work_root = work_root
        self.accept_threshold = accept_threshold

    async def generate(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        feedback: str = "",
    ) -> Dict[str, Any]:
        """Run speculative generation.

        Returns dict:
            {
                "status": "success" | "failed",
                "cost": float,
                "code": str,
                "artifact_path": str,
                "verified": bool,   # True if target model approved/rewrote
                "draft_ok": bool,
            }
        """
        total_cost: float = 0.0
        draft_prompt = self._build_prompt(goal, plan_text, feedback, note="")
        draft_text, draft_cost = await asyncio.to_thread(self.draft_call, draft_prompt)
        total_cost += draft_cost
        draft_code = self._extract_code(draft_text)

        draft_ok = False
        if draft_code.strip():
            draft_dir = self.work_root / f"{mission_id}_spec_draft"
            draft_dir.mkdir(parents=True, exist_ok=True)
            draft_path = draft_dir / "main.py"
            draft_path.write_text(draft_code, encoding="utf-8")
            result = await asyncio.to_thread(run_python, draft_path, draft_dir)
            draft_ok = result.get("returncode", -1) == 0 and bool(
                result.get("stdout", "").strip() or result.get("stderr", "").strip()
            )

        # Fast-heuristic accept: if the draft ran clean, just ship it.
        if draft_ok and self.accept_threshold < 1.0:
            artifact = self.work_root / f"{mission_id}_spec_main.py"
            artifact.write_text(draft_code, encoding="utf-8")
            logger.info("SpeculativeCoder: draft accepted (fast path)")
            return {
                "status": "success",
                "cost": total_cost,
                "code": draft_code,
                "artifact_path": str(artifact),
                "verified": False,
                "draft_ok": True,
            }

        # Target verification / rewrite
        verify_prompt = self._build_prompt(
            goal,
            plan_text,
            feedback,
            note=(
                f"A draft model produced this code:\n\n```python\n{draft_code}\n```\n\n"
                "Please review it, fix any bugs, and return the corrected script. "
                "If the draft is already perfect, return it unchanged."
                if draft_code.strip()
                else "The draft model failed to produce code. Please write the script from scratch."
            ),
        )
        target_text, target_cost = await asyncio.to_thread(self.target_call, verify_prompt)
        total_cost += target_cost
        final_code = self._extract_code(target_text)

        artifact = self.work_root / f"{mission_id}_spec_main.py"
        artifact.write_text(final_code, encoding="utf-8")

        final_ok = bool(final_code.strip())
        logger.info("SpeculativeCoder: target rewrite complete (verified=%s)", draft_ok is False or not final_ok)
        return {
            "status": "success" if final_ok else "failed",
            "cost": total_cost,
            "code": final_code,
            "artifact_path": str(artifact),
            "verified": True,
            "draft_ok": draft_ok,
        }

    @staticmethod
    def _build_prompt(goal: str, plan_text: str, feedback: str, note: str) -> str:
        base = (
            f"You are the Engineer agent. Goal:\n{goal}\n\nPlan:\n{plan_text}\n\n"
            "Write ONE complete, runnable Python script implementing the goal. "
            "If the goal produces anything visual, save it as 'out.png' in the "
            "current working directory. Respond with ONLY the script inside a "
            "single ```python code block."
        )
        if feedback:
            base += f"\n\nFix these problems from the last attempt: {feedback}"
        if note:
            base += f"\n\n{note}"
        return base

    @staticmethod
    def _extract_code(text: str) -> str:
        if not text:
            return ""
        fenced = re.search(r"```python\s*([\s\S]*?)```", text)
        if fenced:
            code = fenced.group(1).strip()
        else:
            fenced = re.search(r"```\s*([\s\S]*?)```", text)
            code = fenced.group(1).strip() if fenced else text.strip()
        lines = [ln for ln in code.splitlines() if not ln.strip().startswith("```")]
        return "\n".join(lines).strip()
