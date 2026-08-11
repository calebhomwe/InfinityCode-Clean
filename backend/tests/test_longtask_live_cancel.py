"""W6 wiring test: 202 launch -> real worker thread -> cooperative cancel."""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.journal import LongTaskJournal  # noqa: E402


class _SlowBuilder:
    """One chat() per second so cancel lands mid-run."""

    def chat(self, messages, max_tokens=3000, **kw):
        time.sleep(0.4)
        return {"text": '{"action":"list_dir","args":{}}', "cost_usd": 0.0001}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import main
    from core.longtask.engine import LongTaskEngine

    monkeypatch.setattr(main, "LONGTASK_JOURNAL",
                        LongTaskJournal(tmp_path / "lt.db"))
    monkeypatch.setattr(main, "_LONGTASK_RUNS", {})
    monkeypatch.setattr(main.app.state, "client", object(), raising=False)
    monkeypatch.setattr(main, "_longtask_learn_async", lambda *a, **k: None)

    def _engine(**kw):
        kw["builder"] = _SlowBuilder()
        return LongTaskEngine(**kw)

    monkeypatch.setattr(main, "LongTaskEngine", _engine)
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_live_launch_and_cancel(client, tmp_path):
    import main
    repo = tmp_path / "repo"
    repo.mkdir()

    # The REAL worker thread (no Thread fake) — POST must return instantly.
    t0 = time.time()
    r = client.post("/api/v1/longtasks",
                    json={"goal": "spin", "repo_path": str(repo),
                          "max_steps": 200})
    assert r.status_code == 202 and time.time() - t0 < 2
    tid = r.json()["id"]
    assert r.json()["status"] == "running"

    # Give the worker a beat to pick the task up.
    deadline = time.time() + 5
    while time.time() < deadline and tid not in main._LONGTASK_RUNS:
        time.sleep(0.05)
    assert tid in main._LONGTASK_RUNS

    rc = client.post(f"/api/v1/longtasks/{tid}/cancel")
    assert rc.status_code == 200 and rc.json()["status"] == "cancelling"

    # The loop sees the event before its next turn and journals 'cancelled'.
    deadline = time.time() + 10
    while time.time() < deadline:
        row = main.LONGTASK_JOURNAL.get_task(tid)
        if row["status"] != "running":
            break
        time.sleep(0.1)
    row = main.LONGTASK_JOURNAL.get_task(tid)
    assert row["status"] == "cancelled"
    # The journal flips to 'cancelled' a hair before the worker thread
    # drops its _LONGTASK_RUNS entry; poll instead of asserting instantly.
    deadline = time.time() + 5
    while time.time() < deadline and tid in main._LONGTASK_RUNS:
        time.sleep(0.05)
    assert tid not in main._LONGTASK_RUNS  # worker cleaned up
    # Cancel on a finished task is a 409 from here on.
    assert client.post(f"/api/v1/longtasks/{tid}/cancel").status_code == 409
