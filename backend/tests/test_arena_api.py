"""API tests for POST /api/v1/arena (variant race endpoint)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402


class _FakeLib:
    def __init__(self, agents):
        self._agents = {a["id"]: a for a in agents}

    def get(self, aid):
        return self._agents.get(aid)


class _FakeAdapter:
    """Stands in for _LongTaskBuilderAdapter: racers get a canned reply; the
    judge (spotted by its marker phrase) crowns variant "a"."""

    def __init__(self, *a, **k):
        pass

    def chat(self, messages, max_tokens=3000):
        content = messages[-1]["content"]
        if "judging a coding-agent race" in content:
            return {"text": '{"winner": "a", "reason": "tighter diff"}',
                    "cost_usd": 0.0001}
        return {"text": "reply from " + messages[0]["content"][:20],
                "cost_usd": 0.001}


@pytest.fixture
def client(monkeypatch):
    import main

    monkeypatch.setattr(main.app.state, "client", object(), raising=False)
    monkeypatch.setattr(main.app.state, "agents", _FakeLib([
        {"id": "a", "name": "Alpha", "emoji": "A", "prompt": "alpha persona"},
        {"id": "b", "name": "Beta", "emoji": "B", "prompt": "beta persona"},
    ]), raising=False)
    monkeypatch.setattr(main, "_LongTaskBuilderAdapter", _FakeAdapter)
    monkeypatch.setattr(main, "_longtask_model_chain", lambda role: ["fake/model"])
    monkeypatch.setattr(main, "_resolve_chat_model", lambda m: "fake/model")
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_arena_race_happy_path(client):
    r = client.post("/api/v1/arena",
                    json={"prompt": "write a fib", "variants": ["a", "b"]})
    assert r.status_code == 200
    d = r.json()
    assert [e["variant_id"] for e in d["entries"]] == ["a", "b"]
    assert d["winner"] == "a" and d["reason"] == "tighter diff"
    assert d["total_cost_usd"] > 0
    assert d["cost_aud"] >= 0  # 0 only when USD_PER_AUD is unset in tests


def test_arena_unknown_variant_400(client):
    r = client.post("/api/v1/arena",
                    json={"prompt": "x", "variants": ["a", "ghost"]})
    assert r.status_code == 400


def test_arena_needs_two_variants(client):
    r = client.post("/api/v1/arena", json={"prompt": "x", "variants": ["a"]})
    assert r.status_code == 400


def test_arena_blank_prompt_400(client):
    # The endpoint guards blank prompts (arena.run would ValueError -> 500).
    r = client.post("/api/v1/arena",
                    json={"prompt": "   ", "variants": ["a", "b"]})
    assert r.status_code == 400
