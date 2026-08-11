"""API-level tests for /api/v1/longtasks (engine monkeypatched)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    import main
    from core.longtask.journal import LongTaskJournal

    monkeypatch.setattr(main, "LONGTASK_JOURNAL",
                        LongTaskJournal(tmp_path / "lt.db"))
    monkeypatch.setattr(main, "_LONGTASK_RUNS", {})
    monkeypatch.setattr(main.app.state, "client", object(), raising=False)
    # Learning would call the real provider chain — keep it off the test path.
    monkeypatch.setattr(main, "_longtask_learn_async", lambda *a, **k: None)

    class _SyncThread:
        """threading.Thread stand-in that runs the target inline."""
        def __init__(self, target=None, args=(), daemon=None, **kw):
            self._run = lambda: target(*args)
        def start(self):
            self._run()

    monkeypatch.setattr(main.threading, "Thread", _SyncThread)

    # main.py imports LongTaskEngine via the backend.* OR core.* branch
    # depending on cwd, so patch the name on main itself (call-time lookup).
    class _FakeEngine:
        builder = None

        def __init__(self, **kw):
            pass

        def request_cancel(self):
            pass

        def run(self, goal, repo_path, task_id=None, **kw):
            main.LONGTASK_JOURNAL.set_status(task_id, "completed", "ok")
            return {"task_id": task_id, "status": "completed", "result": "ok"}

    monkeypatch.setattr(main, "LongTaskEngine", _FakeEngine)
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_create_and_get(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    r = client.post("/api/v1/longtasks",
                    json={"goal": "x", "repo_path": str(repo)})
    # 202 = accepted + launched; the sync-thread fake finished it inline.
    assert r.status_code == 202 and r.json()["status"] == "completed"
    tid = r.json()["id"]
    body = client.get(f"/api/v1/longtasks/{tid}").json()
    assert body["id"] == tid and "steps" in body
    assert isinstance(client.get("/api/v1/longtasks").json(), list)


def test_rejects_bad_repo(client):
    r = client.post("/api/v1/longtasks",
                    json={"goal": "x", "repo_path": "C:/does/not/exist"})
    assert r.status_code == 400


def test_missing_task_404(client):
    assert client.get("/api/v1/longtasks/nope").status_code == 404
