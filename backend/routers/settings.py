"""Infinity Code API router: settings.

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
    ModelRouter, ProviderManager, Settings, _apply_settings, _load_saved_provider_keys, _load_settings, _rebuild_llm_clients, _save_settings,
    _looks_masked,
    app, logger,
)

router = APIRouter()

@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    settings: Settings = getattr(app.state, "settings", None) or _load_settings()
    router: ModelRouter = app.state.router
    payload: Dict[str, Any] = settings.model_dump()
    payload["api_key_connected"] = app.state.client is not None
    payload["models"] = {role: spec.id for role, spec in router.council.items()}
    return payload


@router.put("/settings")
def put_settings(settings: Settings) -> Dict[str, Any]:
    _save_settings(settings)
    app.state.settings = settings
    _apply_settings(settings)
    logger.info(
        "Settings updated (budget $%.2f AUD/day, pass %.2f).",
        settings.daily_budget_aud,
        settings.pass_threshold,
    )
    return get_settings()


# ---------------------------------------------------------------------- #
# Routes: providers (local + paid content)
# ---------------------------------------------------------------------- #


class ProvidersUpdate(BaseModel):
    comfyui_url: Optional[str] = None
    fal_key: Optional[str] = None
    novita_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    deepseek_key: Optional[str] = None
    dashscope_key: Optional[str] = None
    nvidia_key: Optional[str] = None

    @field_validator("comfyui_url")
    @classmethod
    def _validate_comfyui_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return ""
        cleaned = value.strip().rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("ComfyUI URL must use http:// or https://.")
        if not parsed.hostname:
            raise ValueError("ComfyUI URL must include a host.")
        return cleaned

    @field_validator("fal_key", "novita_key", "elevenlabs_key", "deepseek_key", "dashscope_key", "nvidia_key")
    @classmethod
    def _strip_provider_keys(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value


@router.get("/providers")
def get_providers() -> Dict[str, Any]:
    manager: Optional[ProviderManager] = getattr(app.state, "providers", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Providers unavailable.")
    return manager.public()


@router.put("/providers")
def put_providers(update: ProvidersUpdate) -> Dict[str, Any]:
    manager: Optional[ProviderManager] = getattr(app.state, "providers", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Providers unavailable.")
    # Only overwrite keys the user actually sent (masked values are skipped so a
    # re-save of the masked form doesn't clobber the real key).
    updates = {
        k: v
        for k, v in update.model_dump().items()
        if v is not None and not _looks_masked(v)
    }
    manager.save(updates)
    # If an LLM provider key changed, rebuild clients so the next request uses
    # the new credentials without a restart.
    if any(k in updates for k in ("deepseek_key", "dashscope_key", "nvidia_key")):
        _load_saved_provider_keys()
        _rebuild_llm_clients(app)
    return manager.public()


@router.post("/providers/test")
def test_provider(body: Dict[str, str]) -> Dict[str, Any]:
    manager: Optional[ProviderManager] = getattr(app.state, "providers", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Providers unavailable.")
    name = str(body.get("name") or "").strip()
    return manager.test(name)


# ---------------------------------------------------------------------- #
# Routes: workspace (project folder the assistant edits)
# ---------------------------------------------------------------------- #
