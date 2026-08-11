"""Infinity Code FastAPI backend.

All routes live here. Core engines are instantiated once in the lifespan
context and shared via app.state. Missing OPENROUTER_API_KEY degrades
gracefully: the API still serves, but mission runs and critiques report the
missing key instead of pretending to work.

Run with:  cd backend && uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import contextvars
import difflib
import json
import logging
import os
import queue
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

try:
    from backend.core.cost_tracker import CostTracker
    from backend.core.critic_engine import CriticEngine, CritiqueResult
    from backend.core.learn_engine import LearnEngine, LearnEngineError
    from backend.core.router import ModelRouter, build_council, USD_PER_AUD, resolve_mode
    from backend.core.composition import SkillComposer
    from backend.core.economics import EconomicsEngine
    from backend.core.events import MissionEventBus
    from backend.core.self_review import SelfReview
    from backend.core.skill_engine import SkillEngine, SkillEngineError
    from backend.core.suggestions import SuggestionEngine
    from backend.core.swarm import AgentSwarm, SwarmError, init_database
    from backend.core.tools_registry import (
        ToolRegistry,
        TOOL_SCHEMAS,
        TOOL_NAMES,
        ASSISTANT_TOOL_SCHEMAS,
        ACTION_TOOL_NAMES,
        GATED_RISKS,
        ONCE_PER_SESSION_RISKS,
        gates_for_approval_mode,
        risk_of,
    )
    from backend.core.providers import ProviderManager, _looks_masked, estimate_cost
    from backend.core.mcp_client import MCPManager
    from backend.core.capability_packs import seed_mcp_starters, status as capability_status
    from backend.core.memory import MemoryStore
    from backend.core.agent_library import AgentLibrary
    from backend.core.knowledge import KnowledgeStore
    from backend.core.ide_workspace import (
        IDEWorkspaceError,
        IDEWorkspaceStore,
        RevisionConflict,
    )
    from backend.core.local_rag import LocalRAG, LocalRAGUnavailable
    from backend.core.omnibrain import OmniBrainClient
    from backend.core.selftest import SelfTester, local_model
    from backend.core.loop_engine import LoopEngine
    from backend.core.longtask import Budget, LongTaskEngine, LongTaskJournal
    from backend.core.arena import VariantArena
    from backend.core.provider_chat import provider_chat
    from backend.core.longtask.learning import (
        extract_lessons as lt_extract_lessons,
        frontend_related as lt_frontend_related,
        maybe_distill_playbook as lt_maybe_distill_playbook,
        recall_lessons as lt_recall_lessons,
    )
    from backend.core.self_training import SelfTrainer
    from backend.core.wiki_gen import (WikiStore, collect_repo_context,
                                         generate_wiki_pages)
    from backend.core.longtask.vision import (verify_ui, pixel_diff,
                                               capture_screenshot, scan_fake)
    from backend.core.training import TrajectoryCollector
    from backend.core.session_logger import SessionLogger, VALID_SIGNALS
    from backend.core.verifier import Verifier
    from backend.core.dataset_builder import DatasetBuilder
    from backend.core.fine_tuner import FineTuner
    from backend.core.retrain_loop import RetrainLoop
    from backend.core.smart_defaults import SmartDefaults
    from backend.tools.moonshot_client import MoonshotClient
    from backend.tools.dashscope_client import DashScopeClient
    from backend.core.scheduler import Scheduler
    from backend.core.constitution import Constitution
    from backend.core.auto_fix import AutoFix
    from backend.core.evolve import Evolve
    from backend.core.router import LaneRouter, build_council, resolve_mode
    from backend.core.ascension import (
        AscensionEngine,
        SpeedInputs,
        EffortInputs,
        create_engine,
        speed_score,
        effort_score,
    )
    from backend.core.owner import maybe_owner_line, owner_line
    from backend.core.eyes import (
        VisualReport,
        capture as eyes_capture_state,
        diagnose as eyes_diagnose_state,
        verify as eyes_verify_state,
        diff_reports,
        DIAGNOSE_MODEL,
        VERIFY_MODEL,
    )
    from backend.core.gauntlet import (
        latest_reports as gauntlet_latest_reports,
        status as gauntlet_status,
        post_gap as gauntlet_post_gap,
    )
    from backend.core.repo_context import (
        get_repo_map as repo_get_map,
        search_repo_map as repo_search,
    )
    from backend.tools.openrouter_client import OpenRouterClient, OpenRouterError
except ImportError:  # running with backend/ as the working directory
    from core.cost_tracker import CostTracker  # type: ignore[no-redef]
    from core.critic_engine import CriticEngine, CritiqueResult  # type: ignore[no-redef]
    from core.learn_engine import LearnEngine, LearnEngineError  # type: ignore[no-redef]
    from core.router import ModelRouter, build_council, USD_PER_AUD, resolve_mode  # type: ignore[no-redef]
    from core.composition import SkillComposer  # type: ignore[no-redef]
    from core.economics import EconomicsEngine  # type: ignore[no-redef]
    from core.events import MissionEventBus  # type: ignore[no-redef]
    from core.self_review import SelfReview  # type: ignore[no-redef]
    from core.skill_engine import SkillEngine, SkillEngineError  # type: ignore[no-redef]
    from core.suggestions import SuggestionEngine  # type: ignore[no-redef]
    from core.swarm import AgentSwarm, SwarmError, init_database  # type: ignore[no-redef]
    from core.tools_registry import (  # type: ignore[no-redef]
        ToolRegistry,
        TOOL_SCHEMAS,
        TOOL_NAMES,
        ASSISTANT_TOOL_SCHEMAS,
        ACTION_TOOL_NAMES,
        GATED_RISKS,
        ONCE_PER_SESSION_RISKS,
        gates_for_approval_mode,
        risk_of,
    )
    from core.providers import ProviderManager, _looks_masked, estimate_cost  # type: ignore[no-redef]
    from core.mcp_client import MCPManager  # type: ignore[no-redef]
    from core.capability_packs import seed_mcp_starters, status as capability_status  # type: ignore[no-redef]
    from core.memory import MemoryStore  # type: ignore[no-redef]
    from core.agent_library import AgentLibrary  # type: ignore[no-redef]
    from core.knowledge import KnowledgeStore  # type: ignore[no-redef]
    from core.ide_workspace import (  # type: ignore[no-redef]
        IDEWorkspaceError,
        IDEWorkspaceStore,
        RevisionConflict,
    )
    from core.local_rag import LocalRAG, LocalRAGUnavailable  # type: ignore[no-redef]
    from core.omnibrain import OmniBrainClient  # type: ignore[no-redef]
    from core.selftest import SelfTester, local_model  # type: ignore[no-redef]
    from core.loop_engine import LoopEngine  # type: ignore[no-redef]
    from core.longtask import Budget, LongTaskEngine, LongTaskJournal  # type: ignore[no-redef]
    from core.arena import VariantArena  # type: ignore[no-redef]
    from core.provider_chat import provider_chat  # type: ignore[no-redef]
    from core.longtask.learning import (  # type: ignore[no-redef]
        extract_lessons as lt_extract_lessons,
        frontend_related as lt_frontend_related,
        maybe_distill_playbook as lt_maybe_distill_playbook,
        recall_lessons as lt_recall_lessons,
    )
    from core.self_training import SelfTrainer  # type: ignore[no-redef]
    from core.wiki_gen import (WikiStore, collect_repo_context,  # type: ignore[no-redef]
                                generate_wiki_pages)
    from core.longtask.vision import (verify_ui, pixel_diff,  # type: ignore[no-redef]
                                       capture_screenshot, scan_fake)
    from core.training import TrajectoryCollector  # type: ignore[no-redef]
    from core.session_logger import SessionLogger, VALID_SIGNALS  # type: ignore[no-redef]
    from core.verifier import Verifier  # type: ignore[no-redef]
    from core.dataset_builder import DatasetBuilder  # type: ignore[no-redef]
    from core.fine_tuner import FineTuner  # type: ignore[no-redef]
    from core.retrain_loop import RetrainLoop  # type: ignore[no-redef]
    from core.smart_defaults import SmartDefaults  # type: ignore[no-redef]
    from tools.moonshot_client import MoonshotClient  # type: ignore[no-redef]
    from tools.dashscope_client import DashScopeClient  # type: ignore[no-redef]
    from core.scheduler import Scheduler  # type: ignore[no-redef]
    from core.constitution import Constitution  # type: ignore[no-redef]
    from core.auto_fix import AutoFix  # type: ignore[no-redef]
    from core.evolve import Evolve  # type: ignore[no-redef]
    from core.router import LaneRouter, build_council, resolve_mode  # type: ignore[no-redef]
    from core.ascension import (  # type: ignore[no-redef]
        AscensionEngine,
        SpeedInputs,
        EffortInputs,
        create_engine,
        speed_score,
        effort_score,
    )
    from core.owner import maybe_owner_line, owner_line  # type: ignore[no-redef]
    from core.eyes import (  # type: ignore[no-redef]
        VisualReport,
        capture as eyes_capture_state,
        diagnose as eyes_diagnose_state,
        verify as eyes_verify_state,
        diff_reports,
        DIAGNOSE_MODEL,
        VERIFY_MODEL,
    )
    from core.gauntlet import (  # type: ignore[no-redef]
        latest_reports as gauntlet_latest_reports,
        status as gauntlet_status,
        post_gap as gauntlet_post_gap,
    )
    from core.repo_context import (  # type: ignore[no-redef]
        get_repo_map as repo_get_map,
        search_repo_map as repo_search,
    )
    from tools.openrouter_client import OpenRouterClient, OpenRouterError  # type: ignore[no-redef]

# Trace IDs: one per request, propagated to every log line via a Filter
# so concurrent chats can be untangled without grep gymnastics.
_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default="-"
)

class _TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace = _TRACE_ID.get()
        return True

# Debug mode (Part 5 DX): INFINITY_DEBUG=1 boots with verbose DEBUG logs;
# flip at runtime without a restart via POST /api/v1/debug/verbose.
logging.basicConfig(
    level=(logging.DEBUG if os.environ.get("INFINITY_DEBUG") == "1"
           else logging.INFO),
    format="%(asctime)s [%(trace)s] %(name)s %(levelname)s %(message)s",
)
for _h in logging.getLogger().handlers:
    _h.addFilter(_TraceFilter())
logger = logging.getLogger("infinity.main")

BASE_DIR: Path = Path(__file__).resolve().parent
CONFIG_PATH: Path = BASE_DIR / "config.yaml"

# Dev servers plus the Tauri production webview origins (WebView2 on Windows
# serves the bundled app from https://tauri.localhost, macOS/Linux use
# tauri://localhost) ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â without these, the installed app's fetches are
# CORS-blocked and the splash screen never clears.
ALLOWED_ORIGINS: List[str] = [
    "http://localhost:1420",
    "http://localhost:4173",  # vite preview (production build verification)
    "http://localhost:4174",  # local visual QA preview
    "http://localhost:4175",  # alternate local visual QA preview
    "http://127.0.0.1:4173",
    "http://127.0.0.1:4174",
    "http://127.0.0.1:4175",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "tauri://localhost",
]
WS_PUSH_INTERVAL_SECONDS: float = 2.0


def _load_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.is_file():
            loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
    except (yaml.YAMLError, OSError) as exc:
        logger.error("Could not parse %s: %s", CONFIG_PATH, exc)
    return {}


_CONFIG: Dict[str, Any] = _load_config()
_PATHS: Dict[str, Any] = _CONFIG.get("paths", {}) if isinstance(_CONFIG.get("paths"), dict) else {}

# Routing mode: "default" keeps the Qwen/DashScope-first council; "bfb"
# (Bang For Buck) swaps coding roles onto DeepSeek V4 (config.yaml routing.mode).
_ROUTING_CFG: Dict[str, Any] = (
    _CONFIG.get("routing", {}) if isinstance(_CONFIG.get("routing"), dict) else {}
)
ROUTING_MODE: str = resolve_mode(str(_ROUTING_CFG.get("mode", "default")))

try:
    from backend.core.credits import CreditEngine, configure as configure_credits
except ImportError:  # running with backend/ as the working directory
    from core.credits import CreditEngine, configure as configure_credits  # type: ignore[no-redef]

# Frozen builds (PyInstaller) run from a temp extraction dir, so all mutable
# state must live somewhere stable ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the Tauri shell sets INFINITY_DATA_DIR.
DATA_DIR: Path = Path(os.environ.get("INFINITY_DATA_DIR", str(BASE_DIR))).resolve()

# Infinity Code X ascension engine: form state machine + owner override.
_ASCENSION_CFG: Dict[str, Any] = (
    _CONFIG.get("ascension", {}) if isinstance(_CONFIG.get("ascension"), dict) else {}
)
ASCENSION_ENGINE: AscensionEngine = create_engine(
    _ASCENSION_CFG, log_path=str(DATA_DIR / "ascension_log.jsonl")
)

# Owner identity (config overridable): Munesu Homwe Ã¢â‚¬â€ Mr X.
_OWNER_CFG: Dict[str, Any] = (
    _CONFIG.get("owner", {}) if isinstance(_CONFIG.get("owner"), dict) else {}
)
OWNER_NAME: str = str(_OWNER_CFG.get("name", "Munesu Homwe"))
OWNER_ALIAS: str = str(_OWNER_CFG.get("alias", "Mr X"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH: Path = DATA_DIR / "missions.db"
LONGTASK_DB_PATH: Path = DATA_DIR / "longtasks.db"
LONGTASK_JOURNAL = LongTaskJournal(LONGTASK_DB_PATH)
# Engines of currently running tasks Ã¢â‚¬â€ the cancel endpoint looks them up.
_LONGTASK_RUNS: Dict[str, Any] = {}
# SSE subscribers per task id (event bus fed by the engine's event_cb).
_LONGTASK_SUBS: Dict[str, List[queue.Queue]] = {}
# Heartbeat interval for SSE streams (monkeypatched tight in tests).
_SSE_HEARTBEAT_S: float = 15.0


def _longtask_event_cb(task_id: str, kind: str, payload: Dict) -> None:
    """Fan engine events out to every SSE subscriber of this task."""
    frame = {"task_id": task_id, "kind": kind, "payload": payload}
    for q in list(_LONGTASK_SUBS.get(task_id, ())):
        try:
            q.put_nowait(frame)
        except Exception:  # noqa: BLE001 - a slow client never stalls the run
            pass
SKILLS_DB_PATH: Path = DATA_DIR / "skills.db"
SKILLS_DIR: Path = (DATA_DIR / str(_PATHS.get("skills", "./skills"))).resolve()
OUTPUTS_DIR: Path = (DATA_DIR / str(_PATHS.get("outputs", "./outputs"))).resolve()
# Where the Executive Assistant writes files + generated media (user-visible).
# Keep this inside DATA_DIR so frozen installs and uninstalls are clean.
ASSISTANT_OUTPUT_DIR: Path = DATA_DIR / "assistant"
REFERENCES_DIR: Path = (DATA_DIR / str(_PATHS.get("references", "./references"))).resolve()
UPLOADS_DIR: Path = (DATA_DIR / "uploads").resolve()

DAILY_BUDGET_AUD: float = float(
    (_CONFIG.get("costs", {}) or {}).get("daily_budget_aud", 100.0)
)

SETTINGS_PATH: Path = DATA_DIR / "settings.json"
WORKSPACE_PATH: Path = DATA_DIR / "workspace.json"
_VALID_MODES: frozenset[str] = frozenset({"auto", "code", "image", "video", "3d"})
_VALID_EFFORTS: frozenset[str] = frozenset(
    {"low", "med", "high", "xhigh", "max", "ultracode", "vibe"}
)

# The composer contract (src/lib/composer.ts) pins these exact string values.
# Unknown values fall back to the default instead of 422-ing the request.
VALID_MODES: frozenset[str] = frozenset({"auto", "code", "image", "video", "3d"})
VALID_EFFORTS: frozenset[str] = frozenset(
    {"low", "med", "high", "xhigh", "max", "ultracode", "vibe"}
)
DEFAULT_MODE: str = "auto"
DEFAULT_EFFORT: str = "med"


# ---------------------------------------------------------------------- #
# Pydantic models
# ---------------------------------------------------------------------- #


class CreateMissionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1)
    reference_image_path: Optional[str] = None
    end_reference_image_path: Optional[str] = None
    priority: int = 5
    mode: str = DEFAULT_MODE
    effort: str = DEFAULT_EFFORT
    fast: bool = False
    vision_loop: bool = False
    speculative: bool = False
    attachments: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    # These are ids from the bundled, allow-listed AgentLibrary.  They are
    # advisory specialist briefs, not executable prompt text supplied by a
    # caller; create_mission filters them against the library before persisting.
    agents: List[str] = Field(default_factory=list, max_length=3)
    combine_with_default_swarm: bool = True

    @field_validator("mode")
    @classmethod
    def _coerce_mode(cls, value: str) -> str:
        # Invalid mode degrades to the default rather than 422-ing.
        return value if value in VALID_MODES else DEFAULT_MODE

    @field_validator("effort")
    @classmethod
    def _coerce_effort(cls, value: str) -> str:
        # Invalid effort degrades to the default rather than 422-ing.
        return value if value in VALID_EFFORTS else DEFAULT_EFFORT


class MissionResponse(BaseModel):
    id: str
    title: str
    goal: str
    status: str
    priority: int
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_cost_aud: float = 0.0
    reference_image_path: Optional[str] = None
    output_path: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None


class RejectRequest(BaseModel):
    feedback: str = ""


class LearnRequest(BaseModel):
    url: str = Field(min_length=1)
    topic: str = Field(min_length=1)


class LearnResponse(BaseModel):
    skill_id: str
    verified: bool
    steps: int


class SkillSummary(BaseModel):
    name: str
    topic: str
    source_url: Optional[str] = None
    verified: bool = False
    success_rate: float = 0.0
    steps_count: int = 0
    last_used: Optional[str] = None


class CritiqueRequest(BaseModel):
    work_path: str = Field(min_length=1)
    reference_path: str = Field(min_length=1)
    task_type: str = "image generation"


class ComposeRequest(BaseModel):
    skill_a: str = Field(min_length=1)
    skill_b: str = Field(min_length=1)


class ChatCreateRequest(BaseModel):
    model: Optional[str] = None


class ChatMessageRequest(BaseModel):
    content: str = ""
    model: Optional[str] = None
    # Data-URL images attached to this turn (vision models see them).
    images: List[str] = Field(default_factory=list)
    # Route through OpenRouter's live web-search plugin (":online" suffix).
    web: bool = False
    # Let the model call tools (run_python, calculator, fetch_url, skills, ...).
    # ON by default: with these off the model correctly answers "I can't create
    # files / call tools", which is never what this app should say.
    tools: bool = True
    # Per-chat tool opt-outs: positive set of enabled tool ids sent by the
    # frontend. Narrows (never widens) the global settings.enabled_tools set.
    enabled_tools: Optional[List[str]] = None
    # Executive Assistant mode: adds file/system + creative tools and persona.
    assistant: bool = False
    # Optional active file path. When set, the backend attaches LSP-style
    # diagnostics for this file to the system prompt (ruff / tsc syntax).
    file_path: Optional[str] = None
    # Permit side-effectful/paid tools (write_file, generate_image, TTS).
    # ON by default so "save this as a file" just works.
    allow_actions: bool = True
    # Per-chat execution approval posture.  Custom delegates to the owner's
    # INFINITY_GATED_RISKS configuration; all other values are safe presets.
    approval_mode: str = "smart"
    # Active Agent Library persona (its prompt becomes the system prompt).
    agent_id: Optional[str] = None
    # Voice: "chat" (warm, Kimi-style) | "work" (terse, engineering-first).
    tone: str = ""
    # "append" (default) | "regenerate" (redo last assistant reply)
    # | "edit" (truncate history to keep_messages, then append content).
    mode: str = "append"
    keep_messages: Optional[int] = None

    @field_validator("approval_mode")
    @classmethod
    def _valid_approval_mode(cls, value: str) -> str:
        return value if value in {"ask", "smart", "full", "custom"} else "smart"


class ChatPatchRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    # Switching model here triggers the local-GPU kill switch when moving
    # off a local model (see set_chat_model / release_local_gpu).
    model: Optional[str] = None


class Settings(BaseModel):
    """User-editable settings, persisted to settings.json in DATA_DIR."""

    daily_budget_aud: float = Field(default=DAILY_BUDGET_AUD, ge=0.0, le=100000.0)
    default_mode: str = "auto"
    default_effort: str = "med"
    default_fast: bool = False
    default_vision_loop: bool = False
    default_speculative: bool = False
    pass_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    tournament_candidates: int = Field(default=5, ge=1, le=20)
    # Persistent memory: prepended to every chat's system prompt.
    user_notes: str = Field(default="", max_length=4000)
    # Chat defaults. Empty strings/lists resolve to the built-in defaults at
    # call time (CHAT_MODELS etc. are defined after this class).
    default_chat_model: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_response_tokens: int = Field(default=4000, ge=256, le=32000)
    system_prompt: str = Field(default="", max_length=8000)
    # Which chat tools the model may call; empty list = all enabled.
    enabled_tools: List[str] = Field(default_factory=list)
    # Auto semantic memory: remember + recall facts across chats (on by default).
    auto_memory: bool = True
    # OmniBrain: ground answers in the user's own memories/vault/project docs
    # served from the hosted brain (embeds remotely, so it costs no local embed).
    omnibrain_enabled: bool = True
    omnibrain_url: str = "https://fonedo-omnibrain.hf.space"
    # Smart assist: auto-enable tools, web search, and context trimming.
    smart_assist: bool = True
    # Prefer zero-cost DashScope free-tier models for chat.
    free_mode: bool = False

    @field_validator("default_mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        return value if value in _VALID_MODES else "auto"

    @field_validator("default_effort")
    @classmethod
    def _valid_effort(cls, value: str) -> str:
        return value if value in _VALID_EFFORTS else "med"


def _load_settings() -> Settings:
    """Load settings.json, falling back to defaults on any problem."""
    try:
        if SETTINGS_PATH.is_file():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return Settings(**data)
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
        logger.error("Could not load settings (%s); using defaults.", exc)
    return Settings()


def _save_settings(settings: Settings) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(settings.model_dump(), indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save settings: {exc}"
        ) from exc


def _load_saved_provider_keys() -> None:
    """Load provider keys saved in providers.json into environment variables.

    This lets LLM clients pick up keys entered in Settings without a restart.
    Empty saved values deliberately do not erase environment keys.
    """
    try:
        providers_path = DATA_DIR / "providers.json"
        if providers_path.is_file():
            data = json.loads(providers_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                deepseek_key = str(data.get("deepseek_key") or "").strip()
                if deepseek_key:
                    os.environ["DEEPSEEK_API_KEY"] = deepseek_key
                dashscope_key = str(data.get("dashscope_key") or "").strip()
                if dashscope_key:
                    os.environ["DASHSCOPE_API_KEY"] = dashscope_key
                nvidia_key = str(data.get("nvidia_key") or "").strip()
                if nvidia_key:
                    os.environ["NVIDIA_API_KEY"] = nvidia_key
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load saved provider keys: %s", exc)


def _rebuild_llm_clients(application: FastAPI) -> None:
    """Re-create LLM clients after provider keys change."""
    session_logger: Optional[SessionLogger] = getattr(
        application.state, "session_logger", None
    )
    client: Optional[OpenRouterClient] = None
    critic: Optional[CriticEngine] = None
    chat_client: Optional[OpenRouterClient] = None
    try:
        client = OpenRouterClient(session_logger=session_logger)
        critic = CriticEngine(client=client)
        chat_client = OpenRouterClient(
            max_retries=1, timeout=35.0, session_logger=session_logger
        )
        logger.info("LLM clients rebuilt after provider key change.")
    except ValueError as exc:
        logger.warning("Running WITHOUT LLM access after key change: %s", exc)

    application.state.client = client
    application.state.chat_client = chat_client
    application.state.critic = critic

    # Re-wire engines that hold references to the old clients.
    swarm: Optional[Any] = getattr(application.state, "swarm", None)
    if swarm is not None:
        swarm.client = client
        swarm.direct = getattr(application.state, "moonshot", None)
    verifier: Optional[Any] = getattr(application.state, "verifier", None)
    if verifier is not None:
        verifier.vision_client = chat_client or client
    loop_engine: Optional[Any] = getattr(application.state, "loop", None)
    if loop_engine is not None:
        loop_engine.client = chat_client or client
    trainer: Optional[Any] = getattr(application.state, "trainer", None)
    if trainer is not None:
        trainer.client = chat_client or client
        trainer.direct = getattr(application.state, "moonshot", None)


def _probe_provider_routes(client: OpenRouterClient) -> None:
    """Pre-warm the failover health cache in the background so the first chat
    never wastes latency on known-dead quotas. One tiny call per route."""
    import threading

    try:
        from backend.tools import openrouter_client as _orc
    except ImportError:  # running with backend/ as the working directory
        from tools import openrouter_client as _orc  # type: ignore[no-redef]

    probe_models = {
        "dashscope": "qwen-turbo",
        "deepseek": "deepseek-v4-flash",
        "moonshot": "kimi-k2.6",
        # Probing OR with a non-OR slug (e.g. qwen-turbo) 404s and poisons the
        # whole openrouter route dead for 5 minutes Ã¢â‚¬â€ use the meta free router.
        "openrouter": "openrouter/free",
    }

    def _probe() -> None:
        for route in list(client._routes):
            model = probe_models.get(
                route["provider"],
                "qwen-turbo",
            )
            try:
                client._client_for(route).chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=4,
                    timeout=10,
                )
                _orc.mark_route_live(route)
            except Exception as exc:  # noqa: BLE001 - probe must never raise
                if _orc.is_route_fatal(exc):
                    _orc.mark_route_dead(route, str(exc))
        logger.info(
            "Provider failover health: %s",
            [f"{r['provider']}={'live' if _orc.route_state(r) == 'live' else 'dead' if _orc.route_state(r) == 'dead' else '?'}" for r in client._routes],
        )

    threading.Thread(target=_probe, daemon=True, name="provider-probe").start()


def _apply_settings(settings: Settings) -> None:
    """Push live-adjustable settings into the running engines."""
    try:
        tracker: Optional[CostTracker] = getattr(app.state, "cost_tracker", None)
        if tracker is not None:
            tracker.daily_budget_aud = float(settings.daily_budget_aud)
        swarm: Optional[AgentSwarm] = getattr(app.state, "swarm", None)
        if swarm is not None:
            swarm.pass_threshold = float(settings.pass_threshold)
            swarm.tournament_candidates = int(settings.tournament_candidates)
    except (AttributeError, TypeError, ValueError) as exc:
        logger.error("Could not apply settings live: %s", exc)


# ---------------------------------------------------------------------- #
# Workspace: the project folder the assistant edits. Kept separate from
# settings.json so it can be changed independently and validated strictly.
# ---------------------------------------------------------------------- #


def _load_workspace() -> Optional[Path]:
    """Return the persisted workspace path if it is still valid."""
    try:
        if WORKSPACE_PATH.is_file():
            data = json.loads(WORKSPACE_PATH.read_text(encoding="utf-8"))
            path_str = str(data.get("path") or "").strip()
            if path_str:
                p = Path(path_str)
                if p.is_dir() and _is_allowed_knowledge_root(p):
                    return p
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load workspace: %s", exc)
    return None


def _save_workspace(path: Optional[Path]) -> None:
    """Persist or clear the workspace path."""
    try:
        WORKSPACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if path is None:
            WORKSPACE_PATH.write_text(json.dumps({"path": ""}, indent=2), encoding="utf-8")
        else:
            WORKSPACE_PATH.write_text(
                json.dumps({"path": str(path.resolve())}, indent=2), encoding="utf-8"
            )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save workspace: {exc}") from exc


def _workspace_tree(root: Path, max_files: int = 200, max_depth: int = 6) -> List[Dict[str, Any]]:
    """Return a compact tree of files/folders under root."""
    tree: List[Dict[str, Any]] = []
    count = 0

    def walk(dir_path: Path, depth: int) -> List[Dict[str, Any]]:
        nonlocal count
        entries: List[Dict[str, Any]] = []
        try:
            items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return entries
        for item in items:
            if item.name.startswith("."):
                continue
            if item.is_dir() and depth < max_depth:
                children = walk(item, depth + 1)
                entries.append({"name": item.name, "type": "dir", "children": children})
            elif item.is_file():
                count += 1
                if count > max_files:
                    break
                entries.append({"name": item.name, "type": "file"})
        return entries

    tree = walk(root, 1)
    return tree


def _workspace_tree_text(
    root: Path, max_files: int = 150, max_depth: int = 5, max_chars: int = 2000
) -> str:
    """Return a compact text representation of the workspace tree."""
    lines: List[str] = []
    count = 0

    def walk(dir_path: Path, depth: int, prefix: str) -> None:
        nonlocal count
        if depth > max_depth:
            return
        try:
            items = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return
        for i, item in enumerate(items):
            if item.name.startswith("."):
                continue
            is_last = i == len(items) - 1
            marker = "ÃƒÂ¢Ã¢â‚¬ÂÃ¢â‚¬ÂÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ " if is_last else "ÃƒÂ¢Ã¢â‚¬ÂÃ…â€œÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ "
            lines.append(prefix + marker + item.name)
            if len("\n".join(lines)) > max_chars:
                lines.pop()
                lines.append(prefix + "...")
                return
            if item.is_dir():
                count += 1
                if count > max_files:
                    lines.append(prefix + "...")
                    return
                walk(item, depth + 1, prefix + ("    " if is_last else "ÃƒÂ¢Ã¢â‚¬ÂÃ¢â‚¬Å¡   "))

    walk(root, 1, "")
    return "\n".join(lines)


def _resolve_workspace_path(root: Path, rel_path: str) -> Path:
    """Resolve a workspace-relative path, rejecting escapes."""
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Path escapes workspace.") from exc
    return target


# ---------------------------------------------------------------------- #
# Per-action approval registry (human-in-the-loop for the assistant loop)
# ---------------------------------------------------------------------- #


class ApprovalRegistry:
    """Pending tool-call approvals, resolved by the /approvals endpoint.

    The assistant tool loop is a *synchronous* generator running in Starlette's
    threadpool, so it can block-wait on a per-call Queue without stalling the
    event loop. Each pending call gets a Queue(maxsize=1); the frontend POSTs a
    decision which is put on that queue, unblocking the generator.

    A resolved set is kept so a late frontend decision after a timeout returns
    False deterministically instead of being silently dropped.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, "queue.Queue[Dict[str, Any]]"] = {}
        self._resolved: set = set()
        self._lock = threading.Lock()

    def create(self, call_id: str) -> "queue.Queue[Dict[str, Any]]":
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
        with self._lock:
            self._resolved.discard(call_id)
            self._pending[call_id] = q
        return q

    def resolve(self, call_id: str, decision: Dict[str, Any]) -> bool:
        with self._lock:
            if call_id in self._resolved:
                return False
            q = self._pending.get(call_id)
            if q is None:
                return False
            try:
                q.put_nowait(decision)
                self._resolved.add(call_id)
                return True
            except queue.Full:
                self._resolved.add(call_id)
                return False

    def wait(self, call_id: str, timeout: float = 180.0) -> Dict[str, Any]:
        """Block until resolved or timed out; returns the decision (skip on timeout)."""
        with self._lock:
            q = self._pending.get(call_id)
            if q is None or call_id in self._resolved:
                return {"decision": "skip", "reason": "already_resolved"}
        try:
            decision = q.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._resolved.add(call_id)
                self._pending.pop(call_id, None)
            return {"decision": "skip", "reason": "timeout"}
        else:
            with self._lock:
                self._resolved.add(call_id)
                self._pending.pop(call_id, None)
            return decision


def _seed_skills(skills_dir: Path) -> None:
    """Copy bundled reference skills (backend/data/seed_skills/) into the user's
    skills dir on first run so they appear in the Vault of the installed app."""
    seed_dir = BASE_DIR / "data" / "seed_skills"
    if not seed_dir.is_dir():
        return
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        for src in seed_dir.glob("*.json"):
            dest = skills_dir / src.name
            if not dest.exists():
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                logger.info("Seeded reference skill: %s", src.stem)
    except OSError as exc:  # non-fatal
        logger.warning("Could not seed skills: %s", exc)


# ---------------------------------------------------------------------- #
# Lifespan: init DB + engines once
# ---------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    for directory in (SKILLS_DIR, OUTPUTS_DIR, REFERENCES_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Could not create %s: %s", directory, exc)

    init_database(DB_PATH)
    _ensure_params_column()
    _ensure_chat_tables()
    _reconcile_orphaned_missions()  # clear zombies from a prior crash/restart

    # BFB mode pins the provider chain to DeepSeek direct; the DashScope
    # workspace keys stay in the chain as failover routes behind it.
    if ROUTING_MODE == "bfb":
        os.environ["INFINITY_LLM_PROVIDER"] = "deepseek"

    _models_cfg = _CONFIG.get("models", {}) if isinstance(_CONFIG.get("models"), dict) else {}
    model_router = ModelRouter(build_council(_models_cfg, mode=ROUTING_MODE))
    cost_tracker = CostTracker(daily_budget_aud=DAILY_BUDGET_AUD)

    # Session logger for the custom model strategy data flywheel.
    session_logger = SessionLogger(DATA_DIR)
    application.state.session_logger = session_logger

    # Lane router: task shape ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ model lane (cheap/smart/vision/custom).
    _lanes_cfg = _CONFIG.get("lanes", {}) if isinstance(_CONFIG.get("lanes"), dict) else {}
    lane_router = LaneRouter(model_router, lanes=_lanes_cfg)
    application.state.lane_router = lane_router

    # LLM key auto-load. DashScope was revoked (see memory
    # feedback-alibaba-key-revoked); the app now prefers Moonshot direct when
    # a moonshot.key file is present. OpenRouter still wins if that env is set.
    if not (os.environ.get("MOONSHOT_API_KEY") or "").strip():
        _moon_key = ""
        _moon_file = DATA_DIR / "moonshot.key"
        try:
            if _moon_file.is_file():
                _moon_key = _moon_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read %s: %s", _moon_file, exc)
        if _moon_key:
            os.environ["MOONSHOT_API_KEY"] = _moon_key
            logger.warning(
                "DEPRECATED: moonshot key loaded from plaintext file %s. "
                "Set MOONSHOT_API_KEY env var instead (see docs/ops/key-rotation.md).",
                _moon_file,
            )
            if not os.environ.get("INFINITY_LLM_PROVIDER") and not (
                os.environ.get("OPENROUTER_API_KEY") or ""
            ).strip():
                os.environ["INFINITY_LLM_PROVIDER"] = "moonshot"
            logger.info(
                "Moonshot key loaded ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â LLM provider: %s.",
                os.environ.get("INFINITY_LLM_PROVIDER", "auto"),
            )

    # Load provider keys saved via the UI (DeepSeek, etc.) before building LLM
    # clients so the first request after restart uses the saved key.
    _load_saved_provider_keys()

    client: Optional[OpenRouterClient] = None
    critic: Optional[CriticEngine] = None
    chat_client: Optional[OpenRouterClient] = None
    try:
        client = OpenRouterClient(session_logger=session_logger)
        critic = CriticEngine(client=client)
        # Chat rolls across free models on rate-limits, so it wants ONE quick
        # attempt per model (not 3 slow retries) with a short timeout, so a
        # stalling free model fails fast and falls through.
        chat_client = OpenRouterClient(max_retries=1, timeout=35.0, session_logger=session_logger)
        logger.info("OpenRouter client ready.")
        _probe_provider_routes(client)
    except ValueError as exc:
        logger.warning("Running WITHOUT LLM access: %s", exc)

    # Evidence-first verifier: code isn't done until it runs.
    verifier = Verifier(vision_client=chat_client or client)
    application.state.verifier = verifier

    application.state.router = model_router
    application.state.lane_router = lane_router
    application.state.routing_mode = ROUTING_MODE

    # Credit Engine: user-facing wallet + ledger (1 credit = $0.01 spend).
    _credits_cfg = _CONFIG.get("credits", {}) if isinstance(_CONFIG.get("credits"), dict) else {}
    credits_engine = CreditEngine(DATA_DIR / "credits.db", _credits_cfg)
    application.state.credits = credits_engine
    configure_credits(credits_engine)
    application.state.cost_tracker = cost_tracker
    application.state.client = client
    application.state.chat_client = chat_client
    application.state.critic = critic

    # Direct Kimi API (api.moonshot.ai): when a funded MOONSHOT key is present,
    # kimi-* roles bypass the OpenRouter hop (cheaper + faster). Must be built
    # BEFORE the swarm, which takes it as `direct=`.
    # Key sources in order: env var -> app-owned key file in DATA_DIR (survives
    # reinstalls; config.yaml ships inside the frozen exe so it cannot) -> config.
    moonshot_client = None
    _moonshot_cfg = _CONFIG.get("moonshot", {}) if isinstance(_CONFIG.get("moonshot"), dict) else {}
    _moonshot_key = os.environ.get("MOONSHOT_API_KEY") or ""
    if not _moonshot_key:
        _key_file = DATA_DIR / "moonshot.key"
        try:
            if _key_file.is_file():
                _moonshot_key = _key_file.read_text(encoding="utf-8").strip()
                logger.warning(
                    "DEPRECATED: moonshot key read from plaintext file %s. "
                    "Set MOONSHOT_API_KEY env var instead.", _key_file,
                )
        except OSError as exc:
            logger.warning("Could not read %s: %s", _key_file, exc)
    if not _moonshot_key:
        _moonshot_key = str(_moonshot_cfg.get("api_key") or "")
    if _moonshot_key and not _moonshot_key.startswith("${"):
        try:
            moonshot_client = MoonshotClient(api_key=_moonshot_key, session_logger=session_logger)
            logger.info("Moonshot direct client ready (kimi roles bypass OpenRouter).")
        except Exception as exc:  # noqa: BLE001 - optional accelerator, never fatal
            logger.warning("Moonshot direct unavailable: %s", exc)
    application.state.moonshot = moonshot_client

    # Direct DashScope (Alibaba Qwen) client: the owner's benchmarked-fast set
    # (qwen3-coder-480b ~1.5s, qwen3.7-max, qwen3-vl-plus). dashscope/* role
    # models route here; falls back to OpenRouter if the key is absent.
    dashscope_client = None
    _ds_cfg = _CONFIG.get("dashscope", {}) if isinstance(_CONFIG.get("dashscope"), dict) else {}
    _ds_key = os.environ.get("DASHSCOPE_API_KEY") or ""
    if not _ds_key:
        _ds_file = DATA_DIR / "dashscope.key"
        try:
            if _ds_file.is_file():
                _ds_key = _ds_file.read_text(encoding="utf-8").strip()
                logger.warning(
                    "DEPRECATED: dashscope key read from plaintext file %s. "
                    "Set DASHSCOPE_API_KEY env var instead.", _ds_file,
                )
        except OSError as exc:
            logger.warning("Could not read %s: %s", _ds_file, exc)
    if not _ds_key:
        _ds_key = str(_ds_cfg.get("api_key") or "")
    _ds_base = os.environ.get("DASHSCOPE_BASE_URL") or ""
    if not _ds_base:
        _base_file = DATA_DIR / "dashscope.base"
        try:
            if _base_file.is_file():
                _ds_base = _base_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read %s: %s", _base_file, exc)
    if not _ds_base:
        _ds_base = str(_ds_cfg.get("base_url") or "")
    if _ds_base.startswith("${"):
        _ds_base = ""
    if _ds_key and not _ds_key.startswith("${"):
        try:
            dashscope_client = DashScopeClient(api_key=_ds_key, base_url=_ds_base or None)
            logger.info("DashScope direct client ready (qwen roles bypass OpenRouter).")
        except Exception as exc:  # noqa: BLE001 - optional accelerator, never fatal
            logger.warning("DashScope direct unavailable: %s", exc)
    application.state.dashscope = dashscope_client

    # Boot-time model validation: a stale slug should surface in the log, not
    # silently fail a mission halfway through.
    _routing = model_router.validate()
    if _routing["problems"]:
        logger.warning("Model routing problems: %s", "; ".join(_routing["problems"]))
    else:
        logger.info("Model routing OK ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â %d roles bound.", len(_routing["ok"]))

    # Live event bus. Bound to this loop now so the swarm can publish from the
    # worker threads it runs blocking LLM/subprocess work on.
    event_bus = MissionEventBus()
    event_bus.bind_loop()
    application.state.events = event_bus

    application.state.swarm = AgentSwarm(
        db_path=DB_PATH,
        outputs_dir=OUTPUTS_DIR,
        router=model_router,
        cost_tracker=cost_tracker,
        client=client,
        critic=critic,
        direct=moonshot_client,
        dashscope=dashscope_client,
        events=event_bus,
        data_dir=DATA_DIR,
    )
    application.state.learn = LearnEngine(skills_dir=SKILLS_DIR)
    skill_engine = SkillEngine(skills_dir=SKILLS_DIR, db_path=SKILLS_DB_PATH)
    application.state.skill = skill_engine
    _seed_skills(SKILLS_DIR)
    provider_manager = ProviderManager(DATA_DIR / "providers.json")
    application.state.providers = provider_manager
    # Wire the optional paid media capability after the provider registry is
    # ready. Offline/test swarms remain constructible without credentials.
    application.state.swarm.providers = provider_manager
    application.state.memory = MemoryStore(DATA_DIR / "memory.db")
    application.state.ide_workspace = IDEWorkspaceStore(DATA_DIR / "ide_workspace.db")
    application.state.wiki = WikiStore(DATA_DIR / "wiki.db")
    application.state.training = TrajectoryCollector(
        LONGTASK_JOURNAL, DATA_DIR / "training")
    application.state.approvals = ApprovalRegistry()
    application.state.agents = AgentLibrary(BASE_DIR / "data" / "agents.json")
    # Personal knowledge base (RAG) + free local self-testing.
    knowledge_store = KnowledgeStore(DATA_DIR / "knowledge.db")
    application.state.knowledge = knowledge_store
    # Fully local vector loop. Chroma and Ollama are lazy/optional, so startup
    # remains healthy while either dependency or embedding model is installed.
    application.state.local_rag = LocalRAG(DATA_DIR / "chroma")
    # OmniBrain: hosted vector brain over the user's own notes. Remote-embedded,
    # so it adds no local embed cost and runs alongside knowledge.db.
    application.state.omnibrain = OmniBrainClient()
    application.state.selftest = SelfTester(DATA_DIR / "knowledge.db")
    # Self-training: live web -> distil -> RAG index. Benchmark-derived drills
    # use the local free lane by default; paid research remains opt-in/configured.
    _training_cfg = _CONFIG.get("self_training", {}) if isinstance(_CONFIG.get("self_training"), dict) else {}
    application.state.trainer = (
        SelfTrainer(
            knowledge=knowledge_store,
            client=(chat_client or client),
            data_dir=DATA_DIR,
            direct=moonshot_client,
            benchmark_model=str(_training_cfg.get("benchmark_model") or os.environ.get("INFINITY_BENCHMARK_MODEL") or "local/fable-fast"),
        )
        if (chat_client or client) is not None
        else None
    )
    application.state.loop = (
        LoopEngine(
            client=chat_client or client,
            knowledge=knowledge_store,
            cost_tracker=cost_tracker,
            fallback_models=CHAT_FALLBACK_MODELS,
            lane_router=lane_router,
            session_logger=session_logger,
            verifier=verifier,
            plan_work_dir=DATA_DIR,
            data_dir=DATA_DIR,
        )
        if (chat_client or client) is not None
        else None
    )

    # Ascension Engine hook: the Code loop resolves lane/models against the
    # current form's approved list on every run (live, not startup-snapshot).
    _loop = getattr(application.state, "loop", None)
    if _loop is not None:
        _loop.ascension_engine = ASCENSION_ENGINE

    # Custom model strategy: dataset builder + fine-tune + monthly retrain loop.
    dataset_builder = DatasetBuilder(session_logger, DATA_DIR / "datasets")
    application.state.dataset_builder = dataset_builder

    def _eval_harness_factory() -> Any:
        """Build a fresh eval harness when the retrain loop needs one."""
        _client = getattr(application.state, "client", None) or getattr(
            application.state, "chat_client", None
        )
        _router = getattr(application.state, "lane_router", None)
        try:
            from backend.core.eval_harness import EvalHarness
        except ImportError:
            from core.eval_harness import EvalHarness  # type: ignore[no-redef]
        return EvalHarness(_client, _router, DATA_DIR)

    retrain_loop = RetrainLoop(
        dataset_builder=dataset_builder,
        lane_router=lane_router,
        eval_harness_factory=_eval_harness_factory,
        data_dir=DATA_DIR,
        min_examples=int((_CONFIG.get("retrain", {}) or {}).get("min_examples", 100)),
        provider=str((_CONFIG.get("retrain", {}) or {}).get("provider", "together")),
        base_model=str((_CONFIG.get("retrain", {}) or {}).get("base_model", "Qwen/Qwen3-Coder-Next")),
        api_key=((_CONFIG.get("retrain", {}) or {}).get("api_key") or None),
        fireworks_account=((_CONFIG.get("retrain", {}) or {}).get("fireworks_account") or None),
    )
    application.state.retrain_loop = retrain_loop

    # Scheduled tasks: a read-only runner that persists each run as a new chat.
    _sched_client = chat_client or client

    def _run_scheduled(ctx: Dict[str, Any]) -> None:
        mode = str(ctx.get("mode") or "chat").lower()
        if mode == "retrain":
            loop = getattr(application.state, "retrain_loop", None)
            if loop is None:
                logger.warning("scheduled retrain skipped: no retrain loop")
                return
            try:
                result = loop.run()
                logger.info("Scheduled retrain: %s", result.get("status"))
                # If a job was just started, the next tick will poll it.
                if result.get("status") == "started":
                    return
                # Otherwise poll an in-flight or completed job.
                loop.poll()
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduled retrain failed: %s", exc)
            return

        if _sched_client is None:
            return
        prompt = str(ctx.get("prompt") or "")
        title = str(ctx.get("title") or "Scheduled task")
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        reply = "(no response)"
        used_model = CHAT_FALLBACK_MODELS[0]
        for cand in CHAT_FALLBACK_MODELS:
            try:
                res = _sched_client.chat(cand, messages, 2000)
                reply = (res["text"] if isinstance(res, dict) else getattr(res, "text", "")) or reply
                used_model = cand  # remember which candidate actually answered
                break
            except Exception:  # noqa: BLE001
                continue
        now = datetime.now(timezone.utc).isoformat()
        cid = str(uuid.uuid4())
        try:
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO chats (id, title, model, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (cid, f"ÃƒÂ¢Ã‚ÂÃ‚Â° {title}", used_model, now, now),
                )
                user_msg_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO chat_messages (id, chat_id, role, content, created_at) "
                    "VALUES (?, ?, 'user', ?, ?)",
                    (user_msg_id, cid, prompt, now),
                )
                _fts_insert(conn, user_msg_id, cid, prompt)
                assistant_msg_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO chat_messages (id, chat_id, role, content, created_at) "
                    "VALUES (?, ?, 'assistant', ?, ?)",
                    (assistant_msg_id, cid, reply, now),
                )
                _fts_insert(conn, assistant_msg_id, cid, reply)
        except sqlite3.Error as exc:
            logger.warning("scheduled run persist failed: %s", exc)

    scheduler = Scheduler(DATA_DIR / "schedules.db", run_fn=_run_scheduled)
    application.state.scheduler = scheduler
    try:
        scheduler.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler failed to start: %s", exc)
    # Seed the user's known corpora as default sources on first run.
    try:
        if not knowledge_store.list_sources():
            for seed_path, kind in (
                (Path.home() / "OneDrive" / "Documents" / "LLM WIKI", "vault"),
                (Path.home() / "OneDrive" / "Documents" / "Obsidian Vault", "vault"),
                (Path.home() / "Downloads", "downloads"),
            ):
                if seed_path.exists():
                    knowledge_store.add_source(str(seed_path), kind)
                    logger.info("Knowledge source seeded: %s", seed_path)
    except Exception as exc:  # noqa: BLE001 - seeding is best-effort
        logger.warning("Knowledge seeding failed: %s", exc)
    # First-run: index the corpus in a background thread so the app starts
    # instantly and the knowledge base fills itself in (free grounding, no UI needed).
    _embed_client = chat_client or client
    if _embed_client is not None and knowledge_store.counts()["chunks"] == 0:
        def _bg_index() -> None:
            try:
                stats = knowledge_store.reindex(_embed_client.embed)
                logger.info("Knowledge auto-indexed on first run: %s", stats)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Background knowledge index failed: %s", exc)
        threading.Thread(target=_bg_index, daemon=True).start()
    # Self-training: keep learning on its own so the assistant stays current
    # without being asked. First pass shortly after boot (so startup stays
    # instant), then once a day. Set INFINITY_AUTOTRAIN=0 to disable, or
    # INFINITY_AUTOTRAIN_HOURS to change the cadence.
    _trainer = application.state.trainer
    if _trainer is not None and os.environ.get("INFINITY_AUTOTRAIN", "1") != "0":
        try:
            _train_every_h = float(os.environ.get("INFINITY_AUTOTRAIN_HOURS", "24"))
        except ValueError:
            _train_every_h = 24.0

        def _auto_train() -> None:
            time.sleep(300)  # let the app settle before spending anything
            while True:
                try:
                    result = _trainer.run_cycle(None, _embed_client.embed)
                    logger.info(
                        "Self-training cycle: learned %d card(s), %d error(s).",
                        len(result.get("learned", [])), len(result.get("errors", [])),
                    )
                except Exception as exc:  # noqa: BLE001 - never kill the daemon
                    logger.warning("Self-training cycle failed: %s", exc)
                try:
                    lt_maybe_distill_playbook(
                        LONGTASK_JOURNAL, (chat_client or client).chat, SKILLS_DIR)
                except Exception as exc:  # noqa: BLE001 - never kill the daemon
                    logger.warning("Long-task playbook distillation failed: %s", exc)
                time.sleep(max(3600.0, _train_every_h * 3600.0))

        if _embed_client is not None:
            threading.Thread(target=_auto_train, daemon=True).start()
            logger.info("Self-training enabled (every %.0fh).", _train_every_h)

    # MCP: connect to configured servers on a background loop (non-fatal).
    mcp_manager = MCPManager(DATA_DIR / "mcp_servers.json")
    application.state.mcp = mcp_manager
    try:
        seeded = seed_mcp_starters(mcp_manager)
        if seeded:
            logger.info("Registered %d optional MCP capability pack(s).", seeded)
    except Exception as exc:  # noqa: BLE001 - optional packs must never block boot
        logger.warning("Capability-pack registration failed: %s", exc)
    try:
        mcp_manager.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP manager failed to start: %s", exc)
    # Active workspace/project folder for file-editing tools.
    workspace = _load_workspace()
    application.state.workspace = workspace

    application.state.tools = ToolRegistry(
        client=client,
        skill_engine=skill_engine,
        providers=provider_manager,
        output_dir=ASSISTANT_OUTPUT_DIR,
        verifier=verifier,
        workspace=workspace,
    )
    application.state.composer = (
        SkillComposer(skill_engine, client, model_router) if client is not None else None
    )
    application.state.economics = (
        EconomicsEngine(client, model_router) if client is not None else None
    )
    application.state.suggestions = (
        SuggestionEngine(DB_PATH, client, model_router) if client is not None else None
    )
    application.state.self_review = SelfReview(DB_PATH, SKILLS_DB_PATH)

    # Self-improvement governance: constitution-gated auto-fix + evolution.
    _si_cfg = (_CONFIG.get("self_improve", {}) or {})
    _si_client = (chat_client or client) if _si_cfg.get("enabled") else None
    if _si_client is not None:
        try:
            constitution = Constitution(_si_cfg)
            application.state.constitution = constitution
            auto_fix = AutoFix(
                db_path=DB_PATH,
                data_dir=DATA_DIR,
                constitution=constitution,
                client=_si_client,
                model=str(_si_cfg.get("fix_model", "dashscope/qwen-coder-plus")),
            )
            application.state.auto_fix = auto_fix
            evolve = Evolve(
                data_dir=DATA_DIR,
                constitution=constitution,
                eval_harness_factory=_eval_harness_factory,
                self_review=application.state.self_review,
                lanes=list(_si_cfg.get("lanes", ["cheap", "smart", "local"])),
            )
            application.state.evolve = evolve

            def _auto_fix_loop() -> None:
                _fix_every = float(_si_cfg.get("fix_interval_hours", 1)) * 3600.0
                time.sleep(60)  # let the app settle before first scan
                while True:
                    try:
                        result = auto_fix.check()
                        if result.get("patch_applied"):
                            logger.info("AutoFix applied patch for pattern: %s", result.get("pattern"))
                        elif result.get("patch_written"):
                            logger.info("AutoFix wrote patch for review: %s", result.get("patch_path"))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("AutoFix cycle failed: %s", exc)
                    time.sleep(max(300.0, _fix_every))

            def _evolve_loop() -> None:
                _evolve_every = float(_si_cfg.get("evolve_interval_hours", 6)) * 3600.0
                time.sleep(300)  # let the app settle before first benchmark
                while True:
                    try:
                        report = evolve.step()
                        if report.get("proposals"):
                            logger.info("Evolve proposals: %d", len(report["proposals"]))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Evolve cycle failed: %s", exc)
                    time.sleep(max(300.0, _evolve_every))

            threading.Thread(target=_auto_fix_loop, daemon=True).start()
            threading.Thread(target=_evolve_loop, daemon=True).start()
            logger.info(
                "Self-improvement loops active (fix every %.1fh, evolve every %.1fh).",
                float(_si_cfg.get("fix_interval_hours", 1)),
                float(_si_cfg.get("evolve_interval_hours", 6)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Self-improvement init failed: %s", exc)

    # Persisted user settings override config defaults and apply live.
    settings = _load_settings()
    cost_tracker.daily_budget_aud = float(settings.daily_budget_aud)
    application.state.swarm.pass_threshold = float(settings.pass_threshold)
    application.state.swarm.tournament_candidates = int(settings.tournament_candidates)
    application.state.settings = settings
    # DB init functions (previously on_event handlers, now explicit calls).
    init_claude_sync()
    init_claude_bridge()
    init_ai_chats()

    logger.info(
        "Infinity Code backend ready (budget $%.2f AUD/day).",
        settings.daily_budget_aud,
    )
    yield
    logger.info("Infinity Code backend shutting down.")
    try:
        scheduler.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        mcp_manager.shutdown()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Infinity Code", version="0.1.61", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization"],
)

# --- Bearer-token auth (generated per-session, passed to Tauri via IPC) ---
import secrets as _secrets
_API_TOKEN: str = _secrets.token_urlsafe(32)

@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Require Bearer token on all non-health API endpoints."""
    path = request.url.path
    # Exempt: health probe, static files, docs, and CORS preflights.
    # OPTIONS must pass through so the CORSMiddleware (inner) can answer
    # with Access-Control-Allow-Origin - 401-ing it breaks every
    # authenticated fetch from the Tauri WebView / browser.
    if (
        request.method == "OPTIONS"
        or path in ("/api/v1/health", "/api/v1/auth/token", "/docs", "/openapi.json", "/redoc")
        or not path.startswith("/api/")
        # The in-app Browser view loads proxied pages in a sandboxed
        # iframe that cannot send the Authorization header. The proxy's
        # SSRF guard (no loopback/private targets) keeps it narrow.
        or path.startswith("/api/v1/browser/")
    ):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != _API_TOKEN:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)

@app.middleware("http")
async def _trace_middleware(request: Request, call_next):
    """Tag every request with a trace id (client may supply X-Trace-ID).

    Registered after the auth middleware so it wraps it Ã¢â‚¬â€ auth rejections
    are logged with the same id the client sees in the response header.
    """
    tid = request.headers.get("x-trace-id") or uuid.uuid4().hex[:12]
    token = _TRACE_ID.set(tid)
    try:
        response = await call_next(request)
    finally:
        _TRACE_ID.reset(token)
    response.headers["X-Trace-ID"] = tid
    return response

@app.get("/api/v1/auth/token")
def _get_auth_token():
    """Tauri calls this ONCE at startup over localhost to get the session token.
    Only works because the localhost IP check below gates it."""
    return {"token": _API_TOKEN}

# The mount happens at import time, before lifespan runs, so the directory
# must exist already or StaticFiles refuses to start.
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
ASSISTANT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class LocalHostStaticFiles(StaticFiles):
    """StaticFiles that only answer requests originating from localhost.

    /outputs and /media are meant for the bundled desktop app on the same
    machine; refusing remote IPs closes a data-exfiltration footgun.
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            client = scope.get("client")
            host = client[0] if client else None
            if host not in {None, "127.0.0.1", "localhost", "::1"}:
                response = PlainTextResponse("Forbidden", status_code=403)
                await response(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


app.mount("/outputs", LocalHostStaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
# Assistant-generated media (images/audio) served here so the chat can show it.
# Only this dedicated subfolder is exposed ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â write_file output is not.
MEDIA_DIR: Path = ASSISTANT_OUTPUT_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", LocalHostStaticFiles(directory=str(MEDIA_DIR)), name="media")



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

def init_claude_sync():
    """Ensure chats.db schema is ready on startup."""
    try:
        init_chats_db(db_path=INFINITY_CHATS_DB)
        logger.info("Claude sync: chats.db initialized")
    except Exception as e:
        logger.error(f"Claude sync init error: {e}")


# =============================================================================
# Claude Bridge API - Continue work when Claude quota runs out
# =============================================================================

try:
    from backend.core.claude_bridge import (
        export_session_for_infinity,
        get_exported_sessions,
        get_continuation_prompt,
        detect_quota_exhausted,
        create_fallback_prompt,
        find_claude_project_files,
        BRIDGE_DB,
    )
except ImportError:
    from core.claude_bridge import (
        export_session_for_infinity,
        get_exported_sessions,
        get_continuation_prompt,
        detect_quota_exhausted,
        create_fallback_prompt,
        find_claude_project_files,
        BRIDGE_DB,
    )


def init_claude_bridge():
    """Ensure bridge database is ready on startup."""
    try:
        from backend.core.claude_bridge import init_bridge_db
        init_bridge_db(db_path=BRIDGE_DB)
        logger.info("Claude bridge: initialized")
    except Exception as e:
        logger.error(f"Claude bridge init error: {e}")


# =============================================================================
# Unified AI Chat History API (Claude + Codex + GPT)
# =============================================================================

try:
    from backend.core.ai_chat_sync import (
        sync_all_sources,
        get_all_conversations,
        search_all_conversations,
        get_source_stats,
        get_conversation,
        get_conversation_messages,
        init_ai_chats_db,
        AI_CHATS_DB,
    )
except ImportError:
    from core.ai_chat_sync import (
        sync_all_sources,
        get_all_conversations,
        search_all_conversations,
        get_source_stats,
        get_conversation,
        get_conversation_messages,
        init_ai_chats_db,
        AI_CHATS_DB,
    )


def init_ai_chats():
    """Ensure AI chats database is ready on startup."""
    try:
        init_ai_chats_db(db_path=AI_CHATS_DB)
        logger.info("AI chats: database initialized")
    except Exception as e:
        logger.error(f"AI chats init error: {e}")


def _is_allowed_knowledge_root(path: Path) -> bool:
    """Reject system/sensitive paths. Allowed roots: user's home dir and the
    app data dir. Resolves symlinks so a path can't escape via a link."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    home = Path.home().resolve()
    data = DATA_DIR.resolve()
    try:
        resolved.relative_to(home)
        return True
    except ValueError:
        pass
    try:
        resolved.relative_to(data)
        return True
    except ValueError:
        pass
    return False


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DB_PATH), timeout=10.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_params_column() -> None:
    """Idempotently add the missions.params_json column.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so a second run raises
    OperationalError ("duplicate column name") which we swallow. Existing rows
    keep NULL params_json, which _row_to_mission handles gracefully.
    """
    try:
        with _connect() as connection:
            connection.execute("ALTER TABLE missions ADD COLUMN params_json TEXT")
        logger.info("Added missions.params_json column.")
    except sqlite3.OperationalError:
        # Column already present (or table not yet created) ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â nothing to do.
        pass
    except sqlite3.Error as exc:
        logger.error("Could not ensure params_json column: %s", exc)


def _sanitize_filename(name: str) -> str:
    """Strip path components and unsafe characters from an uploaded filename."""
    base: str = os.path.basename(str(name or "")).replace("\\", "")
    cleaned: str = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "upload"


# ---------------------------------------------------------------------- #
# Chat: plain back-and-forth with a model (no swarm), persisted + swappable
# ---------------------------------------------------------------------- #

# Chat uses DashScope plan models (strong, metered), rated by strength (1-5)
# and sorted strongest-first.
CHAT_MODELS: List[Dict[str, Any]] = [
    {
        # Cursor-style: picks for you. Prefers the free local 4080 when its
        # server is up, falls back to the best cloud model when it isn't.
        "id": "auto",
        "label": "Auto",
        "hint": "Picks the best model - local when available, cloud when not",
        "strength": 5,
        "auto": True,
    },
    {
        "id": "local/infinity-ai",
        "label": "X Code",
        "hint": "Your own 4080 - free & private - 64K ctx",
        "strength": 5,
        "local": True,
        "hidden": True,  # off by default; user enables in Settings
    },
    {
        "id": "moonshotai/kimi-k3",
        "label": "Kimi K3",
        "hint": "Deepest reasoning - 1M ctx",
        "strength": 5,
    },
    {
        "id": "z-ai/glm-5.2",
        "label": "GLM 5.2",
        "hint": "Reasoning + code - 1M ctx",
        "strength": 5,
    },
    {
        "id": "moonshotai/kimi-k2.7-code",
        "label": "Kimi K2.7 Code",
        "hint": "Best for coding",
        "strength": 5,
    },
    {
        "id": "minimax/minimax-m3",
        "label": "MiniMax M3",
        "hint": "Fast, cheap, huge context",
        "strength": 4,
    },
    {
        "id": "deepseek/deepseek-chat:free",
        "label": "DeepSeek (Free)",
        "hint": "Zero cost - rate limited",
        "strength": 3,
        "free": True,
    },
    {
        # NVIDIA NIM free tier (build.nvidia.com): surfaces whenever any key is
        # configured; the alias maps below degrade it gracefully per provider.
        "id": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "label": "Nemotron Ultra (NIM)",
        "hint": "NVIDIA free tier - 253B deep reasoning",
        "strength": 4,
        "free": True,
    },
    {
        "id": "meta/llama-3.3-70b-instruct",
        "label": "Llama 3.3 70B (NIM)",
        "hint": "NVIDIA free tier - fast workhorse",
        "strength": 3,
        "free": True,
    },
    {
        "id": "deepseek-ai/deepseek-v4-flash-0731",
        "label": "DeepSeek V4 Flash (NIM)",
        "hint": "NVIDIA free tier - zero cost",
        "strength": 3,
        "free": True,
    },
]
# Local models: served by a server on THIS PC, not OpenRouter. Maps the picker
# id -> (base_url, model id to send). llama-server is single-model so its id is
# cosmetic; LM Studio needs the real identifier.
LOCAL_CHAT_MODELS: Dict[str, Dict[str, str]] = {
    "local/infinity-ai": {
        "base_url": "http://127.0.0.1:8081/v1",
        "model": "infinity-ai",
        "launcher": "FABLE-MAX-LLAMA.bat",
    },
    "local/infinity-ai-fast": {
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "fable-fast",
        "launcher": "FABLE-MINI.bat",
    },
}

# BFB mode surfaces the DeepSeek workhorse in the chat picker.
if ROUTING_MODE == "bfb":
    CHAT_MODELS.insert(
        1,
        {
            "id": "deepseek/deepseek-v4-flash",
            "label": "DeepSeek V4 Flash (BFB)",
            "hint": "BFB workhorse - cheap & fast, thinking off",
            "strength": 4,
        },
    )

CHAT_MODELS.sort(
    key=lambda m: (
        not m.get("auto", False),      # Auto always first
        not m.get("local", False),     # then local (free + private)
        -int(m["strength"]),
        not m.get("free", False),
    )
)

# What "Auto" resolves to when the local server isn't answering.
# Credit-saving: fall back to a FREE DashScope model (zero cost, rate-limited)
# so chat always works even with no credits.
AUTO_CLOUD_MODEL: str = "dashscope/qwen-turbo"
AUTO_LOCAL_MODEL: str = "local/infinity-ai"


def _local_server_alive(base_url: str, timeout: float = 1.5) -> bool:
    """Is a local model server actually answering right now?"""
    try:
        import urllib.request as _u
        with _u.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False


def resolve_auto_model() -> str:
    """Pick a concrete model for the 'auto' selection.

    Free-first: if the local 4080 is serving, use it (zero cost, private).
    Otherwise fall back to a FREE DashScope model so chat works with no
    credits. (Paid strong models stay available via explicit selection.)
    """
    spec = LOCAL_CHAT_MODELS.get(AUTO_LOCAL_MODEL)
    if spec and _local_server_alive(spec["base_url"]):
        return AUTO_LOCAL_MODEL
    # BFB (default routing): prefer the DeepSeek V4 Flash workhorse when
    # its key is configured; otherwise fall back to the free DashScope tier.
    if ROUTING_MODE == "bfb" and os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek/deepseek-v4-flash"
    return AUTO_CLOUD_MODEL


def _local_gpu_busy() -> bool:
    """True if the swarm is mid-mission (it may be using the local models)."""
    swarm = getattr(app.state, "swarm", None)
    if swarm is None:
        return False
    try:
        return bool(getattr(swarm, "_tasks", None))
    except Exception:  # noqa: BLE001 - never let the check block a release
        return False


def release_local_gpu(reason: str = "") -> Dict[str, Any]:
    """Free VRAM held by the local model servers.

    Called when the user switches a chat to a CLOUD model: there is no reason
    to keep ~14GB of the 4080 pinned by llama-server while talking to Kimi.
    Refuses while a swarm mission is running - missions route to local models
    (see swarm._local_engine), so killing mid-mission would break them.
    """
    if _local_gpu_busy():
        logger.info("Local GPU release skipped (%s): swarm missions running.", reason)
        return {"released": False, "why": "swarm missions running"}

    freed: List[str] = []
    # llama.cpp (the 35B, port 8081) - our own single-purpose process.
    try:
        result = subprocess.run(
            ["taskkill", "/IM", "llama-server.exe", "/F"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            freed.append("llama-server")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not stop llama-server: %s", exc)

    # LM Studio (9B/2B on :1234) - unload models, leave the server itself up so
    # nothing gets ECONNREFUSED.
    lms = Path(os.path.expanduser("~")) / ".lmstudio" / "bin" / "lms.exe"
    if lms.is_file():
        try:
            out = subprocess.run(
                [str(lms), "unload", "--all"],
                capture_output=True, text=True, timeout=60,
            )
            if out.returncode == 0 and "No models" not in (out.stdout or ""):
                freed.append("lmstudio models")
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Could not unload LM Studio models: %s", exc)

    if freed:
        logger.info("Local GPU released (%s): %s", reason, ", ".join(freed))
    return {"released": bool(freed), "freed": freed}
_CHAT_MODEL_IDS: frozenset[str] = frozenset(str(m["id"]) for m in CHAT_MODELS)
DEFAULT_CHAT_MODEL: str = str(CHAT_MODELS[0]["id"])
# A transient provider failure rolls through these reliable strong models.
CHAT_FALLBACK_MODELS: List[str] = [
    "dashscope/qwen3.8-max",
    "dashscope/qwen-max",
    "dashscope/qwen-plus",
]
# Zero-cost DashScope-tier models, surfaced when the user enables free mode.
FREE_CHAT_MODELS: List[str] = [
    "dashscope/qwen-turbo",
    "dashscope/qwen-plus",
    "dashscope/qwen-max",
]
CHAT_SYSTEM_PROMPT: str = (
    "You are Infinity Code ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â a sharp, warm creative partner who codes. "
    "YOU HAVE REAL TOOLS: you can write files to disk, run Python, fetch URLs, "
    "search the web, inspect workspaces, launch installed apps and use skills "
    "when those tools are registered for the turn. Never tell the user you 'cannot create "
    "files' or 'cannot attach downloads' ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â call write_file and tell them where "
    "you saved it. If a tool call fails, say what failed; never claim the "
    "capability is missing. When the user asks for an action covered by a tool, "
    "call the tool before giving manual steps. If a matching tool is unavailable, "
    "name the exact missing tool or approval and give one concise next step. "
    "You talk like a trusted collaborator, not a manual: friendly and human, "
    "never stiff or corporate, and never padded with filler or flattery. "
    "Lead with the answer, then the why. Keep it tight ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â long only when the "
    "problem earns it. When you write code, use fenced blocks with a language "
    "tag. If something's a bad idea, say so plainly and offer the better path. "
    "If you're unsure or your knowledge may be stale, say that instead of "
    "guessing. When retrieved knowledge is provided, ground your answer in it "
    "and cite what you used."
)
ASSISTANT_SYSTEM_PROMPT: str = (
    "You are the Executive Assistant inside Infinity Code ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â a capable agent that "
    "uses the user's PC to get real work done. You can look at their screen, read "
    "files and folders, run Python, search and browse the web, research topics, "
    "and (when actions are enabled) write files, launch installed apps and "
    "generate images or speech. "
    "Work in clear steps: state a short plan, use tools to gather what you need, "
    "then act. Prefer showing evidence (tool results) over claims. If the user "
    "requests an action covered by a registered tool, call it before offering "
    "manual instructions. You operate only through registered tools; do not claim "
    "mouse or keyboard control. Side-effectful actions run only after the active "
    "approval policy allows them."
)

_CHATS_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
_CHAT_MESSAGES_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT REFERENCES chats(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_chat_tables() -> None:
    try:
        with _connect() as connection:
            connection.execute(_CHATS_TABLE_SQL)
            connection.execute(_CHAT_MESSAGES_TABLE_SQL)
            # Migration: pinned flag on chats (older DBs lack it).
            try:
                connection.execute(
                    "ALTER TABLE chats ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # already migrated
            # Migration: tools_json on messages so tool cards survive a reload.
            try:
                connection.execute(
                    "ALTER TABLE chat_messages ADD COLUMN tools_json TEXT"
                )
            except sqlite3.OperationalError:
                pass  # already migrated
            # Full-text search over messages; backfill once if empty.
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chat_fts USING fts5("
                "message_id UNINDEXED, chat_id UNINDEXED, content)"
            )
            count = connection.execute("SELECT count(*) FROM chat_fts").fetchone()[0]
            if count == 0:
                connection.execute(
                    "INSERT INTO chat_fts (message_id, chat_id, content) "
                    "SELECT id, chat_id, content FROM chat_messages"
                )
    except sqlite3.Error as exc:
        logger.error("Could not create chat tables: %s", exc)


def _reconcile_orphaned_missions() -> int:
    """Fail any mission left 'running'/'queued' by a previous process.

    Missions run as in-memory asyncio tasks, so a crash/restart/quit mid-run
    strands the DB row at 'running' forever (no `finally` to write a terminal
    status). On boot there can be no genuinely-live run yet, so every such row
    is an orphan: mark it failed with a clear reason and stamp evidence so the
    UI shows *why* instead of spinning indefinitely. Returns the count fixed.
    """
    reason = "Interrupted ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the app closed or restarted while this was running."
    evidence = json.dumps({"failure_reason": reason, "attempts": []})
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as connection:
            cur = connection.execute(
                "UPDATE missions SET status='failed', completed_at=?, "
                "evidence_json=CASE WHEN evidence_json IS NULL OR evidence_json IN ('', '{}') "
                "THEN ? ELSE evidence_json END "
                "WHERE status IN ('running', 'queued') AND completed_at IS NULL",
                (now, evidence),
            )
            fixed = cur.rowcount or 0
            if fixed:
                logger.info("Reconciled %d orphaned mission(s) to 'failed'.", fixed)
            return fixed
    except sqlite3.Error as exc:
        logger.error("Mission reconciliation failed: %s", exc)
        return 0


def _fts_insert(connection: sqlite3.Connection, message_id: str, chat_id: str, content: str) -> None:
    try:
        connection.execute(
            "INSERT INTO chat_fts (message_id, chat_id, content) VALUES (?, ?, ?)",
            (message_id, chat_id, content),
        )
    except sqlite3.Error:
        pass  # search is best-effort; never block the chat write


def _row_to_mission(row: sqlite3.Row) -> MissionResponse:
    data: Dict[str, Any] = dict(row)
    evidence: Optional[Dict[str, Any]] = None
    raw_evidence: Optional[str] = data.get("evidence_json")
    if raw_evidence:
        try:
            parsed = json.loads(raw_evidence)
            evidence = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            evidence = None
    # params_json is a newer column; old rows / pre-migration DBs simply lack it.
    params: Optional[Dict[str, Any]] = None
    raw_params: Optional[str] = data.get("params_json")
    if raw_params:
        try:
            parsed_params = json.loads(raw_params)
            params = parsed_params if isinstance(parsed_params, dict) else None
        except (json.JSONDecodeError, TypeError):
            params = None
    return MissionResponse(
        id=str(data["id"]),
        title=str(data["title"]),
        goal=str(data["goal"]),
        status=str(data.get("status") or "queued"),
        priority=int(data.get("priority") or 5),
        created_at=str(data["created_at"]) if data.get("created_at") else None,
        completed_at=str(data["completed_at"]) if data.get("completed_at") else None,
        total_cost_aud=float(data.get("total_cost_aud") or 0.0),
        reference_image_path=data.get("reference_image_path"),
        output_path=data.get("output_path"),
        evidence=evidence,
        params=params,
    )


def _fetch_mission_or_404(mission_id: str) -> MissionResponse:
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT * FROM missions WHERE id = ?", (mission_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id} not found.")
    return _row_to_mission(row)


def _set_mission_status(mission_id: str, status: str, feedback: Optional[str] = None) -> None:
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT evidence_json FROM missions WHERE id = ?", (mission_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Mission {mission_id} not found."
                )
            evidence: Dict[str, Any] = {}
            if row["evidence_json"]:
                try:
                    parsed = json.loads(row["evidence_json"])
                    evidence = parsed if isinstance(parsed, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    evidence = {}
            if feedback is not None:
                evidence["rejection_feedback"] = feedback
            connection.execute(
                "UPDATE missions SET status = ?, evidence_json = ? WHERE id = ?",
                (status, json.dumps(evidence, ensure_ascii=False), mission_id),
            )
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


# ---------------------------------------------------------------------- #
# Routes: missions
# ---------------------------------------------------------------------- #


class WikiGenerateRequest(BaseModel):
    repo_path: str


class VisionVerifyRequest(BaseModel):
    candidate: str
    reference: str
    threshold: float = 0.85
    repo_path: str = ""


class FeedbackRequest(BaseModel):
    signal: str = "like"
    note: str = ""


class ArenaRequest(BaseModel):
    prompt: str = Field(default="")
    variants: List[str] = Field(default_factory=list)


class LongTaskCreateRequest(BaseModel):
    goal: str
    repo_path: str
    autonomy: str = "ask"
    max_steps: int = Field(default=40, ge=1, le=400)
    max_cost_aud: float = Field(default=2.0, gt=0)
    max_wall_min: int = Field(default=120, ge=1, le=1440)
    # Spec mode: park at the first plan and wait for POST .../approve-plan.
    spec_mode: bool = False


class _LongTaskBuilderAdapter:
    """Adapts provider_chat(model_id, ...) to the engine's
    builder.chat(messages, max_tokens) protocol, walking the fallback chain.
    kimi/* and dashscope/* go to their direct clients; local/* probes the
    llama.cpp server Ã¢â‚¬â€ OpenRouter is only ever the last resort."""

    def __init__(self, client, model_chain, moonshot=None, dashscope=None):
        self.client = client
        self.model_chain = [m for m in model_chain if m]
        self.moonshot = moonshot
        self.dashscope = dashscope

    def chat(self, messages, max_tokens=3000):
        last_err = None
        for model_id in self.model_chain:
            try:
                return provider_chat(
                    model_id, messages, max_tokens, openrouter=self.client,
                    moonshot=self.moonshot, dashscope=self.dashscope)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.info("longtask: builder %s failed (%s)", model_id, exc)
        raise RuntimeError(f"all longtask builder models failed: {last_err}")


def _longtask_model_chain(role: str) -> list:
    council = getattr(app.state, "router", None)
    council = council.council if council is not None else {}
    spec = council.get(role)
    if spec is None:
        return ["dashscope/qwen3-coder-480b-a35b-instruct"]
    return [spec.id, *list(getattr(spec, "fallbacks", ()))]




_DNA_PROMPT = (
    "You are extracting reusable style DNA from a piece of work the user "
    "liked. Reply with ONLY JSON: {\"palette\": \"...\", \"layout\": \"...\", "
    "\"typography\": \"...\", \"tone\": \"...\", \"structure\": \"...\"}. "
    "Each value: one concise sentence."
)


def _extract_style_dna(artifact: Dict[str, Any], chat_fn) -> Dict[str, Any]:
    """One LLM call: describe what made this artifact worth keeping."""
    prompt = (_DNA_PROMPT
              + " ARTIFACT PATH: " + str(artifact.get("path", ""))
              + " -- CONTENT: " + str(artifact.get("body", ""))[:3000])
    reply = chat_fn([{"role": "user", "content": prompt}], max_tokens=400)
    text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def _promote_artifact_to_reference(artifact_id: str) -> None:
    """Like => lock: extract DNA, embed it, store it for future prompts.
    Runs off the request path; every failure degrades to a no-op."""

    def _work() -> None:
        try:
            artifact = LONGTASK_JOURNAL.get_artifact(artifact_id)
            if not artifact:
                return
            client = (getattr(app.state, "client", None)
                      or getattr(app.state, "chat_client", None))
            if client is None:
                return
            adapter = _LongTaskBuilderAdapter(
                client, [_resolve_chat_model(None)],
                moonshot=getattr(app.state, "moonshot", None),
                dashscope=getattr(app.state, "dashscope", None))
            dna = _extract_style_dna(artifact, adapter.chat)
            emb: List[float] = []
            embed_fn = getattr(client, "embed", None)
            if embed_fn is not None:
                try:
                    emb = (embed_fn([json.dumps(dna)]) or [[]])[0]
                except Exception:  # noqa: BLE001
                    emb = []
            LONGTASK_JOURNAL.lock_reference("style", artifact_id,
                                            artifact.get("path", ""), dna,
                                            emb or None)
            logger.info("longtask: locked style reference from artifact %s",
                        artifact_id)
        except Exception as exc:  # noqa: BLE001 - a like never breaks the app
            logger.info("longtask reference lock failed: %s", exc)

    threading.Thread(target=_work, daemon=True).start()


def _locked_dna_lines(goal: str, client) -> List[str]:
    """Top-k locked style DNA for this goal, formatted as prompt lines."""
    try:
        embed_fn = getattr(client, "embed", None)
        if embed_fn is None:
            return []
        emb = (embed_fn([goal]) or [[]])[0]
        if not emb:
            return []
        hits = LONGTASK_JOURNAL.search_locked_references(emb, top_k=2)
        lines: List[str] = []
        for h in hits:
            dna = h.get("dna") or {}
            parts = [f"{k}={v}" for k, v in dna.items() if v]
            if parts:
                lines.append("LOCKED STYLE REFERENCE (user liked this): "
                             + "; ".join(parts)[:600])
        return lines
    except Exception as exc:  # noqa: BLE001
        logger.info("longtask locked-DNA recall failed: %s", exc)
        return []


def _frontend_playbook(goal: str):
    """Return (condensed playbook text <=1500 chars, skill name) when the goal
    is frontend related, else ("", None). The learned playbook (nightly
    distillation, learning.PLAYBOOK_NAME) wins over the seeded frontend-vibe
    one so the flywheel's distilled lessons steer UI work."""
    try:
        if not lt_frontend_related({"goal": goal}, []):
            return "", None
        skill = getattr(app.state, "skill", None)
        if skill is None:
            return "", None
        for name in ("frontend-vibe-learned", "frontend-vibe"):
            data = skill.get_skill(name)
            if not data:
                continue
            steps = sorted(data.get("steps", []), key=lambda s: s.get("order", 99))
            lines = [f"{i}. {s.get('instruction', '')}" for i, s in enumerate(steps, 1)]
            text = f"FRONTEND PLAYBOOK ({name}):\n" + "\n".join(lines)
            if len(text) > 1500:
                text = text[:1497] + "..."
            return text, name
        return "", None
    except Exception as exc:  # noqa: BLE001
        logger.info("longtask playbook lookup skipped: %s", exc)
        return "", None


def _longtask_learn_async(task_id: Optional[str], builder) -> None:
    """Fire lesson extraction off the request path. Learning must never slow
    or break a task; everything inside the thread is failure-proof."""
    if not task_id:
        return

    def _learn() -> None:
        try:
            task = LONGTASK_JOURNAL.get_task(task_id)
            steps = LONGTASK_JOURNAL.steps_for(task_id)
            if not task:
                return
            lessons = lt_extract_lessons(task, steps, builder.chat)
            kind = "frontend" if lt_frontend_related(task, steps) else "general"
            embed_fn = getattr(builder, "embed_fn", None)
            for ls in lessons:
                emb: List[float] = []
                if embed_fn is not None:
                    try:
                        emb = (embed_fn([ls]) or [[]])[0]
                    except Exception:  # noqa: BLE001
                        emb = []
                LONGTASK_JOURNAL.add_lesson(task_id, kind, ls, emb or None)
            if lessons:
                logger.info("longtask %s: learned %d lesson(s) [%s]",
                            task_id, len(lessons), kind)
        except Exception as exc:  # noqa: BLE001
            logger.info("longtask lesson extraction failed: %s", exc)

    threading.Thread(target=_learn, daemon=True).start()


def _resolve_chat_model(model: Optional[str]) -> str:
    # "auto" is a pseudo-model: resolve it to something concrete at call time
    # so the rest of the pipeline (local routing, cost guards) sees a real id.
    if model == "auto":
        return resolve_auto_model()
    if model in _CHAT_MODEL_IDS:
        return str(model)
    # Fall back to the user's default chat model, then the built-in default.
    settings = getattr(app.state, "settings", None)
    preferred = getattr(settings, "default_chat_model", "") if settings else ""
    if preferred in _CHAT_MODEL_IDS:
        return preferred
    # Free mode: default to a zero-cost OpenRouter model.
    if settings and getattr(settings, "free_mode", False) and FREE_CHAT_MODELS:
        return FREE_CHAT_MODELS[0]
    return DEFAULT_CHAT_MODEL



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
    from backend.routers.credits import router as credits_router
except ImportError:  # running with backend/ as the working directory
    from routers.credits import router as credits_router  # type: ignore[no-redef]
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
try:
    from backend.routers.system import router as system_router
except ImportError:  # running with backend/ as the working directory
    from routers.system import router as system_router  # type: ignore[no-redef]
app.include_router(system_router, prefix="/api/v1", tags=['system'])
try:
    from backend.routers.browser import router as browser_router
except ImportError:  # running with backend/ as the working directory
    from routers.browser import router as browser_router  # type: ignore[no-redef]
app.include_router(browser_router, prefix="/api/v1", tags=['browser'])
