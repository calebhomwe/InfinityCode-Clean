"""Agent swarm orchestration for Infinity Code.

Runs a mission's closed loop: Director plans, Engineer writes code, Tester
executes it in a real subprocess, Critic scores the produced image against the
reference, and the loop iterates with feedback until it passes or runs out of
attempts. Every attempt is recorded in SQLite with cost and evidence paths.

The swarm never claims success: a mission only reaches "completed" when its
code actually ran, and (if a reference exists) the vision critique scored it.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # import cycle-free: only needed for the annotation
    from backend.core.events import MissionEventBus

try:
    from backend.core.art_bridge import BlenderBridge, BlenderBridgeError
    from backend.core.agent_library import AgentLibrary
    from backend.core.benchmark_curriculum import BenchmarkCurriculum
    from backend.core.cost_tracker import CostTracker
    from backend.core.critic_engine import CriticEngine
    from backend.core.partial_executor import PartialRolloutConfig
    from backend.core.predictive import PredictiveRouter
    from backend.core.redteam import RedTeamAgent
    from backend.core.router import (  # noqa: E501
        COUNCIL, ModelRouter, ModelSpec, build_council, is_kimi, is_dashscope,
    )
    from backend.core.sandbox import run_sandboxed
    from backend.core import llm_cache
    from backend.core.credits import meter as credits_meter
    from backend.core.speculative import SpeculativeCoder
    from backend.core.tournament import TournamentRunner
    from backend.core.vision_loop import VisionLoop
    from backend.tools.openrouter_client import (
        MODEL_PRICING_USD_PER_MILLION, OpenRouterClient, OpenRouterError,
    )
except ImportError:  # running with backend/ as the working directory
    from core.art_bridge import BlenderBridge, BlenderBridgeError  # type: ignore[no-redef]
    from core.agent_library import AgentLibrary  # type: ignore[no-redef]
    from core.benchmark_curriculum import BenchmarkCurriculum  # type: ignore[no-redef]
    from core.cost_tracker import CostTracker  # type: ignore[no-redef]
    from core.critic_engine import CriticEngine  # type: ignore[no-redef]
    from core.partial_executor import PartialRolloutConfig  # type: ignore[no-redef]
    from core.predictive import PredictiveRouter  # type: ignore[no-redef]
    from core.redteam import RedTeamAgent  # type: ignore[no-redef]
    from core.router import (  # type: ignore[no-redef]
        COUNCIL, ModelRouter, ModelSpec, build_council, is_kimi, is_dashscope,
    )
    from core.sandbox import run_sandboxed  # type: ignore[no-redef]
    from core import llm_cache  # type: ignore[no-redef]
    from core.credits import meter as credits_meter  # type: ignore[no-redef]
    from core.speculative import SpeculativeCoder  # type: ignore[no-redef]
    from core.tournament import TournamentRunner  # type: ignore[no-redef]
    from core.vision_loop import VisionLoop  # type: ignore[no-redef]
    from tools.openrouter_client import (  # type: ignore[no-redef]
        MODEL_PRICING_USD_PER_MILLION,
        OpenRouterClient,
        OpenRouterError,
    )

logger = logging.getLogger(__name__)

AGENT_ROLES: List[str] = ["Director", "Engineer", "Artist", "Critic", "Tester", "Scribe"]
MAX_ATTEMPTS: int = 3
PASS_THRESHOLD: float = 0.8
EXEC_TIMEOUT_SECONDS: int = 60
TOURNAMENT_CANDIDATES: int = 5
def _max_concurrent_missions() -> int:
    """Env-overridable mission concurrency ceiling (INFINITY_SWARM_CONCURRENCY)."""
    try:
        return max(1, int(os.environ.get("INFINITY_SWARM_CONCURRENCY", "8")))
    except (TypeError, ValueError):
        return 8


# How many missions may actually RUN at once; the rest queue. Keeps a burst of
# missions from fork-bombing subprocesses or saturating the SQLite writer.
MAX_CONCURRENT_MISSIONS: int = _max_concurrent_missions()
_IMAGE_SUFFIXES: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# effort -> (max_attempts, primary engineer role). See the SHARED API CONTRACT.
_EFFORT_PLAN: Dict[str, Tuple[int, str]] = {
    "low": (1, "worker"),
    "med": (2, "engineer"),
    "high": (3, "engineer"),
    "xhigh": (4, "architect"),
    "max": (5, "architect"),
    "ultracode": (6, "architect"),
    # Swarm-in-swarm: Director decomposes, cheap workers fan out in parallel,
    # deterministic gate + reviewer pass decide (see _attempt_worker_swarm).
    "swarm": (1, "architect"),
    # Vibe coder: local 35B brain + Kimi/GLM cloud mix (see _vibe_call).
    "vibe": (3, "engineer"),
}

# Worker fan-out bounds: never more than this many modules per decomposition,
# and never more than this many parallel worker calls in flight.
SWARM_MAX_MODULES: int = 6

# Per-mission routing context. Set by _run_mission and read by _call_agent so
# vibe-coder missions route through the local-35B + cloud-mix chain without
# threading a mission id through every helper signature. asyncio.to_thread
# copies the current context into the worker thread, so calls made inside a
# mission see its id; anything outside a mission sees "" and routes normally.
_CURRENT_MISSION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "infinity_mission_id", default=""
)

# Vibe-coder cloud orchestration pool, walked in order after the local 35B.
# Role-specific ordering is applied on top (see _vibe_call): architect/debugger
# lead with the deep reasoner, engineer leads with the code specialist, cheap
# roles lead with MiniMax. glm-5-flash is the cheap tail of the chain.
_VIBE_CLOUD_POOL: Tuple[str, ...] = (
    "moonshotai/kimi-k2.7-code",
    "moonshotai/kimi-k3",
    "z-ai/glm-5.2",
    "minimax/minimax-m3",
    "z-ai/glm-5-flash",
)
_VIBE_ROLE_LEADS: Dict[str, str] = {
    "architect": "moonshotai/kimi-k3",
    "debugger": "moonshotai/kimi-k3",
    "engineer": "moonshotai/kimi-k2.7-code",
    "inspector": "moonshotai/kimi-k2.6",
    "creative": "z-ai/glm-5.2",
    "worker": "minimax/minimax-m3",
    "eye": "minimax/minimax-m3",  # m3 is the vision-capable pool member
}
# Safe defaults for a mission's params_json blob.
_DEFAULT_PARAMS: Dict[str, Any] = {
    "mode": "auto",
    "effort": "med",
    "fast": False,
    "vision_loop": False,
    "speculative": False,
    "attachments": [],
    "tools": [],
    "agents": [],
    "end_reference_image_path": "",
    "combine_with_default_swarm": True,
}
_ATTACHMENT_MAX_CHARS: int = 4000

# The composer's `tools` allow-list, enforced per mission. Each mode needs
# exactly one capability; a NON-EMPTY tools list restricts the mission to the
# tools it names, while an empty list keeps the historical behaviour (every
# tool allowed). Every code-writing branch (auto/code, fast, tournament,
# speculative, vision loop) executes Python, so it needs code execution.
_MODE_REQUIRED_TOOL: Dict[str, str] = {
    "image": "image_gen",
    "video": "image_gen",
    "3d": "render_3d",
}
_DEFAULT_REQUIRED_TOOL: str = "run_code"

_MISSIONS_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
    priority INTEGER DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    total_cost_aud REAL DEFAULT 0.0,
    reference_image_path TEXT,
    output_path TEXT,
    evidence_json TEXT
);
"""

_ATTEMPTS_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    mission_id TEXT REFERENCES missions(id),
    agent_role TEXT NOT NULL,
    model_used TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    cost_aud REAL DEFAULT 0.0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    output_artifact_path TEXT,
    critique_score REAL,
    critique_feedback TEXT,
    parent_attempt_id TEXT
);
"""

_TOURNAMENT_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS tournament_candidates (
    id TEXT PRIMARY KEY,
    mission_id TEXT,
    attempt INTEGER,
    candidate_letter TEXT,
    strategy TEXT,
    output_path TEXT,
    score REAL,
    cost_aud REAL,
    execution_time_ms INTEGER,
    selected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_REDTEAM_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS redteam_attacks (
    id TEXT PRIMARY KEY,
    mission_id TEXT,
    attempt INTEGER,
    attack_vector TEXT,
    passed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_MISSION_UPDATE_FIELDS: Tuple[str, ...] = (
    "status",
    "completed_at",
    "total_cost_aud",
    "output_path",
    "evidence_json",
)


class SwarmError(RuntimeError):
    """Raised when the swarm cannot run a mission at all."""


def init_database(db_path: Path) -> None:
    """Create the missions and attempts tables if they do not exist."""
    try:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path), timeout=10.0) as connection:
            # WAL lets readers and a writer proceed concurrently — needed when
            # several missions touch the DB at once.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute(_MISSIONS_TABLE_SQL)
            connection.execute(_ATTEMPTS_TABLE_SQL)
            connection.execute(_TOURNAMENT_TABLE_SQL)
            connection.execute(_REDTEAM_TABLE_SQL)
    except (sqlite3.Error, OSError) as exc:
        raise SwarmError(f"Cannot initialise mission database {db_path}: {exc}") from exc


class AgentSwarm:
    """Spawns and tracks async mission runs across the agent council."""

    def __init__(
        self,
        db_path: Path,
        outputs_dir: Path,
        router: Optional[ModelRouter] = None,
        cost_tracker: Optional[CostTracker] = None,
        client: Optional[OpenRouterClient] = None,
        critic: Optional[CriticEngine] = None,
        direct: Optional[object] = None,
        dashscope: Optional[object] = None,
        max_attempts: int = MAX_ATTEMPTS,
        pass_threshold: float = PASS_THRESHOLD,
        events: Optional["MissionEventBus"] = None,
        data_dir: Optional[Path] = None,
        providers: Optional[Any] = None,
    ) -> None:
        self.db_path: Path = Path(db_path)
        self.outputs_dir: Path = Path(outputs_dir)
        self.curriculum = BenchmarkCurriculum(data_dir or self.db_path.parent)
        try:
            self.outputs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SwarmError(f"Cannot create outputs dir {self.outputs_dir}: {exc}") from exc
        self.router: ModelRouter = router if router is not None else ModelRouter()
        # BFB (Bang For Buck) council: DeepSeek-first, built once, used by
        # missions that opt in via params mode="bfb" (see _run_mission).
        self._bfb_router: Optional[ModelRouter] = None
        self._bfb_missions: set = set()
        self.cost_tracker: CostTracker = (
            cost_tracker if cost_tracker is not None else CostTracker()
        )
        self.client: Optional[OpenRouterClient] = client
        self.critic: Optional[CriticEngine] = critic
        # Optional direct Kimi client (api.moonshot.ai) — bypasses OpenRouter
        # for kimi-* models when a funded key is configured.
        self.direct = direct
        # Optional direct DashScope (Alibaba Qwen) client for dashscope/* ids.
        self.dashscope = dashscope
        self._last_model_used: str = ""
        # Per-role deployment map: mission_id -> {role: model id actually used}.
        # Feeds get_mission_state so the UI can show live "K3 Agent" /
        # "35B Engineer" style badges instead of a static roster.
        self._role_models: Dict[str, Dict[str, str]] = {}
        # Vibe-coder missions: effort == "vibe" registers here, with the local
        # big-brain probe result cached per mission (None = cloud-only).
        self._vibe_missions: set = set()
        self._vibe_local: Dict[str, Optional[Tuple["OpenRouterClient", str]]] = {}
        self.max_attempts: int = max(1, int(max_attempts))
        self.pass_threshold: float = float(pass_threshold)
        # How many candidates the "Infinity Code" tournament races (1-20).
        self.tournament_candidates: int = TOURNAMENT_CANDIDATES
        self.active_agents: Dict[str, List[str]] = {}
        # Live event bus. Optional so the swarm stays constructible in tests and
        # scripts without one; every emit is a no-op when it is None.
        self.events: Optional["MissionEventBus"] = events
        # Optional paid media provider. Kept duck-typed so tests and offline
        # installs can construct the swarm without fal-client credentials.
        self.providers: Optional[Any] = providers
        self._tasks: Dict[str, "asyncio.Task[None]"] = {}
        # Bounded concurrency: a burst of missions (or one scheduler trigger)
        # would otherwise spawn unlimited subprocesses and saturate the single
        # SQLite writer. Queued missions wait here instead of piling on.
        # Created lazily — the loop may not exist at construction time.
        self._run_semaphore: Optional[asyncio.Semaphore] = None
        self.max_concurrent_missions: int = MAX_CONCURRENT_MISSIONS
        # The catalog is a local, versioned allow-list.  Mission records hold
        # ids only; the actual persona prompt is resolved here at run time.
        self.agent_library = AgentLibrary(
            Path(__file__).resolve().parents[1] / "data" / "agents.json"
        )

        # War-mode engines (only usable when an LLM client is available).
        self.tournament: Optional[TournamentRunner] = None
        self.redteam: Optional[RedTeamAgent] = None
        self.predictive: Optional[PredictiveRouter] = None
        if self.client is not None:
            self.tournament = TournamentRunner(
                self.client, self.router, self.critic, self.outputs_dir
            )
            self.redteam = RedTeamAgent(self.client, self.router)
            self.predictive = PredictiveRouter(self.client, self.router)

        # K3 Partial Rollout configuration for fast mode
        self.partial_config = PartialRolloutConfig(
            enabled=True,
            lambda_threshold=0.7,  # 70% of trajectories completes before yielding
            stream_interval_ms=300,
        )

    # ------------------------------------------------------------------ #
    # Database helpers
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.db_path), timeout=10.0)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as exc:
            raise SwarmError(f"Cannot open mission database {self.db_path}: {exc}") from exc

    def _get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM missions WHERE id = ?", (mission_id,)
                ).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error as exc:
            logger.error("Could not load mission %s: %s", mission_id, exc)
            return None

    def _update_mission(self, mission_id: str, **fields: Any) -> None:
        assignments: List[str] = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in _MISSION_UPDATE_FIELDS:
                logger.warning("Ignoring non-whitelisted mission field %r", key)
                continue
            assignments.append(f"{key} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(mission_id)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"UPDATE missions SET {', '.join(assignments)} WHERE id = ?",
                    tuple(values),
                )
        except sqlite3.Error as exc:
            logger.error("Could not update mission %s: %s", mission_id, exc)
            return
        # Every status transition funnels through here, so this is where the
        # run announces that it moved.
        if "status" in fields:
            self.emit(
                mission_id,
                "status",
                status=fields.get("status"),
                cost=(
                    round(float(fields["total_cost_aud"]), 6)
                    if fields.get("total_cost_aud") is not None
                    else None
                ),
            )

    def _record_attempt(
        self,
        mission_id: str,
        agent_role: str,
        model_used: str,
        status: str,
        cost_aud: float,
        artifact_path: Optional[str] = None,
        critique_score: Optional[float] = None,
        critique_feedback: Optional[str] = None,
        parent_attempt_id: Optional[str] = None,
    ) -> str:
        attempt_id: str = str(uuid.uuid4())
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO attempts
                        (id, mission_id, agent_role, model_used, status, cost_aud,
                         completed_at, output_artifact_path, critique_score,
                         critique_feedback, parent_attempt_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        mission_id,
                        agent_role,
                        model_used,
                        status,
                        round(cost_aud, 6),
                        datetime.now(timezone.utc).isoformat(),
                        artifact_path,
                        critique_score,
                        critique_feedback,
                        parent_attempt_id,
                    ),
                )
        except sqlite3.Error as exc:
            logger.error("Could not record attempt for mission %s: %s", mission_id, exc)
        self.emit(
            mission_id,
            "attempt",
            attempt_id=attempt_id,
            role=agent_role,
            model=model_used,
            status=status,
            cost=round(float(cost_aud), 6),
            score=critique_score,
            artifact=artifact_path,
        )
        return attempt_id

    # ------------------------------------------------------------------ #
    # Live-state helpers (consumed by the WebSocket route)
    # ------------------------------------------------------------------ #

    def emit(self, mission_id: str, event_type: str, **data: Any) -> None:
        """Announce a live event. No-op when no bus is attached.

        Telemetry must never be able to fail a mission, so this swallows
        everything — the bus already guards itself, this is belt-and-braces for
        the case where `events` is some other object entirely.
        """
        bus = self.events
        if bus is None:
            return
        try:
            bus.publish(mission_id, event_type, **data)
        except Exception as exc:  # noqa: BLE001 - never let telemetry break a run
            logger.debug("Event emit failed (%s): %s", event_type, exc)

    def _set_active(self, mission_id: str, agents: List[str]) -> None:
        """Set the currently-working agents, and announce the phase change.

        This is the single choke point every phase transition already passes
        through (Director, Engineer, Tester, Inspector, Critic, RedTeam, Artist,
        Scribe, and the tournament trio), which makes it the natural place to
        emit the run's timeline. Only real changes are announced, so a repeated
        call with the same roster stays quiet.
        """
        previous: List[str] = self.active_agents.get(mission_id, [])
        if agents:
            self.active_agents[mission_id] = agents
        else:
            self.active_agents.pop(mission_id, None)
        if agents != previous:
            self.emit(
                mission_id,
                "phase",
                agents=list(agents),
                lead=agents[0] if agents else None,
                previous=previous[0] if previous else None,
            )

    def get_active_agents(self, mission_id: str) -> List[str]:
        return list(self.active_agents.get(mission_id, []))

    def _latest_screenshot_url(self, mission_id: str) -> Optional[str]:
        mission_dir: Path = self.outputs_dir / mission_id
        if not mission_dir.is_dir():
            return None
        try:
            images: List[Path] = [
                path
                for path in mission_dir.rglob("*")
                if path.suffix.lower() in _IMAGE_SUFFIXES and path.is_file()
            ]
            if not images:
                return None
            latest: Path = max(images, key=lambda path: path.stat().st_mtime)
            relative: Path = latest.relative_to(self.outputs_dir)
            return "/outputs/" + relative.as_posix()
        except (OSError, ValueError) as exc:
            logger.error("Screenshot lookup failed for %s: %s", mission_id, exc)
            return None

    def get_mission_state(self, mission_id: str) -> Optional[Dict[str, Any]]:
        """Snapshot for live streaming: status, cost, active agents, screenshot."""
        mission: Optional[Dict[str, Any]] = self._get_mission(mission_id)
        if mission is None:
            return None
        return {
            "status": mission.get("status", "unknown"),
            "cost": float(mission.get("total_cost_aud") or 0.0),
            "agents_active": self.get_active_agents(mission_id),
            "screenshot": self._latest_screenshot_url(mission_id),
            # Live per-role deployment map: which model actually served each
            # agent role so far (e.g. {"engineer": "local/fable-max-35b"}).
            "role_models": dict(self._role_models.get(mission_id, {})),
        }

    # ------------------------------------------------------------------ #
    # Agent calls
    # ------------------------------------------------------------------ #

    def _spec_for_id(self, model_id: str) -> Optional[ModelSpec]:
        for spec in COUNCIL.values():
            if spec.id == model_id:
                return spec
        return None

    def _provider_chat(
        self, model_id: str, prompt: str, max_tokens: int
    ) -> Dict[str, Any]:
        """Dispatch one chat to the right backend for this model id: Kimi ->
        api.moonshot.ai, dashscope/* -> Alibaba Model Studio, everything else ->
        OpenRouter. Direct providers are used only when their client exists;
        otherwise the OpenRouter path still serves the (un-prefixed) model."""
        messages = [{"role": "user", "content": prompt}]
        # Response cache: exact repeats are free (swarm retries and shared
        # sub-prompts hit this constantly).
        cached_reply: Optional[Dict[str, Any]] = None
        if llm_cache.should_cache(messages, 0.2, max_tokens):
            cached_reply = llm_cache.lookup(
                provider="", base_url="", model=model_id, messages=messages,
                max_tokens=max_tokens, temperature=0.2,
            )
        if cached_reply is not None:
            return {
                "text": cached_reply["text"],
                "input_tokens": cached_reply["input_tokens"],
                "output_tokens": cached_reply["output_tokens"],
                "cached": True,
            }
        if self.direct is not None and is_kimi(model_id):
            result = self.direct.chat(model_id, messages, max_tokens=max_tokens)
        elif self.dashscope is not None and is_dashscope(model_id):
            result = self.dashscope.chat(model_id, messages, max_tokens=max_tokens)
        else:
            # OpenRouter can't serve our namespaced dashscope/* ids — strip to
            # the bare id only as a last resort (the OR slug may differ, but
            # the chain walker will move on if it 404s).
            or_id = model_id.split("/", 1)[1] if model_id.startswith("dashscope/") else model_id
            result = self.client.chat(
                model_id=or_id, messages=messages, max_tokens=max_tokens
            )
            if isinstance(result, dict):
                # Metered already inside the OpenRouter client (_record).
                result["_metered"] = True
        if llm_cache.should_cache(messages, 0.2, max_tokens) and isinstance(result, dict):
            llm_cache.store(
                provider="", base_url="", model=model_id, messages=messages,
                max_tokens=max_tokens, temperature=0.2,
                text=str(result.get("text") or ""),
                input_tokens=int(result.get("input_tokens", 0) or 0),
                output_tokens=int(result.get("output_tokens", 0) or 0),
            )
        # Credit Engine: direct-provider results meter here; OpenRouter-client
        # results are already metered inside the client (_metered tag).
        if isinstance(result, dict) and not result.get("_metered"):
            cin, cout = MODEL_PRICING_USD_PER_MILLION.get(model_id, (0.0, 0.0))
            tin = int(result.get("input_tokens", 0) or 0)
            tout = int(result.get("output_tokens", 0) or 0)
            usd = (tin * cin + tout * cout) / 1_000_000.0
            if result.get("cached"):
                credits_meter(0.0, model_id, "swarm_cached", tin, tout, avoided_usd=usd)
            else:
                credits_meter(usd, model_id, "swarm_direct", tin, tout)
        return result

    def _call_agent(self, role_key: str, prompt: str, max_tokens: int) -> Tuple[str, float]:
        """Blocking LLM call for a role. Walks that role's declared model chain:
        each candidate must clear the budget AND actually answer — a bad slug,
        429 or provider outage degrades to the next model instead of failing the
        mission. Kimi models go straight to api.moonshot.ai when a funded key is
        configured (cheaper + faster than the OpenRouter hop).
        Returns (text, cost_aud)."""
        if self.client is None:
            raise SwarmError("No LLM provider key configured - open Settings > Providers.")
        mission_id: str = _CURRENT_MISSION.get("")
        # Vibe-coder routing: local 35B big brain first, then the Kimi/GLM/
        # MiniMax cloud pool. Only kicks in for missions running effort="vibe".
        if mission_id and mission_id in self._vibe_missions:
            vibe_result = self._vibe_call(role_key, prompt, max_tokens, mission_id)
            if vibe_result is not None:
                return vibe_result
        router = self.router
        if (mission_id and mission_id in self._bfb_missions
                and self._bfb_router is not None):
            router = self._bfb_router
        estimated_input: int = max(1, len(prompt) // 4)
        chain: Tuple[ModelSpec, ...] = router.chain_for(role_key)
        last_error: str = ""

        for spec in chain:
            if not self.cost_tracker.approve_call(spec, estimated_input, max_tokens):
                last_error = f"{spec.id}: over budget"
                continue
            try:
                result = self._provider_chat(spec.id, prompt, max_tokens)
                text = (result.get("text") or "").strip()
                if not text:
                    raise SwarmError("empty response")
                cost_aud: float = self.router.calculate_cost(
                    spec, result["input_tokens"], result["output_tokens"]
                )
                self._last_model_used = spec.id
                if mission_id:
                    self._role_models.setdefault(mission_id, {})[role_key] = spec.id
                return text, cost_aud
            except Exception as exc:  # noqa: BLE001 - degrade to the next model
                last_error = f"{spec.id}: {str(exc)[:160]}"
                logger.warning("Role %s model %s failed (%s); trying next.", role_key, spec.id, last_error)

        # Whole cloud chain is down (out of credits, outage, rate-limited
        # everywhere). A running LM Studio is a free last resort for ANY role —
        # observed 2026-07-30 when OpenRouter 402'd and killed missions before
        # their first attempt.
        local = self._local_engine()
        if local is not None:
            local_client, local_model = local
            try:
                result = local_client.chat(
                    local_model,
                    [{"role": "user", "content": prompt}],
                    # 12000 floor: local reasoning models need ~3-6k tokens of
                    # thinking before any content appears.
                    max(12000, max_tokens),
                )
                text = (result.get("text") or "").strip()
                if text:
                    self._last_model_used = f"local:{local_model}"
                    if mission_id:
                        self._role_models.setdefault(mission_id, {})[role_key] = (
                            f"local:{local_model}"
                        )
                    logger.info(
                        "Role %s served by local fallback %s after cloud chain failed.",
                        role_key, local_model,
                    )
                    return text, 0.0
            except Exception as exc:  # noqa: BLE001 - local fallback is best-effort
                logger.warning("Local fallback for role %s also failed: %s", role_key, exc)

        raise SwarmError(f"All models failed for role {role_key!r}. Last: {last_error}")

    def _vibe_call(
        self, role_key: str, prompt: str, max_tokens: int, mission_id: str
    ) -> Optional[Tuple[str, float]]:
        """Vibe-coder routing for one role. Returns (text, cost) or None to
        fall through to the role's normal chain.

        Order of battle:
          1. The local 35B big brain (llama.cpp on 8081/8082), probed once per
             mission and cached — zero-cost, private, and the strongest model
             on the box.
          2. The cloud pool: Kimi swarm (k2.7-code / k3) + GLM-5.2, with
             MiniMax M3 and GLM-5-flash as the cheap tail. When the local
             server is down this pool IS the orchestration.
        """
        # --- 1) local big brain -------------------------------------------
        local = self._vibe_local.get(mission_id)
        if local is not None:
            local_client, local_model = local
            try:
                result = local_client.chat(
                    local_model,
                    [{"role": "user", "content": prompt}],
                    # 12000 floor: local reasoning models think for ~3-6k
                    # tokens before any content appears.
                    max(12000, max_tokens),
                )
                text = (result.get("text") or "").strip()
                if text:
                    self._last_model_used = f"local:{local_model}"
                    self._role_models.setdefault(mission_id, {})[role_key] = (
                        "local/fable-max-35b"
                    )
                    return text, 0.0
            except Exception as exc:  # noqa: BLE001 - degrade to the cloud pool
                logger.warning(
                    "Vibe local 35B failed for role %s (%s); going to cloud pool.",
                    role_key, exc,
                )

        # --- 2) cloud pool: role lead first, then the rest -----------------
        lead = _VIBE_ROLE_LEADS.get(role_key)
        ordered: List[str] = ([lead] if lead else []) + [
            m for m in _VIBE_CLOUD_POOL if m != lead
        ]
        estimated_input: int = max(1, len(prompt) // 4)
        last_error: str = ""
        for model_id in ordered:
            spec = self.router.spec_for_id(model_id)
            if spec is None:
                continue
            if not self.cost_tracker.approve_call(spec, estimated_input, max_tokens):
                last_error = f"{model_id}: over budget"
                continue
            try:
                result = self._provider_chat(spec.id, prompt, max_tokens)
                text = (result.get("text") or "").strip()
                if not text:
                    raise SwarmError("empty response")
                cost_aud: float = self.router.calculate_cost(
                    spec, result["input_tokens"], result["output_tokens"]
                )
                self._last_model_used = spec.id
                self._role_models.setdefault(mission_id, {})[role_key] = spec.id
                return text, cost_aud
            except Exception as exc:  # noqa: BLE001 - walk the pool
                last_error = f"{model_id}: {str(exc)[:160]}"
                logger.warning("Vibe pool model %s failed (%s); next.", model_id, last_error)
        logger.warning("Vibe routing exhausted for role %s (%s); normal chain next.", role_key, last_error)
        return None

    def _acceptance_check(
        self,
        goal: str,
        code: str,
        execution: Dict[str, Any],
        produced: List[Path],
    ) -> Dict[str, Any]:
        """Did the code actually DO what the goal asked?

        Exiting 0 only proves the script didn't crash — it says nothing about
        whether it solved the goal. The Inspector reads the goal, the source,
        its real stdout/stderr and the artifacts it produced, then returns a
        structured verdict. Concrete failures are fed straight back into the
        retry loop as feedback.

        Fails OPEN: if the Inspector itself errors we return accepted=True so a
        judging outage can never fail otherwise-working work.
        """
        artifacts = ", ".join(p.name for p in produced) or "(none)"
        prompt = (
            "You are the Inspector. Decide whether this program actually "
            "accomplished the stated goal. Judge the OUTCOME, not the style.\n\n"
            f"GOAL:\n{goal}\n\n"
            f"SOURCE:\n```python\n{code[:6000]}\n```\n\n"
            f"EXIT CODE: {execution.get('returncode')}\n"
            f"STDOUT:\n{(execution.get('stdout') or '')[:1500]}\n"
            f"STDERR:\n{(execution.get('stderr') or '')[:800]}\n"
            f"FILES PRODUCED: {artifacts}\n\n"
            "A program that runs cleanly but does the WRONG thing, produces no "
            "required output, silently swallows errors, or only stubs the work "
            "must be rejected.\n"
            "Reply with ONLY a JSON object:\n"
            '{"accepted": true|false, "confidence": 0.0-1.0, '
            '"reasons": ["short, concrete problem", ...]}'
        )
        try:
            # The inspector is a *thinking* model (kimi-k2.6): it spends output
            # tokens reasoning before emitting any content. A tight cap makes it
            # hit the limit mid-thought and return an EMPTY string, which then
            # burns a needless fallback to a pricier judge. Measured: a realistic
            # prompt needs ~720 output tokens, and 700 returned empty every time.
            text, cost = self._call_agent("inspector", prompt, 3000)
            match = re.search(r"\{[\s\S]*\}", text)
            data = json.loads(match.group(0)) if match else {}
            accepted = bool(data.get("accepted", True))
            reasons = [str(r)[:300] for r in (data.get("reasons") or [])][:5]
            return {
                "accepted": accepted,
                "confidence": float(data.get("confidence", 0.0) or 0.0),
                "reasons": reasons,
                "cost": cost,
                "model": self._last_model_used,
            }
        except Exception as exc:  # noqa: BLE001 - never fail work on a judging outage
            logger.warning("Acceptance check unavailable (%s); passing through.", exc)
            return {"accepted": True, "confidence": 0.0, "reasons": [],
                    "cost": 0.0, "model": "", "skipped": str(exc)[:200]}

    @staticmethod
    def _extract_code(text: str) -> str:
        """Pull the python source out of a fenced markdown response."""
        if not text:
            return ""
        fenced = re.search(r"```python\s*([\s\S]*?)```", text)
        if fenced:
            code = fenced.group(1).strip()
        else:
            fenced = re.search(r"```\s*([\s\S]*?)```", text)
            code = fenced.group(1).strip() if fenced else text.strip()
        # Local models sometimes nest or double the fences (observed: extracted
        # "code" still starting with ```python, which is a SyntaxError). Strip
        # any leftover fence lines defensively.
        lines = [
            line for line in code.splitlines()
            if not line.strip().startswith("```")
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _python_executable() -> Optional[str]:
        """Interpreter for running generated code.

        In a PyInstaller build sys.executable is the frozen backend exe, not
        Python — fall back to INFINITY_PYTHON or whatever is on PATH.
        """
        if not getattr(sys, "frozen", False):
            return sys.executable
        override: Optional[str] = os.environ.get("INFINITY_PYTHON")
        if override and Path(override).is_file():
            return override
        for name in ("python", "python3", "py"):
            found: Optional[str] = shutil.which(name)
            if found:
                return found
        return None

    def _execute_code(self, code_path: Path, workdir: Path) -> Dict[str, Any]:
        """Run generated code in a real subprocess and capture the outcome."""
        python_exe: Optional[str] = self._python_executable()
        if python_exe is None:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": (
                    "No Python interpreter available for mission execution. "
                    "Install Python or set INFINITY_PYTHON to a python.exe."
                ),
                "timed_out": False,
            }
        # Untrusted code: scrubbed env (no API keys), memory/process caps, and
        # whole-tree kill on timeout. See core/sandbox.py.
        return run_sandboxed(
            python_exe=python_exe,
            script=code_path,
            workdir=workdir,
            timeout=EXEC_TIMEOUT_SECONDS,
        )

    # ------------------------------------------------------------------ #
    # Per-mission knob parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_params(raw: Any) -> Dict[str, Any]:
        """Parse a mission's params_json into a dict with safe defaults.

        Tolerates None, missing keys, and invalid/non-dict JSON.
        """
        result: Dict[str, Any] = dict(_DEFAULT_PARAMS)
        data: Any = raw
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                return result
        if not isinstance(data, dict):
            return result
        for key in _DEFAULT_PARAMS:
            if key in data and data[key] is not None:
                result[key] = data[key]
        if not isinstance(result["attachments"], list):
            result["attachments"] = []
        if not isinstance(result["tools"], list):
            result["tools"] = []
        # Normalize to clean string ids so the allow-list enforcement in
        # _run_mission can compare reliably (empty result = all tools allowed).
        result["tools"] = [
            str(tool_id).strip() for tool_id in result["tools"] if str(tool_id).strip()
        ]
        if not isinstance(result["agents"], list):
            result["agents"] = []
        result["agents"] = [str(agent_id) for agent_id in result["agents"]][:3]
        result["mode"] = str(result["mode"] or "auto").strip().lower()
        result["effort"] = str(result["effort"] or "med").strip().lower()
        result["fast"] = bool(result["fast"])
        result["vision_loop"] = bool(result.get("vision_loop", False))
        result["speculative"] = bool(result.get("speculative", False))
        result["combine_with_default_swarm"] = bool(
            result.get("combine_with_default_swarm", True)
        )
        return result

    async def _run_selected_crew(
        self, mission_id: str, goal: str, agent_ids: List[str]
    ) -> Tuple[str, float, List[str]]:
        """Ask selected library personas for compact, bounded task briefs.

        The underlying execution models remain the normal routed council.  A
        crew persona contributes an explicit specialist brief before planning,
        which gives a mission a real, inspectable steering layer without
        treating user-provided prompt text as a new executable agent.
        """
        selected: List[Dict[str, Any]] = []
        for agent_id in agent_ids[:3]:
            agent = self.agent_library.get(agent_id)
            if agent is not None:
                selected.append(agent)
        if not selected:
            return "", 0.0, []

        names = [str(agent.get("name") or agent.get("id")) for agent in selected]
        self._set_active(mission_id, [f"Crew: {name}" for name in names])
        # Parallel briefs: run all specialist LLM calls concurrently (3x faster).
        async def _brief_one(agent: Dict[str, Any]) -> Tuple[str, str, float]:
            name = str(agent.get("name") or agent.get("id"))
            persona = str(agent.get("prompt") or "")[:6000]
            if not persona:
                return name, "", 0.0
            prompt = (
                "You are a selected specialist on a software mission. Follow the "
                "persona guidance below, then produce a compact implementation "
                "brief: at most six concrete bullets covering approach, risks, and "
                "acceptance checks. Do not write final code.\n\n"
                f"PERSONA ({name}):\n{persona}\n\nMISSION:\n{goal}"
            )
            try:
                brief, cost = await asyncio.to_thread(
                    self._call_agent, "worker", prompt, 700
                )
                return name, brief, cost
            except (SwarmError, OpenRouterError) as exc:
                logger.warning("Crew specialist %s could not brief: %s", name, exc)
                return name, "", 0.0

        results = await asyncio.gather(*[_brief_one(a) for a in selected])
        briefs: List[str] = []
        total_cost = 0.0
        for name, brief, cost in results:
            total_cost += cost
            if brief.strip():
                briefs.append(f"### {name}\n{brief.strip()[:5000]}")

        return "\n\n".join(briefs), total_cost, names

    @staticmethod
    def _effort_plan(effort: str, fast: bool) -> Tuple[int, str]:
        """Map effort (+fast) to (attempts, engineer role) per the contract."""
        attempts, role = _EFFORT_PLAN.get(
            (effort or "med").strip().lower(), _EFFORT_PLAN["med"]
        )
        if fast:
            role = "worker"
            # 3, not 2: fast missions usually run on the local 2B engine
            # (~5s per attempt, free), and a small model converts markedly
            # better with one extra retry-with-feedback round.
            attempts = min(max(attempts, 3), 3)
        return max(1, int(attempts)), role

    @staticmethod
    def _build_attachment_context(attachments: List[Any]) -> str:
        """Read text-ish attachments (best-effort) into a prompt section.

        Each readable file is capped at ~4000 chars; binary/unreadable files
        are listed by name only. Returns "" when there are no attachments.
        """
        if not attachments:
            return ""
        readable: List[str] = []
        unreadable: List[str] = []
        for item in attachments:
            name: str = str(item)
            try:
                path: Path = Path(str(item))
                name = path.name or str(item)
                if not path.is_file():
                    unreadable.append(name)
                    continue
                text: str = path.read_text(encoding="utf-8")
                readable.append(f"--- {name} ---\n{text[:_ATTACHMENT_MAX_CHARS]}")
            except (OSError, ValueError, UnicodeDecodeError):
                unreadable.append(name)
        parts: List[str] = []
        if readable:
            parts.append(
                "\n\nAttached reference files:\n" + "\n\n".join(readable)
            )
        if unreadable:
            parts.append(
                "\n\nAttached (binary/unreadable) files: " + ", ".join(unreadable)
            )
        return "".join(parts)

    def _image_url_for(self, images: List[Path]) -> Optional[str]:
        """Public /outputs URL for the first produced image, if any."""
        if not images:
            return None
        try:
            return "/outputs/" + images[0].relative_to(self.outputs_dir).as_posix()
        except ValueError:
            return None

    async def _maybe_critique(
        self,
        mission_id: str,
        ready: bool,
        reference_path: Optional[Path],
        produced_images: List[Path],
        task_type: str = "image generation",
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """Run the Critic against the reference if all preconditions hold."""
        if not (
            ready
            and reference_path is not None
            and reference_path.is_file()
            and produced_images
            and self.critic is not None
        ):
            return None, None
        self._set_active(mission_id, ["Critic"])
        try:
            critique = await asyncio.to_thread(
                self.critic.critique, produced_images[0], reference_path, task_type
            )
        except Exception as exc:  # noqa: BLE001 - critique must never crash a mission
            logger.error("Critic failed for %s: %s", mission_id, exc)
            return None, None
        return critique.model_dump(), critique.overall

    # ------------------------------------------------------------------ #
    # Per-mode attempt branches
    # ------------------------------------------------------------------ #

    # Local model choice, best-first. LM Studio's /v1/models lists every
    # DOWNLOADED model (not just loaded ones) and requesting an unloaded id
    # JIT-loads it — so blindly taking models[0] can trigger a 21GB load that
    # spills past the 16GB card into system RAM and runs 50-100x slower
    # (measured 2026-07-30: 6.5GB resident model ~1s/call, 21GB ~90-120s).
    # 9B first WITH a 12000-token budget (see the local call sites): Qwen3.5
    # reasoning models think for ~3-6k tokens on codegen prompts before any
    # code appears, so budgets of 2000-6000 return content="" (measured
    # repeatedly). At 12000 the 9B reasons ~3k then emits a complete correct
    # script in ~50s — far better code than the 2B, which emits instantly but
    # failed every attempt on real tasks. 2B stays as last-resort fallback.
    # /no_think and chat_template_kwargs{enable_thinking:false} do NOT work on
    # the 3.5 family via LM Studio — don't retry those.
    _LOCAL_MODEL_PREFERENCE: Tuple[str, ...] = (
        "fable-fast",        # opencode's standing 9B driver
        "qwen/qwen3.5-9b",
        "qwen/qwen3.5-4b",
        "qwen/qwen3.5-2b",
    )
    # Substring blocklist: models that must never be JIT-picked as the swarm
    # engine — too big for the card, embeddings, or no chat/tool reliability.
    _LOCAL_MODEL_BLOCKLIST: Tuple[str, ...] = (
        "ornith-1.0-35b", "ornith-1.0-9b", "qwen3-coder-30b", "qwen3-vl-30b",
        "qwen3.6-27b", "qwen3.6-35b", "fable-max", "glm-4.6v",
        "embed", "gemma", "unreal-blueprint-assistant",
    )

    # llama.cpp servers, best-first: (port, spec id). 8081 is the 35B big brain
    # (~47 tok/s, fully VRAM-resident); 8082 is the dense 27B when its launcher
    # is up. These are single-model servers already loaded in VRAM, so using
    # them never triggers a slow JIT load — unlike LM Studio's model list.
    _LLAMA_SERVERS: Tuple[Tuple[str, str], ...] = (
        ("http://127.0.0.1:8081/v1", "local/fable-max-35b"),
        ("http://127.0.0.1:8082/v1", "local/fable-fusion-27b"),
    )

    def _local_big_engine(self) -> Optional[Tuple[OpenRouterClient, str]]:
        """Return (client, model) for a running llama.cpp big-brain server.

        Probes 8081 (35B) then 8082 (27B) with a 1.5s timeout each. The model
        id sent is whatever the server reports in /v1/models, so it works with
        or without a --alias on the launcher.
        """
        for base_url, _spec_id in self._LLAMA_SERVERS:
            try:
                import json as _json
                import urllib.request

                with urllib.request.urlopen(
                    base_url.rstrip("/") + "/models", timeout=1.5
                ) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                if not models:
                    continue
                client = OpenRouterClient(
                    api_key="llama-cpp",
                    base_url=base_url,
                    max_retries=1,
                    timeout=300.0,  # the 35B thinks long before it speaks
                )
                return client, models[0]
            except Exception:  # noqa: BLE001 - that server simply isn't running
                continue
        return None

    def _local_engine(self) -> Optional[Tuple[OpenRouterClient, str]]:
        """Return (client, model) for a running local server, else None.

        Local-first: free (electricity-only) inference. Prefers an already-hot
        llama.cpp big brain (35B on 8081, 27B on 8082) over LM Studio, then
        falls to LM Studio's small fast models. Detected fresh each call (1.5s)
        so it picks up servers starting/stopping.
        """
        big = self._local_big_engine()
        if big is not None:
            return big
        try:
            import json as _json
            import urllib.request

            with urllib.request.urlopen(
                "http://localhost:1234/v1/models", timeout=1.5
            ) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            if not models:
                return None
            chosen: Optional[str] = None
            for pref in self._LOCAL_MODEL_PREFERENCE:
                if pref in models:
                    chosen = pref
                    break
            if chosen is None:
                for mid in models:
                    low = mid.lower()
                    if not any(bad in low for bad in self._LOCAL_MODEL_BLOCKLIST):
                        chosen = mid
                        break
            if chosen is None:
                return None
            local_client = OpenRouterClient(
                api_key="lm-studio",
                base_url="http://localhost:1234/v1",
                # 2 retries, not 1: when several missions hit an unloaded
                # model at once, LM Studio JIT-loads it for the first request
                # while the rest get 400 "Model is unloaded" — one retry a few
                # seconds later rides out the load window.
                max_retries=2,
                timeout=120.0,
            )
            return local_client, chosen
        except Exception:  # noqa: BLE001 - LM Studio simply isn't running
            return None

    async def _attempt_code(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        feedback: str,
        attachment_context: str,
        attempt_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
        reference_path: Optional[Path],
        prefer_local: bool = False,
    ) -> Dict[str, Any]:
        """Engineer writes python, Tester runs it, Critic scores it."""
        self._set_active(mission_id, ["Engineer"])
        engineer_prompt: str = (
            f"You are the Engineer agent. Goal:\n{goal}\n\nPlan:\n{plan_text}\n\n"
            "Write ONE complete, runnable Python script implementing the goal. "
            "If the goal produces anything visual, save it as 'out.png' in the "
            "current working directory. Respond with ONLY the script inside a "
            "single ```python code block."
            + (f"\n\nFix these problems from the last attempt: {feedback}" if feedback else "")
            + attachment_context
        )

        # Local-first: fast missions try LM Studio (free) before OpenRouter.
        model_used: str = role_spec.id
        is_local: bool = False
        local = await asyncio.to_thread(self._local_engine) if prefer_local else None
        if local is not None:
            local_client, local_model = local
            self._set_active(mission_id, ["Engineer (local)"])
            try:
                # Small local models fail in predictable ways; pin them down.
                # (Observed: scipy/tkinter imports that aren't installed or
                # need a display, and missing `import random` NameErrors.)
                local_prompt = engineer_prompt + (
                    "\n\nHard constraints: use ONLY the Python standard "
                    "library, numpy, and matplotlib (call matplotlib.use('Agg') "
                    "before importing pyplot; never open a GUI window). Do NOT "
                    "use scipy, tkinter, pygame, PIL or any other third-party "
                    "package. Import every module you use."
                )
                # ASSISTANT-PREFILL: pre-closing the <think> block and opening
                # the code fence makes Qwen3.5/3.6 skip the reasoning spiral and
                # answer immediately. Measured on the 3D benchmark: 5/6 in 349s
                # -> 6/6 in 62s (then 24s on repeat runs), ~20x fewer tokens.
                # Without it the model regularly burned the whole budget inside
                # <think> and returned empty content. See MODEL-PLAYBOOK.md.
                local_result = await asyncio.to_thread(
                    local_client.chat,
                    local_model,
                    [
                        {"role": "user", "content": local_prompt},
                        {"role": "assistant", "content": "</think>\n```python\n"},
                    ],
                    12000,
                    None,
                )
                code_text = local_result["text"]
                # It resumes INSIDE the fence, so re-attach the opening one for
                # _extract_code (which looks for a ```python block).
                if code_text.strip() and not code_text.lstrip().startswith("```"):
                    code_text = "```python\n" + code_text
                if not code_text.strip():
                    raise OpenRouterError(
                        f"local model {local_model} returned empty content "
                        "(reasoning likely consumed the whole token budget)"
                    )
                code_cost = 0.0  # local = electricity only
                model_used = f"local:{local_model}"
                is_local = True
            except OpenRouterError as exc:
                logger.warning("Local engine failed (%s); using OpenRouter.", exc)
                code_text, code_cost = await asyncio.to_thread(
                    self._call_agent, role_key, engineer_prompt, 4000
                )
        else:
            code_text, code_cost = await asyncio.to_thread(
                self._call_agent, role_key, engineer_prompt, 4000
            )
        code: str = self._extract_code(code_text)
        code_path: Path = attempt_dir / "main.py"
        try:
            code_path.write_text(code, encoding="utf-8")
        except OSError as exc:
            logger.error("Could not write code for %s: %s", mission_id, exc)

        if not code.strip():
            # An empty main.py exits 0 under python, so without this guard an
            # engineer reply with no extractable code sails through the tester
            # AND the acceptance gate as a "success" (observed 2026-07-30 with
            # local reasoning models). No code is always a failed attempt.
            return {
                "status": "failed",
                "cost": code_cost,
                "score": 0.0,
                "critique_dict": None,
                "image_url": None,
                "artifact_path": str(code_path),
                "evidence_extra": {
                    "code_path": str(code_path),
                    "code_url": self._image_url_for([code_path]),
                    "code": "",
                    "returncode": -1,
                    "stdout": "",
                    "stderr": "",
                    "local": is_local,
                    "acceptance": {
                        "accepted": False,
                        "confidence": 0.0,
                        "reasons": ["engineer returned no code"],
                        "model": "",
                    },
                },
                "failure_detail": (
                    "Engineer returned no extractable code (empty reply or no "
                    "```python block). Respond with ONLY one complete script "
                    "in a single ```python code block."
                ),
                "agent_role": "Engineer (local)" if is_local else "Engineer",
                "model_used": model_used,
            }

        self._set_active(mission_id, ["Tester"])
        execution: Dict[str, Any] = await asyncio.to_thread(
            self._execute_code, code_path, attempt_dir
        )
        exec_ok: bool = execution["returncode"] == 0
        try:
            produced_images: List[Path] = sorted(
                path
                for path in attempt_dir.iterdir()
                if path.suffix.lower() in _IMAGE_SUFFIXES
            )
        except OSError as exc:
            logger.error("Could not scan images for %s: %s", mission_id, exc)
            produced_images = []

        critique_dict, score = await self._maybe_critique(
            mission_id, exec_ok, reference_path, produced_images
        )

        # Goal-fit gate: only worth asking if the thing actually ran.
        acceptance: Dict[str, Any] = {"accepted": True, "reasons": [], "cost": 0.0}
        if exec_ok:
            self._set_active(mission_id, ["Inspector"])
            acceptance = await asyncio.to_thread(
                self._acceptance_check, goal, code, execution, produced_images
            )
            code_cost += float(acceptance.get("cost", 0.0))
        accepted: bool = bool(acceptance.get("accepted", True))

        failure_detail: str = ""
        if not exec_ok:
            failure_detail = f"Execution failed (code {execution['returncode']}): " + (
                execution["stderr"][-800:] or execution["stdout"][-800:]
            )
        elif not accepted:
            reasons = "; ".join(acceptance.get("reasons") or []) or "did not meet the goal"
            failure_detail = f"Ran, but did not accomplish the goal: {reasons}"
        return {
            "status": "success" if (exec_ok and accepted) else "failed",
            "cost": code_cost,
            "score": score,
            "critique_dict": critique_dict,
            "image_url": self._image_url_for(produced_images),
            "artifact_path": str(code_path),
            "evidence_extra": {
                "code_path": str(code_path),
                "code_url": self._image_url_for([code_path]),
                "code": code[:20000],
                "returncode": execution["returncode"],
                "stdout": execution["stdout"],
                "stderr": execution["stderr"],
                "local": is_local,
                "acceptance": {
                    "accepted": accepted,
                    "confidence": acceptance.get("confidence", 0.0),
                    "reasons": acceptance.get("reasons", []),
                    "model": acceptance.get("model", ""),
                },
            },
            "failure_detail": failure_detail,
            "agent_role": "Engineer (local)" if is_local else "Engineer",
            "model_used": model_used,
        }

    async def _attempt_fast(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        feedback: str,
        attachment_context: str,
        attempt_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
        reference_path: Optional[Path],
    ) -> Dict[str, Any]:
        """K3 Partial Rollout RL — fast execution with early stopping.
        
        Implements λNK early stopping from Kimi K3: executes multiple
        lightweight trajectories in parallel and returns intermediate
        results when λ fraction completes, rather than waiting for all.
        """
        self._set_active(mission_id, ["Engineer (K3 Fast)"])
        
        # Spawn multiple lightweight candidates with different temperatures
        n_trajectories = 3  # Small parallel batch for fast mode
        trajectories: List[asyncio.Task[Dict[str, Any]]] = []
        
        for i in range(n_trajectories):
            temp = 0.3 + (i * 0.3)  # 0.3, 0.6, 0.9
            traj_dir = attempt_dir / f"traj_{i}"
            traj_dir.mkdir(parents=True, exist_ok=True)
            task = asyncio.create_task(
                self._run_fast_trajectory(
                    mission_id, goal, plan_text, feedback, attachment_context,
                    traj_dir, role_key, role_spec, reference_path, temp, i
                )
            )
            trajectories.append(task)
        
        # Wait for λ threshold to complete
        lambda_threshold = self.partial_config.lambda_threshold
        min_completed = max(1, int(n_trajectories * lambda_threshold))
        
        completed: List[Dict[str, Any]] = []
        pending = set(trajectories)
        start_time = asyncio.get_event_loop().time()
        
        while pending and len(completed) < min_completed:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED, timeout=30.0
            )
            for task in done:
                try:
                    result = await task
                    completed.append(result)
                    # Emit partial progress
                    self.emit(
                        mission_id,
                        "partial",
                        trajectory=result.get("traj_id"),
                        status=result.get("status"),
                        lambda_completed=len(completed) / n_trajectories,
                    )
                except Exception as exc:
                    logger.warning("Fast trajectory failed: %s", exc)
        
        # Cancel remaining pending tasks
        for task in pending:
            task.cancel()
        
        # Select best result from completed trajectories
        if not completed:
            return {
                "status": "failed",
                "cost": 0.0,
                "score": 0.0,
                "critique_dict": None,
                "image_url": None,
                "artifact_path": "",
                "evidence_extra": {"error": "All fast trajectories failed"},
                "failure_detail": "K3 fast mode: no trajectories completed",
                "agent_role": "Engineer (K3 Fast)",
                "model_used": role_spec.id,
            }
        
        # Sort by score (higher is better) and cost (lower is better)
        def score_key(r: Dict[str, Any]) -> tuple[bool, float, float]:
            is_success = r.get("status") == "success"
            score = r.get("score") or 0.0
            cost = r.get("cost") or float("inf")
            return (is_success, score, -cost)  # Success > Score > Lower cost
        
        best = max(completed, key=score_key)
        best["agent_role"] = "Engineer (K3 Fast)"
        best["model_used"] = f"{role_spec.id} (K3-λ={lambda_threshold})"
        best["evidence_extra"]["k3_partial"] = {
            "trajectories_total": n_trajectories,
            "trajectories_completed": len(completed),
            "lambda_threshold": lambda_threshold,
            "execution_time_ms": int((asyncio.get_event_loop().time() - start_time) * 1000),
        }
        return best

    async def _run_fast_trajectory(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        feedback: str,
        attachment_context: str,
        traj_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
        reference_path: Optional[Path],
        temperature: float,
        traj_id: int,
    ) -> Dict[str, Any]:
        """Run a single fast trajectory with given temperature."""
        engineer_prompt = (
            f"You are the Engineer agent (trajectory {traj_id}, temp={temperature}). "
            f"Goal:\n{goal}\n\nPlan:\n{plan_text}\n\n"
            "Write ONE compact, runnable Python script. "
            "If visual output needed, save as 'out.png'. "
            "Respond with ONLY the script in a ```python block."
            + (f"\n\nFix these problems: {feedback}" if feedback else "")
            + attachment_context
        )
        
        try:
            code_text, code_cost = await asyncio.to_thread(
                self._call_agent, role_key, engineer_prompt, 3000
            )
            code = self._extract_code(code_text)
            code_path = traj_dir / "main.py"
            code_path.write_text(code, encoding="utf-8")
            
            if not code.strip():
                return {
                    "status": "failed",
                    "cost": code_cost,
                    "score": 0.0,
                    "critique_dict": None,
                    "image_url": None,
                    "artifact_path": str(code_path),
                    "evidence_extra": {"code": "", "traj_id": traj_id},
                    "traj_id": traj_id,
                }
            
            # Execute
            execution = await asyncio.to_thread(self._execute_code, code_path, traj_dir)
            exec_ok = execution["returncode"] == 0
            
            # Find images
            try:
                produced_images = sorted(
                    p for p in traj_dir.iterdir()
                    if p.suffix.lower() in _IMAGE_SUFFIXES
                )
            except OSError:
                produced_images = []
            
            # Critique
            critique_dict, score = await self._maybe_critique(
                mission_id, exec_ok, reference_path, produced_images
            )
            
            return {
                "status": "success" if exec_ok else "failed",
                "cost": code_cost,
                "score": score,
                "critique_dict": critique_dict,
                "image_url": self._image_url_for(produced_images),
                "artifact_path": str(code_path),
                "evidence_extra": {
                    "code_path": str(code_path),
                    "code": code[:10000],
                    "returncode": execution["returncode"],
                    "stdout": execution["stdout"],
                    "stderr": execution["stderr"],
                    "traj_id": traj_id,
                    "temperature": temperature,
                },
                "traj_id": traj_id,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "cost": 0.0,
                "score": 0.0,
                "critique_dict": None,
                "image_url": None,
                "artifact_path": "",
                "evidence_extra": {"error": str(exc), "traj_id": traj_id},
                "traj_id": traj_id,
            }

    @staticmethod
    def _parse_decomposition(text: str) -> List[Dict[str, str]]:
        """Pull the modules list out of a Director reply; tolerate noise."""
        try:
            m = re.search(r"\{.*\}", str(text or ""), re.DOTALL)
            if not m:
                return []
            data = json.loads(m.group(0))
            modules = data.get("modules") or []
            out: List[Dict[str, str]] = []
            for mod in modules:
                if isinstance(mod, dict) and str(mod.get("path") or "").strip():
                    out.append({
                        "path": Path(str(mod["path"])).name,
                        "spec": str(mod.get("spec") or "")[:300],
                    })
            return out
        except (ValueError, TypeError, json.JSONDecodeError):
            return []

    @staticmethod
    def _swarm_fail(attempt_dir: Path, reason: str, cost: float) -> Dict[str, Any]:
        return {
            "agent_role": "WorkerSwarm",
            "model_used": "",
            "status": "failed",
            "cost": float(cost),
            "score": 0.0,
            "critique_dict": None,
            "image_url": None,
            "artifact_path": str(attempt_dir),
            "evidence_extra": {"code_path": "", "errors": [reason]},
            "failure_detail": reason,
        }

    def _insert_candidate(
        self, mission_id: str, attempt: int, letter: str, strategy: str,
        output_path: str, score: Optional[float], cost_aud: float,
        selected: bool,
    ) -> None:
        """Record a parallel candidate in the tournament table (losers too).

        selected is ignored here on purpose: only _mark_candidate_selected may
        set it, so a successful-but-losing candidate is never flagged as the
        winner of its attempt.
        """
        del selected
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO tournament_candidates "
                    "(id, mission_id, attempt, candidate_letter, strategy, "
                    " output_path, score, cost_aud, selected) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), mission_id, attempt, letter, strategy,
                     output_path, score, cost_aud, 0),
                )
        except sqlite3.Error as exc:
            logger.warning("Could not record parallel candidate %s: %s", letter, exc)

    def _mark_candidate_selected(
        self, mission_id: str, attempt: int, letter: str
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE tournament_candidates SET selected = 1 "
                    "WHERE mission_id = ? AND attempt = ? AND candidate_letter = ?",
                    (mission_id, attempt, letter),
                )
        except sqlite3.Error as exc:
            logger.warning("Could not mark candidate %s selected: %s", letter, exc)

    async def _attempt_parallel_candidates(
        self,
        mission_id: str,
        attempt_number: int,
        goal: str,
        plan_text: str,
        attempt_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
        reference_path: Optional[Path],
        n_candidates: int = 3,
    ) -> Dict[str, Any]:
        """Fan out N engineer candidates in parallel; execute each; pick best.

        Every candidate runs the full write -> execute -> critique pipeline in
        its own sub-directory (no file collisions), and the strongest survivor
        carries the attempt. All candidates are recorded in tournament_candidates.
        """
        n: int = max(2, min(5, int(n_candidates)))
        self._set_active(mission_id, ["Engineer"] * n)
        candidates_dir: Path = attempt_dir / "candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        variants: List[Tuple[str, str]] = [
            ("A", plan_text),
            ("B", plan_text + "\n\nVariant B: take a different angle than a "
             "typical solution; prioritize simplicity and correctness."),
            ("C", plan_text + "\n\nVariant C: produce a robust alternative; "
             "prioritize edge cases and defensive coding."),
        ][:n]

        async def _run(letter: str, variant: str) -> Tuple[str, Dict[str, Any]]:
            try:
                return letter, await self._attempt_code(
                    mission_id, goal, variant, "", "",
                    candidates_dir / letter, role_key, role_spec,
                    reference_path, prefer_local=False,
                )
            except Exception as exc:  # noqa: BLE001 - a crashed candidate never kills the attempt
                return letter, {
                    "status": "failed", "cost": 0.0, "score": 0.0,
                    "critique_dict": None, "image_url": None,
                    "artifact_path": str(candidates_dir / letter),
                    "evidence_extra": {"code_path": ""},
                    "failure_detail": f"candidate {letter} crashed: {exc}",
                    "agent_role": "Engineer", "model_used": "",
                }

        results: List[Tuple[str, Dict[str, Any]]] = await asyncio.gather(
            *(_run(l, v) for l, v in variants)
        )
        best_letter: str = "A"
        best_res: Optional[Dict[str, Any]] = None
        for letter, res in results:
            ok = bool(res.get("status") == "success")
            score = res.get("score")
            self._insert_candidate(
                mission_id, attempt_number, letter, "parallel",
                str(res.get("artifact_path", "")),
                float(score) if isinstance(score, (int, float)) else None,
                float(res.get("cost", 0.0)), ok,
            )
            if ok and (best_res is None or (score or 0) > (best_res.get("score") or 0)):
                best_letter, best_res = letter, res
        if best_res is None:
            scored = [r for _, r in results if isinstance(r.get("score"), (int, float))]
            if scored:
                best_res = max(scored, key=lambda r: float(r.get("score") or 0))
            else:
                best_letter, best_res = results[0]
        self._mark_candidate_selected(mission_id, attempt_number, best_letter)
        winner: Dict[str, Any] = dict(best_res)
        winner["cost"] = sum(float(r.get("cost", 0.0)) for _, r in results)
        winner["agent_role"] = f"Engineer x{len(results)}"
        winner["evidence_extra"] = dict(winner.get("evidence_extra") or {})
        winner["evidence_extra"]["parallel_candidates"] = [
            {"letter": l, "status": r.get("status"), "score": r.get("score")}
            for l, r in results
        ]
        return winner

    async def _attempt_worker_swarm(
        self,
        mission_id: str,
        attempt_number: int,
        goal: str,
        plan_text: str,
        attempt_dir: Path,
        reference_path: Optional[Path],
    ) -> Dict[str, Any]:
        """Swarm-in-swarm: Director decomposes, cheap workers fan out in
        parallel, a deterministic compile gate + reviewer pass decide."""
        self._set_active(
            mission_id, ["Director", "Worker", "Worker", "Worker", "Inspector"]
        )
        decomp_prompt: str = (
            "You are the Director of a fan-out swarm. Goal:\n" + goal
            + "\n\nPlan:\n" + str(plan_text)[:1500]
            + "\n\nSplit this into at most " + str(SWARM_MAX_MODULES)
            + " file-partitioned subtasks. Reply with ONLY JSON: "
            '{"main": "main.py", "modules": [{"path": "<relative filename>", '
            '"spec": "<one-line implementation brief>"}]}. No directories, '
            'no ".." in paths.'
        )
        total_cost: float = 0.0
        try:
            decomp_text, decomp_cost = await asyncio.to_thread(
                self._call_agent, "architect", decomp_prompt, 1200
            )
            total_cost += decomp_cost
        except (SwarmError, OpenRouterError) as exc:
            return self._swarm_fail(
                attempt_dir, f"decomposition failed: {exc}", total_cost
            )
        modules: List[Dict[str, str]] = self._parse_decomposition(decomp_text)
        if not modules:
            modules = [{"path": "main.py", "spec": str(goal)[:300]}]
        modules = modules[:SWARM_MAX_MODULES]
        workdir: Path = attempt_dir / "swarm"
        workdir.mkdir(parents=True, exist_ok=True)

        async def _worker(idx: int, mod: Dict[str, str]) -> Tuple[str, str, float, str]:
            path = str(mod.get("path") or f"mod_{idx}.py")
            prompt: str = (
                f"You are Worker #{idx} in a fan-out swarm. Goal:\n{goal}\n\n"
                f"Your file: {path}. Brief: {mod.get('spec', '')}\n\n"
                "Write the COMPLETE implementation for this file only. Respond "
                "with ONLY the code inside a single ```python code block."
            )
            try:
                text, cost = await asyncio.to_thread(
                    self._call_agent, "worker", prompt, 4000
                )
                return path, self._extract_code(text), cost, ""
            except Exception as exc:  # noqa: BLE001 - one worker failing never blocks the swarm
                return path, "", 0.0, str(exc)

        worker_results: List[Tuple[str, str, float, str]] = await asyncio.gather(
            *(_worker(i, m) for i, m in enumerate(modules, 1))
        )
        total_cost += sum(c for _, _, c, _ in worker_results)
        errors: List[str] = []
        for path, code, _c, err in worker_results:
            safe_name: str = Path(path).name  # strip any directory traversal
            if err:
                errors.append(f"{safe_name}: {err}")
                continue
            if not code.strip():
                errors.append(f"{safe_name}: worker returned no code")
                continue
            try:
                (workdir / safe_name).write_text(code, encoding="utf-8")
            except OSError as exc:
                errors.append(f"{safe_name}: {exc}")
        compile_errors: List[str] = []
        for path in sorted(workdir.glob("*.py")):
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                compile_errors.append(f"{path.name}: {exc}")
        main_path: Path = workdir / "main.py"
        gate_ok: bool = not errors and not compile_errors and main_path.is_file()
        review_issues: str = ""
        approved: bool = True
        if gate_ok:
            files_dump: str = "\n".join(
                f"--- {p.name} ---\n{p.read_text(encoding='utf-8')[:1500]}"
                for p in sorted(workdir.glob("*.py"))
            )
            review_prompt: str = (
                "You are a strict reviewer for a fan-out swarm result.\n\n"
                f"GOAL:\n{goal}\n\nFILES:\n{files_dump}\n\n"
                'Reply with ONLY JSON: {"approve": true/false, '
                '"issues": ["...", "..."]}.'
            )
            try:
                review_text, review_cost = await asyncio.to_thread(
                    self._call_agent, "longtask_reviewer", review_prompt, 1000
                )
                total_cost += review_cost
                match = re.search(
                    r'"approve"\s*:\s*(true|false)', review_text or ""
                )
                approved = bool(match and match.group(1) == "true")
                issues = re.findall(
                    r'"issues"\s*:\s*\[(.*?)\]', review_text or "", re.DOTALL
                )
                review_issues = issues[0][:500] if issues else ""
            except (SwarmError, OpenRouterError) as exc:
                logger.warning("Swarm review pass failed for %s: %s", mission_id, exc)
        execution: Dict[str, Any] = {"returncode": -1, "stdout": "", "stderr": ""}
        if gate_ok:
            execution = await asyncio.to_thread(self._execute_code, main_path, workdir)
        exec_ok: bool = bool(execution.get("returncode") == 0)
        if not exec_ok and not errors and not compile_errors:
            errors.append(f"main.py exited {execution.get('returncode')}")
        failure: str = "; ".join(
            (compile_errors + errors + ([review_issues] if review_issues and not approved else []))[:6]
        ) or ("Reviewer rejected the swarm result." if not approved else "")
        status: str = "success" if (gate_ok and exec_ok and approved) else "failed"
        return {
            "agent_role": f"WorkerSwarm x{len(modules)}",
            "model_used": f"swarm:{len(modules)}workers",
            "status": status,
            "cost": total_cost,
            "score": 1.0 if status == "success" else 0.0,
            "critique_dict": None,
            "image_url": None,
            "artifact_path": str(workdir),
            "evidence_extra": {
                "code_path": str(main_path) if main_path.is_file() else "",
                "code_url": "",
                "code": main_path.read_text(encoding="utf-8") if main_path.is_file() else "",
                "returncode": execution.get("returncode"),
                "stdout": str(execution.get("stdout", ""))[:4000],
                "stderr": str(execution.get("stderr", ""))[:2000],
                "modules": [m.get("path") for m in modules],
                "gate_ok": gate_ok,
                "errors": errors[:6],
                "compile_errors": compile_errors[:6],
                "reviewer_approved": approved,
                "reviewer_issues": review_issues,
            },
            "failure_detail": failure,
        }

    async def _attempt_tournament(
        self,
        mission_id: str,
        attempt_number: int,
        goal: str,
        plan_text: str,
        attempt_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
        reference_path: Optional[Path],
        n_candidates: int,
    ) -> Dict[str, Any]:
        """Race N Engineers with different strategies; the best candidate wins."""
        if self.tournament is None:
            raise SwarmError("Tournament runner unavailable (no LLM client).")
        completed_count: int = 0
        total = max(1, n_candidates)

        def _progress(result: Dict[str, Any]) -> None:
            nonlocal completed_count
            completed_count += 1
            self.emit(
                mission_id,
                "partial",
                candidate_letter=result.get("letter"),
                strategy=result.get("strategy"),
                score=result.get("score"),
                cost=result.get("cost_aud"),
                lambda_completed=completed_count / total,
            )

        self._set_active(mission_id, ["Engineer", "Tester", "Critic"])
        run: Dict[str, Any] = await self.tournament.run(
            goal=goal,
            plan=plan_text,
            engineer_role=role_key,
            attempt_dir=attempt_dir,
            reference_path=reference_path,
            n_candidates=n_candidates,
            progress_callback=_progress,
        )
        candidates: List[Dict[str, Any]] = list(run.get("candidates", []))
        self._persist_tournament(mission_id, attempt_number, candidates)

        winner: Optional[Dict[str, Any]] = run.get("winner")
        winner_score: Optional[float] = run.get("winner_score")
        winner_code: Optional[str] = run.get("winner_code_path")
        exec_ok: bool = winner is not None and int(winner.get("returncode", -1)) == 0

        # Critique the winner's image against the reference if one exists.
        critique_dict: Optional[Dict[str, Any]] = None
        score: Optional[float] = winner_score
        winner_image: Optional[str] = run.get("winner_image_url")
        if winner is not None and winner.get("image_path"):
            critique_dict, critic_score = await self._maybe_critique(
                mission_id,
                exec_ok,
                reference_path,
                [Path(str(winner["image_path"]))],
            )
            if critic_score is not None:
                score = critic_score

        failure_detail: str = ""
        if winner is None:
            failure_detail = (
                "All "
                + str(len(candidates))
                + " tournament candidates failed to produce a working result."
            )

        return {
            "status": "success" if exec_ok else "failed",
            "cost": float(run.get("total_cost_aud", 0.0)),
            "score": score,
            "critique_dict": critique_dict,
            "image_url": winner_image,
            "artifact_path": winner_code,
            "evidence_extra": {
                "tournament": True,
                "code_path": winner_code,
                "winner_letter": winner.get("letter") if winner else None,
                "candidates": [
                    {
                        "letter": c.get("letter"),
                        "strategy": c.get("strategy"),
                        "returncode": c.get("returncode"),
                        "score": c.get("score"),
                        "exec_ms": c.get("exec_ms"),
                        "image_url": c.get("image_url"),
                        "selected": c.get("selected"),
                    }
                    for c in candidates
                ],
            },
            "failure_detail": failure_detail,
            "agent_role": "Tournament",
            "model_used": role_spec.id,
        }

    # K3 anti-hacking guard (§4.2.4): zero-cost static scan for "cheat the
    # metric" patterns — the model faking success instead of doing the work.
    # Runs on every code mission even when the LLM red-team is unavailable.
    _FAKING_PATTERNS: Tuple[Tuple[str, str], ...] = (
        (r"except\s*:\s*pass", "bare 'except: pass' swallows every error"),
        (r"except\s+Exception\s*(?:as\s+\w+)?\s*:\s*pass", "'except Exception: pass' swallows errors"),
        (r"assert\s+True\b", "'assert True' — vacuous test assertion"),
        (r"raise\s+NotImplementedError", "core logic stubbed with NotImplementedError"),
        (r"^\s*pass\s*#\s*(stub|todo|implement)", "stubbed-out function body"),
    )

    def _detect_faking(self, code_text: str) -> List[Dict[str, Any]]:
        """Static scan for success-faking patterns. Each hit becomes a failed
        red-team attack so the mission loops instead of shipping the fake."""
        attacks: List[Dict[str, Any]] = []
        for pattern, description in self._FAKING_PATTERNS:
            if re.search(pattern, code_text, flags=re.IGNORECASE | re.MULTILINE):
                attacks.append(
                    {"vector": f"faking: {description}", "passed": False}
                )
        return attacks

    async def _run_redteam(
        self,
        mission_id: str,
        attempt_number: int,
        goal: str,
        code_path_str: Optional[str],
    ) -> Dict[str, Any]:
        """Adversarially attack the produced code; record and return the review."""
        empty: Dict[str, Any] = {"attacks": [], "passed_all": True, "cost": 0.0}
        if not code_path_str:
            return empty
        code_path: Path = Path(code_path_str)
        if not code_path.is_file():
            return empty
        try:
            code_text: str = code_path.read_text(encoding="utf-8")
        except OSError:
            return empty
        self._set_active(mission_id, ["RedTeam"])
        # Static faking scan runs regardless of LLM red-team availability.
        faking_attacks: List[Dict[str, Any]] = self._detect_faking(code_text)
        if self.redteam is None:
            if faking_attacks:
                self._persist_redteam(mission_id, attempt_number, faking_attacks)
                return {
                    "attacks": faking_attacks,
                    "passed_all": False,
                    "failures": faking_attacks,
                    "cost": 0.0,
                }
            return empty
        try:
            review: Dict[str, Any] = await asyncio.to_thread(
                self.redteam.review, goal, code_text, code_path, code_path.parent
            )
        except Exception as exc:  # noqa: BLE001 - red-team must never crash a mission
            logger.error("Red-team failed for %s: %s", mission_id, exc)
            if faking_attacks:
                self._persist_redteam(mission_id, attempt_number, faking_attacks)
                return {
                    "attacks": faking_attacks,
                    "passed_all": False,
                    "failures": faking_attacks,
                    "cost": 0.0,
                }
            return empty
        attacks: List[Dict[str, Any]] = list(review.get("attacks", []))
        attacks.extend(faking_attacks)
        self._persist_redteam(mission_id, attempt_number, attacks)
        passed_all: bool = bool(review.get("passed_all", True)) and not faking_attacks
        return {
            "attacks": attacks,
            "passed_all": passed_all,
            "failures": list(review.get("failures", [])) + faking_attacks,
            "cost": float(review.get("total_cost_aud", 0.0)),
        }

    def _persist_tournament(
        self, mission_id: str, attempt_number: int, candidates: List[Dict[str, Any]]
    ) -> None:
        try:
            with self._connect() as connection:
                for c in candidates:
                    connection.execute(
                        """
                        INSERT INTO tournament_candidates
                            (id, mission_id, attempt, candidate_letter, strategy,
                             output_path, score, cost_aud, execution_time_ms, selected)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            mission_id,
                            attempt_number,
                            c.get("letter"),
                            c.get("strategy"),
                            c.get("code_path"),
                            c.get("score"),
                            c.get("cost_aud"),
                            c.get("exec_ms"),
                            bool(c.get("selected")),
                        ),
                    )
        except sqlite3.Error as exc:
            logger.error("Could not persist tournament for %s: %s", mission_id, exc)

    def _persist_redteam(
        self, mission_id: str, attempt_number: int, attacks: List[Dict[str, Any]]
    ) -> None:
        try:
            with self._connect() as connection:
                for a in attacks:
                    connection.execute(
                        """
                        INSERT INTO redteam_attacks
                            (id, mission_id, attempt, attack_vector, passed)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            mission_id,
                            attempt_number,
                            str(a.get("vector", ""))[:500],
                            bool(a.get("passed")),
                        ),
                    )
        except sqlite3.Error as exc:
            logger.error("Could not persist red-team for %s: %s", mission_id, exc)

    async def _attempt_vision(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        attachment_context: str,
        attempt_dir: Path,
        reference_path: Optional[Path],
    ) -> Dict[str, Any]:
        """Vision-in-the-Loop: generate, execute, critique, refine."""
        if self.critic is None:
            raise SwarmError("VisionLoop requires a CriticEngine.")
        loop = VisionLoop(
            critic=self.critic,
            generate_code=lambda prompt: self._call_agent("engineer", prompt, 4000),
            work_root=attempt_dir,
            score_threshold=self.pass_threshold,
            max_iterations=2,
        )
        result = await loop.refine(
            mission_id=mission_id,
            goal=goal,
            plan_text=plan_text,
            reference_path=reference_path,
            attachment_context=attachment_context,
        )
        artifact_path = Path(result["artifact_path"])
        code = result["code"]
        image_url = result.get("image_url")
        produced_images = [Path(image_url)] if image_url else []
        return {
            "status": result["status"],
            "cost": result["cost"],
            "score": result["score"],
            "critique_dict": result["critique_dict"],
            "image_url": self._image_url_for(produced_images) if produced_images else None,
            "artifact_path": str(artifact_path),
            "evidence_extra": {
                "code_path": str(artifact_path),
                "code_url": self._image_url_for([artifact_path]),
                "code": code[:20000],
                "returncode": 0 if result["status"] == "success" else -1,
                "stdout": "",
                "stderr": "",
                "local": False,
                "acceptance": {
                    "accepted": result["status"] == "success",
                    "confidence": result["score"] or 0.0,
                    "reasons": [],
                    "model": "",
                },
            },
            "failure_detail": (
                "" if result["status"] == "success" else "Vision loop did not reach target score."
            ),
            "agent_role": "Engineer (VisionLoop)",
            "model_used": self.router.get_spec("engineer").id,
        }

    async def _attempt_speculative(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        feedback: str,
        attachment_context: str,
        attempt_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
    ) -> Dict[str, Any]:
        """Draft with a fast model, verify with the target model."""
        coder = SpeculativeCoder(
            draft_call=lambda prompt: self._call_agent("worker", prompt, 4000),
            target_call=lambda prompt: self._call_agent(role_key, prompt, 4000),
            work_root=attempt_dir,
            accept_threshold=0.7,
        )
        result = await coder.generate(
            mission_id=mission_id,
            goal=goal,
            plan_text=plan_text,
            feedback=feedback,
        )
        artifact_path = Path(result["artifact_path"])
        code = result["code"]
        # Run through the normal execution path for full diagnostics
        code_path = attempt_dir / "main.py"
        code_path.write_text(code, encoding="utf-8")
        execution = await asyncio.to_thread(self._execute_code, code_path, attempt_dir)
        exec_ok = execution["returncode"] == 0
        return {
            "status": "success" if (result["status"] == "success" and exec_ok) else "failed",
            "cost": result["cost"],
            "score": None,
            "critique_dict": None,
            "image_url": None,
            "artifact_path": str(code_path),
            "evidence_extra": {
                "code_path": str(code_path),
                "code_url": self._image_url_for([code_path]),
                "code": code[:20000],
                "returncode": execution["returncode"],
                "stdout": execution["stdout"],
                "stderr": execution["stderr"],
                "local": False,
                "acceptance": {
                    "accepted": result["status"] == "success" and exec_ok,
                    "confidence": 1.0 if result.get("verified") else 0.7,
                    "reasons": [],
                    "model": f"draft:{self.router.get_spec('worker').id} target:{role_spec.id}",
                },
            },
            "failure_detail": (
                "" if result["status"] == "success" else "Speculative generation failed."
            ),
            "agent_role": "Engineer (Speculative)",
            "model_used": role_spec.id,
        }

    async def _attempt_image(
        self,
        mission_id: str,
        goal: str,
        attempt_dir: Path,
        reference_path: Optional[Path],
    ) -> Dict[str, Any]:
        """Artist council model generates a real image; Critic scores it."""
        self._set_active(mission_id, ["Artist"])
        artist_spec: ModelSpec = self.router.get_spec("artist")
        # Best-effort budget approval; image generation is priced ~0.
        try:
            self.cost_tracker.approve_call(artist_spec, max(1, len(goal) // 4), 1024)
        except Exception as exc:  # noqa: BLE001 - approval is advisory here
            logger.debug("Budget approval skipped for image gen: %s", exc)

        out_path: Path = attempt_dir / "out.png"
        status: str = "failed"
        failure_detail: str = ""
        cost_aud: float = 0.0
        try:
            result = await asyncio.to_thread(
                self.client.generate_image, goal, "1024x1024", artist_spec.id
            )
            cost_aud = self.router.calculate_cost(
                artist_spec, result["input_tokens"], result["output_tokens"]
            )
            image_b64: str = result.get("image_base64", "")
            if not image_b64:
                failure_detail = "Image model returned no image data: " + (
                    (result.get("text") or "")[:300] or "empty response"
                )
            else:
                await asyncio.to_thread(self.client.save_image, image_b64, out_path)
                status = "success"
        except (OpenRouterError, ValueError, OSError) as exc:
            failure_detail = f"Image generation failed: {exc}"
        except Exception as exc:  # noqa: BLE001 - mode must never crash the mission
            failure_detail = f"Unexpected image generation error: {exc}"

        produced_images: List[Path] = [out_path] if out_path.is_file() else []
        critique_dict, score = await self._maybe_critique(
            mission_id, status == "success", reference_path, produced_images
        )
        return {
            "status": status,
            "cost": cost_aud,
            "score": score,
            "critique_dict": critique_dict,
            "image_url": self._image_url_for(produced_images),
            "artifact_path": str(out_path) if produced_images else None,
            "evidence_extra": {
                "prompt": goal[:2000],
                "image_path": str(out_path) if produced_images else None,
            },
            "failure_detail": failure_detail or "Image generation produced no output.",
            "agent_role": "Artist",
            "model_used": artist_spec.id,
        }

    async def _attempt_3d(
        self,
        mission_id: str,
        goal: str,
        plan_text: str,
        feedback: str,
        attachment_context: str,
        attempt_dir: Path,
        role_key: str,
        role_spec: ModelSpec,
        reference_path: Optional[Path],
    ) -> Dict[str, Any]:
        """Engineer writes a bpy script; BlenderBridge renders it headless."""
        self._set_active(mission_id, ["Engineer"])
        bpy_prompt: str = (
            f"You are the Engineer agent writing a Blender script. Goal:\n{goal}\n\n"
            f"Plan:\n{plan_text}\n\n"
            "Write ONE complete, self-contained Python script using the `bpy` "
            "module that builds the described 3D scene: geometry, materials, "
            "lighting, and a framed camera. Do NOT set "
            "bpy.context.scene.render.filepath and do NOT call render — the host "
            "harness handles output and rendering. Respond with ONLY the script "
            "inside a single ```python code block."
            + (f"\n\nFix these problems from the last attempt: {feedback}" if feedback else "")
            + attachment_context
        )
        script_text, script_cost = await asyncio.to_thread(
            self._call_agent, role_key, bpy_prompt, 4000
        )
        script: str = self._extract_code(script_text)
        script_path: Path = attempt_dir / "scene.py"
        try:
            script_path.write_text(script, encoding="utf-8")
        except OSError as exc:
            logger.error("Could not write bpy script for %s: %s", mission_id, exc)

        self._set_active(mission_id, ["Tester"])
        out_path: Path = attempt_dir / "out.png"
        status: str = "failed"
        failure_detail: str = ""
        scene_validation: Dict[str, Any] = {}
        try:
            bridge: BlenderBridge = BlenderBridge()
            await asyncio.to_thread(bridge.run_script, script, out_path)
            # A rendered PNG alone is not enough: capture the generated scene
            # and require actual geometry plus an explicit camera before
            # accepting the 3D attempt.
            graph = await asyncio.to_thread(bridge.capture_scene, script)
            mesh_count = sum(1 for node in graph.nodes if node.type == "MESH" and node.visible)
            camera_count = sum(1 for node in graph.nodes if node.type == "CAMERA" and node.visible)
            light_count = sum(1 for node in graph.nodes if node.type == "LIGHT" and node.visible)
            scene_validation = {
                "objects": len(graph.nodes),
                "meshes": mesh_count,
                "cameras": camera_count,
                "lights": light_count,
                "valid": mesh_count > 0 and camera_count > 0,
            }
            if not scene_validation["valid"]:
                failure_detail = (
                    "3D scene validation failed: expected at least one visible mesh "
                    "and camera."
                )
            else:
                status = "success"
        except BlenderBridgeError as exc:
            failure_detail = f"Blender render failed: {exc}"
        except Exception as exc:  # noqa: BLE001 - mode must never crash the mission
            failure_detail = f"Unexpected Blender error: {exc}"

        produced_images: List[Path] = [out_path] if out_path.is_file() else []
        critique_dict, score = await self._maybe_critique(
            mission_id, status == "success", reference_path, produced_images, "3d render"
        )
        return {
            "status": status,
            "cost": script_cost,
            "score": score,
            "critique_dict": critique_dict,
            "image_url": self._image_url_for(produced_images),
            "artifact_path": str(script_path),
            "evidence_extra": {
                "script_path": str(script_path),
                "render_path": str(out_path) if produced_images else None,
                "scene_validation": scene_validation,
            },
            "failure_detail": failure_detail or "Blender produced no output.",
            "agent_role": "Engineer",
            "model_used": role_spec.id,
        }

    async def _attempt_video(
        self,
        mission_id: str,
        goal: str,
        attempt_dir: Path,
        start_path: Optional[Path],
        end_path: Optional[Path],
    ) -> Dict[str, Any]:
        """Generate a frame-to-frame clip and persist it as mission evidence."""
        self._set_active(mission_id, ["Artist", "Video"])
        out_path = attempt_dir / "out.mp4"
        if self.providers is None or not hasattr(self.providers, "generate_video"):
            return {
                "status": "failed",
                "cost": 0.0,
                "score": None,
                "critique_dict": None,
                "image_url": None,
                "artifact_path": None,
                "evidence_extra": {"media_type": "video", "video_error": "FAL video provider is unavailable."},
                "failure_detail": "Video provider is unavailable. Configure FAL.AI in Settings → Providers.",
                "agent_role": "Artist (Video)",
                "model_used": "fal.ai",
            }
        if start_path is None:
            return {
                "status": "failed",
                "cost": 0.0,
                "score": None,
                "critique_dict": None,
                "image_url": None,
                "artifact_path": None,
                "evidence_extra": {"media_type": "video", "video_error": "No start frame supplied."},
                "failure_detail": "Video mode needs a start-frame image. Drop one into the composer.",
                "agent_role": "Artist (Video)",
                "model_used": "fal.ai",
            }
        try:
            result = await asyncio.to_thread(
                self.providers.generate_video,
                goal,
                start_path,
                end_path,
                out_path,
            )
            produced = [out_path] if out_path.is_file() else []
            return {
                "status": "success" if produced else "failed",
                "cost": 0.0,
                "score": None,
                "critique_dict": None,
                "image_url": self._image_url_for(produced),
                "artifact_path": str(out_path) if produced else None,
                "evidence_extra": {
                    "media_type": "video",
                    "video_model": result.get("model") if isinstance(result, dict) else None,
                    "video_request_id": result.get("request_id") if isinstance(result, dict) else None,
                },
                "failure_detail": "FAL returned no video output.",
                "agent_role": "Artist (Video)",
                "model_used": "fal.ai",
            }
        except Exception as exc:  # noqa: BLE001 - surface provider failures in evidence
            logger.warning("Video generation failed for %s: %s", mission_id, exc)
            return {
                "status": "failed",
                "cost": 0.0,
                "score": None,
                "critique_dict": None,
                "image_url": None,
                "artifact_path": None,
                "evidence_extra": {"media_type": "video", "video_error": str(exc)[:500]},
                "failure_detail": f"Video generation failed: {str(exc)[:500]}",
                "agent_role": "Artist (Video)",
                "model_used": "fal.ai",
            }

    # ------------------------------------------------------------------ #
    # Mission lifecycle
    # ------------------------------------------------------------------ #

    def spawn(self, mission_id: str) -> None:
        """Start the mission loop as a background asyncio task. The task waits
        on the concurrency semaphore, so a burst queues instead of stampeding."""
        try:
            task: "asyncio.Task[None]" = asyncio.get_running_loop().create_task(
                self._run_mission_bounded(mission_id)
            )
        except RuntimeError as exc:
            raise SwarmError(f"spawn() requires a running event loop: {exc}") from exc
        self._tasks[mission_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(mission_id, None))

    async def _run_mission_bounded(self, mission_id: str) -> None:
        """Gate _run_mission behind the concurrency semaphore."""
        if self._run_semaphore is None:
            # Safe to create here: we're already on the running loop.
            self._run_semaphore = asyncio.Semaphore(self.max_concurrent_missions)
        async with self._run_semaphore:
            await self._run_mission(mission_id)

    def cancel(self, mission_id: str) -> bool:
        """Stop a running mission and mark it failed('cancelled'). Returns True
        if the row was updated (whether or not a live task was found — a
        selected-but-orphaned mission is still cancellable from the UI)."""
        task = self._tasks.get(mission_id)
        if task is not None and not task.done():
            task.cancel()
        self._set_active(mission_id, [])
        try:
            evidence = json.dumps(
                {"failure_reason": "Cancelled by you.", "attempts": []}
            )
            with self._connect() as connection:
                cur = connection.execute(
                    "UPDATE missions SET status='failed', "
                    "completed_at=?, "
                    "evidence_json=CASE WHEN evidence_json IS NULL OR evidence_json IN ('', '{}') "
                    "THEN ? ELSE evidence_json END "
                    "WHERE id=? AND status IN ('running', 'queued')",
                    (datetime.now(timezone.utc).isoformat(), evidence, mission_id),
                )
                return (cur.rowcount or 0) > 0
        except sqlite3.Error as exc:
            logger.error("Could not cancel mission %s: %s", mission_id, exc)
            return False

    async def _run_mission(self, mission_id: str) -> None:
        mission: Optional[Dict[str, Any]] = self._get_mission(mission_id)
        if mission is None:
            logger.error("Mission %s not found; nothing to run.", mission_id)
            return

        mission_dir: Path = self.outputs_dir / mission_id
        attempts_evidence: List[Dict[str, Any]] = []
        total_cost_aud: float = 0.0
        final_status: str = "failed"
        failure_reason: str = ""
        applied_drills: List[Dict[str, Any]] = []
        drill_context: str = ""

        try:
            mission_dir.mkdir(parents=True, exist_ok=True)
            self._update_mission(mission_id, status="running")

            goal: str = str(mission.get("goal", ""))
            drill_context, applied_drills = self.curriculum.context_for(goal)
            if applied_drills:
                self.emit(
                    mission_id,
                    "status",
                    text="Retrieved benchmark drills: " + ", ".join(str(item["id"]) for item in applied_drills),
                )
            reference_raw: Optional[str] = mission.get("reference_image_path")
            reference_path: Optional[Path] = (
                Path(reference_raw) if reference_raw else None
            )

            # --- Per-mission knobs (mode/effort/fast/attachments/tools) --- #
            params: Dict[str, Any] = self._parse_params(mission.get("params_json"))
            mode: str = str(params["mode"])
            # BFB missions route every role through the DeepSeek-first council.
            if mode == "bfb":
                self._bfb_missions.add(mission_id)
                if self._bfb_router is None:
                    self._bfb_router = ModelRouter(build_council(mode="bfb"))
            # Video mode can run directly through the configured FAL provider,
            # so it remains usable when no OpenRouter planning key is present.
            # All other modes still execute model-backed swarm agents below.
            if self.client is None and mode != "video":
                raise SwarmError(
                    "No LLM provider key configured - the swarm cannot call any model (open Settings > Providers)."
                )
            effort: str = str(params["effort"])
            attempts_count, role_key = self._effort_plan(effort, bool(params["fast"]))

            # Enforce the tools allow-list BEFORE spending anything: a
            # non-empty list restricts the mission to the capability it needs.
            # Empty/unset keeps the historical behaviour (all tools allowed).
            allowed_tools: List[str] = params["tools"]
            if allowed_tools:
                required_tool = _MODE_REQUIRED_TOOL.get(mode, _DEFAULT_REQUIRED_TOOL)
                if required_tool not in allowed_tools:
                    raise SwarmError(
                        f"Mode '{mode}' needs the '{required_tool}' tool, but this "
                        f"mission only allows: {', '.join(allowed_tools)}. "
                        "Add the tool in the mission's tool picker and run it again."
                    )

            # Routing context for every _call_agent inside this mission (the
            # contextvar propagates through asyncio.to_thread workers).
            _CURRENT_MISSION.set(mission_id)
            crew_brief, crew_cost, crew_names = await self._run_selected_crew(
                mission_id, goal, params["agents"]
            )
            total_cost_aud += crew_cost
            if crew_names:
                self.emit(
                    mission_id,
                    "status",
                    text=(
                        "Selected crew briefed: " + ", ".join(crew_names)
                        + ("; core swarm will integrate it." if params["combine_with_default_swarm"]
                           else "; executing from the selected crew brief.")
                    ),
                )
            # Vibe coder: register the mission and probe the local big brain
            # ONCE up front — the cached result is reused for every role call.
            if effort == "vibe":
                self._vibe_missions.add(mission_id)
                try:
                    self._vibe_local[mission_id] = await asyncio.to_thread(
                        self._local_big_engine
                    )
                except Exception:  # noqa: BLE001 - probe failure = cloud-only
                    self._vibe_local[mission_id] = None
                self.emit(
                    mission_id,
                    "status",
                    text=(
                        "Vibe coder: local 35B online — qwen + kimi/glm mix"
                        if self._vibe_local.get(mission_id)
                        else "Vibe coder: local 35B offline — kimi swarm + "
                        "minimax m3 + glm-5-flash orchestration"
                    ),
                )

            # --- Predictive cost routing (auto mode): a cheap model decides
            #     whether this needs the expensive architect or a cheaper role.
            if (
                mode == "auto"
                and not bool(params["fast"])
                and self.predictive is not None
            ):
                try:
                    routed = await asyncio.to_thread(
                        self.predictive.route_role, goal, role_key
                    )
                    role_key = str(routed["role"])
                    total_cost_aud += float(routed.get("cost_aud", 0.0))
                    logger.info(
                        "Predictive routing for %s: %s -> %s",
                        mission_id,
                        routed.get("predicted"),
                        role_key,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Predictive routing failed for %s: %s", mission_id, exc)

            role_spec: ModelSpec = self.router.get_spec(role_key)
            # "Infinity Code" effort runs a parallel candidate tournament. The
            # tournament IS the parallel exploration, so it runs once rather
            # than looping the whole tournament every attempt.
            is_bfb_mission: bool = mission_id in self._bfb_missions
            # BFB missions escalate: high -> speculative, xhigh/max -> tournament.
            use_tournament: bool = (
                (effort == "ultracode" or (is_bfb_mission and effort in ("xhigh", "max")))
                and self.tournament is not None
            )
            use_vision_loop: bool = bool(params.get("vision_loop", False)) and self.critic is not None
            use_speculative: bool = (
                bool(params.get("speculative", False))
                or (is_bfb_mission and effort == "high" and not use_tournament)
            )
            use_parallel_candidates: bool = (
                mode in ("auto", "code")
                and not use_tournament
                and not use_vision_loop
                and not use_speculative
                and not bool(params.get("fast"))
                and (is_bfb_mission or effort in ("med", "high", "xhigh", "max", "ultracode"))
            )
            if use_tournament:
                attempts_count = 1
            attachment_context: str = self._build_attachment_context(
                params["attachments"]
            )
            feedback: str = ""
            redteam_retries: int = 0

            for attempt_number in range(1, attempts_count + 1):
                attempt_dir: Path = mission_dir / f"attempt_{attempt_number}"
                attempt_dir.mkdir(parents=True, exist_ok=True)

                # --- Director: plan the attempt (stays on worker) -------- #
                self._set_active(mission_id, ["Director"])
                plan_hint: str = {
                    "image": (
                        "a single vivid image-generation prompt that fulfils the goal"
                    ),
                    "video": (
                        "a concise image-to-video direction with motion, camera, and timing"
                    ),
                    "3d": (
                        "a single self-contained Blender bpy script that builds the "
                        "described 3D scene"
                    ),
                }.get(
                    mode,
                    "a SINGLE self-contained Python script using only the standard "
                    "library plus matplotlib/PIL if needed",
                )
                plan_prompt: str = (
                    f"You are the Director agent. Goal:\n{goal}\n\n"
                    f"Produce a numbered plan (max 6 steps) for solving this with "
                    f"{plan_hint}. Plan only, no code."
                    + (
                        "\n\nSelected crew recommendations to integrate:\n"
                        + crew_brief
                        if crew_brief
                        else ""
                    )
                    + (f"\n\nPrevious attempt feedback: {feedback}" if feedback else "")
                    + drill_context
                )
                # The plan is optional context. A transient model/provider
                # failure here must NOT kill the mission — image mode ignores
                # the plan entirely, and code/3d mode can work from the goal.
                # Fast (turbo) mode skips planning entirely to go straight to
                # the Engineer.
                plan_text: str = crew_brief
                plan_cost = 0.0
                if not bool(params["fast"]) and (
                    not crew_brief or bool(params["combine_with_default_swarm"])
                ):
                    try:
                        director_plan, plan_cost = await asyncio.to_thread(
                            self._call_agent, "worker", plan_prompt, 800
                        )
                        if crew_brief:
                            plan_text = (
                                "Selected crew recommendations:\n"
                                + crew_brief
                                + "\n\nCore-swarm plan:\n"
                                + director_plan
                            )
                        else:
                            plan_text = director_plan
                        total_cost_aud += plan_cost
                    except (SwarmError, OpenRouterError) as exc:
                        logger.warning(
                            "Director plan step failed for %s (continuing without a "
                            "plan): %s",
                            mission_id,
                            exc,
                        )
                if drill_context:
                    plan_text += drill_context

                # --- Branch on mode -------------------------------------- #
                if mode == "image":
                    result: Dict[str, Any] = await self._attempt_image(
                        mission_id,
                        goal
                        + ("\n\nSelected crew recommendations:\n" + crew_brief if crew_brief else "")
                        + drill_context,
                        attempt_dir,
                        reference_path,
                    )
                elif mode == "video":
                    end_reference_raw = str(params.get("end_reference_image_path") or "").strip()
                    end_reference_path = Path(end_reference_raw) if end_reference_raw else None
                    result = await self._attempt_video(
                        mission_id,
                        goal,
                        attempt_dir,
                        reference_path,
                        end_reference_path,
                    )
                elif mode == "3d":
                    result = await self._attempt_3d(
                        mission_id,
                        goal,
                        plan_text,
                        feedback,
                        attachment_context,
                        attempt_dir,
                        role_key,
                        role_spec,
                        reference_path,
                    )
                elif use_tournament:  # "Infinity Code" effort: parallel tournament
                    result = await self._attempt_tournament(
                        mission_id,
                        attempt_number,
                        goal,
                        plan_text,
                        attempt_dir,
                        role_key,
                        role_spec,
                        reference_path,
                        max(1, min(20, self.tournament_candidates)),
                    )
                elif use_vision_loop:
                    result = await self._attempt_vision(
                        mission_id,
                        goal,
                        plan_text,
                        attachment_context,
                        attempt_dir,
                        reference_path,
                    )
                elif use_speculative:
                    result = await self._attempt_speculative(
                        mission_id,
                        goal,
                        plan_text,
                        feedback,
                        attachment_context,
                        attempt_dir,
                        role_key,
                        role_spec,
                    )
                elif bool(params.get("fast")):
                    # K3 Partial Rollout RL — fast execution with early stopping
                    result = await self._attempt_fast(
                        mission_id,
                        goal,
                        plan_text,
                        feedback,
                        attachment_context,
                        attempt_dir,
                        role_key,
                        role_spec,
                        reference_path,
                    )
                elif effort == "swarm" and mode in ("auto", "code"):
                    result = await self._attempt_worker_swarm(
                        mission_id,
                        attempt_number,
                        goal,
                        plan_text,
                        attempt_dir,
                        reference_path,
                    )
                elif use_parallel_candidates:
                    result = await self._attempt_parallel_candidates(
                        mission_id,
                        attempt_number,
                        goal,
                        plan_text,
                        attempt_dir,
                        role_key,
                        role_spec,
                        reference_path,
                        n_candidates=3,
                    )
                else:  # "auto" or "code" — the original code-writing path
                    result = await self._attempt_code(
                        mission_id,
                        goal,
                        plan_text,
                        feedback,
                        attachment_context,
                        attempt_dir,
                        role_key,
                        role_spec,
                        reference_path,
                        prefer_local=False,
                    )

                total_cost_aud += float(result["cost"])

                # --- Red-team: attack the produced code before it ships ---- #
                redteam_summary: Optional[Dict[str, Any]] = None
                if mode in ("auto", "code"):
                    review = await self._run_redteam(
                        mission_id,
                        attempt_number,
                        goal,
                        result.get("evidence_extra", {}).get("code_path"),
                    )
                    total_cost_aud += float(review.get("cost", 0.0))
                    if review.get("attacks"):
                        redteam_summary = {
                            "passed_all": review["passed_all"],
                            "total": len(review["attacks"]),
                            "failed": len(review.get("failures", [])),
                            "attacks": [
                                {"vector": a.get("vector"), "passed": a.get("passed")}
                                for a in review["attacks"]
                            ],
                        }
                score: Optional[float] = result["score"]
                critique_dict: Optional[Dict[str, Any]] = result["critique_dict"]

                self._record_attempt(
                    mission_id=mission_id,
                    agent_role=str(result["agent_role"]),
                    model_used=str(result["model_used"]),
                    status=str(result["status"]),
                    cost_aud=plan_cost + float(result["cost"]),
                    artifact_path=result["artifact_path"],
                    critique_score=score,
                    critique_feedback=(
                        "; ".join(critique_dict.get("fixes", []))
                        if critique_dict
                        else None
                    ),
                )

                evidence_entry: Dict[str, Any] = {
                    "attempt": attempt_number,
                    "mode": mode,
                    "plan": plan_text[:2000],
                    "image_url": result["image_url"],
                    "critique": critique_dict,
                    "cost_aud": round(plan_cost + float(result["cost"]), 6),
                    "drills_applied": [
                        {"id": item["id"], "task_id": item["task_id"], "capability": item["capability"]}
                        for item in applied_drills
                    ],
                }
                evidence_entry.update(result["evidence_extra"])
                if redteam_summary is not None:
                    evidence_entry["redteam"] = redteam_summary
                attempts_evidence.append(evidence_entry)

                passed: bool = result["status"] == "success" and (
                    score is None or score >= self.pass_threshold
                )

                # Hardening loop (bounded): if the work otherwise passed but the
                # red-team broke it, send it back ONCE to be fixed rather than
                # shipping known-vulnerable code. Capped at one hardening retry
                # so an aggressive red-team can't loop the mission forever, and
                # skipped for tournaments (which already explored 5 approaches).
                if (
                    passed
                    and not use_tournament
                    and redteam_summary is not None
                    and not redteam_summary["passed_all"]
                    and redteam_retries < 1
                    and attempt_number < attempts_count
                ):
                    redteam_retries += 1
                    passed = False
                    vectors = "; ".join(
                        str(a["vector"])
                        for a in redteam_summary["attacks"]
                        if not a["passed"]
                    )
                    feedback = f"Red-team broke the code. Fix these: {vectors}"
                    continue

                if passed:
                    final_status = "completed"
                    break

                if result["status"] != "success":
                    feedback = str(result["failure_detail"])
                elif critique_dict is not None:
                    feedback = (
                        f"Critique scored {score:.2f} (< {self.pass_threshold}). "
                        "Fixes: " + "; ".join(critique_dict.get("fixes", []))
                    )
                else:
                    feedback = "Attempt produced no verifiable output."

            if final_status != "completed":
                failure_reason = feedback or "All attempts exhausted."

        except (SwarmError, OpenRouterError) as exc:
            failure_reason = str(exc)
            logger.error("Mission %s failed: %s", mission_id, exc)
        except Exception as exc:  # noqa: BLE001 - mission must never crash the server
            failure_reason = f"Unexpected swarm error: {exc}"
            logger.exception("Mission %s crashed", mission_id)
        finally:
            # --- Scribe: persist the evidence trail -------------------- #
            self._set_active(mission_id, ["Scribe"])
            evidence: Dict[str, Any] = {
                "attempts": attempts_evidence,
                "final_status": final_status,
                "failure_reason": failure_reason,
                "summary": (
                    f"{len(attempts_evidence)} attempt(s); final status: {final_status}."
                ),
                "drill_trace": {
                    "applied": [
                        {"id": item["id"], "task_id": item["task_id"], "failure_reason": item["failure_reason"]}
                        for item in applied_drills
                    ],
                    "outcome": final_status,
                },
            }
            self.curriculum.record_application(
                [str(item["id"]) for item in applied_drills], mission_id, final_status
            )
            try:
                (mission_dir / "EVIDENCE.json").write_text(
                    json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except OSError as exc:
                logger.error("Could not write EVIDENCE.json for %s: %s", mission_id, exc)
            self._update_mission(
                mission_id,
                status=final_status,
                completed_at=datetime.now(timezone.utc).isoformat(),
                total_cost_aud=round(total_cost_aud, 6),
                output_path=str(mission_dir),
                evidence_json=json.dumps(evidence, ensure_ascii=False),
            )
            self._set_active(mission_id, [])
            self._vibe_missions.discard(mission_id)
            self._vibe_local.pop(mission_id, None)
            # Keep the finished mission's role map visible to late-joining WS
            # clients, but bound total memory: evict oldest beyond 200 entries.
            while len(self._role_models) > 200:
                self._role_models.pop(next(iter(self._role_models)))
            logger.info(
                "Mission %s finished: %s ($%.4f AUD)",
                mission_id,
                final_status,
                total_cost_aud,
            )


__all__ = ["AgentSwarm", "SwarmError", "init_database", "AGENT_ROLES"]
