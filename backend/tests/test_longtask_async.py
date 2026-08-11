"""W6: async launch (202 + worker thread) and cooperative cancel."""
from __future__ import annotations

import sys
import threading
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


# --- engine level --------------------------------------------------------- #


def test_cancel_stops_loop(repo, tmp_path):
    b = FakeBuilder(['{"action":"list_dir","args":{}}'] * 50)
    j = LongTaskJournal(tmp_path / "j.db")
    eng = LongTaskEngine(builder=b, journal=j)
    # Cancel from another thread, exactly like the API cancel endpoint does.
    threading.Timer(0.02, eng.request_cancel).start()
    res = eng.run(goal="spin", repo_path=str(repo), budget=Budget(max_steps=50))
    assert res["status"] == "cancelled"
    assert j.get_task(res["task_id"])["status"] == "cancelled"


def test_run_reuses_existing_task_row(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    tid = j.create_task(goal="x", repo_path=str(repo))
    b = FakeBuilder(['{"action":"finish","args":{"summary":"ok"}}'])
    eng = LongTaskEngine(builder=b, journal=j)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5),
                  task_id=tid)
    assert res["task_id"] == tid and res["status"] == "completed"
    assert len(j.list_tasks()) == 1  # async launch must not double-create


# --- API level ------------------------------------------------------------ #


class _SyncThread:
    """threading.Thread stand-in that runs the target inline — keeps the
    async endpoint deterministic without sleeps."""

    def __init__(self, target=None, args=(), daemon=None, **kw):
        self._run = lambda: target(*args)

    def start(self):
        self._run()


@pytest.fixture
def client(monkeypatch, tmp_path):
    import main

    monkeypatch.setattr(main, "LONGTASK_JOURNAL",
                        LongTaskJournal(tmp_path / "lt.db"))
    monkeypatch.setattr(main, "_LONGTASK_RUNS", {})
    monkeypatch.setattr(main.app.state, "client", object(), raising=False)
    # Learning would call the real provider chain — keep it off the test path.
    monkeypatch.setattr(main, "_longtask_learn_async", lambda *a, **k: None)
    monkeypatch.setattr(main.threading, "Thread", _SyncThread)

    class _FakeEngine:
        builder = None

        def __init__(self, **kw):
            self.cancel_requested = False

        def request_cancel(self):
            self.cancel_requested = True

        def run(self, goal, repo_path, task_id=None, **kw):
            main.LONGTASK_JOURNAL.set_status(task_id, "completed", "ok")
            return {"task_id": task_id, "status": "completed", "result": "ok"}

    monkeypatch.setattr(main, "LongTaskEngine", _FakeEngine)
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_post_returns_202_with_journal_row(client, tmp_path):
    repo = tmp_path / "repo2"
    repo.mkdir()
    r = client.post("/api/v1/longtasks",
                    json={"goal": "x", "repo_path": str(repo)})
    assert r.status_code == 202
    body = r.json()
    assert body["id"]  # journal row id — the panel tracks by it
    # The sync-thread fake finished the run inline.
    assert body["status"] == "completed"
    assert client.get(f"/api/v1/longtasks/{body['id']}").status_code == 200


def test_cancel_endpoint_flags_running_engine(client, tmp_path):
    import main
    repo = tmp_path / "repo3"
    repo.mkdir()
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(repo))

    class _Eng:
        cancel_requested = False

        def request_cancel(self):
            self.cancel_requested = True

    eng = _Eng()
    main._LONGTASK_RUNS[tid] = eng
    r = client.post(f"/api/v1/longtasks/{tid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelling"
    assert eng.cancel_requested is True


def test_cancel_not_running_409(client, tmp_path):
    import main
    repo = tmp_path / "repo4"
    repo.mkdir()
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(repo))
    main.LONGTASK_JOURNAL.set_status(tid, "completed", "ok")
    assert client.post(f"/api/v1/longtasks/{tid}/cancel").status_code == 409


def test_cancel_unknown_task_404(client):
    assert client.post("/api/v1/longtasks/nope/cancel").status_code == 404
