"""Tests for the demand dispatcher (Step 5 / 2a): classifier + team templates.

The classifier is tested through parse_category with fake client replies and
through classify_demand with a fake OpenRouterClient-compatible object so no
network or keys are needed. dispatch() shape and cache behavior are covered
directly.
"""
from __future__ import annotations

import pytest

from backend.core.dispatcher import (
    CATEGORY_LABELS,
    TEAM_TEMPLATES,
    VALID_CATEGORIES,
    _CLASSIFIER_CACHE,
    build_team,
    classify_demand,
    dispatch,
    parse_category,
)


class FakeClient:
    """OpenRouterClient-compatible fake: returns a canned verdict, counts calls."""

    def __init__(self, reply: str = "coding"):
        self.reply = reply
        self.calls = 0

    def chat(self, model_id: str, messages: list, max_tokens: int, extra_body=None):
        self.calls += 1
        return {"text": self.reply}


# --------------------------------------------------------------------- #
# parse_category
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "reply,expected",
    [
        ("research_design", "research_design"),
        ("  CODING  ", "coding"),
        ("testing is the answer", "testing"),
        ("media please", "media"),
        ("longtask", "longtask"),
        ("", "coding"),
        (None, "coding"),
        ("bogus text with no category", "coding"),
        ("I think coding is right", "coding"),
    ],
)
def test_parse_category(reply, expected):
    assert parse_category(reply) == expected


# --------------------------------------------------------------------- #
# classify_demand
# --------------------------------------------------------------------- #

def test_classify_demand_uses_client_reply():
    client = FakeClient("research_design")
    assert classify_demand("research the best UI patterns", client) == "research_design"
    assert client.calls == 1


def test_classify_demand_caches_per_goal():
    client = FakeClient("coding")
    first = classify_demand("build a login page", client)
    second = classify_demand("build a login page", client)
    assert first == second == "coding"
    assert client.calls == 1  # cached: only one classifier call


def test_classify_demand_fallback_without_client():
    assert classify_demand("anything at all") == "coding"


def test_classify_demand_unknown_category_falls_back():
    client = FakeClient("research_design extra words")
    # Parse only accepts exact category tokens; extra words still resolve.
    assert classify_demand("plan a feature", client) == "research_design"


# --------------------------------------------------------------------- #
# build_team / dispatch
# --------------------------------------------------------------------- #

def test_all_categories_have_templates():
    assert set(TEAM_TEMPLATES.keys()) == set(VALID_CATEGORIES)
    for cat in VALID_CATEGORIES:
        tpl = TEAM_TEMPLATES[cat]
        assert tpl["mode"] in {"auto", "code", "image"}
        assert tpl["effort"] in {"low", "med", "high", "xhigh", "max"}
        assert isinstance(tpl["tools"], list) and tpl["tools"]
        assert tpl["roles"]
        assert cat in CATEGORY_LABELS


def test_build_team_unknown_category_defaults_to_coding():
    team = build_team("does-not-exist")
    assert team["category"] == "does-not-exist"
    assert team["params"]["mode"] == TEAM_TEMPLATES["coding"]["mode"]


def test_build_team_effort_override():
    team = build_team("coding", effort="max")
    assert team["params"]["effort"] == "max"


def test_dispatch_shape():
    client = FakeClient("testing")
    result = dispatch("run the test suite and fix failures", client)
    assert result["category"] == "testing"
    assert "params" in result and "roles" in result and "rationale" in result
    assert result["params"]["mode"] == TEAM_TEMPLATES["testing"]["mode"]
