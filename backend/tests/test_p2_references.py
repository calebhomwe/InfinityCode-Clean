"""P2: feedback + reference-lock loop (journal, helpers, endpoints)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.journal import LongTaskJournal  # noqa: E402


# --- journal layer --------------------------------------------------------- #


def test_feedback_roundtrip(tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    tid = j.create_task(goal="x", repo_path=str(tmp_path))
    aid = j.add_artifact(tid, "code", "a.py", "print(1)", {})
    fid = j.add_feedback(aid, "like", "loved the palette")
    assert fid > 0
    rows = j.feedback_for(aid)
    assert len(rows) == 1 and rows[0]["signal"] == "like"
    assert rows[0]["note"] == "loved the palette"


def test_get_artifact_and_task_scoping(tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    tid = j.create_task(goal="x", repo_path=str(tmp_path))
    aid = j.add_artifact(tid, "code", "a.py", "body", {})
    art = j.get_artifact(aid)
    assert art is not None and art["task_id"] == tid and art["path"] == "a.py"
    assert j.get_artifact("nope") is None


def test_lock_and_search_references(tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    dna = {"palette": "obsidian + electric blue", "tone": "premium"}
    j.lock_reference("style", "art1", "a.py", dna, [1.0, 0.0])
    j.lock_reference("style", "art2", "b.py", {"tone": "other"}, [0.0, 1.0])
    locked = j.locked_references()
    assert len(locked) == 2
    hits = j.search_locked_references([0.99, 0.05], top_k=1)
    assert len(hits) == 1
    assert hits[0]["dna"]["palette"].startswith("obsidian")
    # Opposite-direction query under min_score -> no hits.
    assert j.search_locked_references([-1.0, 0.0], min_score=0.5) == []
    # No embedding -> graceful empty.
    assert j.search_locked_references([]) == []


# --- helpers --------------------------------------------------------------- #


def test_extract_style_dna_parses_wrapped_json():
    import main

    def chat_fn(messages, max_tokens=400):
        return {"text": 'Sure! {"palette": "dark", "tone": "calm"} hope that helps',
                "cost_usd": 0.0001}

    dna = main._extract_style_dna({"path": "x.py", "body": "print(1)"}, chat_fn)
    assert dna == {"palette": "dark", "tone": "calm"}


def test_locked_dna_lines_formats_hits(monkeypatch, tmp_path):
    import main

    j = LongTaskJournal(tmp_path / "j.db")
    j.lock_reference("style", "art1", "a.py",
                     {"palette": "obsidian", "tone": "premium"}, [1.0, 0.0])
    monkeypatch.setattr(main, "LONGTASK_JOURNAL", j)

    class _Client:
        def embed(self, texts):
            return [[0.99, 0.02]]

    lines = main._locked_dna_lines("build a hero section", _Client())
    assert len(lines) == 1
    assert lines[0].startswith("LOCKED STYLE REFERENCE")
    assert "palette=obsidian" in lines[0]


def test_locked_dna_lines_no_embed(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "LONGTASK_JOURNAL",
                        LongTaskJournal(tmp_path / "j.db"))
    assert main._locked_dna_lines("x", object()) == []


# --- endpoints -------------------------------------------------------------- #


@pytest.fixture
def client(monkeypatch, tmp_path):
    import main
    from core.longtask.journal import LongTaskJournal

    monkeypatch.setattr(main, "LONGTASK_JOURNAL",
                        LongTaskJournal(tmp_path / "lt.db"))
    promoted = []
    monkeypatch.setattr(main, "_promote_artifact_to_reference",
                        lambda aid: promoted.append(aid))
    fixture = {"promoted": promoted}
    from fastapi.testclient import TestClient
    tc = TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})
    tc.fixture = fixture  # type: ignore[attr-defined]
    return tc


def _seed(client, tmp_path):
    import main
    tid = main.LONGTASK_JOURNAL.create_task(goal="x", repo_path=str(tmp_path))
    aid = main.LONGTASK_JOURNAL.add_artifact(tid, "code", "a.py", "print(1)", {})
    return tid, aid


def test_feedback_like_locks(client, tmp_path):
    tid, aid = _seed(client, tmp_path)
    r = client.post(f"/api/v1/longtasks/{tid}/artifacts/{aid}/feedback",
                    json={"signal": "like", "note": "yes"})
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is True and body["signal"] == "like"
    assert client.fixture["promoted"] == [aid]  # type: ignore[attr-defined]


def test_feedback_dislike_no_lock(client, tmp_path):
    tid, aid = _seed(client, tmp_path)
    r = client.post(f"/api/v1/longtasks/{tid}/artifacts/{aid}/feedback",
                    json={"signal": "dislike"})
    assert r.status_code == 200 and r.json()["locked"] is False
    assert client.fixture["promoted"] == []  # type: ignore[attr-defined]


def test_feedback_bad_signal_400(client, tmp_path):
    tid, aid = _seed(client, tmp_path)
    r = client.post(f"/api/v1/longtasks/{tid}/artifacts/{aid}/feedback",
                    json={"signal": "meh"})
    assert r.status_code == 400


def test_feedback_wrong_task_404(client, tmp_path):
    tid, aid = _seed(client, tmp_path)
    r = client.post(f"/api/v1/longtasks/othertask/artifacts/{aid}/feedback",
                    json={"signal": "like"})
    assert r.status_code == 404


def test_locked_references_endpoint(client, tmp_path):
    import main
    main.LONGTASK_JOURNAL.lock_reference("style", "art1", "a.py",
                                         {"tone": "premium"}, None)
    r = client.get("/api/v1/references/locked")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1 and json.loads(rows[0]["dna_json"])["tone"] == "premium"
