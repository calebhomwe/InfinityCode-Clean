"""Tests for the always-on LLM response cache (llm_cache + client wiring)."""
import json
import time

import pytest

try:
    from backend.core import llm_cache
    from backend.tools import openrouter_client as orc
except ImportError:  # running with backend/ as the working directory
    from core import llm_cache  # type: ignore[no-redef]
    from tools import openrouter_client as orc  # type: ignore[no-redef]


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    """Every test gets an isolated cache dir; the real one is never touched."""
    monkeypatch.setenv("INFINITY_LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    yield
    monkeypatch.delenv("INFINITY_LLM_CACHE_DIR", raising=False)


MSGS = [{"role": "user", "content": "repeat me"}]


# --- llm_cache unit behaviour ------------------------------------------- #

def test_store_and_lookup_roundtrip():
    assert llm_cache.should_cache(MSGS, 0.2, 100)
    llm_cache.store("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                    100, 0.2, "hello", 10, 5)
    hit = llm_cache.lookup("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                           100, 0.2)
    assert hit is not None
    assert hit["text"] == "hello"
    assert hit["input_tokens"] == 10
    assert hit["output_tokens"] == 5
    assert hit["cached"] is True


def test_key_drift_means_miss():
    llm_cache.store("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                    100, 0.2, "hello", 10, 5)
    # Same prompt, different budget -> different key -> miss.
    assert llm_cache.lookup("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                            200, 0.2) is None
    # Same budget, different model -> miss.
    assert llm_cache.lookup("dashscope", "http://x/v1", "qwen-plus", MSGS,
                            100, 0.2) is None


def test_high_temperature_bypasses():
    assert not llm_cache.should_cache(MSGS, 0.7, 100)
    assert llm_cache.should_cache(MSGS, 0.3, 100)


def test_image_content_bypasses():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]}]
    assert not llm_cache.should_cache(msgs, 0.2, 100)


def test_tool_role_bypasses():
    msgs = [{"role": "user", "content": "run the tool"},
            {"role": "tool", "content": "result", "tool_call_id": "1"}]
    assert not llm_cache.should_cache(msgs, 0.2, 100)


def test_kill_switch_disables_everything(monkeypatch):
    monkeypatch.setenv("INFINITY_LLM_CACHE", "0")
    assert llm_cache.lookup("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                            100, 0.2) is None
    llm_cache.store("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                    100, 0.2, "hello", 10, 5)  # must not create files
    assert llm_cache.clear() == 0


def test_ttl_expiry():
    llm_cache.store("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                    100, 0.2, "hello", 10, 5)
    llm_cache.clear()
    assert llm_cache.clear() == 0  # nothing left after clear


def test_stale_entry_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("INFINITY_LLM_CACHE_TTL", "1")
    llm_cache.store("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                    100, 0.2, "hello", 10, 5)
    # Age the entry beyond the TTL.
    root = tmp_path / "llm_cache"
    for path in root.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["cached_at"] = time.time() - 10
        path.write_text(json.dumps(data), encoding="utf-8")
    assert llm_cache.lookup("dashscope", "http://x/v1", "qwen-turbo", MSGS,
                            100, 0.2) is None
    assert llm_cache.clear() == 0  # stale file was removed on read


# --- client-level wiring ------------------------------------------------- #

class _FakeUsage:
    prompt_tokens = 12
    completion_tokens = 7


class _FakeMsg:
    content = "hello world"
    reasoning_content = None


class _FakeChoice:
    message = _FakeMsg()
    finish_reason = "stop"


class _FakeResp:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return _FakeResp()


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()


def _fake_route():
    return [orc.Route(
        provider="dashscope", key="sk-ws-test",
        base_url="http://127.0.0.1:9/v1", source="test", free_only=False,
    )]


def test_client_chat_caches_repeat(monkeypatch):
    monkeypatch.setattr(orc, "collect_routes", lambda *a, **k: _fake_route())
    fake = _FakeClient()
    monkeypatch.setattr(orc.OpenRouterClient, "_client_for",
                        lambda self, route: fake)
    client = orc.OpenRouterClient()
    r1 = client.chat("deepseek/deepseek-v4-flash", list(MSGS), max_tokens=50)
    r2 = client.chat("deepseek/deepseek-v4-flash", list(MSGS), max_tokens=50)
    assert r1["text"] == r2["text"] == "hello world"
    assert r1["cost_usd"] > 0.0
    assert r2["cost_usd"] == 0.0
    assert r1["cached"] is False
    assert r2["cached"] is True
    assert fake.chat.completions.calls == 1  # second call never hit the API
    assert client.usage_report()["calls"] == 2  # ledger counts the cached one
    assert client.usage_report()["total_cost_usd"] == r1["cost_usd"]


def test_client_chat_kill_switch_uncaches(monkeypatch):
    monkeypatch.setenv("INFINITY_LLM_CACHE", "0")
    monkeypatch.setattr(orc, "collect_routes", lambda *a, **k: _fake_route())
    fake = _FakeClient()
    monkeypatch.setattr(orc.OpenRouterClient, "_client_for",
                        lambda self, route: fake)
    client = orc.OpenRouterClient()
    client.chat("dashscope/qwen-turbo", list(MSGS), max_tokens=50)
    client.chat("dashscope/qwen-turbo", list(MSGS), max_tokens=50)
    assert fake.chat.completions.calls == 2  # cache disabled -> both hit API


def test_client_stream_shortcircuits_on_hit(monkeypatch):
    monkeypatch.setattr(orc, "collect_routes", lambda *a, **k: _fake_route())
    fake = _FakeClient()
    monkeypatch.setattr(orc.OpenRouterClient, "_client_for",
                        lambda self, route: fake)
    client = orc.OpenRouterClient()
    # Warm the cache with a plain chat first.
    client.chat("dashscope/qwen-turbo", list(MSGS), max_tokens=50)
    assert fake.chat.completions.calls == 1
    wrapper = client.chat_stream("dashscope/qwen-turbo", list(MSGS),
                                 max_tokens=50)
    chunks = list(wrapper)
    assert "".join(chunks) == "hello world"
    assert wrapper.stream_error is None
    assert fake.chat.completions.calls == 1  # stream served from cache
