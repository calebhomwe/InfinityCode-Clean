"""VariantArena contract tests — fake chat/judge fns, no providers."""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from core.arena import VariantArena  # noqa: E402


def _variants(n=3):
    return [{"id": f"v{i}", "name": f"Variant {i}", "emoji": "🤖"}
            for i in range(n)]


def _echo_chat(text="def hello():\n    return 1", cost=0.01, delay=0.0):
    def chat_fn(variant, messages):
        if delay:
            time.sleep(delay)
        return {"text": f"{variant['id']}: {text}", "cost_usd": cost}
    return chat_fn


# --- 1. parallel speedup ---------------------------------------------------- #

def test_parallel_speedup():
    # 3 x 0.3s in parallel must land well under the 0.9s serial cost.
    arena = VariantArena(chat_fn=_echo_chat(delay=0.3), max_workers=3)
    t0 = time.time()
    res = arena.run("write hello", _variants(3))
    wall = time.time() - t0
    assert wall < 0.9 * 0.75  # comfortably under 3x serial
    assert len(res["entries"]) == 3
    assert all(e["error"] is None for e in res["entries"])
    assert res["duration_ms"] >= 250  # sanity: we really did sleep


def test_max_workers_bounds_concurrency():
    # 4 variants but 2 workers -> at least ~2 waves of 0.2s each.
    arena = VariantArena(chat_fn=_echo_chat(delay=0.2), max_workers=2)
    t0 = time.time()
    arena.run("write hello", _variants(4))
    wall = time.time() - t0
    assert wall >= 0.35  # cannot be a single 4-wide wave


# --- 2. order preservation --------------------------------------------------- #

def test_entries_preserve_input_order():
    variants = [{"id": "c", "name": "C"}, {"id": "a", "name": "A"},
                {"id": "b", "name": "B"}]

    def chat_fn(variant, messages):
        # later ids finish first — order must still be c, a, b
        time.sleep(0.05 * {"c": 1, "a": 2, "b": 3}[variant["id"]])
        return {"text": variant["id"], "cost_usd": 0.0}

    res = VariantArena(chat_fn=chat_fn).run("go", variants)
    assert [e["variant_id"] for e in res["entries"]] == ["c", "a", "b"]
    assert res["prompt"] == "go"


# --- 3. failure isolation ------------------------------------------------------ #

def test_one_variant_raising_does_not_kill_the_race():
    def chat_fn(variant, messages):
        if variant["id"] == "v1":
            raise RuntimeError("provider melted down")
        return {"text": f"ok from {variant['id']}", "cost_usd": 0.02}

    res = VariantArena(chat_fn=chat_fn).run("go", _variants(3))
    by_id = {e["variant_id"]: e for e in res["entries"]}
    assert by_id["v1"]["error"] == "provider melted down"
    assert by_id["v1"]["response"] is None
    for vid in ("v0", "v2"):
        assert by_id[vid]["error"] is None
        assert by_id[vid]["response"] == f"ok from {vid}"
    assert abs(res["total_cost_usd"] - 0.04) < 1e-9


def test_error_string_is_capped():
    def chat_fn(variant, messages):
        raise RuntimeError("x" * 9000)

    res = VariantArena(chat_fn=chat_fn).run("go", _variants(2))
    assert all(len(e["error"]) <= 300 for e in res["entries"])


def test_all_fail_still_returns_normally():
    def chat_fn(variant, messages):
        raise RuntimeError("everyone died")

    judge = {"called": False}

    def judge_fn(prompt, entries):
        judge["called"] = True
        return {"winner": None, "reason": ""}

    res = VariantArena(chat_fn=chat_fn, judge_fn=judge_fn).run("go", _variants(2))
    assert res["winner"] is None
    assert "no successful entries" in res["reason"]
    assert judge["called"] is False  # judge never sees error-only fields


# --- 4. judge behaviour --------------------------------------------------------- #

def test_judge_picks_winner_among_entries():
    seen = {}

    def judge_fn(prompt, entries):
        seen["prompt"] = prompt
        seen["entries"] = entries
        return {"winner": "v1", "reason": "cleaner diff"}

    res = VariantArena(chat_fn=_echo_chat(), judge_fn=judge_fn).run(
        "refactor x", _variants(3))
    assert res["winner"] == "v1"
    assert res["reason"] == "cleaner diff"
    assert seen["prompt"] == "refactor x"
    assert {e["variant_id"] for e in seen["entries"]} == {"v0", "v1", "v2"}


def test_judge_only_sees_error_free_entries():
    def chat_fn(variant, messages):
        if variant["id"] == "v0":
            raise RuntimeError("boom")
        return {"text": "hi", "cost_usd": 0.0}

    def judge_fn(prompt, entries):
        return {"winner": entries[0]["variant_id"], "reason": "sole survivor"}

    res = VariantArena(chat_fn=chat_fn, judge_fn=judge_fn).run("go", _variants(2))
    assert res["winner"] == "v1"


def test_judge_raising_gives_no_winner():
    def judge_fn(prompt, entries):
        raise RuntimeError("judge exploded")

    res = VariantArena(chat_fn=_echo_chat(), judge_fn=judge_fn).run(
        "go", _variants(2))
    assert res["winner"] is None
    assert "judge exploded" in res["reason"]


def test_judge_picking_unknown_id_is_ignored():
    def judge_fn(prompt, entries):
        return {"winner": "ghost", "reason": "hallucinated participant"}

    res = VariantArena(chat_fn=_echo_chat(), judge_fn=judge_fn).run(
        "go", _variants(2))
    assert res["winner"] is None
    assert res["reason"] == "judge picked an invalid entry"


def test_judge_picking_a_failed_variant_is_ignored():
    def chat_fn(variant, messages):
        if variant["id"] == "v0":
            raise RuntimeError("boom")
        return {"text": "hi", "cost_usd": 0.0}

    def judge_fn(prompt, entries):
        return {"winner": "v0", "reason": "favoured the crashed one"}

    res = VariantArena(chat_fn=chat_fn, judge_fn=judge_fn).run("go", _variants(2))
    assert res["winner"] is None
    assert res["reason"] == "judge picked an invalid entry"


def test_no_judge_gives_no_winner_with_reason():
    res = VariantArena(chat_fn=_echo_chat()).run("go", _variants(2))
    assert res["winner"] is None
    assert res["reason"]


# --- 5. cost guardrail ------------------------------------------------------------- #

def test_budget_exceeded_flips_when_total_over_budget():
    # 3 x 0.05 = 0.15 total > 0.10 budget
    res = VariantArena(chat_fn=_echo_chat(cost=0.05)).run(
        "go", _variants(3), max_cost_usd=0.10)
    assert res["budget_exceeded"] is True
    assert abs(res["total_cost_usd"] - 0.15) < 1e-9
    # all replies are still recorded — metering never pre-blocks
    assert all(e["response"] for e in res["entries"])


def test_budget_not_exceeded_when_under():
    res = VariantArena(chat_fn=_echo_chat(cost=0.01)).run(
        "go", _variants(3), max_cost_usd=0.10)
    assert res["budget_exceeded"] is False


def test_zero_budget_means_no_guardrail():
    res = VariantArena(chat_fn=_echo_chat(cost=5.0)).run(
        "go", _variants(2), max_cost_usd=0.0)
    assert res["budget_exceeded"] is False
    assert abs(res["total_cost_usd"] - 10.0) < 1e-9


def test_failed_variant_costs_do_not_count():
    def chat_fn(variant, messages):
        if variant["id"] == "v0":
            raise RuntimeError("boom")
        return {"text": "hi", "cost_usd": 0.03}

    res = VariantArena(chat_fn=chat_fn).run("go", _variants(2))
    assert abs(res["total_cost_usd"] - 0.03) < 1e-9


# --- 6. validation -------------------------------------------------------------------- #

def test_blank_prompt_raises_valueerror():
    arena = VariantArena(chat_fn=_echo_chat())
    with pytest.raises(ValueError):
        arena.run("", _variants(2))
    with pytest.raises(ValueError):
        arena.run("   \n\t ", _variants(2))


def test_too_few_variants_raises_valueerror():
    arena = VariantArena(chat_fn=_echo_chat())
    with pytest.raises(ValueError):
        arena.run("go", [])
    with pytest.raises(ValueError):
        arena.run("go", [{"id": "solo", "name": "Solo"}])


# --- 7. system prompt wiring ------------------------------------------------------------- #

def test_messages_shape_and_persona_wiring():
    seen = {}

    def chat_fn(variant, messages):
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "the race prompt"
        seen[variant["id"]] = messages[0]["content"]
        return {"text": "ok", "cost_usd": 0.0}

    variants = [{"id": "custom", "name": "Custom",
                 "prompt": "You are DaCoder, stay in character."},
                {"id": "default", "name": "Default"}]
    VariantArena(chat_fn=chat_fn).run("the race prompt", variants)
    assert seen["custom"] == "You are DaCoder, stay in character."
    assert seen["default"] != ""  # a sane default persona was supplied
    assert seen["default"] != seen["custom"]


def test_entry_fields_contract():
    variants = [{"id": "v0", "name": "Vee", "emoji": "🦾"}]
    res = VariantArena(chat_fn=_echo_chat(cost=0.007)).run(
        "go", variants + [{"id": "v1", "name": "Vee2"}])
    for key in ("prompt", "entries", "winner", "reason",
                "total_cost_usd", "budget_exceeded", "duration_ms"):
        assert key in res
    e = res["entries"][0]
    for key in ("variant_id", "name", "emoji", "response",
                "error", "cost_usd", "duration_ms"):
        assert key in e
    assert e["emoji"] == "🦾" and e["name"] == "Vee"
    assert isinstance(e["duration_ms"], int) and e["duration_ms"] >= 0
    assert isinstance(res["duration_ms"], int) and res["duration_ms"] >= 0
