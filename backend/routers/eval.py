"""Infinity Code API router: eval.

Extracted from backend/main.py. Routes, methods, params, response
shapes and status codes are byte-identical to the original monolith.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml
from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from main import (
    BASE_DIR, DATA_DIR, DatasetBuilder, RetrainLoop, _CONFIG, app, logger,
)

router = APIRouter()

@router.post("/eval/run")
def run_eval(body: Dict[str, Any]) -> Dict[str, Any]:
    """Run the eval harness against a lane/model and return the report path."""
    try:
        from backend.core.eval_harness import EvalHarness
    except ImportError:
        from core.eval_harness import EvalHarness  # type: ignore[no-redef]

    client = getattr(app.state, "client", None) or getattr(app.state, "chat_client", None)
    router = getattr(app.state, "lane_router", None)
    if client is None:
        raise HTTPException(status_code=503, detail="LLM client not available.")
    lane = str(body.get("lane") or "cheap").strip()
    model_id = str(body.get("model") or "").strip() or None
    tasks_path = body.get("tasks_path")
    profile = str(body.get("profile") or "all").strip().lower() or "all"
    output_path = DATA_DIR / "eval_reports" / f"eval_{lane}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    harness = EvalHarness(client, router, DATA_DIR)
    try:
        report = harness.run(
            lane=lane,
            model_id=model_id,
            tasks_path=Path(tasks_path) if tasks_path else None,
            profile=profile,
        )
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return {"ok": True, "report_path": str(output_path), "summary": report.get("summary", {})}
    except Exception as exc:  # noqa: BLE001
        logger.error("Eval run failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Eval run failed: {exc}") from exc


@router.get("/eval/tasks")
def list_eval_tasks() -> Dict[str, Any]:
    try:
        from backend.core.eval_harness import EvalHarness
    except ImportError:
        from core.eval_harness import EvalHarness  # type: ignore[no-redef]
    harness = EvalHarness(None, None, DATA_DIR)
    tasks = harness.load_tasks()
    return {"tasks": [t.__dict__ for t in tasks]}


@router.get("/eval/curriculum")
def get_eval_curriculum() -> Dict[str, Any]:
    """Show unresolved benchmark-driven drills and their latest evidence."""
    try:
        from backend.core.benchmark_curriculum import BenchmarkCurriculum
    except ImportError:
        from core.benchmark_curriculum import BenchmarkCurriculum  # type: ignore[no-redef]
    return {"drills": BenchmarkCurriculum(DATA_DIR).status()}


@router.get("/eval/browsergym/status")
def browsergym_status() -> Dict[str, Any]:
    """Report whether the opt-in BrowserGym Docker scaffold can run locally."""
    try:
        from backend.core.browsergym_runner import BrowserGymRunner
    except ImportError:
        from core.browsergym_runner import BrowserGymRunner  # type: ignore[no-redef]
    return BrowserGymRunner(BASE_DIR.parent).status()


@router.post("/eval/browsergym/run")
def browsergym_run(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Explicitly run the isolated BrowserGym environment smoke test."""
    try:
        from backend.core.browsergym_runner import BrowserGymRunner
    except ImportError:
        from core.browsergym_runner import BrowserGymRunner  # type: ignore[no-redef]
    task = str((body or {}).get("task") or "browsergym/miniwob.click-test")
    return BrowserGymRunner(BASE_DIR.parent).run_smoke(task)


@router.post("/eval/blender/random-scene")
def blender_random_scene_proof(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run an inspectable, Blender-owned random-scene proof for the harness."""
    try:
        from backend.core.blender_benchmark import run_random_scene_proof
    except ImportError:
        from core.blender_benchmark import run_random_scene_proof  # type: ignore[no-redef]
    values = body or {}
    seed = int(values.get("seed", 20260802))
    count = int(values.get("count", 5))
    if not 3 <= count <= 6:
        raise HTTPException(status_code=422, detail="count must be between 3 and 6")
    return run_random_scene_proof(BASE_DIR / "data" / "eval_reports", seed=seed, count=count)


@router.post("/eval/blender/vehicle")
def blender_vehicle_proof() -> Dict[str, Any]:
    """Run the structured car benchmark with relational scene-graph checks."""
    try:
        from backend.core.blender_benchmark import run_vehicle_proof
    except ImportError:
        from core.blender_benchmark import run_vehicle_proof  # type: ignore[no-redef]
    return run_vehicle_proof(BASE_DIR / "data" / "eval_reports")


@router.post("/eval/blender/vehicle/local-9b")
def blender_vehicle_local_9b_proof() -> Dict[str, Any]:
    """Grade a car authored exclusively by the local Fable 9B server."""
    try:
        from backend.core.blender_benchmark import run_local_9b_vehicle_proof
    except ImportError:
        from core.blender_benchmark import run_local_9b_vehicle_proof  # type: ignore[no-redef]
    return run_local_9b_vehicle_proof(BASE_DIR / "data" / "eval_reports")


@router.post("/eval/blender/vehicle/dense")
def blender_dense_vehicle_proof() -> Dict[str, Any]:
    """Run tier three's denser vehicle challenge with 20 required parts."""
    try:
        from backend.core.blender_benchmark import run_dense_vehicle_proof
    except ImportError:
        from core.blender_benchmark import run_dense_vehicle_proof  # type: ignore[no-redef]
    return run_dense_vehicle_proof(BASE_DIR / "data" / "eval_reports")


# ---------------------------------------------------------------------- #
# Routes: dataset builder, fine-tuning, and retrain loop
# ---------------------------------------------------------------------- #


@router.post("/datasets/build")
def build_datasets() -> Dict[str, Any]:
    """Build coder + vision training sets from accepted session logs."""
    builder: Optional[DatasetBuilder] = getattr(app.state, "dataset_builder", None)
    if builder is None:
        raise HTTPException(status_code=503, detail="Dataset builder unavailable.")
    return builder.build()


@router.get("/datasets")
def list_datasets() -> Dict[str, Any]:
    """List all generated training datasets."""
    builder: Optional[DatasetBuilder] = getattr(app.state, "dataset_builder", None)
    if builder is None:
        raise HTTPException(status_code=503, detail="Dataset builder unavailable.")
    return {"runs": builder.list_runs()}


def _huggingface_data() -> Any:
    try:
        from backend.core.huggingface_data import HuggingFaceData
    except ImportError:
        from core.huggingface_data import HuggingFaceData  # type: ignore[no-redef]
    config = _CONFIG.get("huggingface", {}) if isinstance(_CONFIG.get("huggingface"), dict) else {}
    allowlist = config.get("datasets") if isinstance(config.get("datasets"), list) else None
    return HuggingFaceData(DATA_DIR, allowlist)


@router.get("/datasets/huggingface/status")
def huggingface_dataset_status() -> Dict[str, Any]:
    return _huggingface_data().status()


@router.post("/datasets/huggingface/preview")
def huggingface_dataset_preview(body: Dict[str, Any]) -> Dict[str, Any]:
    """Preview an allow-listed HF dataset using the owner's token if needed."""
    try:
        return _huggingface_data().preview(
            dataset=str(body.get("dataset") or ""),
            config=str(body.get("config") or "") or None,
            split=str(body.get("split") or "") or None,
            limit=int(body.get("limit") or 20),
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/datasets/huggingface/export")
def huggingface_dataset_export(body: Dict[str, Any]) -> Dict[str, Any]:
    """Save a reviewed HF sample as a training candidate; it does not train."""
    preview = huggingface_dataset_preview(body)
    return _huggingface_data().export_candidates(preview)


class FineTuneStartRequest(BaseModel):
    dataset_path: str
    provider: str = "together"
    model: str = "Qwen/Qwen3-Coder-Next"
    suffix: Optional[str] = None
    epochs: Optional[int] = None


@router.post("/fine-tune/start")
def start_fine_tune(request: FineTuneStartRequest) -> Dict[str, Any]:
    """Upload a dataset and start a hosted fine-tune job."""
    loop: Optional[RetrainLoop] = getattr(app.state, "retrain_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Retrain loop unavailable.")
    if not loop.fine_tuner.configured():
        raise HTTPException(
            status_code=503,
            detail=f"Fine-tuning provider {request.provider} is not configured (missing API key).",
        )
    path = Path(request.dataset_path)
    if not path.is_file():
        raise HTTPException(status_code=422, detail="dataset_path does not exist.")
    return loop.start_tune(
        path,
        provider=request.provider,
        model=request.model,
    )


@router.get("/fine-tune/status")
def fine_tune_status(job_id: Optional[str] = None) -> Dict[str, Any]:
    """Check the current or a specific fine-tune job."""
    loop: Optional[RetrainLoop] = getattr(app.state, "retrain_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Retrain loop unavailable.")
    if job_id:
        return loop.fine_tuner.status(job_id)
    return loop.status()


@router.post("/retrain/run")
def run_retrain() -> Dict[str, Any]:
    """Manually trigger the retrain loop (build dataset â†’ start tune)."""
    loop: Optional[RetrainLoop] = getattr(app.state, "retrain_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Retrain loop unavailable.")
    return loop.run()


@router.post("/retrain/poll")
def poll_retrain() -> Dict[str, Any]:
    """Poll the current fine-tune job and ship if it passes eval."""
    loop: Optional[RetrainLoop] = getattr(app.state, "retrain_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Retrain loop unavailable.")
    return loop.poll()


@router.get("/retrain/status")
def retrain_status() -> Dict[str, Any]:
    """Summary of the retrain loop (best model, current job, etc.)."""
    loop: Optional[RetrainLoop] = getattr(app.state, "retrain_loop", None)
    if loop is None:
        raise HTTPException(status_code=503, detail="Retrain loop unavailable.")
    return loop.status()


# ---------------------------------------------------------------------- #
# Routes: smart defaults (subtle out-of-the-box intelligence)
# ---------------------------------------------------------------------- #
