"""Infinity Code API router: workspace.

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
    DATA_DIR, IDEWorkspaceError, IDEWorkspaceStore, RevisionConflict, SessionLogger, VALID_SIGNALS, _is_allowed_knowledge_root, _load_workspace,
    _resolve_workspace_path, _save_workspace, _workspace_tree, app, difflib,
)

router = APIRouter()

@router.get("/ide/files")
def get_ide_files(project: str = "default") -> Dict[str, Any]:
    """Load a revisioned virtual project for the embedded browser IDE."""
    store: Optional[IDEWorkspaceStore] = getattr(app.state, "ide_workspace", None)
    if store is None:
        raise HTTPException(status_code=503, detail="IDE workspace storage unavailable.")
    try:
        return store.get(project)
    except IDEWorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/ide/files")
def put_ide_files(body: Dict[str, Any]) -> Dict[str, Any]:
    """Atomically replace a browser-IDE project using optimistic revisions."""
    store: Optional[IDEWorkspaceStore] = getattr(app.state, "ide_workspace", None)
    if store is None:
        raise HTTPException(status_code=503, detail="IDE workspace storage unavailable.")
    project = str(body.get("project") or "default")
    files = body.get("files")
    base_revision = body.get("base_revision")
    try:
        return store.save(
            project,
            files,
            base_revision=base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "current_revision": exc.current_revision,
            },
        ) from exc
    except (IDEWorkspaceError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/workspace")
def get_workspace() -> Dict[str, Any]:
    workspace: Optional[Path] = getattr(app.state, "workspace", None) or _load_workspace()
    if workspace is None or not workspace.is_dir():
        return {"path": "", "tree": [], "valid": False}
    return {
        "path": str(workspace.resolve()),
        "tree": _workspace_tree(workspace),
        "valid": True,
    }


@router.put("/workspace")
def put_workspace(body: Dict[str, str]) -> Dict[str, Any]:
    path_str = str(body.get("path") or "").strip()
    tools: Optional[Any] = getattr(app.state, "tools", None)
    if not path_str:
        _save_workspace(None)
        app.state.workspace = None
        if tools is not None:
            tools.workspace = None
        return {"path": "", "tree": [], "valid": False}
    p = Path(path_str)
    if not p.exists():
        raise HTTPException(status_code=422, detail="Path does not exist.")
    if not p.is_dir():
        raise HTTPException(status_code=422, detail="Path is not a directory.")
    if not _is_allowed_knowledge_root(p):
        raise HTTPException(
            status_code=403,
            detail="Workspace must be inside your home folder or the app data directory.",
        )
    _save_workspace(p)
    app.state.workspace = p
    if tools is not None:
        tools.workspace = p
    return {
        "path": str(p.resolve()),
        "tree": _workspace_tree(p),
        "valid": True,
    }


@router.post("/workspace/preview-edit")
def preview_workspace_edit(body: Dict[str, Any]) -> Dict[str, Any]:
    workspace: Optional[Path] = getattr(app.state, "workspace", None) or _load_workspace()
    if workspace is None:
        raise HTTPException(status_code=400, detail="No workspace selected.")
    rel_path = str(body.get("file_path") or "").strip()
    search = str(body.get("search") or "")
    replace = str(body.get("replace"))
    if not rel_path:
        raise HTTPException(status_code=422, detail="file_path is required.")
    target = _resolve_workspace_path(workspace, rel_path)
    original = ""
    if target.is_file():
        try:
            original = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not read file: {exc}") from exc
    patched = original.replace(search, replace, 1) if search else replace
    if search and patched == original:
        raise HTTPException(
            status_code=409,
            detail="Search block not found in current file. The file may have changed.",
        )
    diff = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )
    return {
        "file_path": rel_path,
        "original": original,
        "patched": patched,
        "diff": "".join(diff),
        "search": search,
        "replace": replace,
    }


@router.post("/workspace/apply-edit")
def apply_workspace_edit(body: Dict[str, Any]) -> Dict[str, Any]:
    workspace: Optional[Path] = getattr(app.state, "workspace", None) or _load_workspace()
    if workspace is None:
        raise HTTPException(status_code=400, detail="No workspace selected.")
    rel_path = str(body.get("file_path") or "").strip()
    search = str(body.get("search") or "")
    replace = str(body.get("replace"))
    if not rel_path:
        raise HTTPException(status_code=422, detail="file_path is required.")
    target = _resolve_workspace_path(workspace, rel_path)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    original = ""
    if target.is_file():
        try:
            original = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not read file: {exc}") from exc
    if search:
        if search not in original:
            raise HTTPException(
                status_code=409,
                detail="Search block not found in current file. The file may have changed.",
            )
        patched = original.replace(search, replace, 1)
    else:
        patched = replace
    try:
        target.write_text(patched, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write file: {exc}") from exc
    return {"file_path": rel_path, "applied": True, "chars": len(patched)}


@router.post("/workspace/run-shell")
def run_workspace_shell(body: Dict[str, str]) -> Dict[str, Any]:
    workspace: Optional[Path] = getattr(app.state, "workspace", None) or _load_workspace()
    if workspace is None:
        raise HTTPException(status_code=400, detail="No workspace selected.")
    command = str(body.get("command") or "").strip()
    if not command:
        raise HTTPException(status_code=422, detail="command is required.")
    import subprocess
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:2000],
        }
    except subprocess.TimeoutExpired:
        return {"command": command, "returncode": -1, "stdout": "", "stderr": "Command timed out after 60s."}
    except Exception as exc:  # noqa: BLE001
        return {"command": command, "returncode": -1, "stdout": "", "stderr": str(exc)[:500]}


# ---------------------------------------------------------------------- #
# Routes: session logging (custom model strategy data flywheel)
# ---------------------------------------------------------------------- #


class SessionSignalRequest(BaseModel):
    turn_id: Optional[str] = None
    signal: str = "accepted"


@router.post("/sessions/{session_id}/signal")
def post_session_signal(session_id: str, request: SessionSignalRequest) -> Dict[str, Any]:
    """Record user feedback (accepted/edited/rejected/reverted) for a session."""
    logger: Optional[SessionLogger] = getattr(app.state, "session_logger", None)
    if logger is None:
        raise HTTPException(status_code=503, detail="Session logging unavailable.")
    signal = str(request.signal).strip().lower()
    if signal not in VALID_SIGNALS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid signal. Choose from: {', '.join(sorted(VALID_SIGNALS))}",
        )
    ok = logger.signal(session_id, request.turn_id or "", signal)
    if not ok:
        raise HTTPException(status_code=404, detail="Session or turn not found.")
    return {"session_id": session_id, "signal": signal, "ok": True}


@router.get("/sessions/stats")
def get_session_stats() -> Dict[str, Any]:
    logger: Optional[SessionLogger] = getattr(app.state, "session_logger", None)
    if logger is None:
        raise HTTPException(status_code=503, detail="Session logging unavailable.")
    return logger.stats()


@router.get("/sessions/{session_id}/plan")
def get_session_plan(session_id: str) -> Dict[str, Any]:
    """Return the plan anchor for a long-horizon session if it exists."""
    plan_path = DATA_DIR / "plans" / session_id / "plan.md"
    if not plan_path.is_file():
        raise HTTPException(status_code=404, detail="No plan found for this session.")
    try:
        content = plan_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read plan: {exc}") from exc
    return {"session_id": session_id, "plan": content}


# ---------------------------------------------------------------------- #
# Routes: eval harness (custom model strategy benchmark)
# ---------------------------------------------------------------------- #
