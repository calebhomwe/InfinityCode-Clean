"""Evidence test: MemoryStore must close every SQLite connection it opens.

Before the fix: sqlite3.connect() is called for every operation but close() is
never called, so the test fails.
After the fix: every connect() is paired with a close(), so the test passes.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.memory import MemoryStore


class _TrackedConnection:
    """Wraps a real sqlite3.Connection and records close() calls."""

    def __init__(self, real_conn, tracker):
        object.__setattr__(self, "_conn", real_conn)
        object.__setattr__(self, "_tracker", tracker)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    def close(self):
        self._tracker["closes"] += 1
        return self._conn.close()

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *args):
        return self._conn.__exit__(*args)


def _make_fake_connect(tracker, real_connect):
    def fake_connect(*args, **kwargs):
        tracker["connects"] += 1
        real_conn = real_connect(*args, **kwargs)
        return _TrackedConnection(real_conn, tracker)

    return fake_connect


def test_memorystore_closes_connections():
    tracker = {"connects": 0, "closes": 0}

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "memory.db"

        real_connect = sqlite3.connect
        with patch.object(sqlite3, "connect", side_effect=_make_fake_connect(tracker, real_connect)):
            store = MemoryStore(db_path)
            store.add("hello", [0.1] * 384, chat_id="chat-1")
            store.search([0.1] * 384, top_k=3)
            store.list_recent(limit=10)
            store.count()
            store.delete("nonexistent-id")
            store.clear()

    assert tracker["connects"] > 0, "expected at least one connect() call"
    assert tracker["closes"] == tracker["connects"], (
        f"connection leak: {tracker['connects']} opens, {tracker['closes']} closes"
    )


if __name__ == "__main__":
    test_memorystore_closes_connections()
    print("1/1 passed")
