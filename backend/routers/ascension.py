"""Infinity Code API router: ascension.

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
    EffortInputs, OWNER_ALIAS, OWNER_NAME, SpeedInputs, app, effort_score, gauntlet_post_gap, gauntlet_status,
    owner_line, speed_score,
)

router = APIRouter()

# --- Infinity Code X: ascension ------------------------------------------

class AscensionEscalateRequest(BaseModel):
    reason: str = ""


class AscensionOverrideRequest(BaseModel):
    target: str
    passphrase: str
    reason: str = ""


class AscensionScoresRequest(BaseModel):
    speed: Optional[Dict[str, Any]] = None
    effort: Optional[Dict[str, Any]] = None


@router.get("/ascension/state")
def ascension_state() -> Dict[str, Any]:
    """Full ascension snapshot for the Council UI."""
    from main import ASCENSION_ENGINE
    snap = ASCENSION_ENGINE.snapshot()
    snap["protected"] = [
        "one_step", "role_lock", "form_lock", "dwell", "cooldown", "owner_only_final",
    ]
    snap["cards"] = ASCENSION_ENGINE.model_cards()
    return snap


@router.post("/ascension/escalate")
def ascension_escalate(req: AscensionEscalateRequest) -> Dict[str, Any]:
    """One-step escalation; result carries ok/blocked_by for the UI."""
    from main import ASCENSION_ENGINE
    return ASCENSION_ENGINE.escalate(req.reason)


@router.post("/ascension/deescalate")
def ascension_deescalate(req: AscensionEscalateRequest) -> Dict[str, Any]:
    """One step down (safety valve, allowed while cooling down)."""
    from main import ASCENSION_ENGINE
    return ASCENSION_ENGINE.deescalate(req.reason)


@router.post("/ascension/override")
def ascension_override(req: AscensionOverrideRequest) -> Dict[str, Any]:
    """Owner-only jump to any form. Wrong passphrase -> 403."""
    from main import ASCENSION_ENGINE
    result = ASCENSION_ENGINE.override(req.target, req.passphrase, req.reason)
    if not result["ok"] and result["blocked_by"] == "passphrase":
        raise HTTPException(status_code=403, detail={
            "error": "invalid passphrase", "blocked_by": "passphrase"})
    return result


@router.get("/ascension/log")
def ascension_log(limit: int = 50) -> Dict[str, Any]:
    """Recent ascension events, newest first."""
    from main import ASCENSION_ENGINE
    return {"entries": ASCENSION_ENGINE.log(limit)}


@router.post("/ascension/scores")
def ascension_scores(req: AscensionScoresRequest) -> Dict[str, Any]:
    """Feed speed/effort measurements; returns recomputed scores."""
    from main import ASCENSION_ENGINE
    if req.speed:
        ASCENSION_ENGINE.speed = SpeedInputs(**req.speed)
    if req.effort:
        ASCENSION_ENGINE.effort = EffortInputs(**req.effort)
    return {
        "speed": speed_score(ASCENSION_ENGINE.speed),
        "effort": effort_score(ASCENSION_ENGINE.effort),
        "state": ASCENSION_ENGINE.snapshot()["form"],
    }


# --- Infinity Code X: Benchmark Gauntlet ----------------------------------

@router.post("/gauntlet/gap")
def gauntlet_gap() -> Dict[str, Any]:
    """Measure benchmark gap, feed the effort score, suggest a form."""
    from main import ASCENSION_ENGINE, DATA_DIR
    return gauntlet_post_gap(ASCENSION_ENGINE, DATA_DIR)


@router.get("/gauntlet/status")
def gauntlet_status_route() -> Dict[str, Any]:
    """Latest benchmark report summary (or {"report": null} when none)."""
    from main import DATA_DIR
    return gauntlet_status(DATA_DIR)


@router.get("/owner")
def owner_info() -> Dict[str, Any]:
    """Owner identity for the Council UI (name, alias, identity line)."""
    return {
        "name": OWNER_NAME,
        "alias": OWNER_ALIAS,
        "line": owner_line(0, name=OWNER_NAME, alias=OWNER_ALIAS),
    }
