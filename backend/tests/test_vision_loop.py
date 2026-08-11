"""Tests for the vision loop: critique parsing + convergence caps."""
from __future__ import annotations

import pytest

from backend.core.ui_vision_loop import (
    DEFAULT_DELTA,
    DEFAULT_MAX_ITERS,
    parse_critique,
    run_vision_loop,
)


def fake_screenshot(score):
    """Build a critique_fn that always returns a given JSON score."""
    def _fn(image_path: str, criteria: str) -> str:
        return f'{{"score": {score}, "issues": ["issue"], "fixes": ["fix it"]}}'
    return _fn


def test_parse_critique_ok():
    parsed = parse_critique('{"score": 7.5, "issues": ["a", "b"], "fixes": ["x"]}')
    assert parsed["score"] == 7.5
    assert parsed["issues"] == ["a", "b"]
    assert parsed["fixes"] == ["x"]


def test_parse_critique_clamps_score():
    assert parse_critique('{"score": 99}')["score"] == 10.0
    assert parse_critique('{"score": -5}')["score"] == 0.0


@pytest.mark.parametrize("reply", [None, "", "not json at all", '{"score": "NaN"}'])
def test_parse_critique_conservative(reply):
    parsed = parse_critique(reply)
    assert parsed["score"] == 0.0
    assert parsed["issues"]


def test_loop_stops_when_score_hits_target():
    shots = []
    report = run_vision_loop(
        lambda i: shots.append(i) or f"shot_{i}.png",
        critique_fn=fake_screenshot(9.5),
        target=9.0,
        max_iters=5,
    )
    assert report["verdict"] == "passed"
    assert len(report["iterations"]) == 1


def test_loop_converges_on_flat_scores():
    report = run_vision_loop(
        lambda i: f"shot_{i}.png",
        critique_fn=fake_screenshot(6.0),
        target=9.0,
        max_iters=5,
    )
    # First iteration sets prev=6.0; second iteration scores 6.0 -> delta 0 < 0.05
    assert report["verdict"] == "converged"
    assert len(report["iterations"]) == 2


def test_loop_respects_max_iters_when_improving():
    scores = iter([5.0, 6.5, 7.5])

    def _crit(image_path: str, criteria: str) -> str:
        s = next(scores)
        return f'{{"score": {s}, "issues": ["i"], "fixes": ["f"]}}'

    report = run_vision_loop(
        lambda i: f"shot_{i}.png",
        critique_fn=_crit,
        target=9.0,
        max_iters=3,
        delta=0.5,  # improvements of 1.0/1.5 exceed delta -> keep going
    )
    assert report["verdict"] == "max_iters"
    assert len(report["iterations"]) == 3
    assert report["scores"] == [5.0, 6.5, 7.5]


def test_loop_applies_fixes():
    applied = []

    def _apply(fixes, i):
        applied.append((i, fixes))

    report = run_vision_loop(
        lambda i: f"shot_{i}.png",
        apply_fix_fn=_apply,
        critique_fn=fake_screenshot(5.0),
        target=9.0,
        max_iters=3,
    )
    assert applied  # fixes were handed to the pluggable applier
    assert report["best_score"] == 5.0
