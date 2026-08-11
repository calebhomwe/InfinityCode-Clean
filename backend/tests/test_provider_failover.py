"""Provider failover chain tests (0.1.59).

Covers: chain ordering per owner directive, auto-swap on quota/auth errors,
retry-in-place on transient errors, free-router slug forcing, all-dead final
error, and reasoning_content fallback (never "no response").

Run:  .venv\\Scripts\\python.exe -m pytest backend/tests/test_provider_failover.py -q
or:   cd backend && ..\\.venv\\Scripts\\python.exe -m tests.test_provider_failover
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from backend.tools import openrouter_client as orc
except ImportError:  # backend/ as cwd
    from tools import openrouter_client as orc  # type: ignore[no-redef]



@pytest.fixture(autouse=True)
def _isolate_llm_cache(tmp_path, monkeypatch):
    """The always-on response cache must never leak between tests: each case
    gets its own cache dir so a repeat prompt cannot short-circuit failover.
    Route health is persisted (Part 5) — isolate it too, or routes marked
    dead by one test poison every later client in the run."""
    orc._ROUTE_HEALTH.clear()
    monkeypatch.setenv("INFINITY_LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.setenv("INFINITY_ROUTE_HEALTH_FILE", str(tmp_path / "route_health.json"))
    yield
    orc._ROUTE_HEALTH.clear()
    monkeypatch.delenv("INFINITY_LLM_CACHE_DIR", raising=False)
    monkeypatch.delenv("INFINITY_ROUTE_HEALTH_FILE", raising=False)


def _routes() -> list:
    return [
        orc.Route(provider="dashscope", key="sk-ws-AAA111", base_url="http://dash",
                  source="env", free_only=False),
        orc.Route(provider="openrouter", key="sk-or-BBB222", base_url="http://or",
                  source="env", free_only=True),
        orc.Route(provider="deepseek", key="sk-ds-CCC333", base_url="http://ds",
                  source="env", free_only=False),
    ]


class _FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._behavior(kwargs)


class _FakeChat:
    def __init__(self, behavior):
        self.completions = _FakeCompletions(behavior)


class _FakeOpenAI:
    registry: dict = {}

    def __init__(self, api_key=None, base_url=None, **kw):
        self.api_key = api_key
        self.chat = _FakeChat(self.registry.get(api_key, lambda k: _ok("default")))


def _ok(text="hello", reasoning=None, finish="stop"):
    msg = types.SimpleNamespace(content=text, reasoning_content=reasoning)
    choice = types.SimpleNamespace(message=msg, finish_reason=finish,
                                   delta=types.SimpleNamespace(content=text))
    return types.SimpleNamespace(choices=[choice],
                                 usage=types.SimpleNamespace(prompt_tokens=1,
                                                             completion_tokens=1))


def _quota_exc():
    return RuntimeError("Error code: 403 - free quota exhausted, add funds")


def _auth_exc():
    return RuntimeError("Error code: 401 - User not found")


def _transient_exc():
    return ConnectionError("Connection timed out")


def _make_client(behavior_by_key, routes=None, max_retries=2):
    orc._ROUTE_HEALTH.clear()
    real_openai = orc.OpenAI
    orc.OpenAI = _FakeOpenAI
    _FakeOpenAI.registry = dict(behavior_by_key)
    real_collect = orc.collect_routes
    orc.collect_routes = lambda *a, **k: (routes or _routes())
    try:
        client = orc.OpenRouterClient(max_retries=max_retries)
        # Pre-warm every route's client while the fake is installed; failover
        # builds clients lazily and the patch is restored right after this.
        for r in client._routes:
            client._client_for(r)
    finally:
        orc.OpenAI = real_openai
        orc.collect_routes = real_collect
    return client


def test_chain_order_dashscope_first_then_deepseek_then_moonshot():
    env = {
        "DASHSCOPE_API_KEY": "sk-ws-AAA111",
        "OPENROUTER_API_KEY": "sk-or-BBB222",
        "DEEPSEEK_API_KEY": "sk-ds-CCC333",
        "KIMI_API_KEY": "sk-km-EEE555",
        "QWEN_WS_KEY2": "sk-ws-DDD444",
        "QWEN_WS_BASE_INTL": "http://ws2",
    }
    routes = orc.collect_routes(env=env, include_files=False)
    providers = [r["provider"] for r in routes]
    assert providers[0] == "dashscope"
    # spare workspace key joins the dashscope stage
    assert any(r["key"] == "sk-ws-DDD444" and r["provider"] == "dashscope" for r in routes)
    # OpenRouter re-enabled 2026-08-10 (owner request): free router stage first,
    # then the paid openrouter route, both before deepseek/moonshot direct.
    or_routes = [r for r in routes if r["provider"] == "openrouter"]
    assert len(or_routes) == 2, or_routes
    assert or_routes[0]["free_only"] is True
    assert or_routes[1]["free_only"] is False
    or_free_idx = routes.index(or_routes[0])
    ds_idx = [i for i, r in enumerate(routes) if r["provider"] == "deepseek"][0]
    ms_idx = [i for i, r in enumerate(routes) if r["provider"] == "moonshot"][0]
    assert or_free_idx < ds_idx < ms_idx


def test_forced_provider_pinned_first():
    env = {
        "DASHSCOPE_API_KEY": "sk-ws-AAA111",
        "DEEPSEEK_API_KEY": "sk-ds-CCC333",
        "INFINITY_LLM_PROVIDER": "deepseek",
    }
    routes = orc.collect_routes(env=env, include_files=False)
    assert routes[0]["provider"] == "deepseek"


def test_quota_error_swaps_to_next_route():
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_auth_exc()),
        "sk-ds-CCC333": lambda k: _ok("from deepseek"),
    })
    result = client.chat("qwen/qwen3.7-max", [{"role": "user", "content": "hi"}],
                         max_tokens=50)
    assert result["text"] == "from deepseek"
    assert client.provider == "deepseek"
    assert orc.route_state(client._routes[0]) == "dead"


def test_transient_error_retries_in_place_first():
    state = {"n": 0}

    def flaky(k):
        state["n"] += 1
        if state["n"] == 1:
            raise _transient_exc()
        return _ok("recovered")

    client = _make_client({
        "sk-ws-AAA111": flaky,
        "sk-or-BBB222": lambda k: _ok("should not be used"),
        "sk-ds-CCC333": lambda k: _ok("should not be used"),
    }, max_retries=2)
    # kill the backoff sleep for speed
    real_sleep = orc.time.sleep
    orc.time.sleep = lambda s: None
    try:
        result = client.chat("qwen/qwen3-coder", [{"role": "user", "content": "hi"}],
                             max_tokens=50)
    finally:
        orc.time.sleep = real_sleep
    assert result["text"] == "recovered"
    assert client.provider == "dashscope"  # stayed on the first route


def test_free_router_stage_forces_free_slug():
    captured = {}

    def capture(k):
        captured.update(k)
        return _ok("free answer")

    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": capture,
        "sk-ds-CCC333": lambda k: _ok("unused"),
    })
    client.chat("qwen/qwen3.7-max", [{"role": "user", "content": "hi"}],
                max_tokens=50)
    # 2026-08-10: the :free catalog rotates (every hardcoded slug 404s); the
    # free stage now forces the stable meta router openrouter/free.
    assert captured["model"] == "openrouter/free"


def test_all_routes_dead_raises_with_health():
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_auth_exc()),
        "sk-ds-CCC333": lambda k: (_ for _ in ()).throw(_quota_exc()),
    })
    try:
        client.chat("qwen/qwen3-coder", [{"role": "user", "content": "hi"}],
                    max_tokens=50)
        raise AssertionError("expected OpenRouterError")
    except orc.OpenRouterError as exc:
        assert "All LLM routes failed" in str(exc)
        assert "dashscope" in str(exc)


def test_reasoning_content_never_no_response():
    client = _make_client({
        "sk-ws-AAA111": lambda k: _ok(text=None, reasoning="THINK ANSWER"),
        "sk-or-BBB222": lambda k: _ok("unused"),
        "sk-ds-CCC333": lambda k: _ok("unused"),
    })
    result = client.chat("deepseek/deepseek-v4-flash",
                         [{"role": "user", "content": "hi"}], max_tokens=50)
    assert result["text"] == "THINK ANSWER"


def test_fatal_classification():
    assert orc.is_route_fatal(_quota_exc())
    assert orc.is_route_fatal(_auth_exc())
    assert orc.is_route_fatal(RuntimeError("404 model not exist"))
    assert not orc.is_route_fatal(_transient_exc())
    assert not orc.is_route_fatal(TimeoutError("read timeout"))


if __name__ == "__main__":
    for fn in [test_chain_order_dashscope_first_then_free_then_open,
               test_forced_provider_pinned_first,
               test_quota_error_swaps_to_next_route,
               test_transient_error_retries_in_place_first,
               test_free_router_stage_forces_free_slug,
               test_all_routes_dead_raises_with_health,
               test_reasoning_content_never_no_response,
               test_fatal_classification]:
        fn()
        print(f"PASS {fn.__name__}")
    print("all failover tests passed")


# --------------------------------------------------------------------------- #
# Part 2 — failover validation (reliability SLA): 429 -> route swap <2s,
# no dropped requests, cost attribution on the surviving route.
# --------------------------------------------------------------------------- #

def _rate_limit_exc():
    return RuntimeError("Error code: 429 - Rate limit reached for free tier")


def test_failover_on_429_swaps_within_2s():
    """A 429 is fatal for the route: the chain must advance immediately (no
    backoff sleep), so the swap completes well inside the 2s SLA."""
    import time
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_rate_limit_exc()),
        "sk-ds-CCC333": lambda k: _ok("paid answer"),
    })
    t0 = time.perf_counter()
    res = client.chat("deepseek/deepseek-v4-flash",
                      [{"role": "user", "content": "hi"}], max_tokens=50)
    elapsed = time.perf_counter() - t0
    assert "paid answer" in res.get("text", "")
    assert elapsed < 2.0, f"failover took {elapsed:.2f}s (SLA 2s)"
    assert client.provider == "deepseek"


def test_failover_does_not_drop_the_request():
    """The transition must answer, not error: one call, failed free route,
    surviving paid route -> text returned with no exception."""
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_rate_limit_exc()),
        "sk-ds-CCC333": lambda k: _ok("survivor"),
    })
    res = client.chat("deepseek/deepseek-v4-flash",
                      [{"role": "user", "content": "hi"}], max_tokens=50)
    assert res.get("text") == "survivor"


def test_cost_attribution_after_failover_uses_surviving_route_pricing():
    """Post-failover spend must be booked against the surviving route's model
    pricing (free router bills 0; deepseek-v4-flash bills > 0)."""
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_rate_limit_exc()),
        "sk-ds-CCC333": lambda k: _ok("paid", finish="stop"),
    })
    client.chat("deepseek/deepseek-v4-flash",
                [{"role": "user", "content": "hi"}], max_tokens=50)
    assert client.total_cost_usd > 0.0
    last = client.call_log[-1]
    assert last["model"] == "deepseek/deepseek-v4-flash"
    assert last["cost_usd"] > 0.0
    assert last["input_tokens"] >= 1 and last["output_tokens"] >= 1


def test_free_stage_429_marks_free_route_dead_only():
    """A 429 on the free stage must mark that route dead, not the paid route
    (both share the key, so the health key must distinguish by free_only)."""
    orc._ROUTE_HEALTH.clear()
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_rate_limit_exc()),
        "sk-ds-CCC333": lambda k: _ok("ok"),
    })
    client.chat("deepseek/deepseek-v4-flash",
                [{"role": "user", "content": "hi"}], max_tokens=50)
    free_dead = any(
        orc.route_state(r) == "dead" for r in client._routes
        if r["provider"] == "openrouter" and r["free_only"]
    )
    assert free_dead


# --------------------------------------------------------------------------- #
# Part 5 — reliability: walk deadline cap + route-health persistence.
# --------------------------------------------------------------------------- #

def test_walk_deadline_raises_with_health():
    """A slow/dead chain must never outlast INFINITY_WALK_TIMEOUT_S; the call
    fails fast with a health report instead of hanging for minutes."""
    import time

    def hang(k):
        time.sleep(1.2)
        raise _transient_exc()  # fail slowly so the walk wants to retry

    client = _make_client({
        "sk-ws-AAA111": hang,
        "sk-or-BBB222": hang,
        "sk-ds-CCC333": hang,
    })
    client._walk_timeout = 1.0
    t0 = time.perf_counter()
    with pytest.raises(orc.OpenRouterError) as exc:
        client.chat("deepseek/deepseek-v4-flash",
                    [{"role": "user", "content": "hi"}], max_tokens=50)
    assert (time.perf_counter() - t0) < 4.0
    assert "Failover walk exceeded" in str(exc.value)
    assert "Health:" in str(exc.value)


def test_route_health_persists_across_clients(tmp_path, monkeypatch):
    """A route proven dead must stay dead for a fresh client (same process),
    so a restart does not re-walk known-dead quotas."""
    orc._ROUTE_HEALTH.clear()
    monkeypatch.setenv("INFINITY_ROUTE_HEALTH_FILE", str(tmp_path / "health.json"))
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_rate_limit_exc()),
        "sk-ds-CCC333": lambda k: _ok("ok"),
    })
    client.chat("deepseek/deepseek-v4-flash",
                [{"role": "user", "content": "hi"}], max_tokens=50)
    assert orc.route_state(client._routes[0]) == "dead"

    # Fresh client in the same process: persisted health must be loaded.
    fresh = _make_client({
        "sk-ws-AAA111": lambda k: _ok("unexpected"),
        "sk-or-BBB222": lambda k: _ok("unexpected"),
        "sk-ds-CCC333": lambda k: _ok("ok"),
    })
    assert orc.route_state(fresh._routes[0]) == "dead"
    assert fresh._route_idx != 0
    orc._ROUTE_HEALTH.clear()


# --------------------------------------------------------------------------- #
# Part 2.1 — live free-tier stress findings (2026-08-10, N=50): route-lock
# serialization (p50 129.6s queueing), upstream-provider 403 killing the
# free route, thinking-model empty-content trap (30/49 empties).
# --------------------------------------------------------------------------- #

def _upstream_provider_exc():
    return RuntimeError(
        "Error code: 403 - {'error': {'message': 'Provider returned error', "
        "'code': 403, 'metadata': {'raw': '{\"code\":403, ' "
        "'\"reason\":\"NOT_ENOUGH_BALANCE\"}', 'provider_name': 'Novita'}}}")

def _upstream_generic_exc():
    return RuntimeError(
        "Error code: 403 - {'error': {'message': 'Provider returned error',"
        " 'code': 403, 'metadata': {'raw': '{\"code\":500, \"reason\":\"UPSTREAM_TIMEOUT\"}',"
        " 'provider_name': 'Novita'}}}")



def test_upstream_provider_error_is_transient_not_route_fatal():
    """On a FREE-router stage, OpenRouter relaying an upstream provider
    failure (Novita balance) must retry in place, never mark the aggregator
    route dead for 300s (the meta router picks another upstream next try)."""
    state = {"n": 0}

    def flaky_upstream(k):
        state["n"] += 1
        if state["n"] == 1:
            raise _upstream_provider_exc()
        return _ok("recovered upstream")

    routes = [
        orc.Route(provider="openrouter", key="sk-or-BBB222", base_url="http://or",
                  source="env", free_only=True),
        orc.Route(provider="deepseek", key="sk-ds-CCC333", base_url="http://ds",
                  source="env", free_only=False),
    ]
    client = _make_client({
        "sk-or-BBB222": flaky_upstream,
        "sk-ds-CCC333": lambda k: _ok("unused"),
    }, routes=routes, max_retries=2)
    real_sleep = orc.time.sleep
    orc.time.sleep = lambda s: None
    try:
        result = client.chat("qwen/qwen3.7-max", [{"role": "user", "content": "hi"}],
                             max_tokens=50)
    finally:
        orc.time.sleep = real_sleep
    assert result["text"] == "recovered upstream"
    assert orc.route_state(client._routes[0]) != "dead"
    # transient on the free-router stage where this incident originates
    assert not orc.is_route_fatal(_upstream_provider_exc(), free_only=True)


def test_concurrent_calls_do_not_serialize_on_route_lock():
    """Stress finding: holding _route_lock across the network call queued all
    50 concurrent requests (p50 129.6s). Four parallel calls whose fake
    network takes 0.5s each must finish in <1.5s (serial would be >=2.0s)."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    def slow(k):
        time.sleep(0.5)
        return _ok("parallel ok")

    client = _make_client({
        "sk-ws-AAA111": slow,
        "sk-or-BBB222": slow,
        "sk-ds-CCC333": slow,
    })
    real_meter = orc.credits_meter
    orc.credits_meter = lambda *a, **k: None  # isolate: measure only the walk
    try:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(
                lambda i: client.chat(
                    "qwen/qwen3.7-max",
                    [{"role": "user", "content": f"concurrent {i}"}],
                    max_tokens=50),
                range(4)))
        elapsed = time.perf_counter() - t0
    finally:
        orc.credits_meter = real_meter
    assert all(r["text"] == "parallel ok" for r in results)
    assert elapsed < 1.5, f"calls serialized: {elapsed:.2f}s for 4x0.5s work"


def test_empty_reply_retries_once_with_token_floor():
    """Free-router trap: a thinking-only model burns a small max_tokens
    budget and returns empty content. chat() must retry once with a 4000
    floor and deliver the answer (exactly one retry, never looping)."""
    calls = []

    def empty_then_answer(k):
        calls.append(k.get("max_tokens"))
        if len(calls) == 1:
            return _ok(text="", finish="stop")
        return _ok(text="the actual answer", finish="stop")

    client = _make_client({
        "sk-ws-AAA111": empty_then_answer,
        "sk-or-BBB222": lambda k: _ok("unused"),
        "sk-ds-CCC333": lambda k: _ok("unused"),
    })
    result = client.chat("openrouter/free", [{"role": "user", "content": "hi"}],
                         max_tokens=64)
    assert result["text"] == "the actual answer"
    assert len(calls) == 2, f"expected exactly one retry, got {len(calls)} calls"
    assert calls[0] == 64 and calls[1] == 4000


def test_advance_route_ignores_stale_concurrent_failure():
    """Two workers failing the same route must not cascade: the second
    _advance_route sees the index already moved and must not kill the new
    current route for someone else's error."""
    client = _make_client({
        "sk-ws-AAA111": lambda k: _ok("a"),
        "sk-or-BBB222": lambda k: _ok("b"),
        "sk-ds-CCC333": lambda k: _ok("c"),
    })
    r0 = client._routes[0]
    assert client._advance_route(RuntimeError("first failure"), failed_route=r0)
    assert orc.route_state(client._routes[0]) == "dead"
    # Stale failure of r0: current already moved — retry there, kill nothing.
    assert client._advance_route(RuntimeError("stale failure"), failed_route=r0)
    assert orc.route_state(client._routes[1]) != "dead"
    assert orc.route_state(client._routes[2]) != "dead"


# --------------------------------------------------------------------------- #
# Part 2.2 — cost attribution post-failover must be correct: the free stage
# bills $0 even when a priced model is requested; the paid stage bills the
# served model's real pricing.
# --------------------------------------------------------------------------- #

def test_free_stage_bills_zero_even_for_priced_requested_model():
    """Requesting deepseek-chat through the free stage must bill $0 — the
    route maps the model to openrouter/free; billing follows the served
    model, not the requested id."""
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: _ok("free answer"),
        "sk-ds-CCC333": lambda k: _ok("unused"),
    })
    res = client.chat("deepseek/deepseek-chat",
                      [{"role": "user", "content": "hi"}], max_tokens=50)
    assert res["text"] == "free answer"
    assert res["cost_usd"] == 0.0
    assert client.total_cost_usd == 0.0
    assert client.call_log[-1]["model"] == "openrouter/free"
    assert client.call_log[-1]["cost_usd"] == 0.0


def test_paid_stage_bills_served_model_pricing():
    """After failover to the paid deepseek route, billing uses the served
    model's real pricing (> $0), attributed to the surviving route."""
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-BBB222": lambda k: (_ for _ in ()).throw(_rate_limit_exc()),
        "sk-ds-CCC333": lambda k: _ok("paid answer"),
    })
    res = client.chat("deepseek/deepseek-chat",
                      [{"role": "user", "content": "hi"}], max_tokens=50)
    assert res["text"] == "paid answer"
    assert res["cost_usd"] > 0.0
    assert client.call_log[-1]["cost_usd"] > 0.0

def test_choices_none_yields_empty_text_not_crash():
    """Live bug 2026-08-11: upstream returned choices=None under concurrent
    free-tier load; extraction raised TypeError past the guard. chat() and
    chat_with_vision() must degrade to empty text, never raise raw."""
    from unittest.mock import patch as _patch

    class _Resp:
        choices = None
        usage = None

    client = _make_client({"sk-or-BBB222": lambda k: _ok("unreached")})
    with _patch.object(client, "_create_completion",
                       return_value=(_Resp(), "openrouter/free")):
        res = client.chat("deepseek/deepseek-chat",
                          [{"role": "user", "content": "hi"}],
                          max_tokens=4000, max_continuations=0)
    assert res["text"] == ""

    client2 = _make_client({"sk-or-BBB222": lambda k: _ok("unreached")})
    with _patch.object(client2, "_create_completion",
                       return_value=(_Resp(), "openrouter/free")):
        vres = client2.chat_with_vision(
            "deepseek/deepseek-chat",
            [{"role": "user", "content": "hi"}])
    assert vres["text"] == ""

def _provider_balance_exc():
    return RuntimeError(
        "Error code: 403 - {'error': {'message': 'Provider returned error',"
        " 'metadata': {'raw': '{\"code\":403, \"reason\":\"NOT_ENOUGH_BALANCE\"}'}}}")


def test_provider_balance_error_is_route_fatal_no_retry_burn():
    """NOT_ENOUGH_BALANCE relayed by a paid stage must break out of the
    retry loop immediately (route-fatal) so the chain advances, while the
    same error stays transient on free-router stages (another upstream may
    answer) and generic upstream errors stay transient everywhere."""
    assert orc.is_route_fatal(_provider_balance_exc()) is True
    assert orc.is_route_fatal(_provider_balance_exc(), free_only=True) is False
    # Novita NOT_ENOUGH_BALANCE is the same shape: paid-fatal, free-transient.
    assert orc.is_route_fatal(_upstream_provider_exc()) is True
    assert orc.is_route_fatal(_upstream_provider_exc(), free_only=True) is False
    # Non-balance upstream hiccups stay transient on every stage.
    assert orc.is_route_fatal(_upstream_generic_exc()) is False
    assert orc.is_route_fatal(_upstream_generic_exc(), free_only=True) is False
    calls = []

    def paid(k):
        calls.append(k)
        raise _provider_balance_exc()

    routes = [
        orc.Route(provider="dashscope", key="sk-ws-AAA111", base_url="http://dash",
                  source="env", free_only=False),
        orc.Route(provider="openrouter", key="sk-or-PPP999", base_url="http://or",
                  source="env", free_only=False),
        orc.Route(provider="deepseek", key="sk-ds-CCC333", base_url="http://ds",
                  source="env", free_only=False),
    ]
    client = _make_client({
        "sk-ws-AAA111": lambda k: (_ for _ in ()).throw(_quota_exc()),
        "sk-or-PPP999": paid,
        "sk-ds-CCC333": lambda k: _ok("survivor"),
    }, routes=routes, max_retries=3)
    res = client.chat("deepseek/deepseek-chat",
                      [{"role": "user", "content": "hi"}], max_tokens=50)
    assert res["text"] == "survivor"
    assert len(calls) == 1  # no in-place retries of a balance failure

def test_stream_skips_choices_none_chunk_without_aborting():
    """A streamed chunk with choices=None must be skipped, not abort the
    whole stream (same defect class as the chat() choices=None fix)."""
    from unittest.mock import patch as _patch

    good = types.SimpleNamespace(
        usage=None,
        choices=[types.SimpleNamespace(
            delta=types.SimpleNamespace(content="ok", reasoning=None))])
    bad = types.SimpleNamespace(usage=None, choices=None)

    class _Resp:
        choices = None
        usage = None

    client = _make_client({"sk-or-BBB222": lambda k: _ok("unreached")})
    with _patch.object(client, "_open_stream",
                       return_value=(iter([bad, good]), "openrouter/free")):
        wrapper = client.chat_stream("deepseek/deepseek-chat",
                                     [{"role": "user", "content": "hi"}])
        out = "".join(list(wrapper))
    assert out == "ok"
    assert wrapper.stream_error is None
