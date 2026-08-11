"""End-to-end engine tests driven by a scripted FakeBuilder."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.engine import Budget, LongTaskEngine  # noqa: E402
from core.longtask.journal import LongTaskJournal  # noqa: E402


class FakeBuilder:
    """Scripted builder: each chat() pops the next reply."""

    def __init__(self, replies, cost=0.001):
        self.replies = list(replies)
        self.calls = 0
        self.cost = cost
        self.seen = []

    def chat(self, messages, max_tokens=3000, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if not self.replies:
            return {"text": '{"action":"finish","args":{"summary":"out of script"}}',
                    "cost_usd": self.cost}
        return {"text": self.replies.pop(0), "cost_usd": self.cost}


class FakeReviewer:
    """Scripted reviewer: each chat() pops the next reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, max_tokens=800, **kw):
        self.calls += 1
        return {"text": self.replies.pop(0), "cost_usd": 0.0001}


def script() -> list:
    return [
        '{"action":"update_plan","args":{"plan":"- [ ] add hello.py"}}',
        '{"action":"write_file","args":{"path":"hello.py","content":"print(\'hi\')\\n"}}',
        '{"action":"run_command","args":{"command":"python hello.py"}}',
        '{"action":"finish","args":{"summary":"added hello.py, runs clean"}}',
    ]


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "base.txt").write_text("x")
    return r


def test_full_loop(repo, tmp_path):
    b = FakeBuilder(script())
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="add hello.py", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")  # default "ask" parks write/run for approval
    assert res["status"] == "completed"
    assert (repo / "hello.py").read_text().startswith("print")
    t = j.get_task(res["task_id"])
    assert t["status"] == "completed" and t["steps"] >= 4
    tools = [s["tool"] for s in j.steps_for(res["task_id"])]
    assert "run_command" in tools and "finish" in tools


def test_step_budget_trips(repo, tmp_path):
    forever = ['{"action":"list_dir","args":{}}'] * 50
    b = FakeBuilder(forever)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="spin", repo_path=str(repo), budget=Budget(max_steps=3))
    assert res["status"] == "budget_exceeded"


def test_malformed_reply_reasks_then_recovers(repo, tmp_path):
    b = FakeBuilder(["let me think...",
                     '{"action":"finish","args":{"summary":"ok"}}'])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5))
    assert res["status"] == "completed"


def test_tool_error_feeds_back_and_loop_continues(repo, tmp_path):
    b = FakeBuilder([
        '{"action":"read_file","args":{"path":"missing.txt"}}',
        '{"action":"finish","args":{"summary":"recovered"}}',
    ])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5))
    assert res["status"] == "completed"
    kinds = [s["kind"] for s in j.steps_for(res["task_id"])]
    assert "tool_error" in kinds


def test_events_emitted(repo, tmp_path):
    events = []
    b = FakeBuilder(script())
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j,
                         event_cb=lambda tid, kind, p: events.append(kind))
    eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
            autonomy="full")  # default "ask" parks write/run for approval
    assert "plan_update" in events and "step" in events and "done" in events


# --- W1: review-and-continue gate ---------------------------------------- #

def test_review_gate_rejects_then_approves(repo, tmp_path):
    b = FakeBuilder(script() + [
        '{"action":"edit_file","args":{"path":"hello.py","old":"hi","new":"hello"}}',
        '{"action":"finish","args":{"summary":"fixed naming"}}',
    ])
    rv = FakeReviewer([
        '{"approve": false, "issues": ["rename print(\'hi\') to say hello"]}',
        '{"approve": true, "issues": []}',
    ])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j, reviewer=rv)
    res = eng.run(goal="add hello.py", repo_path=str(repo), budget=Budget(max_steps=15),
                  autonomy="full")  # headless test: no human to approve tools
    assert res["status"] == "completed"
    assert rv.calls == 2
    kinds = [s["kind"] for s in j.steps_for(res["task_id"])]
    assert kinds.count("review") == 2
    # the rejection feedback reached the builder
    assert any("REVIEWER REJECTED" in m["content"]
               for call in b.seen for m in call)


def test_review_gate_caps_rounds(repo, tmp_path):
    b = FakeBuilder(script() + [
        '{"action":"finish","args":{"summary":"second try"}}',
    ])
    rv = FakeReviewer([
        '{"approve": false, "issues": ["nope"]}',
        '{"approve": false, "issues": ["still nope"]}',
    ])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j, reviewer=rv)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=15),
                  autonomy="full")  # headless test: no human to approve tools
    # after two rejections the third finish stands unreviewed
    assert res["status"] == "completed"
    assert rv.calls == 2


def test_dead_reviewer_never_blocks(repo, tmp_path):
    class Boom:
        def chat(self, messages, max_tokens=800, **kw):
            raise RuntimeError("fable server cold")

    b = FakeBuilder(script())
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j, reviewer=Boom())
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")  # headless test: no human to approve tools
    assert res["status"] == "completed"


# --- W1: cost guardrails --------------------------------------------------- #

class _RecorderTracker:
    def __init__(self):
        self.recorded = []

    def record_actual(self, aud):
        self.recorded.append(aud)


def test_cost_limit_trips(repo, tmp_path):
    # 0.15 USD per reply / 0.67 ~= 0.22 AUD > 0.05 AUD budget after reply 1
    b = FakeBuilder(script(), cost=0.15)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo),
                  budget=Budget(max_steps=10, max_cost_aud=0.05),
                  autonomy="full")  # headless test: no human to approve tools
    assert res["status"] == "budget_exceeded"
    assert "cost limit" in res["result"]


def test_cost_tracker_is_fed_and_cost_persisted(repo, tmp_path):
    tr = _RecorderTracker()
    b = FakeBuilder(script(), cost=0.01)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j, cost_tracker=tr)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")  # headless test: no human to approve tools
    assert res["status"] == "completed"
    assert tr.recorded and all(a > 0 for a in tr.recorded)
    task = j.get_task(res["task_id"])
    assert task["cost_aud"] > 0


# --- W1: history compaction ------------------------------------------------- #

def test_compaction_bounds_history(repo, tmp_path):
    replies = ['{"action":"list_dir","args":{}}'] * 20 + [
        '{"action":"finish","args":{"summary":"done"}}']
    b = FakeBuilder(replies)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=30))
    assert res["status"] == "completed"
    assert len(b.seen[-1]) <= 14  # system + digest + keep-tail(10) + new result
    assert any("condensed" in m["content"] for m in b.seen[-1])


def test_compact_history_pure():
    hist = [{"role": "system", "content": "sys"}]
    for i in range(30):
        hist.append({"role": "assistant", "content": f"a{i}"})
        hist.append({"role": "user", "content": f"u{i}"})
    out = LongTaskEngine.compact_history(hist)
    assert len(out) == 12
    assert out[0]["content"] == "sys"
    assert out[-1]["content"] == "u29"
    assert "condensed" in out[1]["content"]
    # below threshold: untouched
    assert LongTaskEngine.compact_history(hist[:10]) == hist[:10]


# --- W2 hook: lessons injection ---------------------------------------------- #

def test_lessons_injected_into_system_prompt(repo, tmp_path):
    b = FakeBuilder(['{"action":"finish","args":{"summary":"ok"}}'])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5),
            lessons=["always run tests before finishing"])
    assert "LESSONS LEARNED" in b.seen[0][0]["content"]
    assert "always run tests before finishing" in b.seen[0][0]["content"]


# --- Rescue at protocol death (run-9 regression) ------------------------------ #

def test_protocol_death_rescued_when_artifact_exists(repo, tmp_path):
    """Run 9: the kernel delivered papers.md and then failed to emit a
    well-formed finish. Work that already landed on disk must not be
    discarded as an engine error."""
    b = FakeBuilder([
        '{"action":"update_plan","args":{"plan":"- [ ] write notes.md"}}',
        '{"action":"write_file","args":{"path":"notes.md","content":"real work"}}',
        "garbage reply one",
        "garbage reply two",
    ])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10),
                  autonomy="full")
    assert res["status"] == "completed"
    assert "notes.md" in res["result"]
    assert (repo / "notes.md").read_text() == "real work"


def test_protocol_death_stays_error_without_artifacts(repo, tmp_path):
    """No file changes -> the rescue must NOT fire."""
    b = FakeBuilder(["garbage reply one", "garbage reply two"])
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=10))
    assert res["status"] == "error"
    assert "not following protocol" in res["result"]
