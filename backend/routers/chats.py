"""Infinity Code API router: chats.

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
    ACTION_TOOL_NAMES, ASSISTANT_SYSTEM_PROMPT, ASSISTANT_TOOL_SCHEMAS, CHAT_FALLBACK_MODELS, CHAT_MODELS, CHAT_SYSTEM_PROMPT, ChatCreateRequest, ChatMessageRequest,
    ChatPatchRequest, CostTracker, DEFAULT_CHAT_MODEL, FREE_CHAT_MODELS, LOCAL_CHAT_MODELS, ONCE_PER_SESSION_RISKS, OWNER_ALIAS, OWNER_NAME,
    OpenRouterClient, OpenRouterError, Settings, SmartDefaults, TOOL_NAMES, TOOL_SCHEMAS, ThreadPoolExecutor, ToolRegistry,
    USD_PER_AUD, _CHAT_MODEL_IDS, _connect, _fts_insert, _load_settings, _load_workspace, _workspace_tree_text, app,
    estimate_cost, gates_for_approval_mode, logger, maybe_owner_line, release_local_gpu, risk_of,
)
try:
    from backend.core.design_module import DESIGN_REQUEST_RE, DESIGN_SYSTEM_PROMPT
except ImportError:  # running with backend/ as the working directory
    from core.design_module import DESIGN_REQUEST_RE, DESIGN_SYSTEM_PROMPT

try:
    from backend.core.diagnostics import diagnostics_block
except ImportError:  # running with backend/ as the working directory
    from core.diagnostics import diagnostics_block

try:
    from backend.core import vision_assist
except ImportError:  # running with backend/ as the working directory
    from core import vision_assist

def _split_media(items):
    """Split incoming chat attachments into sendable images and media the
    chat ingress cannot process yet (audio/video). Bounded to the first 4
    items; dropped entries are reduced to their data-URL prefix so logs and
    notes stay short. Total function: never raises."""
    images: List[str] = []
    dropped: List[str] = []
    for item in (items or [])[:4]:
        if not isinstance(item, str):
            continue
        if item.startswith("data:image/"):
            images.append(item)
        elif item.startswith("data:audio/") or item.startswith("data:video/"):
            dropped.append(item.split(",", 1)[0])
    return images, dropped


router = APIRouter()

@router.get("/chat/models")
def chat_models(include_hidden: bool = False) -> Dict[str, Any]:
    models = CHAT_MODELS if include_hidden else [m for m in CHAT_MODELS if not m.get("hidden")]
    return {"models": models, "default": DEFAULT_CHAT_MODEL}


@router.get("/chats")
def list_chats() -> List[Dict[str, Any]]:
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT id, title, model, updated_at, pinned FROM chats "
                "ORDER BY pinned DESC, updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


@router.post("/local/release")
def local_release() -> Dict[str, Any]:
    """Manually free the VRAM held by local model servers (kill switch)."""
    return release_local_gpu(reason="manual")


@router.post("/chats/{chat_id}/model")
def set_chat_model(chat_id: str, request: ChatPatchRequest) -> Dict[str, Any]:
    """Switch a chat's model, with the local-GPU kill switch.

    Switching LOCAL -> CLOUD releases the ~14GB the local server is holding
    (unless swarm missions are running). Switching CLOUD -> LOCAL leaves the
    server to JIT-load as needed.
    """
    from main import _resolve_chat_model
    new_model = _resolve_chat_model(getattr(request, "model", None))
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT model FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Chat not found.")
            old_model = str(row["model"] or "")
            connection.execute(
                "UPDATE chats SET model = ?, updated_at = ? WHERE id = ?",
                (new_model, datetime.now(timezone.utc).isoformat(), chat_id),
            )
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    released: Dict[str, Any] = {"released": False}
    was_local = old_model in LOCAL_CHAT_MODELS
    now_local = new_model in LOCAL_CHAT_MODELS
    if was_local and not now_local:
        released = release_local_gpu(reason=f"switched {old_model} -> {new_model}")
    return {"status": "ok", "model": new_model, "gpu": released}


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, request: ChatPatchRequest) -> Dict[str, Any]:
    """Rename and/or pin a chat."""
    # Model change via PATCH gets the same kill-switch treatment.
    requested_model = getattr(request, "model", None)
    if requested_model:
        return set_chat_model(chat_id, request)
    sets: List[str] = []
    values: List[Any] = []
    if request.title is not None and request.title.strip():
        sets.append("title = ?")
        values.append(request.title.strip()[:80])
    if request.pinned is not None:
        sets.append("pinned = ?")
        values.append(1 if request.pinned else 0)
    if not sets:
        raise HTTPException(status_code=422, detail="Nothing to update.")
    values.append(chat_id)
    try:
        with _connect() as connection:
            cursor = connection.execute(
                f"UPDATE chats SET {', '.join(sets)} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Chat not found.")
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    return {"status": "ok"}


@router.get("/search")
def search_chats(q: str) -> Dict[str, Any]:
    """Full-text search across all chat messages (FTS5)."""
    query = q.strip()
    if not query:
        return {"results": []}
    # Quote each term so FTS5 syntax characters in user input can't error.
    fts_query = " ".join(
        '"' + term.replace('"', '""') + '"' for term in query.split()[:8]
    )
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT f.chat_id, c.title, "
                "snippet(chat_fts, 2, '[', ']', 'â€¦', 12) AS snippet "
                "FROM chat_fts f JOIN chats c ON c.id = f.chat_id "
                "WHERE chat_fts MATCH ? ORDER BY rank LIMIT 20",
                (fts_query,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Search error: {exc}") from exc
    # Dedupe by chat, keep best-ranked snippet.
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row["chat_id"] not in seen:
            seen[row["chat_id"]] = dict(row)
    return {"results": list(seen.values())}


@router.post("/chats")
def create_chat(request: ChatCreateRequest) -> Dict[str, Any]:
    from main import _resolve_chat_model
    chat_id: str = str(uuid.uuid4())
    model: str = _resolve_chat_model(request.model)
    now: str = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as connection:
            connection.execute(
                "INSERT INTO chats (id, title, model, created_at, updated_at) "
                "VALUES (?, 'New chat', ?, ?, ?)",
                (chat_id, model, now, now),
            )
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    return {"id": chat_id, "title": "New chat", "model": model, "messages": []}


def _chat_row(chat_id: str) -> Dict[str, Any]:
    with _connect() as connection:
        chat = connection.execute(
            "SELECT id, title, model FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        messages = connection.execute(
            "SELECT id, role, content, created_at, tools_json FROM chat_messages "
            "WHERE chat_id = ? ORDER BY created_at ASC, rowid ASC",
            (chat_id,),
        ).fetchall()
    result: Dict[str, Any] = dict(chat)
    out_messages: List[Dict[str, Any]] = []
    for m in messages:
        row = dict(m)
        raw_tools = row.pop("tools_json", None)
        if raw_tools:
            try:
                row["tools"] = json.loads(raw_tools)
            except (json.JSONDecodeError, TypeError):
                pass
        out_messages.append(row)
    result["messages"] = out_messages
    return result


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str) -> Dict[str, Any]:
    try:
        return _chat_row(chat_id)
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str) -> Dict[str, str]:
    try:
        with _connect() as connection:
            connection.execute("DELETE FROM chat_messages WHERE chat_id = ?", (chat_id,))
            connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    return {"status": "deleted"}


@router.post("/chats/{chat_id}/stream")
async def stream_chat_message(
    chat_id: str, request: ChatMessageRequest
) -> StreamingResponse:
    from main import _resolve_chat_model
    client: Optional[OpenRouterClient] = (
        getattr(app.state, "chat_client", None) or app.state.client
    )
    # Is this chat pinned to a LOCAL model? Local runs on this PC's own GPU:
    # no API key needed and no cost, so both cloud guards below are skipped.
    _requested_model: Optional[str] = None
    _chat_row: Optional[Dict[str, Any]] = None
    try:
        with _connect() as _c:
            _row = _c.execute(
                "SELECT model FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if _row is not None:
                _chat_row = dict(_row)
                _requested_model = str(_row["model"] or "")
    except sqlite3.Error:
        _requested_model = None
    _is_local_chat: bool = _requested_model in LOCAL_CHAT_MODELS

    if client is None and not _is_local_chat:
        raise HTTPException(
            status_code=503, detail="Chat unavailable: no LLM provider key is configured - open Settings > Providers."
        )
    # Hard budget guard: if today's spend is already at cap, refuse before any
    # LLM call. This prevents runaway chat usage from silently exceeding the
    # daily limit (missions/loops already enforce this via CostTracker).
    # Local models cost nothing, so they keep working at cap.
    cost_tracker: Optional[CostTracker] = getattr(app.state, "cost_tracker", None)
    if (
        not _is_local_chat
        and cost_tracker is not None
        and cost_tracker.remaining_aud <= 0
    ):
        raise HTTPException(
            status_code=429,
            detail="Daily budget reached. Increase the budget in Settings or try again tomorrow.",
        )
    mode: str = request.mode if request.mode in ("append", "regenerate", "edit") else "append"
    user_content: str = request.content.strip()
    if mode != "regenerate" and not user_content:
        raise HTTPException(status_code=422, detail="Message content is required.")

    if _chat_row is None:
        raise HTTPException(status_code=404, detail="Chat not found.")
    try:
        with _connect() as connection:
            chat = _chat_row  # reuse row from block 1 — same query, saved a round-trip
            rows = connection.execute(
                "SELECT id, role, content FROM chat_messages WHERE chat_id = ? "
                "ORDER BY created_at ASC, rowid ASC",
                (chat_id,),
            ).fetchall()
            history: List[Dict[str, Any]] = [dict(r) for r in rows]

            # Edit: keep only the first keep_messages turns, drop the rest.
            if mode == "edit" and request.keep_messages is not None:
                keep = max(0, min(request.keep_messages, len(history)))
                dropped = history[keep:]
                history = history[:keep]
                for msg in dropped:
                    connection.execute(
                        "DELETE FROM chat_messages WHERE id = ?", (msg["id"],)
                    )
                    connection.execute(
                        "DELETE FROM chat_fts WHERE message_id = ?", (msg["id"],)
                    )
            # Regenerate: drop trailing assistant replies, re-answer the last user turn.
            elif mode == "regenerate":
                while history and history[-1]["role"] == "assistant":
                    msg = history.pop()
                    connection.execute(
                        "DELETE FROM chat_messages WHERE id = ?", (msg["id"],)
                    )
                    connection.execute(
                        "DELETE FROM chat_fts WHERE message_id = ?", (msg["id"],)
                    )
                if not history or history[-1]["role"] != "user":
                    raise HTTPException(
                        status_code=422, detail="Nothing to regenerate."
                    )
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    model: str = _resolve_chat_model(request.model or chat["model"])
    has_images: bool = any(
        isinstance(i, str) and i.startswith("data:image/") for i in request.images
    )
    # Image turns route to a vision-capable model; text models 400 on image_url.
    # Only override the user's choice if the selected model is not known to
    # support vision â€” otherwise respect their explicit model pick.
    if has_images:
        spec = app.state.router.spec_for_id(model)
        if spec is None or not spec.supports_vision:
            model = "dashscope/qwen3-vl-plus"

    # Smart model routing: if the user didn't explicitly pick a model, nudge
    # toward a specialist for code or vision queries (only when no images are
    # attached â€” vision routing already handled that case).
    current_settings: Settings = getattr(app.state, "settings", None) or _load_settings()
    smart_assist_model: bool = bool(getattr(current_settings, "smart_assist", True))
    if (
        smart_assist_model
        and not request.model
        and not has_images
        and not request.assistant
    ):
        suggested = SmartDefaults.pick_chat_model(user_content)
        if suggested and suggested in _CHAT_MODEL_IDS:
            model = suggested

    # Live web search via OpenRouter's ":online" model suffix is applied later
    # to every candidate so fallbacks don't silently lose web search.

    # An activated Agent Library persona takes over the system prompt; in
    # assistant mode we still append the tool-use operating instructions.
    active_agent: Optional[Dict[str, Any]] = None
    agent_lib = getattr(app.state, "agents", None)
    if request.agent_id and agent_lib is not None:
        active_agent = agent_lib.get(request.agent_id)
    if active_agent and active_agent.get("prompt"):
        system_prompt = active_agent["prompt"]
        if request.assistant:
            system_prompt += "\n\n---\n\n" + ASSISTANT_SYSTEM_PROMPT
    elif request.assistant:
        system_prompt = ASSISTANT_SYSTEM_PROMPT
    else:
        system_prompt = (current_settings.system_prompt or "").strip() or CHAT_SYSTEM_PROMPT
    notes: str = (current_settings.user_notes or "").strip()
    if notes:
        system_prompt += "\n\nPersistent notes about this user:\n" + notes
    # Owner recognition: identity + hint joke on who-owns questions.
    _owner_identity = maybe_owner_line(request.content or "",
                                       name=OWNER_NAME, alias=OWNER_ALIAS)
    if _owner_identity:
        system_prompt += "\n\n" + _owner_identity
    # Vibe voices: Chat mode gets a warm Kimi-style voice, Work mode gets a
    # terse engineering voice. Personas and assistant mode keep their own.
    if request.tone == "chat" and not request.assistant and active_agent is None:
        system_prompt += (
            "\n\nVoice: keep it light and human \u2014 short, warm replies with a "
            "spark of personality (Kimi-style). No emoji. "
            "No corporate filler, no walls of caveats; help first, clearly."
        )
    elif request.tone == "work":
        system_prompt += (
            "\n\nVoice: work mode. Precise, terse, engineering-first: plan \u2192 "
            "code \u2192 verify. No small talk, no emoji."
        )

    # Local-model awareness: tell the on-box model it's running locally so it
    # can be direct, skip cloud-style hedging, and suggest escalation when a
    # task exceeds local capability.
    if _is_local_chat:
        _local_spec = LOCAL_CHAT_MODELS.get(model, {})
        system_prompt += (
            "\n\nENVIRONMENT: You are running LOCALLY on the user's own machine "
            "(not in the cloud). You are served by a local model server "
            f"({_local_spec.get('launcher', 'local server')}) on this PC's GPU. "
            "The user chose you for privacy and zero-cost operation. "
            "Be direct and efficient; you have no API billing concerns. "
            "If you need heavy compute or a capability you lack, suggest switching "
            "to a cloud model for that specific task."
        )
    # Tell the model what local subsystems are actually wired up.
    _caps: list[str] = []
    if getattr(app.state, "local_rag", None) is not None:
        _caps.append("local RAG (ChromaDB)")
    if getattr(app.state, "knowledge", None) is not None:
        _caps.append("knowledge base")
    if getattr(app.state, "memory", None) is not None:
        _caps.append("semantic memory")
    if getattr(app.state, "omnibrain", None) is not None:
        _caps.append("OmniBrain vector search")
    if _caps:
        system_prompt += "\nAvailable local systems: " + ", ".join(_caps) + "."

    # Workspace/project context: give the model the file tree so it can reason
    # about the user's actual codebase without first asking "what files exist?".
    workspace: Optional[Path] = getattr(app.state, "workspace", None) or _load_workspace()
    if workspace is not None and workspace.is_dir():
        try:
            tree_lines = _workspace_tree_text(workspace)
            if tree_lines:
                system_prompt += (
                    "\n\nActive workspace: " + str(workspace.resolve()) +
                    "\nProject tree (workspace-relative paths):\n" + tree_lines
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not build workspace context: %s", exc)

    # Semantic memory + knowledge base (RAG): one shared embed feeds both.
    # Runs in a worker thread under a hard time budget â€” a slow embedding API
    # must never hold the whole reply hostage (it was the #1 TTFT cost).
    memory_store = getattr(app.state, "memory", None)
    local_rag = getattr(app.state, "local_rag", None)
    auto_memory: bool = bool(getattr(current_settings, "auto_memory", False))
    recalled_count: int = 0
    knowledge_store = getattr(app.state, "knowledge", None)
    knowledge_count: int = 0
    skill_lib = getattr(app.state, "skill", None)

    omnibrain = getattr(app.state, "omnibrain", None)
    use_omnibrain: bool = bool(getattr(current_settings, "omnibrain_enabled", False))
    omnibrain_count: int = 0

    def _grounding() -> Tuple[str, int, int, int]:
        """Embed the query once, recall memory + knowledge. Also surface the
        top-K matching skills from the library so the model sees them
        without being asked. Returns
        (suffix, recalled_count, knowledge_count, omnibrain_count).
        """
        suffix = ""
        rec_n = 0
        kn_n = 0
        ob_n = 0
        local_memory_hit = False
        local_knowledge_hit = False

        # OmniBrain embeds server-side, so it needs no local embed call and can
        # overlap the (slow) client.embed() below instead of queueing behind it.
        ob_future = None
        ob_pool = None
        if use_omnibrain and omnibrain is not None:
            try:
                ob_pool = ThreadPoolExecutor(max_workers=1)
                ob_future = ob_pool.submit(omnibrain.search, user_content, 4)
            except Exception as exc:  # noqa: BLE001 - never block the reply
                logger.warning("OmniBrain dispatch failed: %s", exc)
                ob_future = None
        # Skill retrieval is keyword-based (no embed call) so it stays free
        # and can run even when the LLM embed endpoint is unavailable.
        if skill_lib is not None:
            try:
                # Owner-pinned plugins (always: true) load every turn, ahead
                # of the keyword search hits.
                pinned = skill_lib.always_on()
                _seen = {h["name"] for h in pinned}
                sk_hits = pinned + [
                    h for h in skill_lib.search(user_content, top_k=3)
                    if h["name"] not in _seen
                ]
                if sk_hits:
                    lines: List[str] = []
                    for hit in sk_hits:
                        line = f"- {hit['name']}"
                        if hit.get("title") and hit["title"] != hit["name"]:
                            line += f" ({hit['title']})"
                        desc = str(hit.get("description") or "").strip()
                        if desc:
                            line += f": {desc}"
                        lines.append(line)
                    suffix += (
                        "\n\nSKILLS AVAILABLE FOR THIS TURN "
                        "(read the full skill via the read_skill tool if you want to use it):\n"
                        + "\n".join(lines)
                    )
            except Exception as exc:  # noqa: BLE001 - never block the reply
                logger.warning("Skill retrieval failed: %s", exc)

        # Prefer the fully local Chroma + Ollama lane. It avoids a paid/remote
        # embedding call and searches knowledge plus semantic memory together.
        if local_rag is not None:
            try:
                local_hits = local_rag.search(user_content, top_k=7)
                memory_hits = [h for h in local_hits if h.get("kind") == "memory"]
                knowledge_hits = [h for h in local_hits if h.get("kind") == "knowledge"]
                if auto_memory and memory_hits:
                    local_memory_hit = True
                    rec_n = len(memory_hits[:3])
                    suffix += "\n\nRelevant memory from past chats:\n" + "\n".join(
                        f"- {hit['text']}" for hit in memory_hits[:3]
                    )
                if knowledge_hits:
                    local_knowledge_hit = True
                    kn_n = len(knowledge_hits[:4])
                    blocks = "\n\n".join(
                        f"[{i+1}] (source: {hit.get('relpath', 'local')})\n{hit['text'][:1200]}"
                        for i, hit in enumerate(knowledge_hits[:4])
                    )
                    suffix += (
                        "\n\n---\nLOCAL KNOWLEDGE BASE (the user's own notes; treat as authoritative):\n"
                        + blocks
                        + "\n\nGround the answer in these notes and cite them as [1], [2]."
                    )
            except Exception as exc:  # noqa: BLE001 - legacy RAG remains available
                logger.info("Local grounding unavailable: %s", exc)

        def _collect_omnibrain(text: str) -> Tuple[str, int]:
            """Join the in-flight OmniBrain lookup. Never raises."""
            if ob_future is None:
                return text, 0
            try:
                hits = ob_future.result(timeout=2.0) or []
            except Exception as exc:  # noqa: BLE001 - never block the reply
                logger.info("OmniBrain lookup skipped: %s", exc)
                hits = []
            finally:
                if ob_pool is not None:
                    ob_pool.shutdown(wait=False)
            if not hits:
                return text, 0
            text += (
                "\n\n---\nOMNIBRAIN (the user's own engineering notes, memories and "
                "project docs â€” treat as authoritative and prefer it over your own "
                "assumptions):\n"
                + omnibrain.as_prompt_block(hits)
                + "\n\nIf OMNIBRAIN contradicts what you would otherwise answer, follow "
                "OMNIBRAIN and say so â€” it records hard-won fixes specific to this user's "
                "machine and projects. Cite these as [omnibrain: <file>]."
            )
            return text, len(hits)

        need_legacy_memory = auto_memory and memory_store is not None and not local_memory_hit
        need_legacy_knowledge = knowledge_store is not None and not local_knowledge_hit
        if not need_legacy_memory and not need_legacy_knowledge:
            suffix, ob_n = _collect_omnibrain(suffix)
            return suffix, rec_n, kn_n, ob_n

        vecs = client.embed([user_content])
        q = vecs[0] if vecs else []
        if not q:
            # Local embed unavailable â€” OmniBrain still works (it embeds remotely),
            # so grounding degrades instead of disappearing.
            suffix, ob_n = _collect_omnibrain(suffix)
            return suffix, rec_n, kn_n, ob_n
        if need_legacy_memory:
            try:
                recalled = memory_store.search(q, top_k=3)
                rec_n = len(recalled)
                if recalled:
                    suffix += "\n\nRelevant memory from past chats:\n" + "\n".join(
                        f"- {m['text']}" for m in recalled
                    )
            except Exception as exc:  # noqa: BLE001 - never block the reply
                logger.warning("Memory recall failed: %s", exc)
        if need_legacy_knowledge:
            try:
                hits = knowledge_store.search(q, top_k=4)
                kn_n = len(hits)
                if hits:
                    blocks = "\n\n".join(
                        f"[{i+1}] (source: {h['relpath']})\n{h['text'][:1200]}"
                        for i, h in enumerate(hits)
                    )
                    suffix += (
                        "\n\n---\nKNOWLEDGE BASE (the user's own notes â€” treat as authoritative):\n"
                        + blocks
                        + "\n\nGround your answer in the KNOWLEDGE BASE above and cite sources as [1],[2]. "
                        "If it does not contain the answer, say so plainly ('that isn't in your knowledge base') "
                        "rather than inventing facts, file paths, APIs, or code."
                    )
            except Exception as exc:  # noqa: BLE001 - never block the reply
                logger.warning("Knowledge retrieval failed: %s", exc)
        suffix, ob_n = _collect_omnibrain(suffix)
        return suffix, rec_n, kn_n, ob_n

    if client is not None and user_content and (
        knowledge_store is not None
        or skill_lib is not None
        or (use_omnibrain and omnibrain is not None)
        or (auto_memory and memory_store is not None)
    ):
        try:
            (
                suffix,
                recalled_count,
                knowledge_count,
                omnibrain_count,
            ) = await asyncio.wait_for(
                asyncio.to_thread(_grounding), timeout=2.5
            )
            system_prompt += suffix
        except asyncio.TimeoutError:
            logger.info("Grounding exceeded 2.5s budget; answering ungrounded.")
        except Exception as exc:  # noqa: BLE001 - never stall the reply
            logger.warning("Grounding failed: %s", exc)
    # Design module: DeepSeek is a coding workhorse but a design novice -
    # inject the house design playbook whenever a DeepSeek model is asked
    # to design, so it ships Claude-grade UI instead of generic slop.
    # Regenerates probe the last user turn since request.content is empty.
    _design_probe = user_content or next(
        (m["content"] for m in reversed(history) if m["role"] == "user"), ""
    )
    if model.startswith("deepseek/") and DESIGN_REQUEST_RE.search(_design_probe):
        system_prompt += "\n\n" + DESIGN_SYSTEM_PROMPT

    # LSP-style diagnostics for the active file: give the model the same
    # truth the editor shows (ruff for Python, tsc syntax for TS/JS).
    if request.file_path:
        try:
            _diag_block = diagnostics_block(request.file_path)
            if _diag_block:
                system_prompt += "\n\n" + _diag_block
        except Exception as exc:  # noqa: BLE001 - never stall the reply
            logger.warning("Diagnostics injection failed: %s", exc)

    temperature: float = float(getattr(current_settings, "temperature", 0.7) or 0.7)
    max_tokens: int = int(getattr(current_settings, "max_response_tokens", 4000) or 4000)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for row in history:
        messages.append({"role": row["role"], "content": row["content"]})

    images, _dropped_media = _split_media(request.images)
    if _dropped_media:
        # Silent loss of user input lets the model pretend it heard/saw the
        # attachment. Log it and state the limitation inside the turn.
        logger.warning("chat ingress dropped %d non-image attachment(s): %s",
                       len(_dropped_media), _dropped_media)
        user_content += (
            "\n\n[System note: the user attached "
            f"{len(_dropped_media)} audio/video file(s) that chat cannot "
            "process yet. Do NOT claim to have heard or seen that content - "
            "say so plainly and suggest the qwen-mm media tools.]"
        )
    if mode != "regenerate":
        if images:
            content_parts: List[Dict[str, Any]] = [
                {"type": "text", "text": user_content}
            ] + [{"type": "image_url", "image_url": {"url": url}} for url in images]
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

    # Weak-model visual augmentation: text-only models (DeepSeek, local,
    # MiniMax, ...) cannot see images; Qwen-MM describes them so the model
    # works from real visual context instead of guessing. Only triggers
    # when the resolved model lacks vision; fail-open otherwise.
    # Tool-enabled turns always ground images to Qwen-MM text descriptions:
    # function calling rides on text content (fragile with image parts on some
    # providers), and the mcp__qwen-mm tools stay callable for deeper analysis.
    # Vision-capable models keep the raw image when the tool belt is off.
    tools_wanted: bool = bool(request.tools) or request.assistant
    if images and mode != "regenerate":
        _vis_spec = app.state.router.spec_for_id(model)
        needs_augment = _vis_spec is None or not _vis_spec.supports_vision
        if needs_augment or tools_wanted:
            _aug = vision_assist.augment_content_if_needed(images, user_content)
            if _aug != user_content:
                messages[-1]["content"] = _aug
                logger.info("Vision augmentation applied for %s", model)

    # Auto-compact at context limit (OpenCode parity): when the estimated
    # prompt exceeds ~75% of the model's context window, summarize older
    # turns first so the answer stays in-context. Prompt-level only; the
    # manual POST /chats/{id}/compact endpoint trims the database.
    if mode != "regenerate":
        try:
            _ctx_spec = app.state.router.spec_for_id(model)
            _ctx = _ctx_spec.context_window if _ctx_spec else 0
            _est = sum(len(str(m.get("content", ""))) for m in messages) // 4
            if _ctx and _est > int(_ctx * 0.75) and len(messages) > 12:
                _old = messages[1:-8]
                if len(_old) >= 4:
                    _transcript = "\n".join(
                        f"{m['role']}: {str(m['content'])[:600]}" for m in _old
                    )
                    _summ = _summarize(_transcript)
                    if _summ:
                        messages = [messages[0]] + [
                            {"role": "user", "content": "--- Earlier conversation summary ---\n" + _summ}
                        ] + messages[-8:]
                        logger.info("Auto-compact applied for %s (est %d/%d tokens)", model, _est, _ctx)
        except Exception as exc:  # noqa: BLE001 - never stall the reply
            logger.warning("Auto-compact skipped: %s", exc)

    new_title: Optional[str] = None
    if not history and mode != "regenerate":
        new_title = user_content[:48] + ("â€¦" if len(user_content) > 48 else "")

    # Smart assist: subtle defaults that stay out of the way until they help.
    smart_assist: bool = bool(getattr(current_settings, "smart_assist", True))
    use_web: bool = bool(request.web) or (
        smart_assist and SmartDefaults.suggest_web_search(user_content)
    )

    # Assistant mode always has tools; chat mode opts in via request.tools.
    # Images no longer disable the belt (they are grounded to text above).
    use_tools: bool = tools_wanted
    allow_actions: bool = bool(request.allow_actions)
    registry: Optional[ToolRegistry] = getattr(app.state, "tools", None)

    # Which tools the model may call (empty setting = all enabled).
    allowed = set(current_settings.enabled_tools or TOOL_NAMES)
    # These are the core coding harness. Older settings files predate them, so
    # default them on; the per-chat disabled-tool list below can still narrow
    # them explicitly.
    allowed.update({
        "read_workspace_file",
        "read_workspace_files",
        "list_workspace_dir",
        "find_workspace_files",
        "search_workspace_text",
        "apply_workspace_edit",
        "run_workspace_shell",
        "launch_app",
    })
    # Per-chat opt-outs narrow the global set (never widen it).
    if request.enabled_tools is not None:
        allowed = allowed & set(request.enabled_tools)

    # Smart tool curation: in plain chat, auto-enable a small safe set when the
    # query shape suggests it could help. The model still decides whether to call.
    smart_tool_hint: Optional[List[str]] = None
    if (
        smart_assist
        and not request.assistant
        and not request.tools
        and not has_images
    ):
        smart_tool_hint = SmartDefaults.suggest_tools(user_content, enabled=list(allowed))
        if smart_tool_hint:
            use_tools = True
            allowed = set(smart_tool_hint)

    active_schemas = [
        s for s in TOOL_SCHEMAS
        if s["function"]["name"] in allowed
        and (s["function"]["name"] not in ACTION_TOOL_NAMES or allow_actions)
    ]
    if request.assistant:
        # Add file/system + creative tools; drop gated action tools unless
        # the user has enabled actions (so the model can't even try them).
        for s in ASSISTANT_TOOL_SCHEMAS:
            if s["function"]["name"] in ACTION_TOOL_NAMES and not allow_actions:
                continue
            active_schemas.append(s)
    # MCP tools: available in both chat (tools on) and assistant mode.
    mcp_manager = getattr(app.state, "mcp", None)
    if mcp_manager is not None:
        try:
            active_schemas.extend(mcp_manager.tool_schemas())
        except Exception:  # noqa: BLE001
            pass
    # Assistant mode intentionally reuses a few core schemas. Keep one schema
    # per function name so providers never receive duplicate tool definitions.
    deduped_schemas: List[Dict[str, Any]] = []
    seen_tool_names: set[str] = set()
    for schema in active_schemas:
        schema_name = str(schema.get("function", {}).get("name") or "")
        if not schema_name or schema_name in seen_tool_names:
            continue
        seen_tool_names.add(schema_name)
        deduped_schemas.append(schema)
    active_schemas = deduped_schemas
    # Keep the function list sane for the model (soft cap, built-ins first).
    if len(active_schemas) > 96:
        active_schemas = active_schemas[:96]

    # Tell the model what this turn can actually do. This live contract prevents
    # stale persona prose from causing blanket refusals such as "I cannot open
    # Blender" when launch_app is registered, while avoiding promises for tools
    # that are disabled or unavailable.
    active_tool_names = list(dict.fromkeys(
        str(schema.get("function", {}).get("name") or "")
        for schema in active_schemas
        if schema.get("function", {}).get("name")
    ))
    workspace_state = str(workspace.resolve()) if workspace is not None else "not selected"
    if use_tools and active_tool_names:
        messages[0]["content"] += (
            "\n\nLIVE CAPABILITY CONTRACT FOR THIS TURN:\n"
            f"Available registered tools: {', '.join(active_tool_names)}.\n"
            f"Workspace: {workspace_state}. Actions: {'enabled' if allow_actions else 'disabled'}. "
            "Use a matching tool before giving manual instructions. Do not claim a listed "
            "capability is unavailable. Do not claim capabilities that are not listed."
        )
    else:
        messages[0]["content"] += (
            "\n\nLIVE CAPABILITY CONTRACT FOR THIS TURN: tools are disabled. "
            "If an action is requested, say tools are off rather than claiming permanent inability."
        )

    # Smart context management: silently trim huge histories so long chats don't
    # hit token limits or lose coherence. The DB keeps everything; only the model
    # context is compressed.
    if smart_assist:
        messages, _ = SmartDefaults.trim_history(messages)

    approvals = app.state.approvals
    approval_gates = gates_for_approval_mode(request.approval_mode)
    session_approved: set = set()  # once-per-session risks the user chose to remember

    def event_stream() -> Any:
        parts: List[str] = []
        used_model: str = model
        streamed: bool = False
        tool_log: List[Dict[str, Any]] = []  # persisted so cards survive reload
        extra = {"usage": {"include": True}, "temperature": temperature}
        # First frame goes out immediately so the UI is never staring at a
        # silent connection while the model spins up.
        yield "data: " + json.dumps({"start": True, "model": model}) + "\n\n"

        # --- Tool phase: let the model call tools before the final answer. --- #
        # Cloud tool calls need the OpenRouter client; local-only installs have
        # none (client is None), so they skip the tool phase and answer plain.
        if use_tools and client is not None and registry is not None and active_schemas:
            try:
                rounds = 0
                while rounds < 4:
                    rounds += 1
                    try:
                        msg = client.chat_tools(model, messages, active_schemas, 1500)
                    except OpenRouterError:
                        break  # fall through to a plain streamed answer
                    tool_calls = getattr(msg, "tool_calls", None) or []
                    if not tool_calls:
                        # No tool needed (or done). The final streaming pass writes
                        # the visible answer from whatever tool output is now in
                        # context, so don't append this turn (it would duplicate).
                        break
                    # Record the assistant's tool-call turn verbatim.
                    messages.append(
                        {
                            "role": "assistant",
                            "content": msg.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in tool_calls
                            ],
                        }
                    )
                    for tc in tool_calls:
                        name = tc.function.name
                        try:
                            arguments = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            arguments = {}

                        # --- Per-action approval gate ----------------------- #
                        risk = risk_of(name)
                        needs_gate = risk in approval_gates
                        if (
                            needs_gate
                            and risk in ONCE_PER_SESSION_RISKS
                            and name in session_approved
                        ):
                            needs_gate = False  # already approved-and-remembered

                        if needs_gate:
                            call_id = uuid.uuid4().hex
                            approvals.create(call_id)
                            pending_frame: Dict[str, Any] = {
                                "name": name,
                                "args": arguments,
                                "status": "pending",
                                "call_id": call_id,
                                "risk": risk,
                            }
                            if risk == "paid":
                                pending_frame["cost"] = estimate_cost(name, arguments)
                            yield "data: " + json.dumps({"tool": pending_frame}) + "\n\n"
                            decision = approvals.wait(call_id, timeout=180.0)
                            if decision.get("decision") != "approve":
                                output = "[skipped by user]"
                                yield "data: " + json.dumps(
                                    {"tool": {"name": name, "result": output,
                                              "status": "skipped"}}
                                ) + "\n\n"
                                tool_log.append({"name": name, "args": arguments,
                                                 "result": output, "status": "skipped"})
                                messages.append(
                                    {"role": "tool", "tool_call_id": tc.id,
                                     "content": output}
                                )
                                continue
                            if decision.get("remember") and risk in ONCE_PER_SESSION_RISKS:
                                session_approved.add(name)

                        yield "data: " + json.dumps(
                            {"tool": {"name": name, "args": arguments, "status": "running"}}
                        ) + "\n\n"
                        if name.startswith("mcp__") and mcp_manager is not None:
                            output = mcp_manager.call(name, arguments)
                        else:
                            output = registry.dispatch(name, arguments, allow_actions)
                        # Validate before the LLM sees it: bounded, coerced
                        # to str, control chars stripped (Part 5 reliability).
                        from core.tools_registry import sanitize_tool_output
                        output = sanitize_tool_output(output)
                        yield "data: " + json.dumps(
                            {"tool": {"name": name, "result": output[:600], "status": "done"}}
                        ) + "\n\n"
                        tool_log.append({"name": name, "args": arguments,
                                         "result": output[:600], "status": "done"})
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": output[:6000],
                            }
                        )
            except Exception as exc:  # noqa: BLE001 - never kill the stream silently
                logger.error("Tool phase failed for chat %s: %s", chat_id, exc)
                yield "data: " + json.dumps(
                    {"error": f"Tool phase failed: {exc}"}
                ) + "\n\n"
                return

        # LOCAL model selected: swap in a client pointed at this PC's own
        # server (llama.cpp :8081 or LM Studio :1234) instead of OpenRouter,
        # and do NOT fall back to cloud - the user explicitly asked for local.
        local_spec = LOCAL_CHAT_MODELS.get(model)
        # NB: separate name, not `client` - assigning to `client` anywhere in
        # this generator would make it function-local and UnboundLocalError the
        # cloud path that reads it from the enclosing scope.
        local_client: Optional[OpenRouterClient] = None
        if local_spec is not None:
            try:
                local_client = OpenRouterClient(
                    api_key="local",
                    base_url=local_spec["base_url"],
                    max_retries=1,
                    timeout=300.0,
                )
                candidates = [local_spec["model"]]
                attempts = [(candidates[0], extra)]
            except Exception as exc:  # noqa: BLE001
                yield "data: " + json.dumps({
                    "error": f"Local model unavailable: {exc}. "
                             f"Run {local_spec['launcher']} first."
                }) + "\n\n"
                return

        # Free mode: try zero-cost OpenRouter models first, then fall back to
        # the paid chain only if all free ones fail.
        elif current_settings and getattr(current_settings, "free_mode", False):
            free_chain = [m for m in FREE_CHAT_MODELS if m != model]
            base_candidates = [model] + free_chain + [
                m for m in CHAT_FALLBACK_MODELS if m != model and m not in free_chain
            ]
        else:
            base_candidates = [model] + [
                m for m in CHAT_FALLBACK_MODELS if m != model
            ]
        # Local models already fixed their own single candidate above; the
        # cloud-only logic below (:online variants, reasoning knob, fallback
        # chain) must not overwrite it.
        if local_spec is None:
            # If web search is on, every candidate should try the :online variant
            # first. The previous code only added :online to the primary model,
            # so a fallback would silently lose live-web search.
            candidates = [
                (m + ":online" if use_web else m) for m in base_candidates
            ]
            # Interactive chat wants snappy first tokens: disable thinking mode on
            # hybrid reasoning models (an effort hint ENABLES thinking â€” measured
            # 26s TTFT on qwen3.7-max). Models where reasoning is mandatory reject
            # the knob with a 400, so each candidate retries without it.
            extra_now = dict(extra, reasoning={"enabled": False})
            attempts = [(c, e) for c in candidates for e in (extra_now, extra)]
        think_chars = 0
        chat_session_id = str(uuid.uuid4())
        chat_lane = "chat"
        lane_router = getattr(app.state, "lane_router", None)
        if lane_router is not None:
            chat_lane, _ = lane_router.route(
                task_type="chat", complexity="simple", has_images=has_images
            )
        reasoning_parts: List[str] = []
        stream_error: Optional[Exception] = None
        for candidate, body in attempts:
            try:
                chat_stream_iter = (local_client or client).chat_stream(
                    candidate,
                    messages,
                    # Local reasoning models need a real budget or content comes
                    # back empty (thinking eats the whole allowance).
                    max(max_tokens, 12000) if local_client else max_tokens,
                    body,
                    yield_reasoning=True,
                    session_id=chat_session_id,
                    lane=chat_lane,
                )
                for kind, delta in chat_stream_iter:
                    if kind == "reasoning":
                        # Stream the actual reasoning text so the UI can show a
                        # collapsible thinking panel, plus a cheap heartbeat
                        # (~every 300 chars) so non-reasoning UIs still see activity.
                        reasoning_parts.append(delta)
                        think_chars += len(delta)
                        yield "data: " + json.dumps({"reasoning": delta}) + "\n\n"
                        if think_chars // 300 != (think_chars - len(delta)) // 300:
                            yield "data: " + json.dumps({"thinking": think_chars}) + "\n\n"
                        continue
                    streamed = True
                    parts.append(delta)
                    yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                used_model = candidate.removesuffix(":online")
                # Mid-stream failures don't raise: the client records them on
                # the wrapper so a truncated answer isn't treated as complete.
                stream_error = getattr(chat_stream_iter, "stream_error", None)
                break
            except OpenRouterError:
                if streamed:
                    break
                continue
        # Guard: if the model emitted only reasoning tokens, surface them as the
        # visible answer so the UI never shows a blank assistant bubble. If there
        # is nothing at all, stream a clear fallback before the done frame.
        if not "".join(parts).strip():
            if reasoning_parts:
                fallback_text = "".join(reasoning_parts).strip()
                # Strip common thinking wrappers so the answer reads cleanly.
                fallback_text = fallback_text.removeprefix("<think>").removesuffix("</think>").strip()
            else:
                fallback_text = "(no response)"
            parts.append(fallback_text)
            yield "data: " + json.dumps({"delta": fallback_text}) + "\n\n"
        reply: str = "".join(parts).strip() or "(no response)"
        if stream_error is not None:
            # The stream died mid-answer: mark the persisted reply as truncated
            # and tell the UI, instead of presenting a partial answer as complete.
            logger.warning(
                "Chat stream for %s ended early: %s", chat_id, stream_error
            )
            reply += "\n\n[response interrupted]"
            yield "data: " + json.dumps(
                {"error": f"Response interrupted: {stream_error}"}
            ) + "\n\n"
        usage: Optional[Dict[str, Any]] = getattr(client, "last_stream_usage", None)
        # Account the actual streamed cost against today's budget so chat usage
        # cannot silently exceed the daily cap.
        if cost_tracker is not None and usage and isinstance(usage, dict):
            try:
                cost_usd = float(usage.get("cost") or 0.0)
                if cost_usd > 0 and USD_PER_AUD:
                    cost_tracker.record_actual(cost_usd / USD_PER_AUD)
            except (TypeError, ValueError) as exc:
                logger.warning("Could not record chat cost against budget: %s", exc)
        now: str = datetime.now(timezone.utc).isoformat()
        try:
            with _connect() as connection:
                if mode != "regenerate":
                    user_msg_id = str(uuid.uuid4())
                    stored = user_content + (
                        f"\n\n[{len(images)} image(s) attached]" if images else ""
                    )
                    connection.execute(
                        "INSERT INTO chat_messages (id, chat_id, role, content, created_at) "
                        "VALUES (?, ?, 'user', ?, ?)",
                        (user_msg_id, chat_id, stored, now),
                    )
                    _fts_insert(connection, user_msg_id, chat_id, stored)
                assistant_msg_id = str(uuid.uuid4())
                tools_blob = json.dumps(tool_log) if tool_log else None
                connection.execute(
                    "INSERT INTO chat_messages "
                    "(id, chat_id, role, content, created_at, tools_json) "
                    "VALUES (?, ?, 'assistant', ?, ?, ?)",
                    (assistant_msg_id, chat_id, reply, now, tools_blob),
                )
                _fts_insert(connection, assistant_msg_id, chat_id, reply)
                if new_title:
                    connection.execute(
                        "UPDATE chats SET title = ?, model = ?, updated_at = ? WHERE id = ?",
                        (new_title, used_model, now, chat_id),
                    )
                else:
                    connection.execute(
                        "UPDATE chats SET model = ?, updated_at = ? WHERE id = ?",
                        (used_model, now, chat_id),
                    )
        except sqlite3.Error as exc:
            logger.error("Could not persist streamed chat for %s: %s", chat_id, exc)
        # Store a compact memory of this exchange. Embed the memory text ITSELF
        # (not the earlier question vector) so the stored vector matches the text
        # it is attached to â€” otherwise recall compares against the wrong content.
        if (
            auto_memory
            and memory_store is not None
            and mode != "regenerate"
            and reply
            and reply != "(no response)"
        ):
            try:
                memory_text = f"User: {user_content[:400]} | Assistant: {reply[:400]}"
                mem_vecs: List[List[float]] = []
                if local_rag is not None:
                    try:
                        mem_vecs = local_rag.embedder.embed([memory_text])
                    except Exception:
                        mem_vecs = []
                if not mem_vecs:
                    mem_vecs = client.embed([memory_text])
                if mem_vecs:
                    memory_store.add(memory_text, mem_vecs[0], chat_id)
                    if local_rag is not None:
                        local_rag.sync_memories(memory_store.list_recent(800))
            except Exception:  # noqa: BLE001
                pass
        yield "data: " + json.dumps(
            {
                "done": True,
                "model": used_model,
                "title": new_title,
                "usage": usage,
                "recalled": recalled_count,
                "knowledge": knowledge_count,
                "omnibrain": omnibrain_count,
                "session_id": chat_session_id,
                "lane": chat_lane,
            }
        ) + "\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )




# --- Shared summarizer: cloud first, local Ollama fallback ------------------

def _summarize(transcript: str) -> str:
    """Compress a transcript to 3-6 bullets. Cloud first, local qwen3:8b
    fallback; returns "" when both fail so callers can fail open."""
    summary = ""
    try:
        client = OpenRouterClient(max_retries=1, timeout=120.0)
        summary = client.chat(
            [
                {"role": "system", "content": (
                    "You are a conversation compactor. Condense the transcript "
                    "into 3-6 bullet points preserving decisions, file paths, "
                    "and unresolved tasks. No fluff, no emoji."
                )},
                {"role": "user", "content": transcript},
            ],
            max_tokens=700,
        )
    except Exception as exc:  # noqa: BLE001 - compaction must never break chat
        logger.warning("compact: cloud summarization failed: %s", exc)
        # Local fallback: qwen3:8b via Ollama (free). Keeps compaction
        # working with zero cloud budget.
        try:
            import urllib.request as _urq
            _payload = {
                "model": "qwen3:8b",
                "messages": [
                    {"role": "system", "content": "You are a conversation compactor. Condense the transcript into 3-6 bullet points preserving decisions, file paths, and unresolved tasks. No fluff, no emoji."},
                    {"role": "user", "content": transcript},
                ],
                "stream": False,
                "think": False,
            }
            _req = _urq.Request(
                "http://localhost:11434/api/chat",
                data=json.dumps(_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with _urq.urlopen(_req, timeout=240) as _resp:
                _data = json.loads(_resp.read().decode("utf-8"))
            summary = str(_data["message"]["content"])
        except Exception as exc2:  # noqa: BLE001
            logger.warning("compact: local summarization failed: %s", exc2)
            return ""
    return summary

# --- Auto-compact: summarize older turns, keep the recent tail ---------------

@router.post("/chats/{chat_id}/compact")
def compact_chat(chat_id: str) -> Dict[str, Any]:
    """Summarize older messages via the cheap model and trim history.

    Keeps the last 8 turns verbatim; earlier turns are condensed into one
    summary that becomes the first kept user message. No-op below 10 turns.
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT id, role, content FROM chat_messages WHERE chat_id=? ORDER BY id",
        (chat_id,),
    ).fetchall()
    if len(rows) <= 10:
        return {"ok": False, "reason": "too_short", "count": len(rows)}
    keep_last = 8
    old_rows = rows[:-keep_last]

    transcript = "\n".join(
        f"{r['role']}: {str(r['content'])[:600]}" for r in old_rows[-20:]
    )
    summary = _summarize(transcript)
    if not summary:
        return {"ok": False, "reason": "summary_failed", "count": len(rows)}

    try:
        for r in old_rows:
            conn.execute("DELETE FROM chat_messages WHERE id = ?", (r["id"],))
            conn.execute("DELETE FROM chat_fts WHERE message_id = ?", (r["id"],))
        conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content) VALUES (?, 'user', ?)",
            (chat_id, "--- Earlier conversation summary ---\n" + summary),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    return {"ok": True, "count": len(rows), "summary": summary}
