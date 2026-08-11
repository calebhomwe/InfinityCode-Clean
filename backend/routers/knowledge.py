"""Infinity Code API router: knowledge.

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
    LocalRAGUnavailable, _is_allowed_knowledge_root, app, local_model, logger,
)

router = APIRouter()

@router.get("/memories")
def list_memories() -> Dict[str, Any]:
    store = getattr(app.state, "memory", None)
    if store is None:
        return {"memories": [], "count": 0}
    return {"memories": store.list_recent(100), "count": store.count()}


@router.post("/memories")
def add_memory(body: Dict[str, str]) -> Dict[str, Any]:
    """Manually teach the assistant a fact (embedded + recalled like the rest)."""
    store = getattr(app.state, "memory", None)
    client = getattr(app.state, "chat_client", None) or getattr(app.state, "client", None)
    local_rag = getattr(app.state, "local_rag", None)
    text = str(body.get("text") or "").strip()
    if store is None:
        raise HTTPException(status_code=503, detail="Memory unavailable.")
    if not text:
        raise HTTPException(status_code=422, detail="Memory text is required.")
    vecs: List[List[float]] = []
    if local_rag is not None:
        try:
            vecs = local_rag.embedder.embed([text])
        except Exception as exc:  # noqa: BLE001 - fall back to configured provider
            logger.info("Local memory embedding unavailable: %s", exc)
    if not vecs and client is not None:
        vecs = client.embed([text])
    if not vecs:
        raise HTTPException(status_code=502, detail="Could not embed the memory.")
    new_id = store.add(text, vecs[0], None)
    if local_rag is not None:
        try:
            local_rag.sync_memories(store.list_recent(800))
        except Exception as exc:  # noqa: BLE001 - SQLite memory remains authoritative
            logger.info("Local memory vector sync skipped: %s", exc)
    return {"status": "ok", "id": new_id, "count": store.count()}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str) -> Dict[str, str]:
    store = getattr(app.state, "memory", None)
    if store is not None:
        store.delete(memory_id)
        local_rag = getattr(app.state, "local_rag", None)
        if local_rag is not None:
            try:
                local_rag.sync_memories(store.list_recent(800))
            except Exception:
                pass
    return {"status": "ok"}


@router.post("/memories/clear")
def clear_memories() -> Dict[str, str]:
    store = getattr(app.state, "memory", None)
    if store is not None:
        store.clear()
        local_rag = getattr(app.state, "local_rag", None)
        if local_rag is not None:
            try:
                local_rag.sync_memories([])
            except Exception:
                pass
    return {"status": "ok"}


# ---------------------------------------------------------------------- #
# Knowledge base (RAG) + free self-testing
# ---------------------------------------------------------------------- #


def _embedder():
    client = getattr(app.state, "chat_client", None) or getattr(app.state, "client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Embeddings unavailable (no OpenRouter key).")
    return client.embed


@router.get("/knowledge/sources")
def knowledge_sources() -> Dict[str, Any]:
    store = getattr(app.state, "knowledge", None)
    if store is None:
        return {"sources": [], "counts": {"files": 0, "chunks": 0}}
    return {"sources": store.list_sources(), "counts": store.counts()}




@router.post("/knowledge/sources")
def add_knowledge_source(body: Dict[str, str]) -> Dict[str, Any]:
    store = getattr(app.state, "knowledge", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge unavailable.")
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=422, detail="Path is required.")
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=422, detail="Path does not exist.")
    if not _is_allowed_knowledge_root(p):
        raise HTTPException(
            status_code=403,
            detail="Knowledge sources must be inside your home folder or the app data directory.",
        )
    return store.add_source(path, str(body.get("kind") or "vault"))


@router.delete("/knowledge/sources/{source_id}")
def remove_knowledge_source(source_id: str) -> Dict[str, str]:
    store = getattr(app.state, "knowledge", None)
    if store is not None:
        store.remove_source(source_id)
    return {"status": "ok"}


@router.post("/knowledge/sources/{source_id}/toggle")
def toggle_knowledge_source(source_id: str, body: Dict[str, Any]) -> Dict[str, str]:
    store = getattr(app.state, "knowledge", None)
    if store is not None:
        store.toggle_source(source_id, bool(body.get("enabled", True)))
    return {"status": "ok"}


@router.post("/knowledge/reindex")
def knowledge_reindex() -> Dict[str, Any]:
    """Collect stray Downloads notes into an Inbox, then (re)embed all sources.
    Runs in the threadpool (sync def) so embedding batches don't block the loop."""
    store = getattr(app.state, "knowledge", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge unavailable.")
    collected: List[str] = []
    # Organize stray Downloads notes into the first vault's Inbox, then index.
    vaults = [s for s in store.list_sources() if s["kind"] == "vault" and Path(s["path"]).is_dir()]
    downloads = [s for s in store.list_sources() if s["kind"] == "downloads"]
    if vaults and downloads:
        inbox = Path(vaults[0]["path"]) / "Inbox"
        for d in downloads:
            collected += store.collect_downloads(Path(d["path"]), inbox)
    stats = store.reindex(_embedder())
    stats["collected"] = len(collected)
    return {"status": "ok", **stats, "counts": store.counts()}


@router.get("/knowledge/local/status")
def local_knowledge_status() -> Dict[str, Any]:
    """Report whether the free Chroma + Ollama retrieval lane is ready."""
    local_rag = getattr(app.state, "local_rag", None)
    if local_rag is None:
        return {
            "available": False,
            "chromadb": False,
            "ollama": False,
            "vectors": 0,
            "reason": "Local RAG was not initialized.",
        }
    return local_rag.status()


@router.post("/knowledge/local/reindex")
def local_knowledge_reindex() -> Dict[str, Any]:
    """Index enabled knowledge sources and memories using only local compute."""
    local_rag = getattr(app.state, "local_rag", None)
    knowledge = getattr(app.state, "knowledge", None)
    memories = getattr(app.state, "memory", None)
    if local_rag is None or knowledge is None:
        raise HTTPException(status_code=503, detail="Local RAG unavailable.")
    try:
        stats = local_rag.reindex(knowledge.list_sources())
        if memories is not None:
            stats.update(local_rag.sync_memories(memories.list_recent(800)))
        return {"status": "ok", **stats, **local_rag.status()}
    except LocalRAGUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface actionable local setup errors
        raise HTTPException(status_code=500, detail=f"Local reindex failed: {exc}") from exc


@router.post("/knowledge/local/search")
def local_knowledge_search(body: Dict[str, Any]) -> Dict[str, Any]:
    local_rag = getattr(app.state, "local_rag", None)
    query = str(body.get("query") or "").strip()
    if local_rag is None or not query:
        return {"results": []}
    try:
        return {"results": local_rag.search(query, top_k=int(body.get("top_k", 5)))}
    except LocalRAGUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------- #
# Routes: self-training (live web -> K3 distil -> RAG index)
# ---------------------------------------------------------------------- #




@router.get("/training/status")
def training_status() -> Dict[str, Any]:
    trainer = getattr(app.state, "trainer", None)
    if trainer is None:
        return {"available": False, "reason": "No OpenRouter client.", "cards": 0}
    return {"available": True, **trainer.status()}


@router.post("/training/run")
async def training_run(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one learning cycle. Sync-heavy (network + embeddings), so it goes to
    the threadpool to keep the event loop responsive for live missions."""
    trainer = getattr(app.state, "trainer", None)
    if trainer is None:
        raise HTTPException(status_code=503, detail="Self-training unavailable (no OpenRouter key).")
    topics = (body or {}).get("topics") or None
    if topics is not None and not isinstance(topics, list):
        raise HTTPException(status_code=422, detail="topics must be a list of topic keys.")
    result = await asyncio.to_thread(trainer.run_cycle, topics, _embedder())
    return {"status": "ok", **result}


@router.get("/training/personal-topics")
def training_personal_topics_get() -> Dict[str, Any]:
    """Return the user's saved personal-learning topics."""
    trainer = getattr(app.state, "trainer", None)
    if trainer is None:
        return {"topics": []}
    return {"topics": trainer.get_personal_topics()}


@router.post("/training/personal-topics")
def training_personal_topics_set(body: Dict[str, Any]) -> Dict[str, Any]:
    """Save the user's personal-learning topics. Merged into the next cycle."""
    trainer = getattr(app.state, "trainer", None)
    if trainer is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Self-training unavailable. Check the /training/status endpoint "
                "for the specific reason (missing LLM key, missing knowledge store)."
            ),
        )
    topics = body.get("topics") if isinstance(body, dict) else None
    if not isinstance(topics, list):
        raise HTTPException(status_code=422, detail="topics must be a list.")
    saved = trainer.set_personal_topics(topics)
    return {"status": "ok", "topics": saved}


@router.post("/knowledge/search")
def knowledge_search(body: Dict[str, Any]) -> Dict[str, Any]:
    store = getattr(app.state, "knowledge", None)
    if store is None:
        return {"results": []}
    query = str(body.get("query") or "").strip()
    if not query:
        return {"results": []}
    vecs = _embedder()([query])
    if not vecs:
        return {"results": []}
    hits = store.search(vecs[0], top_k=int(body.get("top_k", 5)))
    return {"results": hits}


@router.post("/selftest/run")
def selftest_run(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Free gap-analysis pass via the local LM Studio model (no cloud spend)."""
    tester = getattr(app.state, "selftest", None)
    knowledge = getattr(app.state, "knowledge", None)
    if tester is None or knowledge is None:
        raise HTTPException(status_code=503, detail="Self-test unavailable.")
    n = int((body or {}).get("n", 6))
    return tester.run(knowledge, n=n)


@router.get("/selftest/gaps")
def selftest_gaps() -> Dict[str, Any]:
    tester = getattr(app.state, "selftest", None)
    if tester is None:
        return {"gaps": [], "count": 0, "local_model": None}
    return {"gaps": tester.list_gaps(), "count": tester.gap_count(), "local_model": local_model()}
