"""API-level tests for /api/v1/ascension (engine monkeypatched)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    import main
    from core.ascension import AscensionEngine

    monkeypatch.setattr(
        main, "ASCENSION_ENGINE",
        AscensionEngine(config={"dwell_s": 0, "cooldown_s": 0},
                        log_path=str(tmp_path / "asc.jsonl"),
                        passphrase="x-test"),
    )
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_state_snapshot(client):
    r = client.get("/api/v1/ascension/state")
    assert r.status_code == 200
    body = r.json()
    assert body["form"] == "X Code" and body["level"] == 0
    assert body["models"][0]["id"] == "dashscope/qwen-turbo"
    assert "one_step" in body["protected"]


def test_escalate_one_step(client):
    r = client.post("/api/v1/ascension/escalate", json={"reason": "code task"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["to"] == "SS1"
    r2 = client.post("/api/v1/ascension/escalate", json={"reason": "more"})
    assert r2.json()["to"] == "SS2"  # one step at a time


def test_override_requires_passphrase(client):
    r = client.post("/api/v1/ascension/override",
                    json={"target": "MR_X_FINAL", "passphrase": "wrong", "reason": "x"})
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["blocked_by"] == "passphrase"


def test_override_owner_success(client):
    r = client.post("/api/v1/ascension/override",
                    json={"target": "MR X Final", "passphrase": "x-test", "reason": "owner"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["to"] == "Mr X Final"
    assert body["owner_override"] is True


def test_scores_and_log(client):
    r = client.post("/api/v1/ascension/scores", json={
        "speed": {"tokens_per_second": 120, "p95_latency_ms": 900},
        "effort": {"failed_tests": 4, "task_complexity_0_10": 6},
    })
    assert r.status_code == 200
    body = r.json()
    assert 0 < body["speed"] <= 100 and 0 < body["effort"] <= 100
    r = client.get("/api/v1/ascension/log")
    assert r.status_code == 200
    assert isinstance(r.json()["entries"], list)
