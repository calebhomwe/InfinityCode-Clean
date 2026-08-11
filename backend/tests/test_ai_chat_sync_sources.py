"""Tests for the Qoder + OpenCode chat-sync sources (marathon 2026-08-11).

Hermetic: synthetic fixtures only (tmp qoder jsonl + tmp opencode sqlite).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core import ai_chat_sync as m  # noqa: E402

QODER_SAMPLE = [
    {"role": "user", "message": {"content": [{"type": "text", "text": "hello world"}]}},
    {"role": "assistant", "message": {"content": [
        {"type": "text", "text": "hi there"},
        {"type": "tool_use", "name": "grep", "input": {"q": "x"}},
    ]}},
    {"role": "user", "message": {"content": [{"type": "tool_result", "content": "big output"}]}},
    {"role": "assistant", "message": {"content": "plain string reply"}},
]


@pytest.fixture
def qoder_fixture(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    conv = projects / "chat-1-abc123" / "conversation-history" / "task-xyz"
    conv.mkdir(parents=True)
    (conv / "task-xyz.jsonl").write_text(
        "\n".join(json.dumps(line) for line in QODER_SAMPLE), encoding="utf-8")
    monkeypatch.setattr(m, "QODER_PROJECTS_DIR", projects)
    return projects


@pytest.fixture
def opencode_fixture(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY, title TEXT, path TEXT, time_created INTEGER,
            time_updated INTEGER, time_archived INTEGER);
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER);
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, data TEXT,
            time_created INTEGER);
    """)
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                 ("ses_1", "Test Session", "Users/caleb/proj", 1786400000000,
                  1786410000000, None))
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                 ("ses_archived", "Old", None, 1, 2, 3))
    conn.execute("INSERT INTO message VALUES (?,?,?,?)",
                 ("msg_1", "ses_1", json.dumps({"role": "user", "time": 1786400000000}), 1))
    conn.execute("INSERT INTO message VALUES (?,?,?,?)",
                 ("msg_2", "ses_1", json.dumps({"role": "assistant", "time": 1786401000000}), 2))
    conn.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                 ("prt_1", "msg_1", "ses_1", json.dumps({"type": "text", "text": "user says hi"}), 1))
    conn.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                 ("prt_2", "msg_2", "ses_1", json.dumps({"type": "text", "text": "assistant answers"}), 2))
    conn.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                 ("prt_3", "msg_2", "ses_1", json.dumps({"type": "reasoning", "text": "hidden"}), 3))
    conn.commit()
    conn.close()
    monkeypatch.setattr(m, "OPENCODE_DB", db)
    return db


def test_qoder_sync_imports_and_filters_tools(qoder_fixture, tmp_path):
    db = tmp_path / "out.db"
    res = m.sync_qoder_history(db)
    assert res["imported"] == 1
    convs = m.get_all_conversations(source="qoder", db_path=db)
    assert len(convs) == 1
    assert convs[0]["title"] == "chat-1-abc123"
    msgs = m.get_conversation_messages(convs[0]["id"], db)
    texts = [x["content"] for x in msgs]
    assert texts == ["hello world", "hi there", "plain string reply"]  # tool blocks skipped
    assert [x["role"] for x in msgs] == ["user", "assistant", "assistant"]


def test_qoder_sync_idempotent(qoder_fixture, tmp_path):
    db = tmp_path / "out.db"
    m.sync_qoder_history(db)
    res2 = m.sync_qoder_history(db)
    assert res2["imported"] == 0
    assert len(m.get_all_conversations(source="qoder", db_path=db)) == 1


def test_opencode_sync_imports_text_parts(opencode_fixture, tmp_path):
    db = tmp_path / "out.db"
    res = m.sync_opencode_history(db)
    assert res["imported"] == 1  # archived session skipped
    convs = m.get_all_conversations(source="opencode", db_path=db)
    assert len(convs) == 1
    assert convs[0]["title"] == "Test Session"
    assert convs[0]["project_path"] == "Users/caleb/proj"
    msgs = m.get_conversation_messages(convs[0]["id"], db)
    texts = [x["content"] for x in msgs]
    assert texts == ["user says hi", "assistant answers"]  # reasoning part skipped


def test_opencode_sync_idempotent(opencode_fixture, tmp_path):
    db = tmp_path / "out.db"
    m.sync_opencode_history(db)
    res2 = m.sync_opencode_history(db)
    assert res2["imported"] == 0


def test_search_across_sources(qoder_fixture, opencode_fixture, tmp_path):
    db = tmp_path / "out.db"
    m.sync_qoder_history(db)
    m.sync_opencode_history(db)
    hits = m.search_all_conversations("hello", db_path=db)
    assert any(h["source"] == "qoder" for h in hits)
    hits2 = m.search_all_conversations("assistant answers", db_path=db)
    assert any(h["source"] == "opencode" for h in hits2)


def test_sync_all_wires_new_sources(qoder_fixture, opencode_fixture, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CLAUDE_DIR", tmp_path / "noclude")
    monkeypatch.setattr(m, "CODEX_DIR", tmp_path / "nocodex")
    results = m.sync_all_sources(tmp_path / "all.db")
    assert results["qoder"]["imported"] == 1
    assert results["opencode"]["imported"] == 1
