"""P2 referee: style contract + fidelity in the review gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.longtask.engine import Budget, LongTaskEngine  # noqa: E402
from core.longtask.journal import LongTaskJournal  # noqa: E402


class FakeBuilder:
    def chat(self, messages, max_tokens=3000, **kw):
        return {"text": '{"action":"finish","args":{"summary":"done"}}',
                "cost_usd": 0.001}


class FakeReviewer:
    def __init__(self, verdict: dict):
        self.verdict = verdict
        self.seen_prompts = []

    def chat(self, messages, max_tokens=800, **kw):
        self.seen_prompts.append(messages[-1]["content"])
        return {"text": json.dumps(self.verdict), "cost_usd": 0.0001}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    return r


def test_style_contract_reaches_referee_and_fidelity_lands(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    rev = FakeReviewer({"approve": True, "issues": [], "fidelity": 0.87})
    eng = LongTaskEngine(builder=FakeBuilder(), journal=j, reviewer=rev)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5),
                  style_hints=["LOCKED STYLE REFERENCE (user liked this): "
                               "palette=obsidian; tone=premium"])
    assert res["status"] == "completed"
    # The referee saw the style contract.
    assert "STYLE CONTRACT" in rev.seen_prompts[0]
    assert "palette=obsidian" in rev.seen_prompts[0]
    # Fidelity is journaled on the review step.
    steps = j.steps_for(res["task_id"])
    review = next(s for s in steps if s["kind"] == "review")
    payload = json.loads(review["result_json"])
    assert payload["approve"] is True and payload["fidelity"] == 0.87


def test_no_style_hints_no_contract_no_fidelity(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    rev = FakeReviewer({"approve": True, "issues": []})
    eng = LongTaskEngine(builder=FakeBuilder(), journal=j, reviewer=rev)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5))
    assert res["status"] == "completed"
    assert "STYLE CONTRACT" not in rev.seen_prompts[0]
    steps = j.steps_for(res["task_id"])
    review = next(s for s in steps if s["kind"] == "review")
    payload = json.loads(review["result_json"])
    assert "fidelity" not in payload


def test_bad_fidelity_is_dropped(repo, tmp_path):
    j = LongTaskJournal(tmp_path / "j.db")
    rev = FakeReviewer({"approve": True, "issues": [], "fidelity": "nonsense"})
    eng = LongTaskEngine(builder=FakeBuilder(), journal=j, reviewer=rev)
    res = eng.run(goal="x", repo_path=str(repo), budget=Budget(max_steps=5),
                  style_hints=["hint"])
    assert res["status"] == "completed"
    steps = j.steps_for(res["task_id"])
    review = next(s for s in steps if s["kind"] == "review")
    payload = json.loads(review["result_json"])
    assert "fidelity" not in payload
