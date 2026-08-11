"""Monthly retrain loop for the custom model strategy.

Builds a fresh dataset from accepted session logs, starts a hosted fine-tune job,
polls it to completion, runs the eval harness against the new model, and only
ships the model to the router's custom lane if it beats the previous version.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from backend.core.dataset_builder import DatasetBuilder
    from backend.core.fine_tuner import FineTuner
    from backend.core.router import LANE_CUSTOM, LaneRouter, ModelRouter
except ImportError:  # running with backend/ as the working directory
    from core.dataset_builder import DatasetBuilder  # type: ignore[no-redef]
    from core.fine_tuner import FineTuner  # type: ignore[no-redef]
    from core.router import (  # type: ignore[no-redef]
        LANE_CUSTOM,
        LaneRouter,
        ModelRouter,
    )

logger = logging.getLogger("infinity.retrain")

DEFAULT_MIN_EXAMPLES: int = 100


def _resolve_env(value: Optional[str]) -> Optional[str]:
    """Expand ${VAR} placeholders; return None if the result is empty/placeholder."""
    if not value:
        return None
    m = re.match(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$", value.strip())
    if m:
        value = os.environ.get(m.group(1), "")
    value = (value or "").strip()
    return value if value else None
DEFAULT_EVAL_LANE: str = "custom"
DEFAULT_FINE_TUNE_PROVIDER: str = "together"
DEFAULT_FINE_TUNE_MODEL: str = "Qwen/Qwen3-Coder-Next"


@dataclass
class RetrainState:
    """Persistent state for the retrain loop."""

    best_score: float = 0.0
    best_model: Optional[str] = None
    last_run: Optional[str] = None
    current_job: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_score": self.best_score,
            "best_model": self.best_model,
            "last_run": self.last_run,
            "current_job": self.current_job,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetrainState":
        return cls(
            best_score=float(data.get("best_score", 0.0)),
            best_model=data.get("best_model"),
            last_run=data.get("last_run"),
            current_job=data.get("current_job"),
            history=list(data.get("history", [])),
        )


class RetrainLoop:
    """Orchestrate dataset → fine-tune → eval → ship."""

    def __init__(
        self,
        dataset_builder: DatasetBuilder,
        lane_router: LaneRouter,
        eval_harness_factory: Any,
        data_dir: Path,
        min_examples: int = DEFAULT_MIN_EXAMPLES,
        provider: str = DEFAULT_FINE_TUNE_PROVIDER,
        base_model: str = DEFAULT_FINE_TUNE_MODEL,
        api_key: Optional[str] = None,
        fireworks_account: Optional[str] = None,
    ) -> None:
        self.dataset_builder = dataset_builder
        self.lane_router = lane_router
        self.eval_harness_factory = eval_harness_factory
        self.data_dir = Path(data_dir).resolve()
        self.min_examples = max(0, int(min_examples))
        self.provider = provider
        self.base_model = base_model
        self.fine_tuner = FineTuner(
            provider=provider,
            api_key=_resolve_env(api_key),
            account=_resolve_env(fireworks_account),
        )
        self.state_path = self.data_dir / "retrain_state.json"
        self.state = self._load_state()

    def _load_state(self) -> RetrainState:
        if not self.state_path.is_file():
            return RetrainState()
        try:
            return RetrainState.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            logger.warning("Could not load retrain state: %s", exc)
            return RetrainState()

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(self.state.to_dict(), indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Could not save retrain state: %s", exc)

    @staticmethod
    def _extract_model_id(job_info: Dict[str, Any], provider: str) -> Optional[str]:
        """Best-effort extraction of the serving model id from a completed job."""
        raw = job_info.get("raw") or {}
        if provider == "together":
            # Together returns output_name or fine_tuned_model.
            return (
                raw.get("output_name")
                or raw.get("fine_tuned_model")
                or raw.get("model_output_name")
            )
        if provider == "fireworks":
            return raw.get("model") or raw.get("outputModel")
        return None

    def _run_eval(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Run the eval harness against a model id."""
        harness = self.eval_harness_factory()
        if harness is None:
            return None
        try:
            return harness.run(lane=DEFAULT_EVAL_LANE, model_id=model_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Eval harness failed for %s: %s", model_id, exc)
            return None

    def _ship_if_better(self, model_id: str, score: float) -> bool:
        """Promote the model to the custom lane only if it beats the incumbent."""
        margin = 0.001
        if score <= self.state.best_score + margin:
            logger.info(
                "Model %s score %.4f did not beat %.4f; not shipping.",
                model_id,
                score,
                self.state.best_score,
            )
            return False
        logger.info("Shipping %s as custom lane (score %.4f > %.4f).", model_id, score, self.state.best_score)
        self.state.best_score = score
        self.state.best_model = model_id
        try:
            self.lane_router.set_custom_lane(model_id)
        except AttributeError:
            # Fallback if an older LaneRouter lacks the helper.
            self.lane_router.lanes[LANE_CUSTOM] = (
                model_id,
                "z-ai/glm-5.2",
                "deepseek/deepseek-v4-flash",
            )
        return True

    def build_dataset(self) -> Dict[str, Any]:
        """Build a fresh training set from accepted session logs."""
        return self.dataset_builder.build()

    def start_tune(
        self,
        dataset_path: Path,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a fine-tune job for the given dataset."""
        if not self.fine_tuner.configured():
            return {"ok": False, "error": f"{self.provider} API key not configured"}
        tuner = self.fine_tuner
        if provider and provider != self.provider:
            tuner = FineTuner(provider=provider, api_key=None)
        job = tuner.upload_and_tune(
            file_path=dataset_path,
            model=model or self.base_model,
            suffix=f"infinity-{int(time.time())}",
        )
        if job.get("ok"):
            self.state.current_job = {
                "provider": job["provider"],
                "job_id": job["job_id"],
                "status": job.get("status", "started"),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "dataset_path": str(dataset_path),
            }
            self._save_state()
        return job

    def poll(self) -> Dict[str, Any]:
        """Check the current job; if complete, eval and ship."""
        job = self.state.current_job
        if job is None:
            return {"ok": True, "status": "idle", "detail": "no active fine-tune job"}

        status = self.fine_tuner.status(job["job_id"])
        if "error" in status:
            # Keep the job alive so transient errors don't abort a run.
            return {
                "ok": False,
                "status": "polling_error",
                "error": status["error"],
                "job": job,
            }

        job_status = status.get("status", "unknown").lower()
        self.state.current_job["status"] = job_status
        self.state.current_job["last_poll"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        self._save_state()

        if job_status not in {"completed", "succeeded", "done", "finished"}:
            return {
                "ok": True,
                "status": "running",
                "job_status": job_status,
                "job": job,
            }

        model_id = self._extract_model_id({"raw": status}, job["provider"])
        if not model_id:
            model_id = self.base_model
        eval_report = self._run_eval(model_id)
        score = 0.0
        if eval_report is not None:
            score = float(eval_report.get("aggregate_score", 0.0))

        shipped = self._ship_if_better(model_id, score)
        result = {
            "ok": True,
            "status": "completed",
            "model_id": model_id,
            "score": score,
            "shipped": shipped,
            "eval_report": eval_report.get("summary") if eval_report else None,
        }
        self.state.history.append(result)
        self.state.last_run = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.state.current_job = None
        self._save_state()
        return result

    def run(self) -> Dict[str, Any]:
        """One end-to-end retrain attempt (starts the job; poll separately)."""
        if self.state.current_job is not None:
            return self.poll()

        stats = self.build_dataset()
        total_positive = stats.get("coder_count", 0) + stats.get("vision_count", 0)
        if total_positive < self.min_examples:
            return {
                "ok": False,
                "status": "skipped",
                "reason": f"only {total_positive} positive examples (min {self.min_examples})",
                "stats": stats,
            }

        # Prefer the coder dataset for the code model; fall back to vision if needed.
        dataset_path = stats.get("coder_path") or stats.get("vision_path")
        if not dataset_path:
            return {"ok": False, "status": "skipped", "reason": "no dataset file produced", "stats": stats}

        job = self.start_tune(Path(dataset_path))
        if not job.get("ok"):
            return {
                "ok": False,
                "status": "tune_failed",
                "error": job.get("error"),
                "stats": stats,
            }
        return {
            "ok": True,
            "status": "started",
            "job": self.state.current_job,
            "stats": stats,
        }

    def status(self) -> Dict[str, Any]:
        """Current loop state for UI/health checks."""
        return {
            "best_score": self.state.best_score,
            "best_model": self.state.best_model,
            "last_run": self.state.last_run,
            "current_job": self.state.current_job,
            "configured": self.fine_tuner.configured(),
            "provider": self.provider,
            "base_model": self.base_model,
            "min_examples": self.min_examples,
        }


__all__ = ["RetrainLoop", "RetrainState"]
