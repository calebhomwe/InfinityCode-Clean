"""Tests for core.provider_chat — model-id -> provider dispatch."""
from __future__ import annotations

import pytest

from core.provider_chat import _norm, provider_chat


@pytest.fixture(autouse=True)
def _isolate_llm_cache(tmp_path, monkeypatch):
    """The always-on response cache must never leak between tests: these
    cases share the same prompts, so a repeat would short-circuit dispatch."""
    monkeypatch.setenv("INFINITY_LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    yield
    monkeypatch.delenv("INFINITY_LLM_CACHE_DIR", raising=False)


class FakeClient:
    """Records calls and returns a canned dict reply."""

    def __init__(self, text="ok", cost=0.01):
        self.calls = []
        self.text = text
        self.cost = cost

    def chat(self, model_id, messages, max_tokens=3000):
        self.calls.append((model_id, messages, max_tokens))
        return {"text": self.text, "cost_usd": self.cost}


MSGS = [{"role": "user", "content": "hi"}]


def test_kimi_routes_to_moonshot():
    or_, moon, ds = FakeClient(), FakeClient("kimi"), FakeClient()
    out = provider_chat("moonshotai/kimi-k3", MSGS, 500, or_, moon, ds)
    assert out["text"] == "kimi"
    assert moon.calls and not or_.calls and not ds.calls
    assert moon.calls[0][1] is MSGS  # history passed through intact


def test_dashscope_routes_to_dashscope_client():
    or_, moon, ds = FakeClient(), FakeClient(), FakeClient("qwen", 0.02)
    out = provider_chat("dashscope/qwen3.8-max", MSGS, 500, or_, moon, ds)
    assert out == {"text": "qwen", "cost_usd": 0.02}
    assert ds.calls and not or_.calls


def test_dashscope_without_client_falls_to_openrouter_stripped():
    or_ = FakeClient("or")
    out = provider_chat("dashscope/qwen3.8-max", MSGS, 500, or_, None, None)
    assert out["text"] == "or"
    assert or_.calls[0][0] == "qwen3.8-max"  # prefix stripped for OR


def test_plain_id_goes_to_openrouter_untouched():
    or_ = FakeClient("or")
    provider_chat("qwen/qwen3-coder", MSGS, 500, or_)
    assert or_.calls[0][0] == "qwen/qwen3-coder"


def test_local_without_server_raises(monkeypatch):
    import core.provider_chat as pc
    monkeypatch.setattr(pc, "probe_local", lambda timeout=1.0: None)
    with pytest.raises(RuntimeError, match="no local llama.cpp server"):
        provider_chat("local/fable-max-llamacpp", MSGS, 500, FakeClient())


def test_local_with_server_uses_local_client(monkeypatch):
    import core.provider_chat as pc

    class FakeLocal:
        def __init__(self, api_key=None, base_url=None):
            self.base_url = base_url
            self.calls = []

        def chat(self, model_id, messages, max_tokens=3000):
            self.calls.append((model_id, messages, max_tokens))
            return {"text": "local-ok", "cost_usd": 0.0}

    made = {}

    def fake_ctor(api_key=None, base_url=None):
        made["client"] = FakeLocal(api_key, base_url)
        return made["client"]

    monkeypatch.setattr(pc, "probe_local", lambda timeout=1.0: "http://127.0.0.1:8081/v1")
    # provider_chat imports OpenRouterClient inside the branch; patch every
    # module path the dual-import can resolve to.
    import tools.openrouter_client as ort
    monkeypatch.setattr(ort, "OpenRouterClient", fake_ctor)
    try:
        import backend.tools.openrouter_client as bort
        monkeypatch.setattr(bort, "OpenRouterClient", fake_ctor)
    except ImportError:
        pass
    out = provider_chat("local/fable-max-llamacpp", MSGS, 800, FakeClient())
    assert out == {"text": "local-ok", "cost_usd": 0.0}
    assert made["client"].base_url == "http://127.0.0.1:8081/v1"
    assert made["client"].calls[0][0] == "fable-max-llamacpp"


def test_norm_handles_object_replies():
    class Reply:
        text = "obj"
        cost_usd = 0.5

    assert _norm(Reply()) == {"text": "obj", "cost_usd": 0.5}
    assert _norm({"text": "d"}) == {"text": "d", "cost_usd": 0.0}


def test_norm_estimates_cost_from_token_counts():
    # DashScope direct replies carry tokens but no cost -> estimate from pricing.
    res = {"text": "x", "input_tokens": 1_000_000, "output_tokens": 0}
    assert _norm(res, "dashscope/qwen3.8-max")["cost_usd"] == pytest.approx(1.48)
    # explicit cost always wins over estimation
    res2 = {"text": "x", "cost_usd": 0.5, "input_tokens": 1_000_000}
    assert _norm(res2, "dashscope/qwen3.8-max")["cost_usd"] == 0.5
    # unknown model id -> stays zero, never raises
    assert _norm(res, "nope/unknown")["cost_usd"] == 0.0
