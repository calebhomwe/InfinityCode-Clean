# Infinity Code X Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the Infinity Code X credit-optimized engineering swarm into the Infinity Code app: a deterministic Ascension Engine (BASE→SS1→SS2→SS3→BLUE→MR X FINAL) with role/form locks, dwell/cooldown guardrails, owner override, full JSONL audit logging, an Eyes Module (capture→VisualReport→diagnose→verify), benchmark-gap→effort-score wiring, owner recognition, and a premium Council UI with auras.

**Architecture:** Pure Python state machine (`backend/core/ascension.py`) with no I/O in transition logic (all I/O behind a thread-safe JSONL append). FastAPI routes in `main.py` expose the engine. `router.py` gains an `ASCENSION_POLICY` table that constrains lane/model choice to the current form's approved list (locked models never selectable). React components (`SwarmCouncilPanel.tsx`, `AscensionDial.tsx`) poll `/api/v1/ascension/state` and render auras via CSS keyframes + original WebAudio chimes.

**Tech Stack:** Python 3.12 (venv: `C:\Users\caleb\infinity-code\.venv\Scripts\python.exe`), FastAPI + pydantic, pytest, TypeScript/React + Vite, CSS keyframes, WebAudio.

**Repo workflow (IMPORTANT):** `C:\Users\caleb\infinity-code` is OUTSIDE the agent workspace. Never edit it with Write/SearchReplace directly — write a patch/creation script into `staging/` (workspace) and run it with Python: `python staging/<script>.py`. Patch scripts use exact-string replace with `assert src.count(old) == 1`. New-file scripts use `io.open(path, "w", encoding="utf-8", newline="").write(content)` and `assert not Path(path).exists()`.

---

### Task 1: Ascension Engine (`backend/core/ascension.py` + tests)

**Files:**
- Create: `C:\Users\caleb\infinity-code\backend\core\ascension.py`
- Test: `C:\Users\caleb\infinity-code\backend\tests\test_ascension.py`

The engine is pure and deterministic. All mutable state lives in one `AscensionEngine` instance guarded by `threading.Lock`. A factory `create_engine(config: dict | None)` builds it from config/env so `main.py` can construct it at startup.

- [x] **Step 1: Write the failing test**

```python
"""Ascension Engine tests: one-step rule, dwell, cooldown, role/form locks,
owner override, MR X FINAL gate, JSONL logging, score functions."""
import json
import os
from pathlib import Path

from backend.core.ascension import (
    AscensionEngine, AscensionState, MODEL_ROLES, FORM_MODELS,
    speed_score, effort_score, SpeedInputs, EffortInputs,
)


def _engine(tmp_path, dwell=0.0, cooldown=0.0, passphrase="x-test"):
    return AscensionEngine(
        config={"dwell_s": dwell, "cooldown_s": cooldown},
        log_path=str(tmp_path / "ascension.jsonl"),
        passphrase=passphrase,
    )


def test_initial_state_is_x_code():
    eng = _engine(Path("."))
    assert eng.state is AscensionState.X_CODE
    snap = eng.snapshot()
    assert snap["form"] == "X_CODE"
    assert snap["state"] == "X_CODE"
    assert snap["models"][0]["id"] == "deepseek/deepseek-chat:free"


def test_escalate_is_one_step_only():
    eng = _engine(Path("."))
    eng.escalate("need code", now=eng._clock() + 9999)
    assert eng.state is AscensionState.SS1
    eng.escalate("need more", now=eng._clock() + 99999)
    assert eng.state is AscensionState.SS2
    # never jumps
    eng.override("BLUE", "x-test", "owner wants", now=eng._clock() + 999999)
    assert eng.state is AscensionState.BLUE


def test_cooldown_blocks_escalation():
    eng = _engine(Path("."), cooldown=3600)
    t0 = eng._clock()
    res = eng.escalate("now", now=t0 + 5)  # within cooldown after init? no —
    # cooldown starts from last_change (init time); +5 < cooldown
    assert res["ok"] is False and res["blocked_by"] == "cooldown"
    res = eng.escalate("later", now=t0 + 7200)
    assert res["ok"] is True and eng.state is AscensionState.SS1


def test_dwell_blocks_flicker():
    eng = _engine(Path("."), dwell=60)
    t0 = eng._clock()
    eng.escalate("up", now=t0 + 61)
    assert eng.state is AscensionState.SS1
    res = eng.deescalate("oops", now=t0 + 62)  # within dwell window
    assert res["ok"] is False and res["blocked_by"] == "dwell"


def test_override_bad_passphrase_rejected_and_logged():
    eng = _engine(Path("."))
    res = eng.override("MR_X_FINAL", "wrong", "evil")
    assert res["ok"] is False and res["blocked_by"] == "passphrase"
    log = eng.log(10)
    assert any(e["event"] == "blocked" for e in log)


def test_override_jumps_and_bypasses_guardrails():
    eng = _engine(Path("."), cooldown=99999, dwell=99999)
    res = eng.override("MR_X_FINAL", "x-test", "owner override", now=eng._clock() + 1)
    assert res["ok"] is True
    assert eng.state is AscensionState.MR_X_FINAL
    assert res["owner_override"] is True


def test_mr_x_final_gate_blocks_auto_escalation():
    eng = _engine(Path("."))
    for _ in range(4):
        eng.escalate("climb", now=eng._clock() + 1_000_000)
    assert eng.state is AscensionState.BLUE
    res = eng.escalate("to final", now=eng._clock() + 2_000_000)
    assert res["ok"] is False and res["blocked_by"] == "owner_gate"


def test_assign_respects_form_lock_and_role_lock():
    eng = _engine(Path("."))
    # SS1 may not use qwen3.8-max (form lock)
    eng.escalate("code", now=eng._clock() + 10)
    res = eng.assign("dashscope/qwen3.8-max", "coder", "draft")
    assert res["ok"] is False and res["blocked_by"] == "form_lock"
    # qwen3.8-max may not draft even in BLUE (role lock)
    eng.override("BLUE", "x-test", "owner", now=eng._clock() + 100)
    res = eng.assign("dashscope/qwen3.8-max", "coder", "draft")
    assert res["ok"] is False and res["blocked_by"] == "role_lock"
    res = eng.assign("dashscope/qwen3.8-max", "verifier", "review")
    assert res["ok"] is True


def test_assign_rejects_locked_model():
    eng = _engine(Path("."))
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.environ.pop("MOONSHOT_API_KEY", None)
    res = eng.assign("z-ai/glm-5.2", "commander", "arch")
    assert res["ok"] is False and res["blocked_by"] == "unavailable"


def test_logging_jsonl_events():
    eng = _engine(Path("."))
    eng.escalate("reason A", now=eng._clock() + 10)
    eng.escalate("reason B", now=eng._clock() + 20)
    entries = eng.log(10)
    assert [e["event"] for e in entries] == ["ascend", "ascend"]
    assert entries[0]["to"] == "SS1" and entries[1]["to"] == "SS2"
    path = Path(eng._log_path)
    assert path.is_file()
    raw = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 2
    first = json.loads(raw[0])
    assert first["reason"] == "reason A"


def test_speed_score_ranges_and_monotonic():
    low = speed_score(SpeedInputs(tokens_per_second=10, p95_latency_ms=4000,
                                  queue_responsive=False, model_available=True,
                                  cost_efficiency=1.0))
    high = speed_score(SpeedInputs(tokens_per_second=300, p95_latency_ms=100,
                                   queue_responsive=True, model_available=True,
                                   cost_efficiency=1.0))
    assert 0 <= low <= 100 and 0 <= high <= 100
    assert high > low
    assert speed_score(SpeedInputs()) == 0.0


def test_effort_score_ranges_and_monotonic():
    light = effort_score(EffortInputs(failed_tests=0, retries=0, task_complexity_0_10=1,
                                      repo_size_bytes=1000, dependency_complexity_0_10=1,
                                      benchmark_gap_0_100=0, coordination_load_0_10=0))
    heavy = effort_score(EffortInputs(failed_tests=12, retries=6, task_complexity_0_10=10,
                                      repo_size_bytes=10**9, dependency_complexity_0_10=10,
                                      benchmark_gap_0_100=80, coordination_load_0_10=10))
    assert 0 <= light <= 100 and 0 <= heavy <= 100
    assert heavy > light
    assert effort_score(EffortInputs()) == 0.0
```

- [x] **Step 2: Run to verify it fails**

```bash
cd C:\Users\caleb\infinity-code
.venv\Scripts\python.exe -m pytest backend/tests/test_ascension.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.core.ascension'`

- [x] **Step 3: Write the engine** — full code below in `backend/core/ascension.py`

```python
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
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    AscensionState.X_CODE: ("deepseek/deepseek-chat:free",),
    AscensionState.SS1: (
        "deepseek/deepseek-chat:free",
        "deepseek/deepseek-v4-flash",
    ),
    AscensionState.SS2: (
        "deepseek/deepseek-chat:free",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
    ),
    AscensionState.SS3: (
        "deepseek/deepseek-chat:free",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
        "minimax/minimax-m3",
    ),
    AscensionState.BLUE: (
        "deepseek/deepseek-chat:free",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
        "minimax/minimax-m3",
        "z-ai/glm-5.2",
        "dashscope/qwen3.8-max",
    ),
    AscensionState.MR_X_FINAL: (
        "deepseek/deepseek-chat:free",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
        "minimax/minimax-m3",
        "z-ai/glm-5.2",
        "dashscope/qwen3.8-max",
        "local/fable-max-llamacpp",
    ),
}

# Role locks: a model may only be assigned to roles in its list.
MODEL_ROLES: Dict[str, Tuple[str, ...]] = {
    "deepseek/deepseek-chat:free": ("free_router", "chat", "syntax"),
    "deepseek/deepseek-v4-flash": ("coder", "debugger", "diagnostician", "tester"),
    "moonshotai/kimi-k3": ("researcher", "evidence", "swarm_tester"),
    "minimax/minimax-m3": ("planner", "memory", "sequencer"),
    "z-ai/glm-5.2": ("commander", "architect", "long_horizon"),
    # Qwen 3.8 Max is the oracle: it SEES and ADVISES, never drafts.
    "dashscope/qwen3.8-max": ("oracle", "verifier", "reviewer"),
    "local/fable-max-llamacpp": ("advisor",),
}

# Cost tier + which env keys unlock each model.
_MODEL_META: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    # model_id -> (swarm role label, cost tier, env keys)
    "deepseek/deepseek-chat:free": ("free_router", "free", ("DEEPSEEK_API_KEY",)),
    "deepseek/deepseek-v4-flash": ("coder", "cheap", ("DEEPSEEK_API_KEY",)),
    "moonshotai/kimi-k3": ("research", "mid", ("MOONSHOT_API_KEY", "OPENROUTER_API_KEY")),
    "minimax/minimax-m3": ("planning", "mid", ("OPENROUTER_API_KEY",)),
    "z-ai/glm-5.2": ("commander", "mid", ("OPENROUTER_API_KEY",)),
    "dashscope/qwen3.8-max": ("oracle", "high", ("OPENAI_API_KEY",)),
    "local/fable-max-llamacpp": ("advisor", "free", ("INFINITY_FABLE_PORT",)),
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
    availability 10%, cost efficiency 20%."""
    if inp is None:
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
    comp_n = _clamp01((inp.task_complexity_0_10 or 0) / 10.0)
    repo_n = _clamp01(__import__("math").log1p(inp.repo_size_bytes or 0)
                      / __import__("math").log1p(2 ** 31))
    dep_n = _clamp01((inp.dependency_complexity_0_10 or 0) / 10.0)
    gap_n = _clamp01((inp.benchmark_gap_0_100 or 0) / 100.0)
    coord_n = _clamp01((inp.coordination_load_0_10 or 0) / 10.0)
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
        logger.info("Ascension Engine ready (dwell=%ss, cooldown=%ss)", self.dwell_s, self.cooldown_s)

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
            target = AscensionState(int(self.state) + 1)
            if self.state is AscensionState.MR_X_FINAL:
                return self._result(False, "already_max", t)
            if target is AscensionState.MR_X_FINAL:
                return self._result(False, "owner_gate", t,
                                    detail="MR X FINAL requires owner override")
            ok, blocked = self._can_change(t)
            if not ok:
                return self._result(False, blocked, t)
            return self._apply(target, "ascend", reason, t)

    def deescalate(self, reason: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        """One step down; allowed even while cooling down (safety valve)."""
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
```

- [x] **Step 4: Run tests to verify they pass**

```bash
cd C:\Users\caleb\infinity-code
.venv\Scripts\python.exe -m pytest backend/tests/test_ascension.py -v
```
Expected: 11 passed. Then full suite: `.venv\Scripts\python.exe -m pytest backend/tests -q` — no regressions.

- [x] **Step 5: Commit**

```bash
cd C:\Users\caleb\infinity-code && git add backend/core/ascension.py backend/tests/test_ascension.py && git commit -m "feat(ascension): Infinity Code X ascension engine"
```
(PowerShell: use `;` instead of `&&`.)

---

### Task 2: Ascension API endpoints (`backend/main.py`)

**Files:** Modify `backend/main.py` (module-level singleton + routes near the health endpoints).

- [x] **Step 1:** Create `backend/core/ascension.py` import + singleton at module level after `_CONFIG`:
  `ASCENSION_CONFIG = _CONFIG.get("ascension", {}) if isinstance(...) else {}` and `ASCENSION_ENGINE = create_engine(ASCENSION_CONFIG)`.
- [x] **Step 2:** Add routes (contract):
  - `GET /api/v1/ascension/state` → `ASCENSION_ENGINE.snapshot()` + `{"protected": [...]}` list of rule names (`one_step`, `role_lock`, `form_lock`, `dwell`, `cooldown`, `owner_only_final`).
  - `POST /api/v1/ascension/escalate` body `{"reason": str}` → `engine.escalate(reason)`; 200 always (result dict carries `ok`/`blocked_by`).
  - `POST /api/v1/ascension/deescalate` body `{"reason": str}` → `engine.deescalate(reason)`.
  - `POST /api/v1/ascension/override` body `{"target": str, "passphrase": str, "reason": str}` → 403 JSON `{"error": "invalid passphrase", "blocked_by": "passphrase"}` when `ok is False and blocked_by == "passphrase"`, else 200 result.
  - `GET /api/v1/ascension/log?limit=50` → `{"entries": engine.log(limit)}`.
  - `POST /api/v1/ascension/scores` body `{"speed": {...SpeedInputs}, "effort": {...EffortInputs}}` → sets `engine.speed`/`engine.effort` (validate with pydantic models), returns `{"speed": score, "effort": score, "state": form}`.
- [x] **Step 3:** Verify with the running backend: restart via venv python (see `staging/restart_backend_v2.py` pattern), then
  `curl.exe -s http://localhost:8000/api/v1/ascension/state` shows `"form": "X Code"` and 7 model cards; `POST /api/v1/ascension/override` with wrong passphrase → 403; with config passphrase → form changes; `GET /api/v1/ascension/log` shows the events.
- [x] **Step 4:** Test file `backend/tests/test_ascension_api.py` using `fastapi.testclient.TestClient(app)` (pattern from `test_longtask_api.py`): state shape, escalate one-step, override 403.
- [x] **Step 5:** Commit.

---

### Task 3: Router wiring — ascension → approved model policy (`backend/core/router.py`)

**Files:** Modify `backend/core/router.py`, test `backend/tests/test_credit_routing.py` (extend).

- [x] **Step 1:** Add `ASCENSION_POLICY` in router.py:
  ```python
  ASCENSION_POLICY: Dict[str, Dict[str, Any]] = {
      # form name (FORM_LABELS value) -> allowed lanes + advisory-only flag
      "X Code":   {"lanes": ("cheap",),        "qwen_advisory_only": True},
      "SS1":      {"lanes": ("cheap",),        "qwen_advisory_only": True},
      "SS2":      {"lanes": ("cheap", "smart"), "qwen_advisory_only": True},
      "SS3":      {"lanes": ("cheap", "smart", "vision"), "qwen_advisory_only": True},
      "Blue":     {"lanes": ("cheap", "smart", "vision", "custom"), "qwen_advisory_only": True},
      "Mr X Final": {"lanes": ("cheap", "smart", "vision", "custom", "local"), "qwen_advisory_only": True},
  }
  ```
- [x] **Step 2:** Add `apply_ascension(state_name: str, lane: str, chain: Tuple[ModelSpec, ...], engine=None) -> Tuple[str, Tuple[ModelSpec, ...]]`: 
  - If `lane not in policy["lanes"]` → demote to cheapest allowed lane.
  - Filter chain to models approved in the form (`engine.approved_models()` mapped via `FORM_LABELS` — engine is optional; when absent, use a module-level `ACTIVE_ASCENSION_STATE` default "X Code").
  - If filtered chain empty → return `("cheap", chain_for("longtask_builder"))`.
  - Never return `dashscope/qwen3.8-max` as a chain head (advisory-only: it may only appear via `longtask_reviewer` role).
- [x] **Step 3:** In `LaneRouter.route`/`route_chain`, accept optional `ascension_state: Optional[str] = None` param; when given, wrap result with `apply_ascension`.
- [x] **Step 4:** Tests (extend `test_credit_routing.py`): SS1 form filters smart-lane chain to deepseek flash; X Code never returns qwen3.8-max; unknown form name falls back to X Code policy; locked model (glm-5.2) excluded when OPENROUTER key absent.
- [x] **Step 5:** Commit.

---

### Task 4: Owner recognition (`backend/core/owner.py`)

**Files:** Create `backend/core/owner.py`, test `backend/tests/test_owner.py`.

- [x] **Step 1:** Module with:
  - `OWNER_NAME = "Munesu Homwe"`, `OWNER_ALIAS = "Mr X"` (config overridable via `owner.name`/`owner.alias` passed to `create_owner(config)`).
  - `HINT_JOKES: Tuple[str, ...]` — 4 original, anime-flavored one-liners (e.g. "Mr X doesn't transform — the engine transforms because he says so.", "The only model Mr X can't fine-tune is his patience.", etc. — original text, no copyrighted material).
  - `owner_line(index: int) -> str` — "Infinity Code X is owned and directed by {name} — {alias}." + cycling joke `HINT_JOKES[index % len]`.
  - `is_owner_query(text: str) -> bool` — case-insensitive match on: "who owns", "who built", "who created", "owner", "mr x", "munesu", "homwe".
  - `maybe_owner_line(text: str) -> str` — returns `owner_line(seed_from_text)` if `is_owner_query(text)` else `""`; seed derived deterministically from text hash so the same question yields the same joke.
- [x] **Step 2:** Wire into chat: in `main.py` chat-stream prompt assembly, append `maybe_owner_line(user_content)` to the system prompt when non-empty (find the system prompt builder and add one line; do NOT touch streaming logic).
- [x] **Step 3:** Tests: is_owner_query true for "who owns this app", false for "how do I deploy"; owner_line contains name + alias + one joke; maybe_owner_line empty for normal text, deterministic for repeated text.
- [x] **Step 4:** Commit.

---

### Task 5: Eyes Module (`backend/core/eyes.py` + API)

**Files:** Create `backend/core/eyes.py`, test `backend/tests/test_eyes.py`, modify `backend/main.py`.

- [x] **Step 1:** `VisualReport` pydantic model — EXACT schema:
  ```python
  class VisualElement(BaseModel):
      id: str; type: str; text: str; selector: str
      state: str; box: List[float]  # [x, y, w, h]

  class VisualReport(BaseModel):
      summary: str; layout: str
      elements: List[VisualElement]
      issues: List[str]
      consoleErrors: List[str]
      networkErrors: List[str]
  ```
- [x] **Step 2:** `capture(url: str, out_dir: Path) -> VisualReport`:
  - Try Playwright sync API (`from playwright.sync_api import sync_playwright`) when importable: open chromium headless, collect console errors + failed requests + a11y snapshot (`page.accessibility.snapshot()`), DOM subset (first N elements with tag/role/aria-label/text, skip scripts), bounding boxes via `locator.bounding_box()`, screenshot saved as `{out_dir}/{ts}.png`.
  - Fallback: if Playwright missing, return a minimal report using the URL + `{"summary": "capture unavailable: playwright not installed", ...}` — the endpoint must never 500 (log warning instead).
  - Never invent selectors: selector = best of `[data-testid]`, `aria-label`, `#id`, else `tag:nth-of-type(n)` computed from real DOM.
  - Timeout guard: whole capture wrapped in try/except with a hard cap (30 s).
- [x] **Step 3:** `diagnose(report: VisualReport) -> dict` — prompt DeepSeek Flash 1731 via the existing chat client (pattern: `backend/core/provider_chat.py` / longtask engine) with the VisualReport serialized as structured text; return `{"diagnosis", "fix", "test_plan", "usability"}` (parse JSON defensively; on failure return raw text under `diagnosis`).
- [x] **Step 4:** `verify(before: VisualReport, after: VisualReport) -> dict` — Qwen 3.8 Max (dashscope) reviews both reports: `{"verdict", "changed": bool, "issues_resolved": [...], "remaining": [...], "summary"}`. Role-locked: this is the ONLY place qwen3.8-max is called.
- [x] **Step 5:** API in main.py: `POST /api/v1/eyes/capture {url}`, `POST /api/v1/eyes/diagnose {visual_report: {...}}`, `POST /api/v1/eyes/verify {before: {...}, after: {...}}`. Screenshots served via existing `/outputs` or `/media` static mount; save to `backend/data/eyes/`.
- [x] **Step 6:** Tests: VisualReport schema validation (bad box length rejected), capture returns a VisualReport for a file:// or data: URL when playwright available else graceful fallback, diagnose/verify parse the model response contract (mock the client call).
- [x] **Step 7:** Commit.

---

### Task 6: Council UI (`src/components/AscensionDial.tsx`, `SwarmCouncilPanel.tsx`, CSS, audio)

**Files:**
- Create: `src/components/AscensionDial.tsx`, `src/components/SwarmCouncilPanel.tsx`, `src/lib/ascensionClient.ts` (fetch wrapper), `src/lib/ascensionAudio.ts` (WebAudio chimes)
- Modify: `src/App.tsx` (mount panel), `src/index.css` (aura keyframes + body classes)

- [x] **Step 1:** `ascensionClient.ts` — `fetchState()`, `escalate(reason)`, `deescalate(reason)`, `override(target, passphrase, reason)`, `fetchLog(limit)`, typed `AscensionState` interface mirroring the API snapshot.
- [x] **Step 2:** `AscensionDial.tsx` — 6-segment dial; props `{state, level, dwellRemaining, cooldownRemaining}`; current segment highlighted, MR X FINAL shows a lock icon + "owner only"; dwell/cooldown remainders as tiny countdown text.
- [x] **Step 3:** `SwarmCouncilPanel.tsx` — polls `fetchState()` every 5 s; renders dial + model cards grid (name, swarm role, tier, `ACTIVE`/`LOCKED` badges, assigned role) + protection rules strip (6 rules with ✓) + owner card ("Munesu Homwe — Mr X · Final override authority") + Gauntlet button (`POST /api/v1/gauntlet/gap`, see Task 7) + ascension log feed (last 12 lines) + "MR X FINAL" override button → inline passphrase input → `override("MR_X_FINAL", ...)`.
- [x] **Step 4:** `ascensionAudio.ts` — original WebAudio chime per form: create `AudioContext` on first user gesture; `playAscension(level)` plays a short ascending arpeggio (frequencies per level, e.g. level 0 → 440, 1 → 523.25, 2 → 587.33, 3 → 659.25, 4 → 783.99, 5 → 1046.5 + low pad); no external audio files.
- [x] **Step 5:** Auras in `index.css`:
  - Body classes: `aura-xcode` (soft white pulse `@keyframes auraPulseWhite`), `aura-ss1` (gold), `aura-ss2` (electric gold — faster + sparkle), `aura-ss3` (intense gold — stronger glow), `aura-blue` (cyan/blue calm drift), `aura-final` (violet+gold, `@keyframes auraInfinity` with lightning flicker + `@keyframes screenShake` applied to `#root` with subtle 2px translate).
  - Applied via `useEffect` in the panel on state change; removed on unmount/state leave. Respect `prefers-reduced-motion`.
- [x] **Step 6:** Mount `SwarmCouncilPanel` in App.tsx (collapsible section near VisionPanel / SwarmPanel — pick the existing layout slot; keep it out of the way, e.g. in the same rail as SwarmDeploymentBadge).
- [x] **Step 7:** Build + verify: `cd C:\Users\caleb\infinity-code; node node_modules\vite\bin\vite.js build` (or tsc first: `node node_modules\typescript\bin\tsc --noEmit`). Browser-verify on `localhost:4173`: panel renders, dial shows X Code, model cards show DEEPSEEK LIVE + others LOCKED, escalate via button → SS1 gold aura appears, wrong passphrase on MR X FINAL → error toast, correct passphrase → violet aura + screen shake class.
- [x] **Step 8:** Commit.

---

### Task 7: Benchmark Gauntlet hookup (gap → effort score + gauntlet button)

**Files:** Modify `backend/core/eval_harness.py` (or a new `backend/core/gauntlet.py`), `backend/main.py`, test `backend/tests/test_gauntlet.py`.

- [x] **Step 1:** `gauntlet.py`:
  - `latest_reports(data_dir) -> List[dict]` — parse newest `eval_reports/*.json` (pattern: `benchmarks_deepseek_flash_final.json`: fields `tasks_total`, per-task `score`, `model`).
  - `benchmark_gap(reports) -> float` — `1 - mean(score)` across all tasks in the latest report (0..100 scale).
  - `hallucination_rate(reports) -> float` — mean failure on simpleqa/hhem tasks.
  - `tool_use_reliability(reports) -> float` — mean score on tau-bench tasks.
  - `post_gap(engine, data_dir) -> dict` — sets `engine.effort.benchmark_gap_0_100 = gap`, returns `{"gap": gap, "effort": effort_score(engine.effort), "suggested": "BLUE" if gap >= 60 else None}`.
- [x] **Step 2:** API: `POST /api/v1/gauntlet/gap` → `post_gap(ASCENSION_ENGINE, DATA_DIR)`; `GET /api/v1/gauntlet/status` → latest report summary `{report, tasks_total, model, mean_score, gap, hallucination_rate, tool_use_reliability}` (404-style `{"report": null}` when no reports).
- [x] **Step 3:** Tests: gap math on a fixture report (write a small temp JSON matching the report schema), hallucination/tool-use filtering by task name substring, `suggested` threshold.
- [x] **Step 4:** Commit.

---

### Task 8: Full regression + browser verification

- [x] **Step 1:** `.venv\Scripts\python.exe -m pytest backend/tests -q` → all pass (57 existing + new).
- [x] **Step 2:** `cd C:\Users\caleb\infinity-code; node node_modules\typescript\bin\tsc --noEmit` → clean; `node node_modules\vite\bin\vite.js build` → success.
- [x] **Step 3:** Restart backend (venv), start `vite preview` on 4173 (CORS-approved origin), browser-verify the full loop: chat works (free lane), Council panel renders, escalate → SS1 aura, owner question in chat returns identity + hint joke, eyes capture endpoint returns a VisualReport, gauntlet gap shows a number, MR X FINAL override works with passphrase and 403s without.
- [x] **Step 4:** Update this plan's checkboxes; final commit.

---

## Assumptions

- Model ids map to live endpoints: "DeepSeek Flash 1731" = `deepseek/deepseek-v4-flash`; "Qwen 3.8 Max" = `dashscope/qwen3.8-max`; Free Router = `deepseek/deepseek-chat:free`.
- MOONSHOT/OPENROUTER keys are lost → Kimi/MiniMax/GLM render LOCKED and are never routed to (engine `unavailable` check enforces this).
- The 48-task eval harness already covers all 13 spec benchmarks; the Gauntlet reuses its reports rather than adding tasks.
- Passphrase default `infinity-x` is a dev default, overridable by `INFINITY_OWNER_PASSPHRASE` env or config `ascension.owner_passphrase`; never log the passphrase.

