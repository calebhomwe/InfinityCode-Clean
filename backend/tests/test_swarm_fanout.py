"""Swarm fan-out tests: parallel candidates, worker fan-out, concurrency."""
import asyncio
import json
import time
import types
from pathlib import Path

import pytest

try:
    from backend.core import swarm as swarm_mod
    from backend.core.swarm import AgentSwarm, init_database
except ImportError:  # running with backend/ as the working directory
    from core import swarm as swarm_mod  # type: ignore[no-redef]
    from core.swarm import AgentSwarm, init_database  # type: ignore[no-redef]


def _make_swarm(tmp_path) -> AgentSwarm:
    db = tmp_path / "swarm.db"
    out = tmp_path / "out"
    init_database(db)
    return AgentSwarm(db_path=db, outputs_dir=out)


# --- effort tier + concurrency ceiling ----------------------------------- #

def test_swarm_effort_tier_exists():
    assert swarm_mod._EFFORT_PLAN["swarm"] == (1, "architect")


def test_concurrency_ceiling_default_and_env(monkeypatch):
    assert swarm_mod.MAX_CONCURRENT_MISSIONS == 8
    monkeypatch.setenv("INFINITY_SWARM_CONCURRENCY", "4")
    assert swarm_mod._max_concurrent_missions() == 4
    monkeypatch.setenv("INFINITY_SWARM_CONCURRENCY", "bogus")
    assert swarm_mod._max_concurrent_missions() == 8


# --- decomposition parsing ------------------------------------------------ #

def test_parse_decomposition_ok():
    text = ('here is my plan\n{"main": "main.py", "modules": '
            '[{"path": "utils.py", "spec": "helpers"}, '
            '{"path": "main.py", "spec": "entry"}]}')
    modules = AgentSwarm._parse_decomposition(text)
    assert [m["path"] for m in modules] == ["utils.py", "main.py"]


def test_parse_decomposition_garbage_returns_empty():
    assert AgentSwarm._parse_decomposition("no json here") == []
    assert AgentSwarm._parse_decomposition("") == []
    assert AgentSwarm._parse_decomposition(None) == []


def test_parse_decomposition_strips_traversal():
    text = '{"main": "main.py", "modules": [{"path": "../../etc/passwd", "spec": "x"}]}'
    modules = AgentSwarm._parse_decomposition(text)
    assert modules[0]["path"] == "passwd"


# --- parallel candidates -------------------------------------------------- #

def _candidate_result(letter: str, status: str, score, cost: float) -> dict:
    return {
        "agent_role": f"Engineer:{letter}",
        "model_used": f"model-{letter}",
        "status": status,
        "cost": cost,
        "score": score,
        "critique_dict": None,
        "image_url": None,
        "artifact_path": f"/tmp/{letter}/main.py",
        "evidence_extra": {"code_path": f"/tmp/{letter}/main.py"},
        "failure_detail": "",
    }


def test_parallel_candidates_parallel_and_selects_winner(tmp_path):
    swarm = _make_swarm(tmp_path)

    gauge = {"active": 0, "peak": 0}

    async def fake_attempt_code(self, mission_id, goal, plan_text, feedback,
                                attachment_context, attempt_dir, role_key,
                                role_spec, reference_path, prefer_local=False):
        gauge["active"] += 1
        gauge["peak"] = max(gauge["peak"], gauge["active"])
        try:
            await asyncio.sleep(0.2)  # simulate a real generation
            letter = Path(attempt_dir).name
            if letter == "A":
                return _candidate_result("A", "failed", 0.2, 1.0)
            if letter == "B":
                return _candidate_result("B", "success", 0.9, 2.0)
            return _candidate_result("C", "success", 0.5, 3.0)
        finally:
            gauge["active"] -= 1

    swarm._attempt_code = types.MethodType(fake_attempt_code, swarm)
    attempt_dir = tmp_path / "attempt_1"
    attempt_dir.mkdir(exist_ok=True)

    t0 = time.time()
    result = asyncio.run(swarm._attempt_parallel_candidates(
        mission_id="m1", attempt_number=1, goal="g", plan_text="p",
        attempt_dir=attempt_dir, role_key="engineer", role_spec=None,
        reference_path=None, n_candidates=3,
    ))
    elapsed = time.time() - t0
    # Wall-clock bounds flake under loaded CI; the overlap gauge is the
    # deterministic proof that candidates fanned out in parallel.
    assert gauge["peak"] >= 2, "candidates ran serially (no overlap)"
    assert elapsed < 3.0, f"parallel candidates took too long ({elapsed:.2f}s)"
    assert result["status"] == "success"
    assert result["model_used"] == "model-B"
    assert result["agent_role"] == "Engineer x3"
    assert result["cost"] == 6.0  # all three candidates' spend is counted
    # All candidates land in the tournament table; B is selected.
    with swarm._connect() as conn:
        rows = conn.execute(
            "SELECT candidate_letter, selected FROM tournament_candidates "
            "WHERE mission_id='m1' ORDER BY candidate_letter"
        ).fetchall()
    letters = [r[0] for r in rows]
    assert letters == ["A", "B", "C"]
    assert dict((r[0], r[1]) for r in rows) == {"A": 0, "B": 1, "C": 0}


def test_parallel_candidates_all_fail_still_returns_best(tmp_path):
    swarm = _make_swarm(tmp_path)

    async def fake_attempt_code(self, mission_id, goal, plan_text, feedback,
                                attachment_context, attempt_dir, role_key,
                                role_spec, reference_path, prefer_local=False):
        letter = Path(attempt_dir).name
        return _candidate_result(letter, "failed", 0.4 if letter == "B" else 0.1, 1.0)

    swarm._attempt_code = types.MethodType(fake_attempt_code, swarm)
    attempt_dir = tmp_path / "attempt_1"
    attempt_dir.mkdir(exist_ok=True)
    result = asyncio.run(swarm._attempt_parallel_candidates(
        mission_id="m2", attempt_number=1, goal="g", plan_text="p",
        attempt_dir=attempt_dir, role_key="engineer", role_spec=None,
        reference_path=None, n_candidates=3,
    ))
    assert result["status"] == "failed"
    assert result["model_used"] == "model-B"  # highest score survives


# --- worker fan-out ------------------------------------------------------- #

def _stub_call_agent(architect_reply, worker_code, reviewer_reply):
    def fake_call_agent(self, role_key, prompt, max_tokens):
        if role_key == "architect":
            return architect_reply, 0.5
        if role_key == "worker":
            return worker_code, 0.1
        if role_key == "longtask_reviewer":
            return reviewer_reply, 0.05
        raise AssertionError(f"unexpected role {role_key}")
    return fake_call_agent


def test_worker_swarm_merges_and_gates(tmp_path):
    swarm = _make_swarm(tmp_path)
    architect_reply = json.dumps({
        "main": "main.py",
        "modules": [
            {"path": "main.py", "spec": "entry point that prints swarm ok"},
            {"path": "helpers.py", "spec": "VALUE constant"},
        ],
    })
    worker_replies = [
        "```python\nprint('swarm ok')\n```",
        "```python\nVALUE = 42\n```",
    ]
    calls = {"n": 0}

    def fake_call_agent(self, role_key, prompt, max_tokens):
        calls["n"] += 1
        if role_key == "architect":
            return architect_reply, 0.5
        if role_key == "worker":
            return worker_replies.pop(0), 0.1
        if role_key == "longtask_reviewer":
            return '{"approve": true, "issues": []}', 0.05
        raise AssertionError(f"unexpected role {role_key}")

    swarm._call_agent = types.MethodType(fake_call_agent, swarm)
    attempt_dir = tmp_path / "attempt_1"
    attempt_dir.mkdir(exist_ok=True)
    result = asyncio.run(swarm._attempt_worker_swarm(
        mission_id="m3", attempt_number=1, goal="print swarm ok",
        plan_text="plan", attempt_dir=attempt_dir, reference_path=None,
    ))
    assert result["status"] == "success", result["failure_detail"]
    assert result["agent_role"] == "WorkerSwarm x2"
    assert (attempt_dir / "swarm" / "main.py").is_file()
    assert (attempt_dir / "swarm" / "helpers.py").is_file()
    assert calls["n"] == 4  # 1 architect + 2 workers + 1 reviewer
    assert result["evidence_extra"]["gate_ok"] is True
    assert result["cost"] == pytest.approx(0.5 + 0.1 + 0.1 + 0.05)


def test_worker_swarm_reviewer_rejects(tmp_path):
    swarm = _make_swarm(tmp_path)
    architect_reply = json.dumps({
        "main": "main.py",
        "modules": [{"path": "main.py", "spec": "entry"}],
    })

    def fake_call_agent(self, role_key, prompt, max_tokens):
        if role_key == "architect":
            return architect_reply, 0.5
        if role_key == "worker":
            return "```python\nprint('hi')\n```", 0.1
        if role_key == "longtask_reviewer":
            return '{"approve": false, "issues": ["missing feature X"]}', 0.05
        raise AssertionError(f"unexpected role {role_key}")

    swarm._call_agent = types.MethodType(fake_call_agent, swarm)
    attempt_dir = tmp_path / "attempt_1"
    attempt_dir.mkdir(exist_ok=True)
    result = asyncio.run(swarm._attempt_worker_swarm(
        mission_id="m4", attempt_number=1, goal="g",
        plan_text="plan", attempt_dir=attempt_dir, reference_path=None,
    ))
    assert result["status"] == "failed"
    assert "missing feature X" in result["failure_detail"]
    assert result["evidence_extra"]["reviewer_approved"] is False


def test_worker_swarm_bad_worker_code_fails_gate(tmp_path):
    swarm = _make_swarm(tmp_path)
    architect_reply = json.dumps({
        "main": "main.py",
        "modules": [{"path": "main.py", "spec": "entry"}],
    })

    def fake_call_agent(self, role_key, prompt, max_tokens):
        if role_key == "architect":
            return architect_reply, 0.5
        if role_key == "worker":
            return "```python\ndef broken(:\n```", 0.1
        raise AssertionError(f"unexpected role {role_key}")

    swarm._call_agent = types.MethodType(fake_call_agent, swarm)
    attempt_dir = tmp_path / "attempt_1"
    attempt_dir.mkdir(exist_ok=True)
    result = asyncio.run(swarm._attempt_worker_swarm(
        mission_id="m5", attempt_number=1, goal="g",
        plan_text="plan", attempt_dir=attempt_dir, reference_path=None,
    ))
    assert result["status"] == "failed"
    assert result["evidence_extra"]["compile_errors"], "syntax error must be caught"
    # Reviewer never runs when the deterministic gate fails.
    assert result["evidence_extra"]["reviewer_approved"] is True  # skipped flag default
