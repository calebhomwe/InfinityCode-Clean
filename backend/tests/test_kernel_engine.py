"""End-to-end tests for KernelTaskEngine — kernel-mode longtask (Worker 2).

Kernel-mode protocol (pinned in _swarm_stage2/goal.md): the builder replies
with Python code (fenced or bare); each reply is executed in a persistent
KernelSession subprocess. Host tools are handed off from kernel code by
raising `_ToolCall(name, args)`; the engine dispatches file/shell tools
through the parent PathJail tool bridge and treats `update_plan` / `finish`
as the plan / finish protocol markers. Kernel state persists across turns.

These tests are written against the PINNED CONTRACT only — they deliberately
do not read the project's kernel_engine.py (Worker 1 writes it in parallel;
the orchestrator copies this file into backend/tests/ to run the suite).

Suite-time guards: kernel_timeout=10 bounds every exec, scripts are short,
budgets are small (max_steps=10 default) and the FakeBuilder falls back to a
scripted finish when its script runs out, so no loop can spin forever.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.engine import Budget  # noqa: E402
from core.longtask.journal import LongTaskJournal  # noqa: E402
from core.longtask.kernel_engine import KernelTaskEngine  # noqa: E402


def _plan(plan: str) -> str:
    return 'raise _ToolCall("update_plan", {"plan": %r})' % plan


def _write_file(path: str, content: str) -> str:
    return ('raise _ToolCall("write_file", '
            '{"path": %r, "content": %r})') % (path, content)


def _finish(summary: str = "done") -> str:
    return 'raise _ToolCall("finish", {"summary": %r})' % summary


class FakeBuilder:
    """Scripted builder: each chat() pops the next Python-code reply."""

    def __init__(self, replies, cost=0.001):
        self.replies = list(replies)
        self.calls = 0
        self.cost = cost
        self.seen = []

    def chat(self, messages, max_tokens=3000, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if not self.replies:
            # Safety net: never let the loop hang when a script runs out.
            return {"text": _finish("out of script"), "cost_usd": self.cost}
        return {"text": self.replies.pop(0), "cost_usd": self.cost}


class FakeReviewer:
    """Scripted reviewer: each chat() pops the next JSON verdict."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, max_tokens=800, **kw):
        self.calls += 1
        return {"text": self.replies.pop(0), "cost_usd": 0.0001}


class FakeJudge:
    """Scripted rubric judge.

    The parent `_judge_gate` (reused by the kernel engine) forks the sandbox
    and then calls `_judge.run_rubric_judge(fork, goal, self.judge.chat)`, so
    the gate is drivable entirely from a stub whose `.chat()` returns
    {"text": <verdict JSON>, "cost_usd": ...} — no skip required.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, max_tokens=1000, **kw):
        self.calls += 1
        return {"text": self.replies.pop(0), "cost_usd": 0.0}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    # Non-empty repo: judge hidden check `has_files` passes on the fork.
    (r / "base.txt").write_text("x")
    return r


def _run(repo, tmp_path, replies, reviewer=None, judge=None, budget=None,
         snapshot_every=10, cost=0.001):
    b = FakeBuilder(replies, cost=cost)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = KernelTaskEngine(builder=b, journal=j, reviewer=reviewer, judge=judge,
                           kernel_timeout=10, snapshot_every=snapshot_every)
    res = eng.run(goal="test goal", repo_path=str(repo),
                  budget=budget or Budget(max_steps=10), autonomy="full")
    return b, j, res


# --- 1. full loop with kernel state across turns --------------------------- #

def test_full_loop_with_kernel_state(repo, tmp_path):
    # plan -> compute in the kernel -> write file using the computed value in
    # a LATER turn (kernel state persisted) -> finish.
    b, j, res = _run(repo, tmp_path, [
        _plan("- [ ] compute total\n- [ ] write total.txt"),
        "total = 6*7",
        # str(total) is evaluated inside the kernel: total is still 42 here.
        'raise _ToolCall("write_file", '
        '{"path": "total.txt", "content": str(total)})',
        _finish("wrote total.txt"),
    ])
    assert res["status"] == "completed"
    assert (repo / "total.txt").read_text() == "42"
    steps = j.steps_for(res["task_id"])
    kinds_tools = [(s["kind"], s["tool"]) for s in steps]
    assert ("plan", "update_plan") in kinds_tools
    assert ("kernel", "kernel_exec") in kinds_tools
    assert ("tool", "write_file") in kinds_tools
    assert ("finish", "finish") in kinds_tools


# --- 2. PathJail blocks escaping write_file -------------------------------- #

def test_pathjail_blocks_escape(repo, tmp_path):
    outside = repo.parent / "outside.txt"
    b, j, res = _run(repo, tmp_path, [
        _write_file("../outside.txt", "EVIL"),
        _finish("continue after escape"),
    ])
    assert res["status"] == "completed"  # tool error, not a crash
    assert not outside.exists()
    steps = j.steps_for(res["task_id"])
    wf = [s for s in steps if s["tool"] == "write_file"]
    assert wf, "expected a write_file step"
    assert (wf[0]["kind"] == "tool_error"
            or "error" in str(wf[0].get("result_json", "")))


# --- 3. reviewer rejects once, then approves ------------------------------- #

def test_reviewer_rejects_once_then_approves(repo, tmp_path):
    rv = FakeReviewer([
        '{"approve": false, "issues": ["add tests"]}',
        '{"approve": true, "issues": []}',
    ])
    b, j, res = _run(repo, tmp_path,
                     [_finish("first try"), _finish("second try")],
                     reviewer=rv)
    assert res["status"] == "completed"
    assert rv.calls >= 1
    steps = j.steps_for(res["task_id"])
    assert sum(1 for s in steps if s["kind"] == "finish") == 2
    # the rejection feedback reached the builder
    assert any("REVIEWER REJECTED" in m["content"]
               for call in b.seen for m in call)


# --- 4. judge gate feedback loop (fail -> fix -> pass) --------------------- #

def test_judge_gate_feedback_loop(repo, tmp_path):
    judge = FakeJudge([
        '{"pass": false, "issues": ["x"], "total": 2}',
        '{"pass": true, "issues": [], "total": 5}',
    ])
    b, j, res = _run(repo, tmp_path,
                     [_finish("first submission"), _finish("second")],
                     judge=judge)
    assert res["status"] == "completed"
    assert judge.calls == 2
    steps = j.steps_for(res["task_id"])
    assert sum(1 for s in steps if s["tool"] == "judge") == 2
    assert any("JUDGE REJECTED" in m["content"]
               for call in b.seen for m in call)


# --- 5. cost budget trips -------------------------------------------------- #

def test_cost_budget_trips(repo, tmp_path):
    # 0.001 USD/reply -> ~0.00149 AUD > 0.0001 AUD budget after reply 1.
    b, j, res = _run(repo, tmp_path, ["x = 1"],
                     budget=Budget(max_steps=10, max_cost_aud=0.0001),
                     cost=0.001)
    assert res["status"] == "budget_exceeded"


# --- 6. step limit trips --------------------------------------------------- #

def test_step_limit_trips(repo, tmp_path):
    forever = ["x = 1"] * 20  # kernel execs forever; step budget cuts it off
    b, j, res = _run(repo, tmp_path, forever, budget=Budget(max_steps=3))
    assert res["status"] == "budget_exceeded"


# --- 7. protocol error after two bad replies ------------------------------- #

def test_protocol_error_after_second_bad_reply(repo, tmp_path):
    # No code block -> one automatic re-ask -> second failure -> "error".
    b, j, res = _run(repo, tmp_path, ["not code at all", "not code at all"])
    assert res["status"] == "error"
    steps = j.steps_for(res["task_id"])
    assert any(s["kind"] == "protocol_error" for s in steps)


# --- 8. snapshots written to <repo>/.kernel -------------------------------- #

def test_snapshots_written(repo, tmp_path):
    b, j, res = _run(repo, tmp_path,
                     ["x = 1", "y = 2", _finish("done")], snapshot_every=1)
    assert res["status"] == "completed"
    snap_dir = repo / ".kernel"
    assert snap_dir.is_dir()
    pkls = list(snap_dir.glob("*.pkl"))
    assert len(pkls) >= 2  # one periodic snapshot per exec + final snapshot
    steps = j.steps_for(res["task_id"])
    assert any(s["tool"] == "kernel_snapshot" for s in steps)


# --- 9. shell block -------------------------------------------------------- #

def test_shell_block(repo, tmp_path):
    b, j, res = _run(repo, tmp_path,
                     ["!echo kernel-shell-ok", _finish("done")])
    assert res["status"] == "completed"
    steps = j.steps_for(res["task_id"])
    kernel_steps = [s for s in steps if s["tool"] == "kernel_exec"]
    assert any("kernel-shell-ok" in str(s.get("result_json", ""))
               for s in kernel_steps)


# --- 10. kernel error self-corrects ---------------------------------------- #

def test_kernel_error_self_corrects(repo, tmp_path):
    b, j, res = _run(repo, tmp_path,
                     ["1/0", "2+2", _finish("recovered")])
    assert res["status"] == "completed"  # error surfaced, task NOT failed
    # the error text reached the model so it could self-correct
    assert any("ZeroDivisionError" in m["content"]
               for call in b.seen for m in call)
    steps = j.steps_for(res["task_id"])
    assert any(s["tool"] == "kernel_exec"
               and "ZeroDivisionError" in str(s.get("result_json", ""))
               for s in steps)


# --- 11. rescue at protocol death (run-9 regression) ------------------------ #

def test_protocol_death_rescued_when_artifact_exists(repo, tmp_path):
    """Run 9 shape: the builder wrote papers.md, then stopped producing
    code blocks. The landed artifact upgrades the end to completed."""
    b, j, res = _run(repo, tmp_path, [
        _write_file("papers.md", "| t |\n| a |"),
        "not code at all",
        "not code at all",
    ])
    assert res["status"] == "completed"
    assert "papers.md" in res["result"]
    assert (repo / "papers.md").exists()


def test_protocol_death_stays_error_without_artifacts(repo, tmp_path):
    """No file changes -> rescue does not fire (existing contract kept)."""
    b, j, res = _run(repo, tmp_path, ["not code at all", "not code at all"])
    assert res["status"] == "error"
    steps = j.steps_for(res["task_id"])
    assert any(s["kind"] == "protocol_error" for s in steps)
