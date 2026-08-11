"""Infinity Code API router: missions.

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
    AgentSwarm, CreateMissionRequest, MissionResponse, RejectRequest, SmartDefaults, SwarmError, UPLOADS_DIR, WS_PUSH_INTERVAL_SECONDS,
    _connect, _fetch_mission_or_404, _row_to_mission, _sanitize_filename, _set_mission_status, app, logger,
)

router = APIRouter()

@router.get("/")
def root() -> Dict[str, str]:
    return {"app": "Infinity Code", "status": "running", "docs": "/docs"}


@router.post("/api/v1/missions", response_model=MissionResponse)
async def create_mission(request: CreateMissionRequest) -> MissionResponse:
    mission_id: str = str(uuid.uuid4())
    mode = request.mode
    effort = request.effort
    smart: Optional[Dict[str, Any]] = None
    if mode == "auto":
        smart = SmartDefaults.classify_goal(request.goal)
        mode = smart["mode"]
        effort = smart["effort"]
    # Demand dispatcher (2a): when the user did not hand-pick a crew,
    # auto-deploy the matching small team (research+design / coding /
    # testing / media / longtask). Explicit agent picks still win. The
    # classifier is cheap, cached per goal, and fails open to coding.
    if not request.agents:
        try:
            from backend.core.dispatcher import dispatch as _dispatch
        except ImportError:  # running with backend/ as the working directory
            from core.dispatcher import dispatch as _dispatch
        _client = None
        try:
            from main import OpenRouterClient as _OpenRouterClient
            _client = _OpenRouterClient(max_retries=1, timeout=20.0)
        except Exception:  # noqa: BLE001 - heuristic fallback is fine
            _client = None
        _team = _dispatch(request.goal, client=_client)
        if not request.mode or request.mode == "auto":
            mode = _team["params"]["mode"]
        if not request.effort or request.effort == "auto":
            effort = _team["params"]["effort"]
        if not request.tools:
            request.tools = _team["params"]["tools"]
    # Never persist arbitrary caller-supplied ids as crew members.  That keeps
    # the mission runner tied to the shipped persona library and makes stale UI
    # selections harmless after an agent-library update.
    agent_lib = getattr(app.state, "agents", None)
    selected_agents: List[str] = []
    if agent_lib is not None:
        for agent_id in request.agents:
            if agent_id not in selected_agents and agent_lib.get(agent_id) is not None:
                selected_agents.append(agent_id)

    params: Dict[str, Any] = {
        "vision_loop": request.vision_loop,
        "speculative": request.speculative,
        "mode": mode,
        "effort": effort,
        "fast": request.fast,
        "attachments": request.attachments,
        "end_reference_image_path": request.end_reference_image_path,
        "tools": request.tools,
        "agents": selected_agents,
        "combine_with_default_swarm": request.combine_with_default_swarm,
        "smart": smart,
    }
    try:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO missions
                    (id, title, goal, status, priority, created_at,
                     reference_image_path, params_json)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    request.title.strip(),
                    request.goal.strip(),
                    request.priority,
                    datetime.now(timezone.utc).isoformat(),
                    request.reference_image_path,
                    json.dumps(params, ensure_ascii=False),
                ),
            )
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    try:
        app.state.swarm.spawn(mission_id)
    except SwarmError as exc:
        _set_mission_status(mission_id, "failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _fetch_mission_or_404(mission_id)


MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024  # 25 MB


@router.post("/api/v1/upload")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    original_name: str = file.filename or "upload"
    safe_name: str = _sanitize_filename(original_name)
    try:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        contents: bytes = await file.read()
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File too large ({len(contents) // (1024 * 1024)} MB). "
                    f"Limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                ),
            )
        dest: Path = UPLOADS_DIR / f"{uuid.uuid4().hex}_{safe_name}"
        dest.write_bytes(contents)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc
    finally:
        await file.close()
    return {
        "path": str(dest.resolve()),
        "name": original_name,
        "size": len(contents),
    }


@router.get("/api/v1/missions", response_model=List[MissionResponse])
def list_missions() -> List[MissionResponse]:
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT * FROM missions ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_mission(row) for row in rows]
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


@router.get("/api/v1/missions/{mission_id}", response_model=MissionResponse)
def get_mission(mission_id: str) -> MissionResponse:
    return _fetch_mission_or_404(mission_id)


@router.post("/api/v1/missions/{mission_id}/approve", response_model=MissionResponse)
def approve_mission(mission_id: str) -> MissionResponse:
    _fetch_mission_or_404(mission_id)
    _set_mission_status(mission_id, "approved")
    return _fetch_mission_or_404(mission_id)


@router.post("/api/v1/missions/{mission_id}/reject", response_model=MissionResponse)
def reject_mission(mission_id: str, request: RejectRequest) -> MissionResponse:
    _fetch_mission_or_404(mission_id)
    _set_mission_status(mission_id, "rejected", feedback=request.feedback)
    return _fetch_mission_or_404(mission_id)


@router.post("/api/v1/missions/{mission_id}/cancel", response_model=MissionResponse)
def cancel_mission(mission_id: str) -> MissionResponse:
    """Stop a running/queued mission so it can never spin forever."""
    _fetch_mission_or_404(mission_id)
    swarm: AgentSwarm = app.state.swarm
    swarm.cancel(mission_id)
    return _fetch_mission_or_404(mission_id)


# ---------------------------------------------------------------------------
# Long Tasks - long-horizon agentic coding runs. Spec:
# docs/superpowers/specs/2026-08-05-long-horizon-coder-design.md
# Runs launch async into a daemon worker (202 + journal row); the panel
# polls GET /{id} for steps and POST /{id}/cancel stops a run cooperatively.
# ---------------------------------------------------------------------------



@router.websocket("/api/v1/missions/{mission_id}/ws")
async def mission_websocket(websocket: WebSocket, mission_id: str) -> None:
    """Live mission feed: a snapshot, the run's story so far, then events.

    This used to re-send the same four scalars on a timer, which meant every
    expressive thing the swarm did (a phase starting, an attempt being scored,
    a status flipping) was invisible until the mission finished. Now the swarm
    announces those as they happen and this route relays them.

    The wire protocol is a superset of the old one â€” every frame carries a
    "type", and the "sync" frame has exactly the old fields â€” so an older
    client that ignores unknown keys keeps working.
    """
    await websocket.accept()
    swarm: AgentSwarm = app.state.swarm
    bus = getattr(app.state, "events", None)

    # Last snapshot actually put on the wire. The heartbeat compares against
    # this so a quiet mission costs nothing â€” re-sending an identical snapshot
    # every couple of seconds is exactly the poll this route replaced.
    last_snapshot: Optional[Dict[str, Any]] = None

    async def send_sync(force: bool = False) -> bool:
        """Push a snapshot if it changed. False when the mission has gone away."""
        nonlocal last_snapshot
        snapshot: Optional[Dict[str, Any]] = swarm.get_mission_state(mission_id)
        if snapshot is None:
            await websocket.send_json({"error": f"Mission {mission_id} not found."})
            return False
        if force or snapshot != last_snapshot:
            last_snapshot = snapshot
            await websocket.send_json({"type": "sync", **snapshot})
        return True

    try:
        # The opening frame is unconditional: a fresh client has no state yet.
        if not await send_sync(force=True):
            return

        if bus is None:
            # No bus wired (tests, or a partial boot): degrade to the old poll
            # rather than sitting silent.
            while True:
                await asyncio.sleep(WS_PUSH_INTERVAL_SECONDS)
                if not await send_sync():
                    return

        # Replay what already happened so a client joining mid-run â€” or
        # reconnecting after a drop â€” sees the whole story, not just the tail.
        for past in bus.history(mission_id):
            await websocket.send_json(past)

        with bus.subscription(mission_id) as queue:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=WS_PUSH_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    # Quiet stretch. Cost and screenshots accrue *inside* long
                    # agent calls without firing an event, so refresh them.
                    if not await send_sync():
                        return
                    continue
                await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.debug("WebSocket for mission %s disconnected.", mission_id)
    except Exception as exc:  # noqa: BLE001 - a dead socket must not crash the app
        logger.error("WebSocket for mission %s errored: %s", mission_id, exc)
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed is fine
            pass


# ---------------------------------------------------------------------- #
# Routes: learning + skills
# ---------------------------------------------------------------------- #
