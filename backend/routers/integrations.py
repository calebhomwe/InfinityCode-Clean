"""Infinity Code API router: integrations.

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
    AI_CHATS_DB, BRIDGE_DB, INFINITY_CHATS_DB, app, create_fallback_prompt, detect_quota_exhausted, export_session_for_infinity, find_claude_project_files,
    get_all_conversations, get_continuation_prompt, get_conversations, get_exported_sessions, get_source_stats, get_sync_status, logger, search_all_conversations,
    get_conversation, get_conversation_messages, search_conversations,
    sync_all_sources, sync_claude_history,
)

router = APIRouter()

@router.get("/claude-sync/status")
def claude_sync_status() -> Dict[str, Any]:
    """Check sync status between Claude's history and Infinity's copy.
    
    Claude can poll this to check if Infinity has the latest data.
    """
    try:
        return get_sync_status(db_path=INFINITY_CHATS_DB)
    except Exception as e:
        logger.error(f"Claude sync status error: {e}")
        return {"error": str(e), "claude_available": False}


@router.post("/claude-sync/trigger")
def claude_sync_trigger(force: bool = False) -> Dict[str, Any]:
    """Manually trigger a sync from Claude's history to Infinity.
    
    Args:
        force: If True, re-import even if hash matches (default: False)
    """
    try:
        result = sync_claude_history(db_path=INFINITY_CHATS_DB, force=force)
        return result
    except Exception as e:
        logger.error(f"Claude sync trigger error: {e}")
        return {"success": False, "error": str(e)}


@router.get("/claude-sync/chats")
def list_claude_sync_chats(limit: int = 100) -> Dict[str, Any]:
    """List synced Claude conversations (Infinity's copy)."""
    try:
        conversations = get_conversations(db_path=INFINITY_CHATS_DB, limit=limit)
        return {"conversations": conversations, "count": len(conversations)}
    except Exception as e:
        logger.error(f"List chats error: {e}")
        return {"error": str(e), "conversations": []}


@router.get("/chats/search")
def search_chats(q: str, limit: int = 20) -> Dict[str, Any]:
    """Search conversations by content (searches Infinity's copy)."""
    try:
        results = search_conversations(
            query=q, db_path=INFINITY_CHATS_DB, limit=limit
        )
        return {"results": results, "count": len(results), "query": q}
    except Exception as e:
        logger.error(f"Search chats error: {e}")
        return {"error": str(e), "results": []}


# Initialize chats.db on startup (called from lifespan, not on_event).


@router.get("/claude-bridge/status")
def claude_bridge_status() -> Dict[str, Any]:
    """Check if Claude quota is exhausted and bridge is ready."""
    try:
        quota_exhausted = detect_quota_exhausted()
        sessions = get_exported_sessions(db_path=BRIDGE_DB)
        
        return {
            "quota_exhausted": quota_exhausted,
            "bridge_ready": len(sessions) > 0,
            "exported_sessions": len(sessions),
            "can_continue": quota_exhausted and len(sessions) > 0,
        }
    except Exception as e:
        logger.error(f"Bridge status error: {e}")
        return {"error": str(e), "quota_exhausted": False}


@router.get("/claude-bridge/sessions")
def claude_bridge_sessions() -> Dict[str, Any]:
    """List all exported Claude sessions available for continuation."""
    try:
        sessions = get_exported_sessions(db_path=BRIDGE_DB)
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        logger.error(f"Bridge sessions error: {e}")
        return {"error": str(e), "sessions": []}


@router.post("/claude-bridge/export")
def claude_bridge_export(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Export a Claude session for continuation in Infinity.
    
    Args:
        session_id: Claude session ID or path to JSONL file
    """
    try:
        session_id = payload.get("session_id") or payload.get("project_path")
        if not session_id:
            return {"success": False, "error": "session_id or project_path required"}
        
        result = export_session_for_infinity(session_id, db_path=BRIDGE_DB)
        return result or {"success": False, "error": "Export failed"}
    except Exception as e:
        logger.error(f"Bridge export error: {e}")
        return {"success": False, "error": str(e)}


@router.get("/claude-bridge/continue/{exported_id}")
def claude_bridge_continue(exported_id: int) -> Dict[str, Any]:
    """Get the continuation prompt for a specific exported session.
    
    This prompt can be sent to Infinity's models to continue the work.
    """
    try:
        prompt = get_continuation_prompt(exported_id, db_path=BRIDGE_DB)
        if not prompt:
            return {"error": "Session not found or no prompt available"}
        
        return {
            "exported_id": exported_id,
            "prompt": prompt,
            "ready": True,
            "message": "Send this prompt to Infinity's models to continue the work",
        }
    except Exception as e:
        logger.error(f"Bridge continue error: {e}")
        return {"error": str(e)}


@router.get("/claude-bridge/discover")
def claude_bridge_discover(project_path: Optional[str] = None) -> Dict[str, Any]:
    """Discover available Claude sessions for a project.
    
    Args:
        project_path: Optional project path to filter by
    """
    try:
        if project_path:
            files = find_claude_project_files(project_path)
        else:
            # Find all projects
            projects_dir = Path.home() / ".claude" / "projects"
            files = []
            if projects_dir.exists():
                for proj_dir in projects_dir.iterdir():
                    if proj_dir.is_dir():
                        files.extend(list(proj_dir.glob("*.jsonl")))
        
        sessions = []
        for f in files[:50]:  # Limit results
            sessions.append({
                "path": str(f),
                "name": f.name,
                "project": f.parent.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        logger.error(f"Bridge discover error: {e}")
        return {"error": str(e), "sessions": []}


@router.post("/claude-bridge/fallback")
def claude_bridge_fallback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a fallback prompt when Claude quota is exhausted.
    
    This generates a prompt for Infinity to continue work.
    """
    try:
        project_path = payload.get("project_path", "unknown")
        last_query = payload.get("last_query", "Continue the current work")
        
        prompt = create_fallback_prompt(project_path, last_query)
        
        return {
            "fallback_ready": True,
            "prompt": prompt,
            "message": "Claude quota exhausted. Use this prompt with Infinity's models.",
            "models_available": ["qwen", "deepseek", "openrouter"],
        }
    except Exception as e:
        logger.error(f"Bridge fallback error: {e}")
        return {"error": str(e)}


# Initialize bridge DB on startup (called from lifespan, not on_event).


@router.get("/ai-chats")
def list_ai_chats(
    source: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """List AI conversations from all sources (Claude, Codex, GPT).
    
    Args:
        source: Filter by source ('claude', 'codex', 'gpt', or None for all)
        limit: Max number of results
    """
    try:
        conversations = get_all_conversations(
            source=source,
            limit=limit,
        )
        return {
            "conversations": conversations,
            "count": len(conversations),
            "source": source or "all",
        }
    except Exception as e:
        logger.error(f"AI chats list error: {e}")
        return {"error": str(e), "conversations": []}


@router.get("/ai-chats/search")
def search_ai_chats(
    q: str,
    source: Optional[str] = None,
    limit: int = 20
) -> Dict[str, Any]:
    """Search AI conversations across all sources.
    
    Args:
        q: Search query
        source: Filter by source
        limit: Max results
    """
    try:
        results = search_all_conversations(
            query=q,
            source=source,
            limit=limit,
        )
        return {
            "results": results,
            "count": len(results),
            "query": q,
            "source": source or "all",
        }
    except Exception as e:
        logger.error(f"AI chats search error: {e}")
        return {"error": str(e), "results": []}


@router.get("/ai-chats/stats")
def ai_chats_stats() -> Dict[str, Any]:
    """Get conversation counts per AI source."""
    try:
        stats = get_source_stats()
        total = sum(stats.values())
        return {
            "stats": stats,
            "total": total,
            "sources": list(stats.keys()),
        }
    except Exception as e:
        logger.error(f"AI chats stats error: {e}")
        return {"error": str(e), "stats": {}, "total": 0}


@router.post("/ai-chats/sync")
def ai_chats_sync() -> Dict[str, Any]:
    """Sync all AI chat sources (Claude, Codex, GPT) to Infinity."""
    try:
        results = sync_all_sources()
        total_imported = sum(r.get("imported", 0) for r in results.values())
        return {
            "success": True,
            "results": results,
            "total_imported": total_imported,
            "message": f"Synced {total_imported} conversations",
        }
    except Exception as e:
        logger.error(f"AI chats sync error: {e}")
        return {"success": False, "error": str(e)}


@router.get("/ai-chats/{conversation_id}/messages")
def ai_chats_messages(conversation_id: int) -> Dict[str, Any]:
    """All messages of one synced conversation, oldest first."""
    try:
        conv = get_conversation(conversation_id)
        if conv is None:
            return {"error": "conversation not found"}
        return {
            "conversation": conv,
            "messages": get_conversation_messages(conversation_id),
        }
    except Exception as e:
        logger.error(f"AI chats messages error: {e}")
        return {"error": str(e), "messages": []}


# Initialize AI chats DB on startup (called from lifespan, not on_event).
