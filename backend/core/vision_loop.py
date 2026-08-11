"""Vision-in-the-Loop code generation for Infinity Code.

Implements the Kimi-K3 "Vision-in-the-Loop" pattern: after an Engineer
produces code, the Tester runs it, the Critic judges the resulting image
against a reference, and the loop refines the code until it passes or hits
max_iterations.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from backend.core.exec_utils import run_python
    from backend.core.critic_engine import CriticEngine
except ImportError:  # running with backend/ as the working directory
    from core.exec_utils import run_python  # type: ignore[no-redef]
    from core.critic_engine import CriticEngine  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Extensions we treat as visual outputs from generated code.
_IMAGE_SUFFIXES: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", "bmp", ".webp")


class VisionLoop:
    """Iteratively refine code using a vision critic."""

    def __init__(
        self,
        critic: CriticEngine,
        generate_code: Callable[[str], Tuple[str, float]],
        work_root: Path,
        score_threshold: float = 0.75,
        max_iterations: int = 2,
    ) -> None:
        """
        Args:
            critic: The vision CriticEngine for scoring outputs.
            generate_code: Callable that takes a prompt and returns (code_text, cost_aud).
            work_root: Directory where temporary attempt folders are created.
            score_threshold: Minimum critic score to accept without further refinement.
            max_iterations: Maximum refine loops (including the initial generation).
        """
        self.critic = critic
        self.generate_code = generate_code
        self.work_root = work_root
        self.score_threshold = score_threshold
        self.max_iterations = max_iterations

    async def refine(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        reference_path: Optional[Path],
        attachment_context: str = "",
    ) -> Dict[str, Any]:
        """Run the vision-in-the-loop pipeline.

        Returns a dict compatible with the swarm's attempt result shape:
            {
                "status": "success" | "failed",
                "cost": float,
                "score": float | None,
                "critique_dict": dict | None,
                "image_url": str | None,
                "artifact_path": str,
                "code": str,
                "iterations": int,
            }
        """
        total_cost: float = 0.0
        best_code: str = ""
        best_score: Optional[float] = None
        best_critique: Optional[Dict[str, Any]] = None
        best_image: Optional[Path] = None
        best_artifact: Path = self.work_root / f"{mission_id}_vision_main.py"

        feedback: str = ""
        for iteration in range(1, self.max_iterations + 1):
            prompt = (
                f"You are the Engineer agent. Goal:\n{goal}\n\nPlan:\n{plan_text}\n\n"
                "Write ONE complete, runnable Python script implementing the goal. "
                "If the goal produces anything visual, save it as 'out.png' in the "
                "current working directory. Respond with ONLY the script inside a "
                "single ```python code block."
                + (f"\n\nFix these problems from the last attempt: {feedback}" if feedback else "")
                + attachment_context
            )

            code_text, code_cost = await asyncio.to_thread(self.generate_code, prompt)
            total_cost += code_cost
            code = self._extract_code(code_text)
            if not code.strip():
                logger.warning("VisionLoop iteration %d returned no code", iteration)
                break

            best_code = code
            iter_dir = self.work_root / f"{mission_id}_vision_iter_{iteration}"
            iter_dir.mkdir(parents=True, exist_ok=True)
            code_path = iter_dir / "main.py"
            code_path.write_text(code, encoding="utf-8")

            # Run the code
            exec_result = await asyncio.to_thread(run_python, code_path, iter_dir)
            if exec_result.get("returncode", -1) != 0:
                feedback = f"Execution failed: {exec_result.get('stderr', '')[:600]}"
                continue

            # Find produced image
            images: List[Path] = sorted(
                p for p in iter_dir.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES
            )
            if not images:
                feedback = "No visual output was produced (expected an image file)."
                continue

            best_image = images[0]

            # Critique
            if reference_path is None:
                # No reference — accept first success
                best_score = 1.0
                best_critique = {"note": "no reference; accepted on execution success"}
                break

            critique_result = self.critic.critique(best_image, reference_path, goal)
            best_critique = {
                "score": critique_result.score,
                "verdict": critique_result.verdict,
                "reasoning": critique_result.reasoning,
            }
            best_score = critique_result.score

            if best_score is not None and best_score >= self.score_threshold:
                logger.info(
                    "VisionLoop accepted at iteration %d (score %.2f)",
                    iteration,
                    best_score,
                )
                break

            feedback = critique_result.reasoning or "The output did not match the reference well enough."
            logger.info(
                "VisionLoop iteration %d score %.2f below threshold %.2f; refining...",
                iteration,
                best_score or 0.0,
                self.score_threshold,
            )

        if best_code:
            best_artifact.write_text(best_code, encoding="utf-8")

        return {
            "status": "success" if (best_score is not None and best_score >= self.score_threshold) else "failed",
            "cost": total_cost,
            "score": best_score,
            "critique_dict": best_critique,
            "image_url": str(best_image) if best_image else None,
            "artifact_path": str(best_artifact),
            "code": best_code,
            "iterations": iteration if best_code else 0,
        }

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
