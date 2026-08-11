"""Infinity Code API router: system.

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
    BASE_DIR, DATA_DIR, DIAGNOSE_MODEL, EconomicsEngine, LOCAL_CHAT_MODELS, MCPManager, ModelRouter, SelfReview, Settings,
    SmartDefaults, SuggestionEngine, VERIFY_MODEL, VisualReport, _CONFIG, _load_settings, _local_server_alive, app,
    capability_status, diff_reports, eyes_capture_state, eyes_diagnose_state, eyes_verify_state, local_model, logger, provider_chat,
    repo_get_map, repo_search,
)

router = APIRouter()

@router.get("/health")
def health() -> Dict[str, str]:
    """Liveness probe used by the splash + Settings to confirm the backend."""
    return {"status": "ok", "version": app.version}


@router.get("/health/dependencies")
def health_dependencies() -> Dict[str, Any]:
    """Read-only health of every dependency the harness relies on.

    Fast and side-effect-free (the only I/O is a 1.5s-capped localhost ping
    of the optional local model server), so it can be polled by dashboards
    or the readiness checklist without spending tokens.
    """
    try:
        from backend.tools import openrouter_client as _orc
    except ImportError:  # running with backend/ as the working directory
        from tools import openrouter_client as _orc  # type: ignore[no-redef]

    out: Dict[str, Any] = {"status": "ok"}

    # Cloud provider failover routes (from the persisted in-memory health).
    client = getattr(app.state, "client", None)
    if client is not None and getattr(client, "_routes", None):
        out["provider_routes"] = _orc.routes_health_report(client._routes)
    else:
        out["provider_routes"] = []

    # API keys (presence only — never the values).
    out["openrouter_key"] = bool(os.environ.get("OPENROUTER_API_KEY"))

    # MCP plugin servers.
    mcp = getattr(app.state, "mcp", None)
    if mcp is not None:
        out["mcp"] = [
            {
                "name": s["name"],
                "enabled": s["enabled"],
                "connected": s["connected"],
                "tool_count": s["tool_count"],
                "error": s["error"],
            }
            for s in mcp.status()["servers"]
        ]
    else:
        out["mcp"] = []

    # Optional local model server (llama.cpp / LM Studio). local_model()
    # probes with its own 60s-cached timeout; None means nothing listening.
    try:
        _mid = local_model()
        _spec = LOCAL_CHAT_MODELS.get(_mid) if _mid else None
        out["local_model_server"] = bool(
            _spec and _local_server_alive(_spec["base_url"])
        )
    except Exception:  # noqa: BLE001 - health must never raise
        out["local_model_server"] = False

    # Data dir writability (atomic state persistence depends on it).
    try:
        probe = DATA_DIR / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out["data_dir_writable"] = True
    except OSError:
        out["data_dir_writable"] = False

    degraded = (
        not out["provider_routes"]
        or not any(r.get("state") == "live" for r in out["provider_routes"])
    )
    if degraded:
        out["status"] = "degraded"
    return out


@router.get("/debug/state")
def debug_state() -> Dict[str, Any]:
    """Current debug-mode state (Part 5 DX)."""
    root = logging.getLogger()
    return {
        "debug": root.level <= logging.DEBUG,
        "level": logging.getLevelName(root.level),
        "env_debug": os.environ.get("INFINITY_DEBUG") == "1",
    }


@router.post("/debug/verbose")
def debug_verbose(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runtime debug mode: flip root logging between DEBUG (verbose) and
    INFO without a restart. `enabled` omitted -> toggle."""
    root = logging.getLogger()
    current = root.level <= logging.DEBUG
    enabled = bool((body or {}).get("enabled", not current))
    root.setLevel(logging.DEBUG if enabled else logging.INFO)
    logger.info("debug mode %s via API", "ON" if enabled else "OFF")
    return {"debug": enabled, "level": logging.getLevelName(root.level)}


@router.post("/routes/reset")
def routes_reset() -> Dict[str, Any]:
    """Ops: clear persisted route-health (unstick routes marked DEAD
    without waiting out the dead-TTL). Safe: routes re-probe on next call."""
    try:
        from backend.tools import openrouter_client as _orc
    except ImportError:  # running with backend/ as the working directory
        from tools import openrouter_client as _orc  # type: ignore[no-redef]
    return {"cleared": _orc.reset_route_health()}


@router.post("/cache/clear")
def cache_clear() -> Dict[str, Any]:
    """Ops: drop the LLM response cache (e.g. after prompt changes)."""
    try:
        from backend.core import llm_cache as _cache
    except ImportError:  # running with backend/ as the working directory
        from core import llm_cache as _cache  # type: ignore[no-redef]
    return {"cleared": _cache.clear()}


@router.get("/diagnostics")
def diagnostics_route(path: str) -> Dict[str, Any]:
    """LSP-style diagnostics for a single file (ruff / tsc syntax), cached by mtime."""
    try:
        from backend.core.diagnostics import diagnostics_block
    except ImportError:
        from core.diagnostics import diagnostics_block
    return {"path": path, "block": diagnostics_block(path)}




# --- Infinity Code X: Project Context --------------------------------------

@router.get("/context/repo-map")
def repo_map_route(root: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
    """Cached repository map (paths, sizes, symbol outlines) for the council."""
    from main import DATA_DIR
    target = Path(root) if root else BASE_DIR.parent
    return repo_get_map(target, DATA_DIR, force=refresh)


@router.get("/context/search")
def repo_search_route(q: str, root: Optional[str] = None) -> Dict[str, Any]:
    """Keyword search over the repository map (paths and symbols)."""
    from main import DATA_DIR
    target = Path(root) if root else BASE_DIR.parent
    repo_map = repo_get_map(target, DATA_DIR)
    return {"query": q, "hits": repo_search(repo_map, q)}


# --- Infinity Code X: Eyes Module ----------------------------------------

EYES_DIR: Path = DATA_DIR / "eyes"


class EyesCaptureRequest(BaseModel):
    url: str


class EyesDiagnoseRequest(BaseModel):
    visual_report: Dict[str, Any]


class EyesVerifyRequest(BaseModel):
    before: Dict[str, Any]
    after: Dict[str, Any]


def _eyes_chat(model_id: str, messages: List[Dict[str, str]]) -> str:
    """Dispatch one Eyes model call through provider_chat; '' when offline."""
    openrouter = getattr(app.state, "chat_client", None) or getattr(
        app.state, "client", None)
    if openrouter is None:
        return ""
    moonshot = getattr(app.state, "moonshot", None)
    dashscope = getattr(app.state, "dashscope", None)
    result = provider_chat(model_id, messages, 2000,
                           openrouter, moonshot, dashscope)
    return str(result.get("text", ""))


@router.post("/eyes/capture")
def eyes_capture(req: EyesCaptureRequest) -> Dict[str, Any]:
    """Playwright capture -> strict VisualReport JSON + screenshot path."""
    result = eyes_capture_state(req.url, EYES_DIR)
    return {"ok": bool(result["screenshot"]), **result}


@router.post("/eyes/diagnose")
def eyes_diagnose(req: EyesDiagnoseRequest) -> Dict[str, Any]:
    """DeepSeek Flash 1731 turns a VisualReport into a diagnosis + fix."""
    report = VisualReport.model_validate(req.visual_report)
    return {"ok": True,
            **eyes_diagnose_state(report, lambda m: _eyes_chat(DIAGNOSE_MODEL, m))}


@router.post("/eyes/verify")
def eyes_verify(req: EyesVerifyRequest) -> Dict[str, Any]:
    """Qwen 3.8 Max (advisory) verdict on before/after VisualReports."""
    before = VisualReport.model_validate(req.before)
    after = VisualReport.model_validate(req.after)
    diff = diff_reports(before, after)
    verdict = eyes_verify_state(
        before, after, lambda m: _eyes_chat(VERIFY_MODEL, m))
    return {"ok": True, "diff": diff, **verdict}




_STARTUP_TIME = time.time()


@router.get("/health-report")
def health_report() -> Dict[str, Any]:
    """Comprehensive system health snapshot for the HealthReportModal."""
    from main import LONGTASK_JOURNAL
    uptime_s = time.time() - _STARTUP_TIME
    # Schedule stats.
    sched = getattr(app.state, "scheduler", None)
    sched_items = sched.list() if sched else []
    sched_active = sum(1 for s in sched_items if s.get("enabled"))
    # Long-task stats.
    try:
        lt_all = LONGTASK_JOURNAL.list_tasks(limit=500)
        lt_running = sum(1 for t in lt_all if t.get("status") == "running")
        lt_completed = sum(1 for t in lt_all if t.get("status") == "completed")
        lt_total_cost = sum(float(t.get("cost_aud") or 0) for t in lt_all)
    except Exception:  # noqa: BLE001
        lt_running, lt_completed, lt_total_cost = 0, 0, 0.0
    # Wiki stats.
    wiki = getattr(app.state, "wiki", None)
    wiki_repos = len(wiki.repos()) if wiki else 0
    # Memory stats.
    mem = getattr(app.state, "memory", None)
    mem_items = 0
    if mem is not None:
        try:
            mem_items = len(mem.recent(9999))
        except Exception:  # noqa: BLE001
            pass
    return {
        "status": "ok",
        "version": app.version,
        "uptime_s": round(uptime_s),
        "schedules": {"total": len(sched_items), "active": sched_active},
        "longtasks": {"running": lt_running, "completed": lt_completed,
                      "total_cost_aud": round(lt_total_cost, 2)},
        "wiki_repos": wiki_repos,
        "memory_items": mem_items,
    }


_SCHEDULE_TEMPLATES = [
    {"id": "daily-brief", "title": "Daily Brief",
     "prompt": "Summarize recent changes, open tasks, and what to focus on today.",
     "cadence": "1d", "description": "Morning summary of project activity"},
    {"id": "code-review", "title": "Weekly Code Review",
     "prompt": "Review recent code changes for quality, patterns, and potential issues.",
     "cadence": "7d", "description": "Automated weekly code quality pass"},
    {"id": "health-check", "title": "System Health Check",
     "prompt": "Run a system health check and report any anomalies.",
     "cadence": "6h", "description": "Periodic backend + infrastructure check"},
    {"id": "wiki-refresh", "title": "Wiki Refresh",
     "prompt": "Regenerate the knowledge wiki for the current project.",
     "cadence": "7d", "description": "Keep wiki pages up to date"},
    {"id": "lesson-harvest", "title": "Lesson Harvest",
     "prompt": "Extract lessons learned from recent long-task runs and store them.",
     "cadence": "3d", "description": "Capture insights from completed tasks"},
]


@router.get("/schedule-templates")
def schedule_templates() -> list:
    """Pre-built schedule templates the user can one-click create from."""
    return _SCHEDULE_TEMPLATES


# =============================================================================
# Claude History Sync API (read-only from ~/.claude/, writes to Infinity's chats.db)
# =============================================================================

try:
    from backend.core.claude_sync import (
        sync_claude_history,
        get_sync_status,
        get_conversations,
        search_conversations,
        init_chats_db,
        INFINITY_CHATS_DB,
    )
except ImportError:
    from core.claude_sync import (
        sync_claude_history,
        get_sync_status,
        get_conversations,
        search_conversations,
        init_chats_db,
        INFINITY_CHATS_DB,
    )




@router.get("/omnibrain/status")
def omnibrain_status() -> Dict[str, Any]:
    """Is the hosted brain reachable, and how many vectors does it serve?"""
    ob = getattr(app.state, "omnibrain", None)
    if ob is None:
        return {"ok": False, "vectors": 0, "url": None, "enabled": False}
    out = ob.health()
    cfg: Settings = getattr(app.state, "settings", None) or _load_settings()
    out["enabled"] = bool(getattr(cfg, "omnibrain_enabled", False))
    return out


@router.post("/omnibrain/search")
def omnibrain_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Manual lookup against the user's brain (Settings / debugging)."""
    ob = getattr(app.state, "omnibrain", None)
    if ob is None:
        return {"results": []}
    return {
        "results": ob.search(
            str(payload.get("query") or ""),
            top_k=int(payload.get("k") or 6),
            scope=str(payload.get("scope") or "personal"),
            project=payload.get("project"),
            timeout=6.0,
        )
    }


@router.get("/system/info")
def system_info() -> Dict[str, Any]:
    """Version + data-folder + per-DB sizes for the Settings â€º About tab."""
    from main import DATA_DIR
    dbs: Dict[str, int] = {}
    for name in (
        "missions.db",
        "skills.db",
        "memory.db",
        "knowledge.db",
        "ide_workspace.db",
    ):
        p = DATA_DIR / name
        dbs[name] = p.stat().st_size if p.exists() else 0
    know = getattr(app.state, "knowledge", None)
    tester = getattr(app.state, "selftest", None)
    return {
        "version": app.version,
        "data_dir": str(DATA_DIR),
        "db_sizes": dbs,
        "knowledge": know.counts() if know is not None else {"files": 0, "chunks": 0},
        "gaps": tester.gap_count() if tester is not None else 0,
        "local_model": local_model(),
    }


# The authoritative catalog of built-in chat/assistant tools, grouped. This is
# the single source of truth so the UI can't drift from the backend registry.
_TOOL_CATALOG: List[Dict[str, str]] = [
    {"id": "run_python", "label": "Run Python", "cat": "Compute"},
    {"id": "calculator", "label": "Calculator", "cat": "Compute"},
    {"id": "web_search", "label": "Web search", "cat": "Web"},
    {"id": "fetch_url", "label": "Fetch URL", "cat": "Web"},
    {"id": "research", "label": "Research", "cat": "Web"},
    {"id": "review_screen", "label": "Review screen", "cat": "Vision"},
    {"id": "see_image", "label": "See image", "cat": "Vision"},
    {"id": "render_feedback", "label": "Render feedback", "cat": "Vision (paid)"},
    {"id": "critique", "label": "Critique", "cat": "Reasoning"},
    {"id": "list_skills", "label": "List skills", "cat": "Knowledge"},
    {"id": "read_skill", "label": "Read skill", "cat": "Knowledge"},
    {"id": "read_file", "label": "Read file", "cat": "Files"},
    {"id": "list_dir", "label": "List directory", "cat": "Files"},
    {"id": "write_file", "label": "Write file", "cat": "Files (action)"},
    {"id": "read_workspace_file", "label": "Read workspace file", "cat": "Workspace"},
    {"id": "read_workspace_files", "label": "Read workspace files", "cat": "Workspace"},
    {"id": "list_workspace_dir", "label": "List workspace dir", "cat": "Workspace"},
    {"id": "find_workspace_files", "label": "Find workspace files", "cat": "Workspace"},
    {"id": "search_workspace_text", "label": "Search workspace text", "cat": "Workspace"},
    {"id": "apply_workspace_edit", "label": "Apply workspace edit", "cat": "Workspace (action)"},
    {"id": "run_workspace_shell", "label": "Run workspace shell", "cat": "Workspace (action)"},
    {"id": "launch_app", "label": "Launch app", "cat": "Desktop (action)"},
    {"id": "generate_image", "label": "Generate image", "cat": "Create (action)"},
    {"id": "text_to_speech", "label": "Text to speech", "cat": "Create (action)"},
    {"id": "spatial_raycast", "label": "Spatial raycast", "cat": "3D"},
    {"id": "spatial_measure", "label": "Spatial measure", "cat": "3D"},
    {"id": "spatial_camera_frame", "label": "Camera frame", "cat": "3D"},
    {"id": "spatial_collision", "label": "Collision check", "cat": "3D"},
]


@router.get("/tools")
def list_tools() -> Dict[str, Any]:
    """The full, live tool catalog: built-in tools (grouped) + connected MCP
    tools. The UI renders this so it always reflects reality (incl. MCP)."""
    mcp = getattr(app.state, "mcp", None)
    mcp_tools: List[Dict[str, Any]] = []
    if mcp is not None:
        try:
            for srv in mcp.status().get("servers", []):
                if srv.get("connected"):
                    for tname in srv.get("tools", []):
                        mcp_tools.append({"server": srv["name"], "tool": tname})
        except Exception:  # noqa: BLE001
            pass
    return {
        "built_in": _TOOL_CATALOG,
        "mcp": mcp_tools,
        "counts": {
            "built_in": len(_TOOL_CATALOG),
            "mcp": len(mcp_tools),
            "total": len(_TOOL_CATALOG) + len(mcp_tools),
        },
    }




@router.post("/loop/run")
def loop_run(body: Dict[str, Any]) -> StreamingResponse:
    """Run the quality-gated Code loop and stream its step trail as SSE.
    plan -> generate -> gate -> retry/escalate, grounded + anti-hallucination."""
    engine = getattr(app.state, "loop", None)
    goal = str(body.get("goal") or "").strip()
    if engine is None or not goal:
        raise HTTPException(status_code=422, detail="goal required (and loop engine ready).")
    job_type = str(body.get("job_type") or "code")
    max_iter = max(1, min(int(body.get("max_iter", 5)), 10))

    def gen() -> Any:
        try:
            for event in engine.run(goal, job_type=job_type, max_iter=max_iter):
                yield "data: " + json.dumps(event) + "\n\n"
        except Exception as exc:  # noqa: BLE001
            yield "data: " + json.dumps({"type": "done", "ok": False, "error": str(exc)[:200]}) + "\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------- #
# Scheduled tasks
# ---------------------------------------------------------------------- #




@router.get("/schedules")
def list_schedules() -> Dict[str, Any]:
    s = getattr(app.state, "scheduler", None)
    return {"schedules": s.list() if s is not None else []}


@router.post("/schedules")
def create_schedule(body: Dict[str, Any]) -> Dict[str, Any]:
    s = getattr(app.state, "scheduler", None)
    if s is None:
        raise HTTPException(status_code=503, detail="Scheduler unavailable.")
    title = str(body.get("title") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    cadence = str(body.get("cadence") or "1d").strip()
    if not title or not prompt:
        raise HTTPException(status_code=422, detail="title and prompt required.")
    try:
        return s.create(title, prompt, cadence, str(body.get("mode") or "chat"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/schedules/{sid}")
def delete_schedule(sid: str) -> Dict[str, str]:
    s = getattr(app.state, "scheduler", None)
    if s is not None:
        s.delete(sid)
    return {"status": "ok"}


@router.post("/schedules/{sid}/toggle")
def toggle_schedule(sid: str, body: Dict[str, Any]) -> Dict[str, str]:
    s = getattr(app.state, "scheduler", None)
    if s is not None:
        s.toggle(sid, bool(body.get("enabled", True)))
    return {"status": "ok"}


@router.post("/schedules/{sid}/run-now")
async def run_schedule_now(sid: str) -> Dict[str, Any]:
    s = getattr(app.state, "scheduler", None)
    if s is None:
        raise HTTPException(status_code=503, detail="Scheduler unavailable.")
    try:
        # run_now does a full blocking LLM round-trip â€” keep it off the event loop.
        return await asyncio.to_thread(s.run_now, sid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No such schedule.") from exc


@router.get("/self-improve/status")
def self_improve_status() -> Dict[str, Any]:
    """Return current self-improvement governance state."""
    constitution = getattr(app.state, "constitution", None)
    auto_fix = getattr(app.state, "auto_fix", None)
    evolve = getattr(app.state, "evolve", None)
    cfg = _CONFIG.get("self_improve", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled")),
        "auto_approve": bool(cfg.get("auto_approve", False)),
        "constitution_loaded": constitution is not None,
        "daily_patch_limit": getattr(constitution, "daily_patch_limit", None) if constitution else None,
        "daily_budget_aud": getattr(constitution, "daily_budget_aud", None) if constitution else None,
        "auto_fix_ready": auto_fix is not None,
        "evolve_ready": evolve is not None,
    }


@router.get("/self-improve/patches")
def self_improve_patches() -> List[Dict[str, Any]]:
    """List pending patches waiting for human review."""
    from main import DATA_DIR
    patches_dir = DATA_DIR / "patches"
    if not patches_dir.exists():
        return []
    patches: List[Dict[str, Any]] = []
    for p in sorted(patches_dir.glob("*.diff"), key=lambda x: x.stat().st_mtime, reverse=True):
        patches.append({
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "modified_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
            "path": str(p.resolve()),
        })
    return patches


@router.get("/self-improve/evolution")
def self_improve_evolution(limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent evolution log entries (newest first)."""
    from main import DATA_DIR
    log_path = DATA_DIR / "evolution_log.jsonl"
    if not log_path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(entries[-limit:]))


@router.get("/swarm/council")
def swarm_council() -> Dict[str, Any]:
    """The council that actually runs a mission: role -> model degradation chain.

    Distinct from /api/v1/agents, which serves the 245 chat *personas*. These
    eight roles are the ones a mission walks, and the UI needs the real binding
    rather than a hand-maintained copy that can drift from the router.
    """
    router: ModelRouter = app.state.router
    roles: List[Dict[str, Any]] = []
    for role in router.council:
        try:
            chain = router.chain_for(role)
        except Exception as exc:  # noqa: BLE001 - a bad role must not 500 the page
            logger.warning("Could not resolve chain for role %s: %s", role, exc)
            continue
        roles.append(
            {
                "role": role,
                "primary": chain[0].id if chain else None,
                "chain": [spec.id for spec in chain],
                "depth": len(chain),
            }
        )
    validation = router.validate()
    # Local brains: which on-box servers are answering right now, so the UI
    # can badge "35B Local â—" vs "cloud-only" without guessing.
    local_servers: List[Dict[str, Any]] = []
    for name, spec in LOCAL_CHAT_MODELS.items():
        local_servers.append(
            {
                "id": name,
                "base_url": spec["base_url"],
                "launcher": spec.get("launcher", ""),
                "online": _local_server_alive(spec["base_url"]),
            }
        )
    return {
        "roles": roles,
        "count": len(roles),
        "problems": validation.get("problems", []),
        "local_servers": local_servers,
    }


@router.get("/agents")
def list_agents() -> Dict[str, Any]:
    """The Agent Library: 245 specialist personas grouped by division (no bodies)."""
    lib = getattr(app.state, "agents", None)
    if lib is None:
        return {"divisions": {}, "count": 0, "agents": []}
    return lib.list()


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> Dict[str, Any]:
    """Full agent incl. the system-prompt body (used to preview / activate)."""
    lib = getattr(app.state, "agents", None)
    agent = lib.get(agent_id) if lib is not None else None
    if agent is None:
        raise HTTPException(status_code=404, detail="No such agent.")
    return agent


@router.post("/approvals/{call_id}")
def resolve_approval(call_id: str, body: Dict[str, Any]) -> Dict[str, str]:
    """Approve or skip a pending tool call (per-action gate in the assistant loop)."""
    registry = getattr(app.state, "approvals", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Approvals unavailable.")
    decision = str(body.get("decision") or "skip").lower()
    if decision not in ("approve", "skip"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'skip'.")
    ok = registry.resolve(
        call_id,
        {"decision": decision, "remember": bool(body.get("remember"))},
    )
    if not ok:
        raise HTTPException(status_code=404, detail="No such pending approval (expired?).")
    return {"status": "ok"}


# ---------------------------------------------------------------------- #
# DB helpers
# ---------------------------------------------------------------------- #




@router.post("/smart/suggest")
def smart_suggest(body: Dict[str, Any]) -> Dict[str, Any]:
    """Given a goal or chat message, return the smart defaults the app would use."""
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required.")
    classification = SmartDefaults.classify_goal(text)
    tools = SmartDefaults.suggest_tools(text)
    web = SmartDefaults.suggest_web_search(text)
    model = SmartDefaults.pick_chat_model(text)
    return {
        "text": text,
        "mode": classification["mode"],
        "effort": classification["effort"],
        "lane": classification["lane"],
        "tools": tools,
        "web_search": web,
        "chat_model": model,
    }


# ---------------------------------------------------------------------- #
# Routes: MCP servers (tool-calling superpower)
# ---------------------------------------------------------------------- #


@router.get("/mcp/status")
def mcp_status() -> Dict[str, Any]:
    manager: Optional[MCPManager] = getattr(app.state, "mcp", None)
    if manager is None:
        return {"servers": []}
    return manager.status()


@router.get("/capabilities")
def capability_packs() -> Dict[str, Any]:
    """Status for optional capability packs; no secrets are exposed."""
    return capability_status(getattr(app.state, "mcp", None))


@router.get("/mcp/servers")
def mcp_servers() -> Dict[str, Any]:
    manager: Optional[MCPManager] = getattr(app.state, "mcp", None)
    if manager is None:
        return {"servers": {}}
    return manager.config_public()


@router.put("/mcp/servers")
def put_mcp_servers(body: Dict[str, Any]) -> Dict[str, Any]:
    manager: Optional[MCPManager] = getattr(app.state, "mcp", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP unavailable.")
    servers = body.get("servers", body)
    if not isinstance(servers, dict):
        raise HTTPException(status_code=422, detail="Expected a servers map.")
    manager.save_config(servers)
    manager.reconnect()
    return manager.status()


@router.post("/mcp/reconnect")
def mcp_reconnect() -> Dict[str, Any]:
    manager: Optional[MCPManager] = getattr(app.state, "mcp", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP unavailable.")
    manager.reconnect()
    return manager.status()


@router.post("/mcp/import")
def mcp_import() -> Dict[str, Any]:
    """Import server definitions from the user's global ~/.mcp.json."""
    manager: Optional[MCPManager] = getattr(app.state, "mcp", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP unavailable.")
    src = Path.home() / ".mcp.json"
    if not src.is_file():
        raise HTTPException(status_code=404, detail="No ~/.mcp.json found.")
    try:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
        servers = data.get("mcpServers", data)
        count = manager.import_servers(servers if isinstance(servers, dict) else {})
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}") from exc
    return {"imported": count, **manager.config_public()}


# ---------------------------------------------------------------------- #
# Routes: war-mode intelligence (market, suggestions, self-review)
# ---------------------------------------------------------------------- #


@router.get("/market")
def market_report() -> Dict[str, Any]:
    engine: Optional[EconomicsEngine] = getattr(app.state, "economics", None)
    if engine is None:
        raise HTTPException(
            status_code=503, detail="Market unavailable: no LLM provider key is configured - open Settings > Providers."
        )
    report: Dict[str, Any] = engine.market_report()
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return report


@router.get("/suggestions")
def mission_suggestions(n: int = 3) -> Dict[str, Any]:
    engine: Optional[SuggestionEngine] = getattr(app.state, "suggestions", None)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Suggestions unavailable: no LLM provider key is configured - open Settings > Providers.",
        )
    return {"suggestions": engine.suggest(max(1, min(6, n)))}


@router.get("/self-review")
def self_review() -> Dict[str, Any]:
    reviewer: Optional[SelfReview] = getattr(app.state, "self_review", None)
    if reviewer is None:
        return {"metrics": {}, "proposals": []}
    return {"metrics": reviewer.analyze(), "proposals": reviewer.proposals()}



# ==== ROUTER REGISTRATION (managed by extract.py) ====

# Router registration. Routers are imported at the BOTTOM of main.py,
# after every module-level helper/constant/model is defined, so their
# top-level `from main import ...` lines always resolve (no cycles).
# Tests import main both as `import main` and `from backend import main`;
# alias the sys.modules keys so both resolve to THIS module object.
import sys as _sys
_sys.modules.setdefault("backend.main", _sys.modules[__name__])
_sys.modules.setdefault("main", _sys.modules[__name__])

try:
    from backend.routers.ascension import router as ascension_router
except ImportError:  # running with backend/ as the working directory
    from routers.ascension import router as ascension_router  # type: ignore[no-redef]
app.include_router(ascension_router, prefix="/api/v1", tags=['ascension'])
try:
    from backend.routers.integrations import router as integrations_router
except ImportError:  # running with backend/ as the working directory
    from routers.integrations import router as integrations_router  # type: ignore[no-redef]
app.include_router(integrations_router, prefix="/api/v1", tags=['integrations'])
try:
    from backend.routers.missions import router as missions_router
except ImportError:  # running with backend/ as the working directory
    from routers.missions import router as missions_router  # type: ignore[no-redef]
app.include_router(missions_router, prefix="", tags=['missions'])
try:
    from backend.routers.longtask import router as longtask_router
except ImportError:  # running with backend/ as the working directory
    from routers.longtask import router as longtask_router  # type: ignore[no-redef]
app.include_router(longtask_router, prefix="/api/v1", tags=['longtask'])
try:
    from backend.routers.knowledge import router as knowledge_router
except ImportError:  # running with backend/ as the working directory
    from routers.knowledge import router as knowledge_router  # type: ignore[no-redef]
app.include_router(knowledge_router, prefix="/api/v1", tags=['knowledge'])
try:
    from backend.routers.learning import router as learning_router
except ImportError:  # running with backend/ as the working directory
    from routers.learning import router as learning_router  # type: ignore[no-redef]
app.include_router(learning_router, prefix="/api/v1", tags=['learning'])
try:
    from backend.routers.chats import router as chats_router
except ImportError:  # running with backend/ as the working directory
    from routers.chats import router as chats_router  # type: ignore[no-redef]
app.include_router(chats_router, prefix="/api/v1", tags=['chats'])
try:
    from backend.routers.settings import router as settings_router
except ImportError:  # running with backend/ as the working directory
    from routers.settings import router as settings_router  # type: ignore[no-redef]
app.include_router(settings_router, prefix="/api/v1", tags=['settings'])
try:
    from backend.routers.workspace import router as workspace_router
except ImportError:  # running with backend/ as the working directory
    from routers.workspace import router as workspace_router  # type: ignore[no-redef]
app.include_router(workspace_router, prefix="/api/v1", tags=['workspace'])
try:
    from backend.routers.eval import router as eval_router
except ImportError:  # running with backend/ as the working directory
    from routers.eval import router as eval_router  # type: ignore[no-redef]
app.include_router(eval_router, prefix="/api/v1", tags=['eval'])
