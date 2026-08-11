"""P1: spec-mode plan approval + artifacts (engine + journal)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.engine import Budget, LongTaskEngine  # noqa: E402
from core.longtask.journal import LongTaskJournal  # noqa: E402


class FakeBuilder:
    def __init__(self, replies, cost=0.001):
        self.replies = list(replies)
        self.cost = cost

    def chat(self, messages, max_tokens=3000, **kw):
        if not self.replies:
            return {"text": '{"action":"finish","args":{"summary":"done"}}',
                    "cost_usd": self.cost}
        return {"text": self.replies.pop(0), "cost_usd": self.cost}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    return r


def script_with_plan():
    return [
        '{"action":"update_plan","args":{"plan":"- [ ] write hello.py"}}',
        '{"action":"write_file","args":{"path":"hello.py","content":"print(\'hi\')\\n"}}',
        '{"action":"finish","args":{"summary":"wrote hello.py"}}',
    ]


# --- spec mode: pauses at the plan until approved ------------------------- #


def test_spec_mode_waits_for_approval(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder(script_with_plan())
    eng = LongTaskEngine(builder=b, journal=j)
    seen: list = []

    def watcher():
        # Poll until the task parks in awaiting_plan, then approve it.
        for _ in range(200):
            tasks = j.list_tasks()
            if tasks and tasks[0]["status"] == "awaiting_plan":
                seen.append("awaiting_plan")
                eng.approve_plan()
                return
            time.sleep(0.02)

    t = threading.Thread(target=watcher, daemon=True)
    t.start()
    res = eng.run(goal="write hello.py", repo_path=str(repo),
                  budget=Budget(max_steps=10), spec_mode=True,
                  autonomy="full")  # headless test: no human to approve tools
    t.join(timeout=2)
    assert seen == ["awaiting_plan"]
    assert res["status"] == "completed"
    assert (repo / "hello.py").exists()


def test_spec_mode_wall_timeout_without_approval(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder(script_with_plan())
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo),
                  budget=Budget(max_steps=10, max_wall_min=0), spec_mode=True)
    assert res["status"] == "budget_exceeded"
    assert j.get_task(res["task_id"])["status"] == "budget_exceeded"


def test_plain_mode_ignores_approval(repo, tmp_path):
    """spec_mode off: update_plan never parks the run (regression guard)."""
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder(script_with_plan())
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")  # headless test: no human to approve tools
    assert res["status"] == "completed"


def test_cancel_while_awaiting_plan(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder(script_with_plan())
    eng = LongTaskEngine(builder=b, journal=j)

    def canceler():
        for _ in range(200):
            tasks = j.list_tasks()
            if tasks and tasks[0]["status"] == "awaiting_plan":
                eng.request_cancel()
                return
            time.sleep(0.02)

    t = threading.Thread(target=canceler, daemon=True)
    t.start()
    res = eng.run(goal="x", repo_path=str(repo),
                  budget=Budget(max_steps=10), spec_mode=True)
    t.join(timeout=2)
    assert res["status"] == "cancelled"


# --- artifacts: write_file produces a journaled artifact + compile gate --- #


def test_write_file_records_artifact_with_gate(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder(script_with_plan())
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")  # headless test: no human to approve tools
    arts = j.artifacts_for(res["task_id"])
    assert len(arts) == 1
    a = arts[0]
    assert a["kind"] == "code" and a["path"] == "hello.py"
    assert "print" in a["body"]
    gates = __import__("json").loads(a["gates_json"])
    assert gates.get("compile") == "pass"


def test_artifact_gate_flags_broken_python(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder([
        '{"action":"write_file","args":{"path":"bad.py","content":"def o(:\\n"}}',
        '{"action":"finish","args":{"summary":"wrote bad.py"}}',
    ])
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")  # headless test: no human to approve tools
    arts = j.artifacts_for(res["task_id"])
    assert len(arts) == 1
    gates = __import__("json").loads(arts[0]["gates_json"])
    assert gates.get("compile") == "fail"
