"""P1: spec mode must bounce premature actions (regression test)."""
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


def test_spec_mode_blocks_actions_before_approval(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    j = LongTaskJournal(tmp_path / "j.db")
    b = FakeBuilder([
        # Premature action: must be bounced, not executed.
        '{"action":"write_file","args":{"path":"early.py","content":"x=1\\n"}}',
        '{"action":"update_plan","args":{"plan":"- [ ] write early.py"}}',
        '{"action":"write_file","args":{"path":"early.py","content":"x=1\\n"}}',
        '{"action":"finish","args":{"summary":"done"}}',
    ])
    eng = LongTaskEngine(builder=b, journal=j)

    def watcher():
        for _ in range(200):
            tasks = j.list_tasks()
            if tasks and tasks[0]["status"] == "awaiting_plan":
                eng.approve_plan()
                return
            time.sleep(0.02)

    t = threading.Thread(target=watcher, daemon=True)
    t.start()
    res = eng.run(goal="x", repo_path=str(repo),
                  budget=Budget(max_steps=10), spec_mode=True,
                  autonomy="full")  # headless test: no human to approve tools
    t.join(timeout=2)
    assert res["status"] == "completed"
    kinds = [s["kind"] for s in j.steps_for(res["task_id"])]
    # The premature write_file was gated, never journaled ahead of the plan.
    assert kinds[0] == "plan"
    assert (repo / "early.py").exists()
