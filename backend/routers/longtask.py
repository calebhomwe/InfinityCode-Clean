"""Infinity Code API router: longtask.

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
    ArenaRequest, Budget, FeedbackRequest, LongTaskCreateRequest, REFERENCES_DIR, USD_PER_AUD, VariantArena, VisionVerifyRequest,
    WikiGenerateRequest, _CONFIG, _frontend_playbook, _longtask_event_cb, app, collect_repo_context, generate_wiki_pages, logger,
    lt_recall_lessons, queue, verify_ui,
)

router = APIRouter()

@router.post("/longtasks", status_code=202)
def create_longtask(req: LongTaskCreateRequest) -> dict:
    from main import LONGTASK_JOURNAL, LongTaskEngine, _LONGTASK_RUNS, _LongTaskBuilderAdapter, _locked_dna_lines, _longtask_model_chain
    if not Path(req.repo_path).is_dir():
        raise HTTPException(400, "repo_path is not a directory")
    client = getattr(app.state, "client", None) or getattr(app.state, "chat_client", None)
    if client is None:
        raise HTTPException(503, "no LLM client available")
    chain = _longtask_model_chain("longtask_builder")
    moonshot = getattr(app.state, "moonshot", None)
    dashscope = getattr(app.state, "dashscope", None)
    builder = _LongTaskBuilderAdapter(client, chain,
                                      moonshot=moonshot, dashscope=dashscope)
    builder.embed_fn = getattr(client, "embed", None)
    # Reviewer walks its own chain — local FABLE first, so reviews are free.
    review_chain = _longtask_model_chain("longtask_reviewer")
    lt_cfg = _CONFIG.get("longtask", {}) if isinstance(_CONFIG.get("longtask"), dict) else {}
    if lt_cfg.get("free_mode"):
        # Zero cloud spend: both roles on local llama.cpp (:8081). Documented
        # in HANDOVER.md — the server must be up before launching tasks.
        chain = ["local/fable-max-llamacpp"]
        review_chain = ["local/fable-max-llamacpp"]
        builder.model_chain = list(chain)
    reviewer = _LongTaskBuilderAdapter(client, review_chain,
                                       moonshot=moonshot, dashscope=dashscope)
    engine = LongTaskEngine(builder=builder, journal=LONGTASK_JOURNAL,
                            cost_tracker=getattr(app.state, "cost_tracker", None),
                            reviewer=reviewer, event_cb=_longtask_event_cb,
                            references_dir=REFERENCES_DIR,
                            judge=reviewer)
    lessons = _longtask_recall(req.goal, client)
    playbook_text, playbook_name = _frontend_playbook(req.goal)
    if playbook_text:
        lessons = [playbook_text,
                   "For UI work, prioritize distinctive visual identity "
                   "over generic templates."] + lessons
    locked_lines = _locked_dna_lines(req.goal, client)
    if locked_lines:
        lessons = locked_lines + lessons
    # Async launch: pre-create the journal row so the caller gets an id back
    # immediately, then run the engine in a daemon worker thread. The panel
    # polls GET /api/v1/longtasks/{id}; POST .../{id}/cancel stops the run.
    task_id = LONGTASK_JOURNAL.create_task(
        goal=req.goal, repo_path=req.repo_path, model_builder=chain[0],
        model_reviewer=review_chain[0],
        budget={"max_steps": req.max_steps, "max_cost_aud": req.max_cost_aud,
                "max_wall_min": req.max_wall_min}, autonomy=req.autonomy)
    _LONGTASK_RUNS[task_id] = engine
    threading.Thread(target=_longtask_worker, daemon=True,
                     args=(engine, task_id, req, lessons, playbook_name,
                           chain, review_chain, locked_lines)).start()
    return LONGTASK_JOURNAL.get_task(task_id) or \
        {"id": task_id, "task_id": task_id, "status": "running"}


def _longtask_worker(engine, task_id: str, req: LongTaskCreateRequest,
                     lessons: List[str], playbook_name: Optional[str],
                     chain: list, review_chain: list,
                     style_hints: Optional[List[str]] = None) -> None:
    """Run one long task off the request path. The journal row is the
    source of truth the panel polls; a crash still lands as 'error'."""
    from main import LONGTASK_JOURNAL, _LONGTASK_RUNS, _longtask_learn_async
    try:
        result = engine.run(goal=req.goal, repo_path=req.repo_path,
                            budget=Budget(req.max_steps, req.max_cost_aud,
                                          req.max_wall_min),
                            autonomy=req.autonomy, model_builder=chain[0],
                            model_reviewer=review_chain[0], lessons=lessons,
                            task_id=task_id, spec_mode=req.spec_mode,
                            style_hints=style_hints or None)
    except Exception as exc:  # noqa: BLE001
        logger.info("longtask %s worker failed: %s", task_id, exc)
        LONGTASK_JOURNAL.set_status(task_id, "error", f"worker failed: {exc}")
        result = {"task_id": task_id, "status": "error"}
    finally:
        _LONGTASK_RUNS.pop(task_id, None)
    _longtask_learn_async(task_id, getattr(engine, "builder", None))
    if playbook_name:
        try:
            skill = getattr(app.state, "skill", None)
            if skill is not None:
                skill.record_result(playbook_name,
                                    result.get("status") == "completed")
        except Exception as exc:  # noqa: BLE001
            logger.info("longtask playbook record_result skipped: %s", exc)


@router.post("/longtasks/{task_id}/cancel")
def cancel_longtask(task_id: str) -> dict:
    """Cooperative cancel — the engine stops before its next builder turn."""
    from main import LONGTASK_JOURNAL, _LONGTASK_RUNS
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    engine = _LONGTASK_RUNS.get(task_id)
    if engine is None:
        raise HTTPException(409, "task is not running")
    engine.request_cancel()
    return {"task_id": task_id, "status": "cancelling"}


@router.post("/longtasks/{task_id}/approve-plan")
def approve_longtask_plan(task_id: str) -> dict:
    """Release a spec-mode run parked at its plan."""
    from main import LONGTASK_JOURNAL, _LONGTASK_RUNS
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    engine = _LONGTASK_RUNS.get(task_id)
    if engine is None:
        raise HTTPException(409, "task is not running")
    engine.approve_plan()
    return {"task_id": task_id, "status": "approved"}


@router.post("/longtasks/{task_id}/approve-tool")
def approve_longtask_tool(task_id: str) -> dict:
    """Release an ask-mode run parked at a write/run action."""
    from main import LONGTASK_JOURNAL, _LONGTASK_RUNS
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    engine = _LONGTASK_RUNS.get(task_id)
    if engine is None:
        raise HTTPException(409, "task is not running")
    engine.approve_tool()
    return {"task_id": task_id, "status": "tool_approved"}


class RejectToolRequest(BaseModel):
    reason: str = ""


@router.post("/longtasks/{task_id}/reject-tool")
def reject_longtask_tool(task_id: str, req: RejectToolRequest) -> dict:
    """Reject a parked ask-mode action; the loop continues with a denial."""
    from main import LONGTASK_JOURNAL, _LONGTASK_RUNS
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    engine = _LONGTASK_RUNS.get(task_id)
    if engine is None:
        raise HTTPException(409, "task is not running")
    engine.reject_tool(req.reason)
    return {"task_id": task_id, "status": "tool_rejected"}


@router.get("/longtasks/{task_id}/scorepad")
def longtask_scorepad(task_id: str) -> dict:
    """Latest rubric-judge scorepad for a task (P9 evidence)."""
    from main import LONGTASK_JOURNAL
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    steps = LONGTASK_JOURNAL.steps_for(task_id)
    judge_steps = [s for s in steps if s.get("kind") == "judge"]
    if not judge_steps:
        raise HTTPException(404, "no judge scorepad yet")
    result = judge_steps[-1].get("result_json")
    try:
        pad = json.loads(result) if isinstance(result, str) else (result or {})
    except Exception:  # noqa: BLE001
        pad = {}
    return {"task_id": task_id, "scorepad": pad}


@router.get("/training/stats")
def training_stats() -> dict:
    """Flywheel telemetry: how many accepted trajectories are banked."""
    col = getattr(app.state, "training", None)
    if col is None:
        raise HTTPException(503, "training collector not ready")
    return col.stats()


@router.post("/training/export")
def training_export() -> dict:
    """Write the accepted-trajectory dataset to JSONL for a LoRA refresh."""
    col = getattr(app.state, "training", None)
    if col is None:
        raise HTTPException(503, "training collector not ready")
    n = col.export_dataset()
    return {"exported": n, "path": str(col.out_dir / "dataset.jsonl")}


@router.get("/longtasks/{task_id}/artifacts")
def longtask_artifacts(task_id: str) -> list:
    from main import LONGTASK_JOURNAL
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    return LONGTASK_JOURNAL.artifacts_for(task_id)


@router.post("/longtasks/{task_id}/artifacts/{artifact_id}/feedback")
def artifact_feedback(task_id: str, artifact_id: str,
                      req: FeedbackRequest) -> dict:
    """Like/dislike/edit an artifact; a like locks its style DNA."""
    from main import LONGTASK_JOURNAL, _promote_artifact_to_reference
    if req.signal not in ("like", "dislike", "edit"):
        raise HTTPException(400, "signal must be like, dislike, or edit")
    artifact = LONGTASK_JOURNAL.get_artifact(artifact_id)
    if not artifact or artifact.get("task_id") != task_id:
        raise HTTPException(404, "artifact not found")
    fid = LONGTASK_JOURNAL.add_feedback(artifact_id, req.signal, req.note)
    if req.signal == "like":
        _promote_artifact_to_reference(artifact_id)
    return {"id": fid, "signal": req.signal,
            "locked": req.signal == "like"}


@router.get("/references/locked")
def list_locked_references() -> list:
    from main import LONGTASK_JOURNAL
    return LONGTASK_JOURNAL.locked_references()


@router.get("/vision/references")
def vision_references() -> dict:
    """List the locked reference images used for pixel-diff verification."""
    refs = []
    if REFERENCES_DIR.is_dir():
        for f in sorted(REFERENCES_DIR.glob("*")):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                refs.append({"id": f.stem, "name": f.name,
                             "size": f.stat().st_size})
    return {"dir": str(REFERENCES_DIR), "references": refs}


@router.post("/vision/verify")
def vision_verify(req: VisionVerifyRequest) -> dict:
    """Deterministic pixel-diff of a candidate image vs a locked reference."""
    cand = Path(req.candidate)
    if not cand.is_file() and req.repo_path:
        cand = Path(req.repo_path) / req.candidate
    if not cand.is_file():
        raise HTTPException(400, "candidate image not found")
    ref = None
    if REFERENCES_DIR.is_dir():
        for f in REFERENCES_DIR.glob("*"):
            if f.stem == req.reference or f.name == req.reference:
                ref = f
                break
    if ref is None:
        raise HTTPException(404, f"unknown locked reference: {req.reference}")
    try:
        return verify_ui(cand, ref, threshold=req.threshold)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"vision verify failed: {str(exc)[:200]}")


@router.post("/wiki/generate")
def wiki_generate(req: WikiGenerateRequest) -> dict:
    """One LLM pass over the repo tree -> persisted wiki pages."""
    from main import _LongTaskBuilderAdapter, _resolve_chat_model
    repo = Path(req.repo_path)
    if not repo.is_dir():
        raise HTTPException(400, "repo_path is not a directory")
    client = (getattr(app.state, "client", None)
              or getattr(app.state, "chat_client", None))
    if client is None:
        raise HTTPException(503, "no LLM client available")
    adapter = _LongTaskBuilderAdapter(
        client, [_resolve_chat_model(None)],
        moonshot=getattr(app.state, "moonshot", None),
        dashscope=getattr(app.state, "dashscope", None))
    model = _resolve_chat_model(None)
    try:
        pages = generate_wiki_pages(collect_repo_context(repo),
                                    adapter.chat, model=model)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"wiki generation failed: {str(exc)[:200]}")
    store = getattr(app.state, "wiki", None)
    stored = store.upsert_pages(str(repo), pages) if store is not None else 0
    return {"repo": str(repo), "pages": pages, "stored": stored}


@router.get("/wiki")
def wiki_list(repo: str = "") -> dict:
    store = getattr(app.state, "wiki", None)
    if store is None:
        return {"repo": "", "repos": [], "pages": []}
    repos = store.repos()
    if not repo and repos:
        repo = repos[0]
    return {"repo": repo, "repos": repos,
            "pages": store.list_pages(repo) if repo else []}


@router.get("/wiki/page")
def wiki_page(repo: str, path: str) -> dict:
    store = getattr(app.state, "wiki", None)
    page = store.get_page(repo, path) if store is not None else None
    if not page:
        raise HTTPException(404, "page not found")
    return page


@router.get("/longtasks/{task_id}/events")
def longtask_events(task_id: str):
    """SSE stream of engine events (plan/step/review/artifact/done).
    Late subscribers get a snapshot frame first, then live frames."""
    from main import LONGTASK_JOURNAL, _LONGTASK_SUBS, _SSE_HEARTBEAT_S
    if not LONGTASK_JOURNAL.get_task(task_id):
        raise HTTPException(404, "task not found")
    q: queue.Queue = queue.Queue(maxsize=500)
    _LONGTASK_SUBS.setdefault(task_id, []).append(q)

    def gen():
        try:
            task = LONGTASK_JOURNAL.get_task(task_id) or {}
            snap = {"kind": "snapshot",
                    "payload": {"status": task.get("status"),
                                "steps": len(LONGTASK_JOURNAL.steps_for(task_id))}}
            yield "data: " + json.dumps(snap) + "\n\n"
            while True:
                try:
                    frame = q.get(timeout=_SSE_HEARTBEAT_S)
                    yield "data: " + json.dumps(frame, default=str) + "\n\n"
                    if frame.get("kind") == "done":
                        break
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    cur = LONGTASK_JOURNAL.get_task(task_id)
                    if cur and cur.get("status") not in (
                            "running", "awaiting_plan", "pending"):
                        fin = {"kind": "done",
                               "payload": {"status": cur.get("status")}}
                        yield "data: " + json.dumps(fin) + "\n\n"
                        break
        finally:
            subs = _LONGTASK_SUBS.get(task_id)
            if subs is not None and q in subs:
                subs.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Variant Arena - one prompt races coding variants (DaCoder, X-Coder, ...).
# Racers use the default chat model; the judge rides the reviewer chain
# (local FABLE first), so crowning a winner is free.
# ---------------------------------------------------------------------------

@router.post("/arena")
def arena_race(req: ArenaRequest) -> dict:
    from main import _LongTaskBuilderAdapter, _longtask_model_chain, _resolve_chat_model
    client = getattr(app.state, "client", None) or getattr(app.state, "chat_client", None)
    if client is None:
        raise HTTPException(503, "no LLM client available")
    lib = getattr(app.state, "agents", None)
    if lib is None:
        raise HTTPException(503, "agent library unavailable")
    # Blank-prompt guard: arena.run() would ValueError -> 500 otherwise.
    if not req.prompt.strip():
        raise HTTPException(400, "arena prompt must not be blank")
    variants: List[Dict[str, Any]] = []
    for vid in req.variants:
        agent = lib.get(vid)
        if agent is None:
            raise HTTPException(400, f"unknown variant: {vid}")
        variants.append(agent)
    if len(variants) < 2:
        raise HTTPException(400, "an arena needs at least 2 variants")
    moonshot = getattr(app.state, "moonshot", None)
    dashscope = getattr(app.state, "dashscope", None)
    racer = _LongTaskBuilderAdapter(client, [_resolve_chat_model(None)],
                                  moonshot=moonshot, dashscope=dashscope)

    def chat_fn(variant, messages):
        return racer.chat(messages, max_tokens=2000)

    judge_adapter = _LongTaskBuilderAdapter(
        client, _longtask_model_chain("longtask_reviewer"),
        moonshot=moonshot, dashscope=dashscope)

    def judge_fn(prompt, entries):
        listing = "\n\n".join(
            f"[{e['variant_id']}]\n{str(e.get('response') or '')[:1500]}"
            for e in entries)
        reply = judge_adapter.chat([{
            "role": "user",
            "content": ("You are judging a coding-agent race. Pick the best "
                        "response to the prompt. Reply ONLY with JSON: "
                        '{"winner": "<variant_id>", "reason": "..."}.\n\n'
                        f"PROMPT: {prompt}\n\nRESPONSES:\n{listing}")}],
            max_tokens=400)
        text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
        # A malformed verdict raises here on purpose: VariantArena._judge
        # catches it and records "judge failed" instead of crashing.
        verdict = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return {"winner": str(verdict.get("winner", "")),
                "reason": str(verdict.get("reason", ""))[:500]}

    arena = VariantArena(chat_fn=chat_fn, judge_fn=judge_fn)
    result = arena.run(req.prompt, variants)
    cost_aud = result["total_cost_usd"] / USD_PER_AUD if USD_PER_AUD else 0.0
    if cost_aud > 0:
        try:
            getattr(app.state, "cost_tracker", None).record_actual(cost_aud)
        except Exception:  # noqa: BLE001 - metering never kills the race
            pass
    result["cost_aud"] = round(cost_aud, 6)
    return result




def _longtask_recall(goal: str, client) -> List[str]:
    """Cosine-recall past lessons for this goal; bumps the hits' use counters.
    One embed call; any failure degrades to zero lessons, never an error."""
    from main import LONGTASK_JOURNAL
    try:
        embed_fn = getattr(client, "embed", None)
        if embed_fn is None:
            return []
        hits = lt_recall_lessons(goal, embed_fn, LONGTASK_JOURNAL)
        for h in hits:
            LONGTASK_JOURNAL.bump_lesson_use(h["id"])
        return [h["lesson"] for h in hits]
    except Exception as exc:  # noqa: BLE001
        logger.info("longtask lesson recall skipped: %s", exc)
        return []




@router.get("/longtasks")
def list_longtasks() -> list:
    from main import LONGTASK_JOURNAL
    return LONGTASK_JOURNAL.list_tasks()


@router.get("/longtasks/{task_id}")
def get_longtask(task_id: str) -> dict:
    from main import LONGTASK_JOURNAL
    task = LONGTASK_JOURNAL.get_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    task["steps"] = LONGTASK_JOURNAL.steps_for(task_id)
    return task
