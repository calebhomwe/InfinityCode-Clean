"""Infinity Code API router: learning.

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
    ComposeRequest, CostTracker, CriticEngine, CritiqueRequest, CritiqueResult, LearnEngine, LearnEngineError, LearnRequest,
    LearnResponse, OpenRouterClient, SKILLS_DB_PATH, SkillComposer, SkillEngine, SkillEngineError, SkillSummary, app,
    logger,
)

router = APIRouter()

@router.post("/learn", response_model=LearnResponse)
async def ingest_tutorial(request: LearnRequest) -> LearnResponse:
    learn: LearnEngine = app.state.learn
    try:
        skill = await asyncio.to_thread(
            learn.ingest_youtube, request.url.strip(), request.topic.strip()
        )
    except LearnEngineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LearnResponse(
        skill_id=skill["name"],
        verified=bool(skill["verified"]),
        steps=len(skill["steps"]),
    )


@router.get("/skills", response_model=List[SkillSummary])
def list_skills(topic: Optional[str] = None) -> List[SkillSummary]:
    skill_engine: SkillEngine = app.state.skill

    stats: Dict[str, Dict[str, Any]] = {}
    try:
        with sqlite3.connect(str(SKILLS_DB_PATH), timeout=10.0) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute(
                "SELECT name, success_rate, verified, last_used FROM skills"
            ).fetchall():
                stats[str(row["name"])] = dict(row)
    except sqlite3.Error as exc:
        logger.error("Could not read skill stats: %s", exc)

    summaries: List[SkillSummary] = []
    try:
        for name in skill_engine.list_skills():
            skill: Optional[Dict[str, Any]] = skill_engine.get_skill(name)
            if skill is None:
                continue
            skill_topic: str = str(skill.get("topic", "general"))
            if topic and topic.lower() not in skill_topic.lower():
                continue
            skill_stats: Dict[str, Any] = stats.get(name, {})
            summaries.append(
                SkillSummary(
                    name=name,
                    topic=skill_topic,
                    source_url=skill.get("source_url"),
                    verified=bool(skill_stats.get("verified", skill.get("verified", False))),
                    success_rate=float(skill_stats.get("success_rate", 0.0) or 0.0),
                    steps_count=len(skill.get("steps", []) or []),
                    last_used=skill_stats.get("last_used"),
                )
            )
    except SkillEngineError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return summaries


@router.get("/skills/{name}")
def get_skill(name: str) -> Dict[str, Any]:
    """Full skill definition (incl. `content` for reference docs)."""
    skill_engine: SkillEngine = app.state.skill
    skill = skill_engine.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="No such skill.")
    return skill


# ---------------------------------------------------------------------- #
# Routes: critic + cost
# ---------------------------------------------------------------------- #


@router.post("/critic")
async def critique(request: CritiqueRequest) -> Dict[str, Any]:
    critic: Optional[CriticEngine] = app.state.critic
    if critic is None:
        raise HTTPException(
            status_code=503,
            detail="Critic unavailable: no LLM provider key is configured - open Settings > Providers.",
        )
    result: CritiqueResult = await asyncio.to_thread(
        critic.critique,
        Path(request.work_path),
        Path(request.reference_path),
        request.task_type,
    )
    return result.model_dump()


@router.get("/providers/health")
def providers_health() -> Dict[str, Any]:
    """Live/dead state of every LLM quota on this machine (failover chain)."""
    client: Optional[OpenRouterClient] = getattr(app.state, "client", None)
    if client is None:
        return {"active": None, "routes": []}
    return {
        "active": client.provider,
        "mode": getattr(app.state, "routing_mode", "default"),
        "routes": client.health(),
    }


@router.get("/cost")
def cost_report() -> Dict[str, Any]:
    tracker: CostTracker = app.state.cost_tracker
    return tracker.report()


# ---------------------------------------------------------------------- #
# Routes: chat (plain conversation, no swarm)
# ---------------------------------------------------------------------- #




@router.post("/skills/compose")
def compose_skills(request: ComposeRequest) -> Dict[str, Any]:
    composer: Optional[SkillComposer] = getattr(app.state, "composer", None)
    if composer is None:
        raise HTTPException(
            status_code=503,
            detail="Composition unavailable: no LLM provider key is configured - open Settings > Providers.",
        )
    fused: Optional[Dict[str, Any]] = composer.compose(
        request.skill_a.strip(), request.skill_b.strip()
    )
    if fused is None:
        raise HTTPException(
            status_code=422,
            detail="These skills could not be composed (incompatible or missing).",
        )
    # Stamp the creation time the composer intentionally left blank.
    fused["created_at"] = datetime.now(timezone.utc).isoformat()
    return {"fused": fused}


# ---------------------------------------------------------------------- #
# Routes: settings
# ---------------------------------------------------------------------- #
