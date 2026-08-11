"""LoopEngine — the self-iterating, quality-gated work loop for Code mode.

plan -> generate -> deterministic gate -> (pass | retry same tier | escalate a
tier | reject). Grounded in the personal knowledge base and told to refuse
invention (anti-hallucination). Every tier call auto-continues on truncation.
A budget guard and a hard max-iteration cap keep long runs safe. Emits a quiet
step trail (no agent-theater) that the UI renders as a checklist.

Ported from the proven GENESIS orchestrator; reuses quality_gate.check/route/
escalate and the auto-continuation in OpenRouterClient.chat.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

try:
    from backend.core.router import (
        Complexity,
        LANE_SMART,
        LaneRouter,
        ModelSpec,
        USD_PER_AUD,
        _spec_or_default,
    )
except ImportError:  # running with backend/ as the working directory
    from core.router import (  # type: ignore[no-redef]
        Complexity,
        LANE_SMART,
        LaneRouter,
        ModelSpec,
        USD_PER_AUD,
        _spec_or_default,
    )

try:
    from backend.core.plan_anchor import PlanAnchor
    from backend.core.benchmark_curriculum import BenchmarkCurriculum
    from backend.core.session_logger import SessionLogger
except ImportError:
    from core.plan_anchor import PlanAnchor  # type: ignore[no-redef]
    from core.benchmark_curriculum import BenchmarkCurriculum  # type: ignore[no-redef]
    from core.session_logger import SessionLogger  # type: ignore[no-redef]

from . import quality_gate as qg

logger = logging.getLogger(__name__)

CODE_JOBS = {"code", "debug", "refactor", "system_design", "architecture"}


class LoopEngine:
    def __init__(
        self,
        client: Any,
        knowledge: Any = None,
        cost_tracker: Any = None,
        fallback_models: Optional[List[str]] = None,
        lane_router: Optional[LaneRouter] = None,
        session_logger: Optional[SessionLogger] = None,
        verifier: Any = None,
        plan_work_dir: Optional[Any] = None,
        data_dir: Optional[Any] = None,
    ) -> None:
        self.client = client
        self.knowledge = knowledge
        self.cost_tracker = cost_tracker
        self.fallbacks = fallback_models or ["qwen/qwen3.7-max"]
        self.lane_router = lane_router
        self.session_logger = session_logger
        self.verifier = verifier
        self.plan_work_dir = Path(plan_work_dir) if plan_work_dir else None
        # NB: the plan anchor is per-run state (a local in run()), NOT an
        # instance attribute — a stale anchor from a long-horizon run must not
        # leak into a later short run, and concurrent loops must not share one.
        self.curriculum = BenchmarkCurriculum(Path(data_dir)) if data_dir else None
        # Ascension Engine hook (set by main.py): live form policy applied per
        # run so lane/model choice honors the current form's locks.
        self.ascension_engine: Any = None

    # --- grounding ------------------------------------------------------- #

    def _ground(self, goal: str) -> str:
        if self.knowledge is None or self.client is None:
            return ""
        try:
            vecs = self.client.embed([goal])
            hits = self.knowledge.search(vecs[0], top_k=4) if vecs else []
        except Exception:  # noqa: BLE001
            return ""
        if not hits:
            return ""
        blocks = "\n\n".join(
            f"[{i+1}] ({h['relpath']})\n{h['text'][:1000]}" for i, h in enumerate(hits)
        )
        return (
            "\n\nGROUNDING (the user's own verified notes — treat as authoritative, "
            "cite as [n]):\n" + blocks
        )

    # --- one guarded model call ----------------------------------------- #

    def _call(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        fallback_models: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> Tuple[str, float]:
        """Try the lane model; fall back to known-good chat models on failure.

        Returns the generated text plus the actual AUD cost of the call.
        """
        fallbacks = fallback_models if fallback_models is not None else self.fallbacks
        candidates = [model] + [m for m in fallbacks if m != model]
        last_err: Optional[Exception] = None
        for cand in candidates:
            try:
                # OpenRouterClient.chat accepts session_id/lane for logging.
                result = self.client.chat(
                    cand, messages, max_tokens, session_id=session_id, lane=lane
                )
                # ChatResult is a TypedDict (plain dict) -> subscript, not attr.
                text = str(
                    result["text"]
                    if isinstance(result, dict)
                    else getattr(result, "text", "")
                )
                cost_usd = float(
                    result.get("cost_usd", 0.0)
                    if isinstance(result, dict)
                    else getattr(result, "cost_usd", 0.0)
                )
                return text, cost_usd / USD_PER_AUD
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.info("loop: model %s failed (%s), trying next", cand, exc)
        raise RuntimeError(f"all models failed: {last_err}")

    # --- the loop -------------------------------------------------------- #

    def run(
        self,
        goal: str,
        job_type: str = "code",
        max_iter: int = 5,
        max_tokens: int = 3000,
    ) -> Iterator[Dict[str, Any]]:
        """Yield step events; the final event has type 'done' with the result."""
        expect_code = job_type in CODE_JOBS
        grounding = self._ground(goal)
        drill_context, drills = self.curriculum.context_for(goal) if self.curriculum else ("", [])
        grounding += drill_context
        session_id = str(uuid.uuid4())
        yield {"type": "step", "label": "Plan", "status": "done",
               "detail": f"grounded on {grounding.count('[') if grounding else 0} sources; {len(drills)} active drills"}

        # Map job type to lane-aware model chain.
        if job_type in ("system_design", "architecture"):
            complexity: Complexity = "architectural"
        elif job_type in CODE_JOBS or job_type in ("debug", "refactor"):
            complexity = "complex"
        else:
            complexity = "simple"
        long_horizon = len(goal) > 6000

        # Plan anchor for long-horizon tasks. Kept local to this run so it
        # can't leak into the next run or be shared across concurrent runs.
        plan_anchor: Optional[PlanAnchor] = None
        if long_horizon and self.plan_work_dir is not None:
            plan_anchor = PlanAnchor(
                goal=goal,
                work_dir=self.plan_work_dir / "plans" / session_id,
            )

        if self.lane_router is not None:
            state_name = None
            approved: Optional[Tuple[str, ...]] = None
            available: Optional[Callable[[str], bool]] = None
            if self.ascension_engine is not None:
                state_name = self.ascension_engine.snapshot()["form"]
                approved = self.ascension_engine.approved_models()
                available = lambda mid: bool(  # noqa: E731
                    (self.ascension_engine.model_meta(mid) or {}).get("available", False)
                )
            lane, chain = self.lane_router.route_chain(
                task_type=job_type,
                complexity=complexity,
                long_horizon=long_horizon,
                ascension_state=state_name,
                approved=approved,
                available=available,
            )
        else:
            lane = LANE_SMART
            chain = (_spec_or_default("qwen/qwen3.7-max"),)
        primary_spec = chain[0]
        fallback_models = [s.id for s in chain[1:]]

        feedback = ""
        trail: List[Dict[str, Any]] = []
        best = ""
        for i in range(1, max_iter + 1):
            yield {"type": "step", "label": f"Generate · {lane.upper()}",
                   "status": "running", "detail": f"iteration {i}/{max_iter}"}
            plan_section = ""
            if plan_anchor is not None:
                plan_section = "\n\n" + plan_anchor.to_prompt()
            system = (
                "You are Infinity Code's Code engine. Produce complete, correct, "
                "runnable output. Never invent APIs, file paths, or functions — if "
                "unsure, say so. Verify claims against the GROUNDING below."
                + grounding
                + plan_section
            )
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": goal + feedback}]

            # Atomic budget guard: reserve the estimated cost before the call so
            # concurrent loops cannot double-spend the same remaining budget.
            spec: Optional[ModelSpec] = None
            est_input = 0
            est_output = 0
            if self.cost_tracker is not None:
                try:
                    spec = primary_spec
                    est_input = max(1, (len(system) + len(goal + feedback)) // 4)
                    est_output = max_tokens
                    if not self.cost_tracker.approve_call(spec, est_input, est_output):
                        yield {"type": "step", "label": "Budget",
                               "status": "skipped", "detail": "daily cap reached"}
                        break
                except Exception:  # noqa: BLE001
                    pass

            try:
                text, actual_aud = self._call(
                    primary_spec.id,
                    messages,
                    max_tokens,
                    fallback_models=fallback_models,
                    session_id=session_id,
                    lane=lane,
                )
            except RuntimeError as exc:
                yield {"type": "step", "label": "Generate", "status": "error",
                       "detail": str(exc)[:120]}
                break

            # Replace the reserved estimate with what the call actually cost.
            if self.cost_tracker is not None and spec is not None:
                try:
                    self.cost_tracker.reconcile(spec, est_input, est_output, actual_aud)
                except Exception:  # noqa: BLE001
                    pass

            best = text or best

            if plan_anchor is not None:
                try:
                    plan_anchor.update_from_response(text)
                except Exception:  # noqa: BLE001
                    pass

            gate = qg.check(text, expect_code=expect_code)
            verify_result: Optional[Dict[str, Any]] = None
            if expect_code and self.verifier is not None:
                try:
                    vres = self.verifier.verify_code(text)
                    verify_result = {
                        "ok": vres.ok,
                        "stage": vres.stage,
                        "reason": vres.reason,
                        "stdout": vres.stdout,
                        "stderr": vres.stderr,
                    }
                    yield {"type": "step", "label": "Verify",
                           "status": "done" if vres.ok else "error",
                           "detail": vres.reason[:120]}
                    if not vres.ok:
                        gate = qg.GateResult(
                            qg.RETRY,
                            [f"verification failed ({vres.stage}): {vres.reason}"],
                        )
                except Exception as exc:  # noqa: BLE001
                    yield {"type": "step", "label": "Verify",
                           "status": "error", "detail": str(exc)[:120]}

            trail.append({"iteration": i, "lane": lane, "model": primary_spec.id,
                          "verdict": gate.verdict, "reasons": gate.reasons,
                          "verify": verify_result,
                          "drills_applied": [item["id"] for item in drills]})

            if gate.verdict == qg.PASS:
                yield {"type": "step", "label": f"Gate · {lane.upper()}",
                       "status": "done", "detail": "passed"}
                yield {"type": "done", "ok": True, "text": text, "trail": trail,
                       "iterations": i, "session_id": session_id,
                       "drills_applied": [item["id"] for item in drills]}
                return

            reason_str = "; ".join(gate.reasons) or gate.verdict
            if gate.verdict == qg.ESCALATE:
                # Lane escalation: move to the next model in the lane chain if
                # one remains; otherwise keep retrying at the current lane.
                if len(chain) > 1:
                    chain = chain[1:]
                    primary_spec = chain[0]
                    fallback_models = [s.id for s in chain[1:]]
                yield {"type": "step", "label": f"Gate · {lane.upper()}",
                       "status": "escalate", "detail": f"{reason_str} → {primary_spec.id}"}
            else:  # RETRY
                yield {"type": "step", "label": f"Gate · {lane.upper()}",
                       "status": "retry", "detail": reason_str}
            feedback = (
                f"\n\n[Revision {i}] The previous attempt failed a quality check: "
                f"{reason_str}. Fix it and return the complete corrected output."
            )

        # Exhausted iterations — surface the best attempt + the trail, no fake success.
        yield {"type": "done", "ok": False, "text": best, "trail": trail,
               "iterations": max_iter, "session_id": session_id,
               "drills_applied": [item["id"] for item in drills],
               "note": "max iterations reached without a clean pass"}


__all__ = ["LoopEngine"]
