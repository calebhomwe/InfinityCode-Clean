"""Ascension Engine for Infinity Code X.

Deterministic state machine governing which models may be used in which
form. Transition logic is pure (unit-testable, no I/O); all I/O (JSONL
append) happens under a single thread-safe method.

States: X_CODE(0) -> SS1 -> SS2 -> SS3 -> BLUE -> MR_X_FINAL(5).
Rules: one-step ascension, dwell time (anti-flicker), cooldown
(anti-spam), role locks, form locks, owner override (passphrase-gated).
MR X FINAL additionally requires owner confirmation via override().
"""

from __future__ import annotations

import enum
import hmac
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------

class AscensionState(enum.IntEnum):
    X_CODE = 0
    SS1 = 1
    SS2 = 2
    SS3 = 3
    BLUE = 4
    MR_X_FINAL = 5


FORM_LABELS: Dict[AscensionState, str] = {
    AscensionState.X_CODE: "X Code",
    AscensionState.SS1: "SS1",
    AscensionState.SS2: "SS2",
    AscensionState.SS3: "SS3",
    AscensionState.BLUE: "Blue",
    AscensionState.MR_X_FINAL: "Mr X Final",
}

# Approved model list per form: strict superset as the form rises.
FORM_MODELS: Dict[AscensionState, Tuple[str, ...]] = {
    AscensionState.X_CODE: ("dashscope/qwen-turbo",),
    AscensionState.SS1: (
        "dashscope/qwen-turbo",
        "dashscope/qwen3.7-flash",
    ),
    AscensionState.SS2: (
        "dashscope/qwen-turbo",
        "dashscope/qwen3.7-flash",
        "dashscope/qwen-coder-plus",
    ),
    AscensionState.SS3: (
        "dashscope/qwen-turbo",
        "dashscope/qwen3.7-flash",
        "dashscope/qwen-coder-plus",
        "dashscope/qwen-plus",
    ),
    AscensionState.BLUE: (
        "dashscope/qwen-turbo",
        "dashscope/qwen3.7-flash",
        "dashscope/qwen-coder-plus",
        "dashscope/qwen-plus",
        "dashscope/qwen-max",
        "dashscope/qwen3.8-max",
    ),
    AscensionState.MR_X_FINAL: (
        "dashscope/qwen-turbo",
        "dashscope/qwen3.7-flash",
        "dashscope/qwen-coder-plus",
        "dashscope/qwen-plus",
        "dashscope/qwen-max",
        "dashscope/qwen3.8-max",
        "local/fable-max-llamacpp",
    ),
}

# Role locks: a model may only be assigned to roles in its list.
MODEL_ROLES: Dict[str, Tuple[str, ...]] = {
    "dashscope/qwen-turbo": ("free_router", "chat", "syntax"),
    "dashscope/qwen3.7-flash": ("coder", "debugger", "diagnostician", "tester"),
    "dashscope/qwen-coder-plus": ("coder", "debugger", "tester"),
    "dashscope/qwen-plus": ("planner", "memory", "sequencer"),
    "dashscope/qwen-max": ("researcher", "evidence", "swarm_tester", "commander", "architect", "long_horizon"),
    # Qwen 3.8 Max is the oracle: it SEES and ADVISES, never drafts.
    "dashscope/qwen3.8-max": ("oracle", "verifier", "reviewer"),
    "local/fable-max-llamacpp": ("advisor",),
}

# Cost tier + which env keys unlock each model.
_MODEL_META: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    # model_id -> (swarm role label, cost tier, env keys)
    "dashscope/qwen-turbo": ("free_router", "free", ("DASHSCOPE_API_KEY",)),
    "dashscope/qwen3.7-flash": ("coder", "cheap", ("DASHSCOPE_API_KEY",)),
    "dashscope/qwen-coder-plus": ("coder", "cheap", ("DASHSCOPE_API_KEY",)),
    "dashscope/qwen-plus": ("planning", "cheap", ("DASHSCOPE_API_KEY",)),
    "dashscope/qwen-max": ("commander", "mid", ("DASHSCOPE_API_KEY",)),
    "dashscope/qwen3.8-max": ("oracle", "high", ("DASHSCOPE_API_KEY",)),
    "local/fable-max-llamacpp": ("advisor", "free", ("INFINITY_FABLE_PORT",)),
}

# Display names for the Council UI (model id -> brand label).
_MODEL_LABELS: Dict[str, str] = {
    "dashscope/qwen-turbo": "Qwen Turbo",
    "dashscope/qwen3.7-flash": "Qwen 3.7 Flash",
    "dashscope/qwen-coder-plus": "Qwen Coder Plus",
    "dashscope/qwen-plus": "Qwen Plus",
    "dashscope/qwen-max": "Qwen Max",
    "dashscope/qwen3.8-max": "Qwen 3.8 Max",
    "local/fable-max-llamacpp": "Local FABLE",
}

DEFAULT_PASSPHRASE = "infinity-x"  # dev default; override via config/env


# --------------------------------------------------------------------------
# Scores (pure functions)
# --------------------------------------------------------------------------

@dataclass
class SpeedInputs:
    tokens_per_second: float = 0.0
    p95_latency_ms: float = 0.0
    queue_responsive: bool = True
    model_available: bool = True
    cost_efficiency: float = 1.0  # 0..1 where 1 = free/cheapest


@dataclass
class EffortInputs:
    failed_tests: int = 0
    retries: int = 0
    task_complexity_0_10: float = 0.0
    repo_size_bytes: int = 0
    dependency_complexity_0_10: float = 0.0
    benchmark_gap_0_100: float = 0.0
    coordination_load_0_10: float = 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def speed_score(inp: SpeedInputs) -> float:
    """0-100; higher is better. Weighted: tps 30%, p95 25%, queue 15%,
    availability 10%, cost efficiency 20%. No throughput data -> 0."""
    if inp is None or (inp.tokens_per_second or 0.0) <= 0.0:
        return 0.0
    tps_n = _clamp01(inp.tokens_per_second / 200.0)
    lat_n = _clamp01(1.0 - inp.p95_latency_ms / 5000.0)
    queue_n = 1.0 if inp.queue_responsive else 0.0
    avail_n = 1.0 if inp.model_available else 0.0
    cost_n = _clamp01(inp.cost_efficiency)
    return round(
        100.0 * (0.30 * tps_n + 0.25 * lat_n + 0.15 * queue_n
                 + 0.10 * avail_n + 0.20 * cost_n), 2)


def effort_score(inp: EffortInputs) -> float:
    """0-100; higher = heavier task. Weighted: tests 20%, retries 10%,
    complexity 20%, repo 10%, deps 10%, benchmark gap 20%, coordination 10%."""
    if inp is None:
        return 0.0
    tests_n = (inp.failed_tests or 0) / ((inp.failed_tests or 0) + 5)
    retry_n = (inp.retries or 0) / ((inp.retries or 0) + 3)
    comp_n = _clamp01((inp.task_complexity_0_10 or 0.0) / 10.0)
    repo_n = _clamp01(math.log1p(inp.repo_size_bytes or 0)
                      / math.log1p(2 ** 31))
    dep_n = _clamp01((inp.dependency_complexity_0_10 or 0.0) / 10.0)
    gap_n = _clamp01((inp.benchmark_gap_0_100 or 0.0) / 100.0)
    coord_n = _clamp01((inp.coordination_load_0_10 or 0.0) / 10.0)
    return round(
        100.0 * (0.20 * tests_n + 0.10 * retry_n + 0.20 * comp_n
                 + 0.10 * repo_n + 0.10 * dep_n + 0.20 * gap_n
                 + 0.10 * coord_n), 2)


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

def _env_has(*names: str) -> bool:
    return any(bool(os.environ.get(n)) for n in names)


class AscensionEngine:
    """Thread-safe deterministic state machine for form selection."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        log_path: Optional[str] = None,
        passphrase: Optional[str] = None,
        clock=time.time,
    ) -> None:
        cfg = config or {}
        self._clock = clock
        self._lock = threading.Lock()
        self.state: AscensionState = AscensionState.X_CODE
        self._last_change: float = self._clock()
        self.dwell_s: float = float(cfg.get("dwell_s", 45))
        self.cooldown_s: float = float(cfg.get("cooldown_s", 180))
        self._passphrase: str = (
            passphrase
            or os.environ.get("INFINITY_OWNER_PASSPHRASE")
            or str(cfg.get("owner_passphrase", DEFAULT_PASSPHRASE))
        )
        default_log = Path(__file__).resolve().parent.parent / "data" / "ascension_log.jsonl"
        self._log_path: Path = Path(log_path) if log_path else default_log
        self.speed: SpeedInputs = SpeedInputs()
        self.effort: EffortInputs = EffortInputs()
        logger.info("Ascension Engine ready (dwell=%ss, cooldown=%ss)",
                    self.dwell_s, self.cooldown_s)

    # -- registry helpers ---------------------------------------------------

    def model_meta(self, model_id: str) -> Optional[Dict[str, Any]]:
        meta = _MODEL_META.get(model_id)
        if meta is None:
            return None
        role, tier, keys = meta
        available = _env_has(*keys) if keys else True
        return {"id": model_id, "role": role, "tier": tier,
                "available": available, "locked": not available}

    def approved_models(self, state: Optional[AscensionState] = None) -> Tuple[str, ...]:
        return FORM_MODELS[state if state is not None else self.state]

    def model_cards(self) -> List[Dict[str, Any]]:
        """Full swarm roster for the Council UI (all known models, locked too)."""
        cards: List[Dict[str, Any]] = []
        for mid in _MODEL_META:
            meta = self.model_meta(mid)
            if meta is None:
                continue
            card = dict(meta)
            card["label"] = _MODEL_LABELS.get(mid, mid)
            card["forms"] = [FORM_LABELS[s] for s in AscensionState
                             if mid in FORM_MODELS[s]]
            card["min_form"] = card["forms"][0] if card["forms"] else ""
            cards.append(card)
        return cards

    def available_models(self, state: Optional[AscensionState] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for mid in FORM_MODELS[state if state is not None else self.state]:
            meta = self.model_meta(mid)
            if meta is not None:
                out.append(meta)
        return out

    # -- guardrails ---------------------------------------------------------

    def _remaining(self, now: float, window_s: float) -> float:
        return max(0.0, self._last_change + window_s - now)

    def _can_change(self, now: float) -> Tuple[bool, Optional[str]]:
        if now - self._last_change < self.cooldown_s:
            return False, "cooldown"
        if now - self._last_change < self.dwell_s:
            return False, "dwell"
        return True, None

    # -- transitions --------------------------------------------------------

    def escalate(self, reason: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        """One step up, subject to guardrails. MR X FINAL requires owner."""
        with self._lock:
            t = now if now is not None else self._clock()
            if self.state is AscensionState.MR_X_FINAL:
                return self._result(False, "already_max", t)
            target = AscensionState(int(self.state) + 1)
            if target is AscensionState.MR_X_FINAL:
                return self._result(False, "owner_gate", t,
                                    detail="MR X FINAL requires owner override")
            ok, blocked = self._can_change(t)
            if not ok:
                return self._result(False, blocked, t)
            return self._apply(target, "ascend", reason, t)

    def deescalate(self, reason: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        """One step down; allowed while cooling down (safety valve)."""
        with self._lock:
            t = now if now is not None else self._clock()
            target = AscensionState(int(self.state) - 1)
            if target < AscensionState.X_CODE:
                return self._result(False, "already_min", t)
            if t - self._last_change < self.dwell_s:
                return self._result(False, "dwell", t)
            return self._apply(target, "deescalate", reason, t)

    def override(self, target: str, passphrase: str,
                 reason: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        """Owner override: jump to any form. Passphrase-gated; bypasses
        one-step, dwell and cooldown. Wrong passphrase -> blocked + logged."""
        with self._lock:
            t = now if now is not None else self._clock()
            try:
                target_state = AscensionState[str(target).upper().replace(" ", "_")]
            except (KeyError, ValueError):
                return self._result(False, "bad_target", t, detail=f"unknown form {target!r}")
            if not hmac.compare_digest(str(passphrase or ""), self._passphrase):
                self._append("blocked", t, blocked_by="passphrase",
                             detail="owner override with wrong passphrase",
                             actor="unknown")
                return self._result(False, "passphrase", t)
            if target_state is self.state:
                return self._result(True, None, t, owner_override=True,
                                    detail="already in target form")
            return self._apply(target_state, "override", reason, t, owner_override=True)

    # -- assignments --------------------------------------------------------

    def assign(self, model_id: str, role: str, task: str = "") -> Dict[str, Any]:
        """Validate form lock + role lock + availability, log, return result."""
        with self._lock:
            t = self._clock()
            if model_id not in FORM_MODELS[self.state]:
                return self._result(False, "form_lock", t,
                                    detail=f"{model_id} not approved in {FORM_LABELS[self.state]}")
            allowed = MODEL_ROLES.get(model_id)
            if allowed is None or role not in allowed:
                return self._result(False, "role_lock", t,
                                    detail=f"{role!r} not in {model_id} role list {allowed}")
            meta = self.model_meta(model_id)
            if meta is None or not meta["available"]:
                return self._result(False, "unavailable", t,
                                    detail=f"{model_id} has no configured key")
            self._append("assignment", t, model=model_id, role=role, task=task)
            return {"ok": True, "model": model_id, "role": role, "task": task,
                    "state": FORM_LABELS[self.state], "blocked_by": None,
                    "owner_override": False}

    # -- internals ----------------------------------------------------------

    def _apply(self, target: AscensionState, event: str, reason: str,
               t: float, owner_override: bool = False) -> Dict[str, Any]:
        prev = self.state
        self.state = target
        self._last_change = t
        self._append(event, t, from_state=FORM_LABELS[prev],
                     to_state=FORM_LABELS[target], reason=reason,
                     owner_override=owner_override)
        logger.info("ASCENSION %s: %s -> %s (%s)", event.upper(),
                    FORM_LABELS[prev], FORM_LABELS[target], reason or "no reason")
        return {"ok": True, "state": FORM_LABELS[target],
                "from": FORM_LABELS[prev], "to": FORM_LABELS[target],
                "reason": reason, "owner_override": owner_override,
                "blocked_by": None}

    def _result(self, ok: bool, blocked_by: Optional[str], t: float,
                detail: str = "", owner_override: bool = False) -> Dict[str, Any]:
        return {"ok": ok, "state": FORM_LABELS[self.state],
                "blocked_by": blocked_by, "detail": detail,
                "owner_override": owner_override,
                "dwell_remaining_s": round(self._remaining(t, self.dwell_s), 1),
                "cooldown_remaining_s": round(self._remaining(t, self.cooldown_s), 1)}

    def _append(self, event: str, t: float, **fields: Any) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            entry: Dict[str, Any] = {
                "ts": round(t, 3),
                "event": event,
                "state": FORM_LABELS[self.state],
                "speed": speed_score(self.speed),
                "effort": effort_score(self.effort),
                **fields,
            }
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("ascension log append failed: %s", exc)

    def log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most recent log entries, newest first."""
        if not self._log_path.is_file():
            return []
        lines = self._log_path.read_text(encoding="utf-8").strip().splitlines()
        out: List[Dict[str, Any]] = []
        for line in lines[-max(1, int(limit)):]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))

    def snapshot(self) -> Dict[str, Any]:
        """Full state for the UI (GET /api/v1/ascension/state)."""
        with self._lock:
            t = self._clock()
            return {
                "state": FORM_LABELS[self.state],
                "level": int(self.state),
                "form": FORM_LABELS[self.state],
                "models": self.available_models(),
                "dwell_s": self.dwell_s,
                "cooldown_s": self.cooldown_s,
                "dwell_remaining_s": round(self._remaining(t, self.dwell_s), 1),
                "cooldown_remaining_s": round(self._remaining(t, self.cooldown_s), 1),
                "speed": speed_score(self.speed),
                "effort": effort_score(self.effort),
                "owner_override_required": self.state is AscensionState.MR_X_FINAL,
            }


def create_engine(config: Optional[Dict[str, Any]] = None,
                  log_path: Optional[str] = None,
                  passphrase: Optional[str] = None) -> AscensionEngine:
    """Factory for main.py; config keys: dwell_s, cooldown_s,
    owner_passphrase (or env INFINITY_OWNER_PASSPHRASE)."""
    return AscensionEngine(config=config, log_path=log_path, passphrase=passphrase)


__all__ = [
    "AscensionState", "FORM_LABELS", "FORM_MODELS", "MODEL_ROLES",
    "SpeedInputs", "EffortInputs", "speed_score", "effort_score",
    "AscensionEngine", "create_engine", "DEFAULT_PASSPHRASE",
]
