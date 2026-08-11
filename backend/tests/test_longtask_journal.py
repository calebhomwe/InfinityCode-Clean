"""Tests for the Long Task SQLite journal."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.longtask.journal import LongTaskJournal  # noqa: E402


def test_journal_roundtrip(tmp_path):
    j = LongTaskJournal(tmp_path / "lt.db")
    tid = j.create_task(goal="add feature", repo_path=str(tmp_path),
                        model_builder="fake", model_reviewer="fake")
    j.append_step(tid, seq=1, kind="tool", tool="write_file",
                  args={"path": "a.py"}, result={"ok": True},
                  model="fake", cost_aud=0.01, duration_ms=12)
    j.set_status(tid, "completed", result="done")
    t = j.get_task(tid)
    assert t["status"] == "completed" and t["goal"] == "add feature"
    assert abs(t["cost_aud"] - 0.01) < 1e-9
    steps = j.steps_for(tid)
    assert steps[0]["tool"] == "write_file" and steps[0]["seq"] == 1
    assert j.list_tasks()[0]["id"] == tid


def test_reopen_journal(tmp_path):
    db = tmp_path / "lt.db"
    j = LongTaskJournal(db)
    tid = j.create_task(goal="g", repo_path=str(tmp_path))
    j.conn.close()
    j2 = LongTaskJournal(db)
    assert j2.get_task(tid)["goal"] == "g"
