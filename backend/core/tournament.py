"""Parallel candidate tournament for Infinity Code.

Instead of trusting a single Engineer, this module spawns N Engineers who each
attack the same goal with a DIFFERENT MODEL, temperature and strategy. Every
candidate writes one runnable Python script, all of them run concurrently in
real subprocesses, and the best surviving candidate wins.

Diversity is real, not cosmetic: candidates cycle through DIVERSITY_POOL so a
5-way race explores five different reasoners. (Sampling one model five times
with different adjectives just yields five near-identical programs.)

Scoring is evidence-based, in priority order:
  1. Reference image + vision Critic, when both are available.
  2. Otherwise a goal-fit judgement (0.0-1.0) from the inspector model — does
     the program actually DO what was asked?
  3. Only if judging is unavailable does it fall back to the old binary signal.
Step 2 matters: without it every non-crashing candidate scored an identical 1.0
and the "winner" was really just the cheapest program that didn't crash.
Ties break toward the cheaper, then faster candidate.

Nothing here can crash the tournament: a candidate that throws anywhere in its
flow is recorded as a failed candidate with score 0.0. Malformed model output is
tolerated by the shared ``extract_code`` fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from backend.core.critic_engine import CriticEngine
    from backend.core.exec_utils import extract_code, run_python
    from backend.core.router import ModelRouter, ModelSpec
    from backend.tools.openrouter_client import OpenRouterClient, OpenRouterError
except ImportError:  # running with backend/ as the working directory
    from core.critic_engine import CriticEngine  # type: ignore[no-redef]
    from core.exec_utils import extract_code, run_python  # type: ignore[no-redef]
    from core.router import ModelRouter, ModelSpec  # type: ignore[no-redef]
    from tools.openrouter_client import (  # type: ignore[no-redef]
        OpenRouterClient,
        OpenRouterError,
    )

logger = logging.getLogger(__name__)

CANDIDATE_EXEC_TIMEOUT_SECONDS: int = 60
ENGINEER_MAX_TOKENS: int = 4000
_IMAGE_SUFFIXES: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
_PREFERRED_IMAGE_NAME: str = "out.png"

# 20 distinct engineering strategies, A..T. Each candidate Engineer is told to
# implement the same goal while optimising for exactly one of these.
# K3 rubric-first judging (tech report §4.1.2): the judge derives explicit,
# task-specific criteria BEFORE scoring, instead of one-shot "pick a winner".
# Used when rubric generation is unavailable.
_DEFAULT_RUBRIC: List[Tuple[str, float]] = [
    ("correctness", 0.40),
    ("robustness", 0.20),
    ("simplicity", 0.15),
    ("performance", 0.15),
    ("style", 0.10),
]
# A candidate longer than this multiple of the median candidate length is
# verbosity-hacking the judge — K3's rule is an automatic loss, approximated
# here by capping its score below any clean candidate.
_VERBOSITY_CAP_FACTOR: float = 2.0
_VERBOSITY_SCORE_CAP: float = 0.49

STRATEGIES: List[Tuple[str, str]] = [    ("A", "Fastest implementation, minimal code"),
    ("B", "Most robust, full error handling and input validation"),
    ("C", "Most creative, unconventional approach"),
    ("D", "Most readable, heavy comments and descriptive names"),
    ("E", "Most performant, optimized data structures and hot paths"),
    ("F", "Most secure, defensive against malformed or malicious input"),
    ("G", "Most testable, small pure functions with clear seams"),
    ("H", "Most portable, standard-library only and cross-platform paths"),
    ("I", "Most elegant, concise idiomatic Python"),
    ("J", "Most defensive, guards every edge case and failure mode"),
    ("K", "Most modular, clean separation into reusable components"),
    ("L", "Most memory-efficient, streaming and lazy evaluation"),
    ("M", "Most maintainable, self-documenting structure"),
    ("N", "Most explicit, no magic and everything spelled out"),
    ("O", "Most functional, pure functions and immutable data"),
    ("P", "Most object-oriented, well-encapsulated classes"),
    ("Q", "Most concurrent, parallelism wherever it helps"),
    ("R", "Most fault-tolerant, retries and graceful degradation"),
    ("S", "Most minimal-dependency, zero third-party imports"),
    ("T", "Most observable, thorough step-by-step logging"),
]

# Real diversity comes from racing DIFFERENT MODELS, not one model wearing
# different adjectives — two samples of the same model at the same temperature
# converge on nearly the same program. Candidates cycle through this pool, so a
# 5-way tournament genuinely explores 5 different engines. Ids are resolved
# against the router's pricing registry; unknown ones are skipped.
DIVERSITY_POOL: List[str] = [
    "dashscope/qwen3-coder-480b-a35b-instruct",  # fastest code engine (~1.5s)
    "moonshotai/kimi-k2.7-code",   # code specialist, different lineage
    "moonshotai/kimi-k3",          # deep reasoner
    "dashscope/qwen3.7-max-2026-05-17",  # benchmarked reasoning pick
    "google/gemini-3.1-flash-lite",  # fast + cheap outlier
]
# Temperature is varied alongside the model so even a repeated engine explores
# a different part of the space.
DIVERSITY_TEMPS: List[float] = [0.2, 0.6, 0.4, 0.8, 0.3]


class TournamentRunner:
    """Runs many strategy-varied Engineers in parallel and picks the winner."""

    def __init__(
        self,
        client: OpenRouterClient,
        router: ModelRouter,
        critic: Optional[CriticEngine],
        outputs_dir: Path,
    ) -> None:
        self.client: OpenRouterClient = client
        self.router: ModelRouter = router
        self.critic: Optional[CriticEngine] = critic
        self.outputs_dir: Path = Path(outputs_dir)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _new_candidate(letter: str, strategy: str) -> Dict[str, Any]:
        """A candidate record pre-filled with safe defaults."""
        return {
            "letter": letter,
            "strategy": strategy,
            "model": None,
            "temperature": None,
            "code_path": None,
            "code_len": 0,
            "returncode": None,
            "image_url": None,
            "image_path": None,
            "score": 0.0,
            "cost_aud": 0.0,
            "exec_ms": 0,
            "selected": False,
            "error": None,
        }

    @staticmethod
    def _build_prompt(goal: str, plan: str, strategy: str) -> str:
        """Engineer prompt = goal + plan + strategy + fixed output contract."""
        return (
            f"{goal}\n\n{plan}\n\n"
            f"Strategy: {strategy}. Write ONE complete runnable Python script; "
            "if it produces anything visual save it as out.png in the current "
            "directory. Respond with ONLY a python code block."
        )

    def _find_image(self, candidate_dir: Path) -> Optional[Path]:
        """Return the produced image (prefer out.png, else any image), or None."""
        try:
            preferred: Path = candidate_dir / _PREFERRED_IMAGE_NAME
            if preferred.is_file():
                return preferred
            images: List[Path] = sorted(
                path
                for path in candidate_dir.iterdir()
                if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
            )
            return images[0] if images else None
        except OSError as exc:
            logger.error("Image lookup failed in %s: %s", candidate_dir, exc)
            return None

    def _image_url_for(self, image_path: Path) -> Optional[str]:
        """Public /outputs URL for an image, or None if it is outside outputs_dir."""
        try:
            relative: Path = Path(image_path).relative_to(self.outputs_dir)
            return "/outputs/" + relative.as_posix()
        except ValueError:
            return None

    def _build_rubric(self, goal: str) -> List[Tuple[str, float]]:
        """K3 rubric-first protocol: the judge derives explicit, task-specific
        criteria (with weights summing to 1.0) BEFORE seeing any candidate.
        One extra inspector call per tournament; falls back to the default
        rubric if generation fails."""
        judge_spec = self.router.get_spec("inspector")
        prompt = (
            "Design the grading rubric for this coding task. Pick 4-6 criteria "
            "that genuinely discriminate a correct solution for THIS task, each "
            "with a weight; weights must sum to 1.0.\n\n"
            f"TASK:\n{goal}\n\n"
            'Reply with ONLY JSON: {"criteria": [{"name": "...", "weight": 0.4}, ...]}'
        )
        try:
            result = self.client.chat(
                model_id=judge_spec.id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
            )
            text = (result.get("text") or "").strip()
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return list(_DEFAULT_RUBRIC)
            raw = json.loads(match.group(0)).get("criteria", [])
            criteria: List[Tuple[str, float]] = []
            for item in raw:
                name = str(item.get("name", "")).strip().lower()
                weight = float(item.get("weight", 0.0))
                if name and weight > 0:
                    criteria.append((name, weight))
            if not criteria:
                return list(_DEFAULT_RUBRIC)
            total = sum(w for _, w in criteria)
            return [(name, w / total) for name, w in criteria]
        except Exception as exc:  # noqa: BLE001 - judging must never crash a run
            logger.warning("Rubric generation unavailable: %s", exc)
            return list(_DEFAULT_RUBRIC)

    def _judge_goal_fit(
        self,
        goal: str,
        code: str,
        stdout: str,
        returncode: Optional[int],
        rubric: Optional[List[Tuple[str, float]]] = None,
    ) -> Optional[float]:
        """Reference-free discriminator: how well does this candidate actually
        satisfy the goal, 0.0-1.0?

        Scored against the explicit rubric (K3 protocol): per-criterion scores,
        aggregated by weight — not a one-shot overall impression, which is the
        weakest known LLM-judge setup. Without this every non-crashing
        candidate scored an identical 1.0, so the "winner" was decided entirely
        by the cost tie-break — the cheapest program that didn't crash,
        regardless of whether it did the right thing. Returns None if judging
        is unavailable, so callers can fall back.
        """
        judge_spec = self.router.get_spec("inspector")
        criteria: List[Tuple[str, float]] = rubric or list(_DEFAULT_RUBRIC)
        rubric_lines = "\n".join(
            f"- {name} (weight {weight:.2f})" for name, weight in criteria
        )
        prompt = (
            "Score how well this program accomplishes the goal against each "
            "rubric criterion. Judge the OUTCOME, not the style.\n\n"
            f"GOAL:\n{goal}\n\n"
            f"RUBRIC:\n{rubric_lines}\n\n"
            f"PROGRAM:\n```python\n{code[:5000]}\n```\n\n"
            f"EXIT CODE: {returncode}\nSTDOUT:\n{(stdout or '')[:1200]}\n\n"
            "1.0 = fully and correctly satisfies the criterion. 0.5 = partially. "
            "0.0 = wrong, stubbed, or not at all.\n"
            'Reply with ONLY JSON: {"scores": {"<criterion>": 0.0-1.0, ...}, '
            '"why": "one short line"}'
        )
        try:
            # Generous cap: the judge is a thinking model and returns an EMPTY
            # string if it hits the limit mid-reasoning.
            result = self.client.chat(
                model_id=judge_spec.id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000,
            )
            text = (result.get("text") or "").strip()
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return None
            scores = json.loads(match.group(0)).get("scores", {})
            if not isinstance(scores, dict) or not scores:
                return None
            total = 0.0
            for name, weight in criteria:
                try:
                    value = float(scores.get(name, 0.0))
                except (TypeError, ValueError):
                    value = 0.0
                total += max(0.0, min(1.0, value)) * weight
            return max(0.0, min(1.0, total))
        except Exception as exc:  # noqa: BLE001 - judging must never crash a run
            logger.warning("Goal-fit judging unavailable: %s", exc)
            return None

    def _score_candidate(
        self,
        returncode: Optional[int],
        image_path: Optional[Path],
        has_reference: bool,
        reference_path: Optional[Path],
        goal: str = "",
        code: str = "",
        stdout: str = "",
        rubric: Optional[List[Tuple[str, float]]] = None,
    ) -> float:
        """Reference critique when possible; otherwise a goal-fit judgement.
        Falls back to the old binary signal only if judging is unavailable."""
        if (
            has_reference
            and self.critic is not None
            and image_path is not None
            and reference_path is not None
        ):
            try:
                result = self.critic.critique(
                    image_path, reference_path, "image generation"
                )
                return float(result.overall)
            except Exception as exc:  # noqa: BLE001 - critique must never crash a run
                logger.error("Critic failed to score %s: %s", image_path, exc)
                return 0.0
        if returncode != 0:
            return 0.0
        if goal and code:
            judged = self._judge_goal_fit(goal, code, stdout, returncode, rubric)
            if judged is not None:
                return judged
        return 1.0

    # ------------------------------------------------------------------ #
    # Per-candidate blocking flow (runs inside a worker thread)
    # ------------------------------------------------------------------ #

    def _run_candidate(
        self,
        letter: str,
        strategy: str,
        goal: str,
        plan: str,
        spec: ModelSpec,
        attempt_dir: Path,
        has_reference: bool,
        reference_path: Optional[Path],
        temperature: float = 0.4,
        rubric: Optional[List[Tuple[str, float]]] = None,
    ) -> Dict[str, Any]:
        """Full blocking flow for one candidate. Never raises."""
        candidate: Dict[str, Any] = self._new_candidate(letter, strategy)
        candidate["model"] = spec.id
        candidate["temperature"] = temperature
        try:
            prompt: str = self._build_prompt(goal, plan, strategy)

            # 1. Engineer writes code.
            try:
                # Kimi thinking models reject any temperature other than 1, so
                # they explore via strategy alone; everything else gets the
                # per-candidate temperature for genuine sampling diversity.
                extra: Optional[Dict[str, Any]] = None
                if not spec.id.startswith(("moonshotai/kimi-k3", "moonshotai/kimi-k2.6",
                                           "moonshotai/kimi-k2.7")):
                    extra = {"temperature": temperature}
                chat_result = self.client.chat(
                    model_id=spec.id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=ENGINEER_MAX_TOKENS,
                    extra_body=extra,
                )
            except OpenRouterError as exc:
                candidate["error"] = f"engineer chat failed: {exc}"
                return candidate
            except Exception as exc:  # noqa: BLE001
                candidate["error"] = f"engineer chat crashed: {exc}"
                return candidate

            try:
                candidate["cost_aud"] = self.router.calculate_cost(
                    spec,
                    int(chat_result.get("input_tokens", 0) or 0),
                    int(chat_result.get("output_tokens", 0) or 0),
                )
            except (TypeError, ValueError):
                candidate["cost_aud"] = 0.0

            code: str = extract_code(chat_result.get("text", "") or "")
            candidate["code_len"] = len(code)

            # 2. Persist candidate_<letter>/main.py under the attempt dir.
            candidate_dir: Path = attempt_dir / f"candidate_{letter}"
            try:
                candidate_dir.mkdir(parents=True, exist_ok=True)
                code_path: Path = candidate_dir / "main.py"
                code_path.write_text(code, encoding="utf-8")
                candidate["code_path"] = str(code_path)
            except OSError as exc:
                candidate["error"] = f"could not write candidate code: {exc}"
                return candidate

            # 3. Run it in a real subprocess, timing it with a monotonic clock.
            start: float = time.perf_counter()
            try:
                execution: Dict[str, Any] = run_python(
                    code_path,
                    candidate_dir,
                    timeout=CANDIDATE_EXEC_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 - harness must degrade, not crash
                candidate["exec_ms"] = int(round((time.perf_counter() - start) * 1000))
                candidate["error"] = f"execution harness failed: {exc}"
                return candidate
            candidate["exec_ms"] = int(round((time.perf_counter() - start) * 1000))
            candidate["returncode"] = int(execution.get("returncode", -1))

            # 4. Locate any produced image and expose its /outputs URL.
            image_path: Optional[Path] = self._find_image(candidate_dir)
            if image_path is not None:
                candidate["image_path"] = str(image_path)
                candidate["image_url"] = self._image_url_for(image_path)

            # 5. Score.
            candidate["score"] = self._score_candidate(
                candidate["returncode"], image_path, has_reference, reference_path,
                goal=goal, code=code,
                stdout=str(execution.get("stdout", "") or ""),
                rubric=rubric,
            )
        except Exception as exc:  # noqa: BLE001 - last-resort guard per candidate
            if candidate["error"] is None:
                candidate["error"] = f"candidate crashed: {exc}"
            candidate["score"] = 0.0
        return candidate

    # ------------------------------------------------------------------ #
    # Notify-wrapped runner
    # ------------------------------------------------------------------ #

    async def _run_and_notify(
        self,
        letter: str,
        strategy: str,
        goal: str,
        plan: str,
        spec: ModelSpec,
        attempt_dir: Path,
        has_reference: bool,
        reference_path: Optional[Path],
        temperature: float,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]],
        rubric: Optional[List[Tuple[str, float]]] = None,
    ) -> Dict[str, Any]:
        """Run one candidate and fire the optional progress hook."""
        result = await asyncio.to_thread(
            self._run_candidate,
            letter,
            strategy,
            goal,
            plan,
            spec,
            attempt_dir,
            has_reference,
            reference_path,
            temperature,
            rubric,
        )
        if progress_callback is not None:
            try:
                progress_callback(result)
            except Exception:  # noqa: BLE001 - telemetry must never crash
                pass
        return result

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def run(
        self,
        goal: str,
        plan: str,
        engineer_role: str,
        attempt_dir: Path,
        reference_path: Optional[Path],
        n_candidates: int = 5,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run the first ``n_candidates`` strategies concurrently and pick a winner.

        Returns a dict with a per-candidate breakdown, the selected winner (or
        None if none succeeded), and the aggregated cost in AUD.
        """
        attempt_dir = Path(attempt_dir)
        try:
            attempt_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Could not create attempt dir %s: %s", attempt_dir, exc)

        selected_strategies: List[Tuple[str, str]] = STRATEGIES[: max(0, n_candidates)]

        default_spec: ModelSpec = self.router.get_spec(engineer_role)
        has_reference: bool = (
            reference_path is not None and Path(reference_path).is_file()
        )
        ref: Optional[Path] = Path(reference_path) if has_reference else None

        # Give each candidate a DIFFERENT engine (and temperature). Racing one
        # model against itself just produces five near-identical programs; real
        # exploration needs genuinely different reasoners. Unknown pool ids fall
        # back to the role's own model so the race always fills.
        pool: List[ModelSpec] = []
        for model_id in DIVERSITY_POOL:
            resolved = self.router.spec_for_id(model_id)
            if resolved is not None:
                pool.append(resolved)
        if not pool:
            pool = [default_spec]

        # K3 rubric-first judging: derive the task's grading criteria ONCE,
        # before any candidate is scored, so every judgement shares one
        # explicit standard instead of five independent impressions.
        rubric: List[Tuple[str, float]] = await asyncio.to_thread(
            self._build_rubric, goal
        )

        tasks = [
            self._run_and_notify(
                letter,
                strategy,
                goal,
                plan,
                pool[index % len(pool)],
                attempt_dir,
                has_reference,
                ref,
                DIVERSITY_TEMPS[index % len(DIVERSITY_TEMPS)],
                progress_callback,
                rubric,
            )
            for index, (letter, strategy) in enumerate(selected_strategies)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: List[Dict[str, Any]] = []
        for (letter, strategy), result in zip(selected_strategies, results):
            if isinstance(result, BaseException):
                # asyncio.gather itself failed — record a failed candidate.
                logger.error("Candidate %s crashed in task: %s", letter, result)
                failed: Dict[str, Any] = self._new_candidate(letter, strategy)
                failed["error"] = f"task crashed: {result}"
                candidates.append(failed)
            else:
                candidates.append(result)

        # A candidate is a valid winner only if it did not error and either exited
        # cleanly or produced a scorable image.
        eligible: List[Dict[str, Any]] = [
            candidate
            for candidate in candidates
            if candidate["error"] is None
            and (candidate["returncode"] == 0 or candidate["image_path"] is not None)
        ]

        # K3 verbosity guard: a candidate ballooning past ~2x the median code
        # length is hacking the judge with bulk, not quality — cap its score
        # below any tight, clean candidate. Needs 3+ measured candidates to
        # have a meaningful median.
        code_lens = sorted(
            int(c["code_len"]) for c in eligible if int(c.get("code_len") or 0) > 0
        )
        if len(code_lens) >= 3:
            median_len = max(1, code_lens[len(code_lens) // 2])
            for candidate in eligible:
                if int(candidate.get("code_len") or 0) > _VERBOSITY_CAP_FACTOR * median_len:
                    candidate["score"] = min(
                        float(candidate["score"]), _VERBOSITY_SCORE_CAP
                    )

        winner: Optional[Dict[str, Any]] = None
        if eligible:
            # Highest score; tie-break by lower cost, then faster execution.
            winner = max(
                eligible,
                key=lambda candidate: (
                    candidate["score"],
                    -candidate["cost_aud"],
                    -candidate["exec_ms"],
                ),
            )
            winner["selected"] = True

        total_cost_aud: float = 0.0
        for candidate in candidates:
            try:
                total_cost_aud += float(candidate["cost_aud"])
            except (TypeError, ValueError):
                continue

        return {
            "candidates": candidates,
            "winner": winner,
            "winner_code_path": winner["code_path"] if winner else None,
            "winner_image_url": winner["image_url"] if winner else None,
            "winner_score": winner["score"] if winner else None,
            "total_cost_aud": total_cost_aud,
        }


__all__ = [
    "TournamentRunner",
    "STRATEGIES",
    "CANDIDATE_EXEC_TIMEOUT_SECONDS",
]
