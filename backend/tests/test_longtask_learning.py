"""Tests for the W2 self-teaching flywheel: journal lessons + learning.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.journal import LongTaskJournal  # noqa: E402
from core.longtask.learning import (  # noqa: E402
    DISTILL_THRESHOLD,
    PLAYBOOK_NAME,
    extract_lessons,
    frontend_related,
    maybe_distill_playbook,
    recall_lessons,
)


@pytest.fixture
def journal(tmp_path):
    return LongTaskJournal(tmp_path / "j.db")


# --- journal lesson storage ----------------------------------------------- #

def test_add_and_search_lessons(journal):
    journal.add_lesson("t1", "general", "always run tests before finishing",
                       [1.0, 0.0, 0.0])
    journal.add_lesson("t2", "frontend", "use one accent color only",
                       [0.0, 1.0, 0.0])
    hits = journal.search_lessons([0.99, 0.05, 0.0], top_k=3, min_score=0.25)
    assert hits and hits[0]["lesson"].startswith("always run tests")
    assert hits[0]["score"] > 0.9
    # orthogonal query only finds nothing above threshold for the other lesson
    hits2 = journal.search_lessons([0.0, 0.0, 1.0])
    assert hits2 == []


def test_bump_and_distill_flags(journal):
    lid = journal.add_lesson("t1", "frontend", "spacing scale of 4s", [1.0])
    journal.bump_lesson_use(lid)
    row = [l for l in journal.lessons() if l["id"] == lid][0]
    assert row["uses"] == 1 and row["distilled"] == 0
    journal.mark_lessons_distilled([lid])
    assert journal.lessons(undistilled_only=True) == []
    assert journal.lessons(kind="frontend", undistilled_only=True) == []
    assert len(journal.lessons(kind="frontend")) == 1


def test_lesson_without_embedding_never_matches(journal):
    journal.add_lesson("t1", "general", "no vector stored", None)
    assert journal.search_lessons([1.0, 0.0]) == []


# --- frontend_related -------------------------------------------------------- #

def test_frontend_related_by_goal():
    assert frontend_related({"goal": "Build a landing page with nice vibes"}, [])
    assert frontend_related({"goal": "style the dashboard"}, [])
    assert not frontend_related({"goal": "fix the database migration"}, [])


def test_frontend_related_by_touched_files():
    steps = [{"args_json": json.dumps({"path": "src/App.tsx"})}]
    assert frontend_related({"goal": "do the thing"}, steps)
    steps_py = [{"args_json": json.dumps({"path": "backend/main.py"})}]
    assert not frontend_related({"goal": "do the thing"}, steps_py)
    # malformed args_json must not raise
    assert not frontend_related({"goal": "x"}, [{"args_json": "{oops"}])


# --- extract_lessons ----------------------------------------------------------- #

def test_extract_lessons_parses_and_caps():
    def chat_fn(messages, max_tokens=600):
        assert max_tokens == 600
        return {"text": "- run the tests before finishing\n"
                        "- read files before editing them\n"
                        "- keep commits small\n"
                        "- extra fourth line ignored"}

    task = {"goal": "g", "status": "completed", "result": "ok"}
    lessons = extract_lessons(task, [], chat_fn)
    assert len(lessons) == 3
    assert lessons[0] == "run the tests before finishing"


def test_extract_lessons_never_raises():
    def boom(messages, max_tokens=600):
        raise RuntimeError("model dead")

    assert extract_lessons({"goal": "g"}, [], boom) == []
    # chat returning garbage yields no lessons, not an exception
    assert extract_lessons({"goal": "g"}, [],
                           lambda m, max_tokens=600: {"text": "hmm"}) == []


# --- recall_lessons -------------------------------------------------------------- #

def test_recall_lessons_uses_journal_search(journal):
    journal.add_lesson("t1", "general", "verify the build before finish",
                       [1.0, 0.0])
    hits = recall_lessons("anything", lambda texts: [[1.0, 0.0]], journal)
    assert hits and hits[0]["lesson"] == "verify the build before finish"
    # embed failure degrades to []
    def boom(texts):
        raise RuntimeError("embed down")
    assert recall_lessons("x", boom, journal) == []
    # empty embed result degrades to []
    assert recall_lessons("x", lambda texts: [], journal) == []


# --- playbook distillation ------------------------------------------------------ #

def test_distill_below_threshold_noop(journal, tmp_path):
    for i in range(DISTILL_THRESHOLD - 1):
        journal.add_lesson(f"t{i}", "frontend", f"frontend rule number {i}", [1.0])
    out = maybe_distill_playbook(journal, lambda m, max_tokens=900: {"text": "- x"},
                                 tmp_path / "skills")
    assert out is None


def test_distill_writes_playbook_and_marks_lessons(journal, tmp_path):
    ids = [journal.add_lesson(f"t{i}", "frontend", f"frontend rule {i} ok", [1.0])
           for i in range(DISTILL_THRESHOLD)]

    def chat_fn(messages, max_tokens=900):
        return {"text": "- use a spacing scale\n- one accent color per view\n"}

    skills_dir = tmp_path / "skills"
    out = maybe_distill_playbook(journal, chat_fn, skills_dir)
    assert out == skills_dir / f"{PLAYBOOK_NAME}.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["name"] == PLAYBOOK_NAME
    assert [s["instruction"] for s in data["steps"]] == [
        "use a spacing scale", "one accent color per view"]
    # lessons consumed by the distillation are marked
    assert journal.lessons(kind="frontend", undistilled_only=True) == []
