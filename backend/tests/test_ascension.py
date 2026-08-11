"""Ascension Engine tests: one-step rule, dwell, cooldown, role/form locks,
owner override, MR X FINAL gate, JSONL logging, score functions."""
import json
from pathlib import Path

from backend.core.ascension import (
    AscensionEngine, AscensionState,
    speed_score, effort_score, SpeedInputs, EffortInputs,
)


def _engine(tmp_path, dwell=0.0, cooldown=0.0, passphrase="x-test"):
    return AscensionEngine(
        config={"dwell_s": dwell, "cooldown_s": cooldown},
        log_path=str(tmp_path / "ascension.jsonl"),
        passphrase=passphrase,
    )


def test_initial_state_is_x_code(tmp_path):
    eng = _engine(tmp_path)
    assert eng.state is AscensionState.X_CODE
    snap = eng.snapshot()
    assert snap["form"] == "X Code"
    assert snap["level"] == 0
    assert snap["models"][0]["id"] == "dashscope/qwen-turbo"


def test_escalate_is_one_step_only(tmp_path):
    eng = _engine(tmp_path)
    eng.escalate("need code", now=eng._clock() + 9999)
    assert eng.state is AscensionState.SS1
    eng.escalate("need more", now=eng._clock() + 99999)
    assert eng.state is AscensionState.SS2
    # never jumps
    eng.override("BLUE", "x-test", "owner wants", now=eng._clock() + 999999)
    assert eng.state is AscensionState.BLUE


def test_cooldown_blocks_escalation(tmp_path):
    eng = _engine(tmp_path, cooldown=3600)
    t0 = eng._clock()
    res = eng.escalate("now", now=t0 + 5)  # within cooldown after init
    assert res["ok"] is False and res["blocked_by"] == "cooldown"
    res = eng.escalate("later", now=t0 + 7200)
    assert res["ok"] is True and eng.state is AscensionState.SS1


def test_dwell_blocks_flicker(tmp_path):
    eng = _engine(tmp_path, dwell=60)
    t0 = eng._clock()
    eng.escalate("up", now=t0 + 61)
    assert eng.state is AscensionState.SS1
    res = eng.deescalate("oops", now=t0 + 62)  # within dwell window
    assert res["ok"] is False and res["blocked_by"] == "dwell"


def test_override_bad_passphrase_rejected_and_logged(tmp_path):
    eng = _engine(tmp_path)
    res = eng.override("MR_X_FINAL", "wrong", "evil")
    assert res["ok"] is False and res["blocked_by"] == "passphrase"
    log = eng.log(10)
    assert any(e["event"] == "blocked" for e in log)


def test_override_jumps_and_bypasses_guardrails(tmp_path):
    eng = _engine(tmp_path, cooldown=99999, dwell=99999)
    res = eng.override("MR X Final", "x-test", "owner override", now=eng._clock() + 1)
    assert res["ok"] is True
    assert eng.state is AscensionState.MR_X_FINAL
    assert res["owner_override"] is True


def test_mr_x_final_gate_blocks_auto_escalation(tmp_path):
    eng = _engine(tmp_path)
    for _ in range(4):
        eng.escalate("climb", now=eng._clock() + 1_000_000)
    assert eng.state is AscensionState.BLUE
    res = eng.escalate("to final", now=eng._clock() + 2_000_000)
    assert res["ok"] is False and res["blocked_by"] == "owner_gate"


def test_assign_respects_form_lock_and_role_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dummy")
    eng = _engine(tmp_path)
    eng.escalate("code", now=eng._clock() + 10)
    # SS1 may not use qwen3.8-max (form lock)
    res = eng.assign("dashscope/qwen3.8-max", "coder", "draft")
    assert res["ok"] is False and res["blocked_by"] == "form_lock"
    # qwen3.8-max may not draft even in BLUE (role lock)
    eng.override("BLUE", "x-test", "owner", now=eng._clock() + 100)
    res = eng.assign("dashscope/qwen3.8-max", "coder", "draft")
    assert res["ok"] is False and res["blocked_by"] == "role_lock"
    res = eng.assign("dashscope/qwen3.8-max", "verifier", "review")
    assert res["ok"] is True


def test_assign_rejects_locked_model(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    eng.override("BLUE", "x-test", "owner", now=eng._clock() + 1)
    res = eng.assign("dashscope/qwen-max", "commander", "arch")
    assert res["ok"] is False and res["blocked_by"] == "unavailable"


def test_logging_jsonl_events(tmp_path):
    eng = _engine(tmp_path)
    eng.escalate("reason A", now=eng._clock() + 10)
    eng.escalate("reason B", now=eng._clock() + 20)
    entries = eng.log(10)
    assert [e["event"] for e in entries] == ["ascend", "ascend"]
    # log() is newest-first
    assert entries[0]["to_state"] == "SS2" and entries[1]["to_state"] == "SS1"
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
