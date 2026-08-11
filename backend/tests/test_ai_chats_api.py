"""API-level tests for /api/v1/ai-chats (unified chat history).

Hermetic: patches core.ai_chat_sync's AI_CHATS_DB + source dirs; the
integrations router resolves the db path at call time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

from core import ai_chat_sync as m  # noqa: E402
import backend.core.ai_chat_sync as bm  # noqa: E402  (main imports via the backend. package path -> separate module object)

QODER_LINES = [
    {"role": "user", "message": {"content": [{"type": "text", "text": "api hello"}]}},
    {"role": "assistant", "message": {"content": [{"type": "text", "text": "api reply"}]}},
]


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    conv = projects / "chat-api-test" / "conversation-history" / "task-a"
    conv.mkdir(parents=True)
    (conv / "task-a.jsonl").write_text(
        "\n".join(json.dumps(x) for x in QODER_LINES), encoding="utf-8")
    monkeypatch.setattr(m, "QODER_PROJECTS_DIR", projects)
    monkeypatch.setattr(m, "OPENCODE_DB", tmp_path / "nodb")
    db = tmp_path / "ai_chats.db"
    monkeypatch.setattr(m, "AI_CHATS_DB", db)
    # main.py imports core.ai_chat_sync via the `backend.` package path,
    # which creates a SECOND module object with its own globals — patch both.
    monkeypatch.setattr(bm, "QODER_PROJECTS_DIR", projects)
    monkeypatch.setattr(bm, "OPENCODE_DB", tmp_path / "nodb")
    monkeypatch.setattr(bm, "AI_CHATS_DB", db)
    m.sync_qoder_history(db)
    return db


@pytest.fixture
def client(seeded):
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_list_conversations(client):
    r = client.get("/api/v1/ai-chats")
    assert r.status_code == 200
    body = r.json()
    assert any(c["title"] == "chat-api-test" for c in body["conversations"])
    assert body["count"] >= 1


def test_list_filtered_by_source(client):
    r = client.get("/api/v1/ai-chats", params={"source": "qoder"})
    assert r.status_code == 200
    assert all(c["source"] == "qoder" for c in r.json()["conversations"])
    r2 = client.get("/api/v1/ai-chats", params={"source": "nonexistent"})
    assert r2.json()["conversations"] == []


def test_search_endpoint(client):
    r = client.get("/api/v1/ai-chats/search", params={"q": "api hello"})
    assert r.status_code == 200
    hits = r.json()["results"]
    assert any(h["source"] == "qoder" for h in hits)


def test_messages_endpoint(client):
    convs = client.get("/api/v1/ai-chats").json()["conversations"]
    cid = next(c["id"] for c in convs if c["title"] == "chat-api-test")
    r = client.get(f"/api/v1/ai-chats/{cid}/messages")
    assert r.status_code == 200
    body = r.json()
    assert [x["content"] for x in body["messages"]] == ["api hello", "api reply"]
    assert body["conversation"]["source"] == "qoder"


def test_messages_unknown_conversation(client):
    r = client.get("/api/v1/ai-chats/999999/messages")
    assert r.status_code == 200
    assert "not found" in r.json()["error"]


def test_stats_endpoint(client):
    r = client.get("/api/v1/ai-chats/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"].get("qoder", 0) >= 1
    assert body["total"] >= 1


def test_sync_endpoint(client):
    r = client.post("/api/v1/ai-chats/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "qoder" in body["results"] and "opencode" in body["results"]
