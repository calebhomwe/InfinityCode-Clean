"""P1 API tests: approve-plan, artifacts, SSE events."""
from __future__ import annotations

import json
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
    monkeypatch.setattr(main, "_LONGTASK_SUBS", {})
    monkeypatch.setattr(main, "_SSE_HEARTBEAT_S", 0.05)
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_approve_plan_flags_engine(client, tmp_path):
    import main
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(tmp_path))

    class _Eng:
        approved = False

        def approve_plan(self):
            self.approved = True

    eng = _Eng()
    main._LONGTASK_RUNS[tid] = eng
    r = client.post(f"/api/v1/longtasks/{tid}/approve-plan")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert eng.approved is True


def test_approve_plan_not_running_409(client, tmp_path):
    import main
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(tmp_path))
    assert client.post(f"/api/v1/longtasks/{tid}/approve-plan").status_code == 409


def test_approve_plan_unknown_404(client):
    assert client.post("/api/v1/longtasks/nope/approve-plan").status_code == 404


def test_artifacts_endpoint(client, tmp_path):
    import main
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(tmp_path))
    main.LONGTASK_JOURNAL.add_artifact(tid, "code", "a.py", "print(1)",
                                       {"compile": "pass"})
    r = client.get(f"/api/v1/longtasks/{tid}/artifacts")
    assert r.status_code == 200
    arts = r.json()
    assert len(arts) == 1 and arts[0]["path"] == "a.py"
    assert json.loads(arts[0]["gates_json"])["compile"] == "pass"
    assert client.get("/api/v1/longtasks/nope/artifacts").status_code == 404


def test_sse_snapshot_and_done_for_finished_task(client, tmp_path):
    import main
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(tmp_path))
    main.LONGTASK_JOURNAL.set_status(tid, "completed", "ok")
    frames = []
    with client.stream("GET", f"/api/v1/longtasks/{tid}/events") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
            if any(f.get("kind") == "done" for f in frames):
                break
    kinds = [f["kind"] for f in frames]
    assert kinds[0] == "snapshot"
    assert frames[0]["payload"]["status"] == "completed"
    assert "done" in kinds
    # Subscriber cleaned up after the stream ends.
    assert main._LONGTASK_SUBS.get(tid, []) == []


def test_sse_unknown_task_404(client):
    assert client.get("/api/v1/longtasks/nope/events").status_code == 404


def test_event_bus_fanout(client, tmp_path):
    import main
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(tmp_path))
    main._longtask_event_cb(tid, "step", {"seq": 1})
    # No subscribers -> no crash; add one and verify delivery.
    import queue as _q
    q = _q.Queue()
    main._LONGTASK_SUBS.setdefault(tid, []).append(q)
    main._longtask_event_cb(tid, "review", {"approve": True})
    frame = q.get_nowait()
    assert frame["kind"] == "review" and frame["payload"]["approve"] is True
