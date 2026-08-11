"""BFB (Bang For Buck) routing mode tests.

Covers: default council untouched, BFB swaps coding roles onto DeepSeek V4,
the oracle role lock survives BFB, config overrides still win, mode validation,
and the flash thinking-disabled payload on DeepSeek routes only.
"""
import pytest

try:
    from backend.core.router import (
        BFB_OVERRIDES, COUNCIL, ModelRouter, build_council, resolve_mode,
    )
    from backend.tools import openrouter_client as orc
except ImportError:  # running with backend/ as the working directory
    from core.router import (  # type: ignore[no-redef]
        BFB_OVERRIDES, COUNCIL, ModelRouter, build_council, resolve_mode,
    )
    from tools import openrouter_client as orc  # type: ignore[no-redef]


# --- mode resolution ------------------------------------------------------ #

def test_resolve_mode_validation():
    assert resolve_mode("bfb") == "bfb"
    assert resolve_mode("default") == "default"
    assert resolve_mode("weird") == "default"
    assert resolve_mode("") == "default"
    assert resolve_mode(None) == "default"  # type: ignore[arg-type]


def test_bfb_overrides_cover_only_coding_roles():
    # The oracle and the cheapest lane must never move.
    assert "longtask_reviewer" not in BFB_OVERRIDES
    assert "worker" not in BFB_OVERRIDES
    assert BFB_OVERRIDES["longtask_builder"] == "deepseek/deepseek-v4-flash"
    assert BFB_OVERRIDES["engineer"] == "deepseek/deepseek-v4-flash"
    assert BFB_OVERRIDES["debugger"] == "deepseek/deepseek-v4-pro"


# --- council construction -------------------------------------------------- #

def test_default_mode_council_unchanged():
    council = build_council()
    for role, spec in COUNCIL.items():
        assert council[role].id == spec.id, f"{role} drifted in default mode"


def test_bfb_mode_swaps_coders_only():
    council = build_council(mode="bfb")
    assert council["longtask_builder"].id == "deepseek/deepseek-v4-flash"
    assert council["engineer"].id == "deepseek/deepseek-v4-flash"
    assert council["debugger"].id == "deepseek/deepseek-v4-pro"
    assert council["worker"].id == "dashscope/qwen-turbo"
    assert council["longtask_reviewer"].id == "dashscope/qwen3.8-max"
    # Fallback chains are preserved by the swap.
    assert "dashscope/qwen-coder-plus" in council["longtask_builder"].fallbacks


def test_bfb_role_lock_oracle_never_drafts():
    router = ModelRouter(build_council(mode="bfb"))
    for role in ("longtask_builder", "engineer", "debugger", "worker", "creative"):
        chain = router.chain_for(role)
        ids = [spec.id for spec in chain]
        assert "dashscope/qwen3.8-max" not in ids, f"{role} chain drafts the oracle"


def test_explicit_overrides_win_over_bfb():
    council = build_council(
        overrides={"longtask_builder": "dashscope/qwen-turbo"}, mode="bfb",
    )
    assert council["longtask_builder"].id == "dashscope/qwen-turbo"


def test_bfb_deepseek_specs_are_priced():
    router = ModelRouter(build_council(mode="bfb"))
    flash = router.spec_for_id("deepseek/deepseek-v4-flash")
    pro = router.spec_for_id("deepseek/deepseek-v4-pro")
    assert flash is not None and flash.cost_in_per_million > 0.0
    assert pro is not None and pro.cost_in_per_million > 0.0


# --- thinking control on the wire ----------------------------------------- #

class _FakeUsage:
    prompt_tokens = 8
    completion_tokens = 4


class _FakeMsg:
    content = "ok"
    reasoning_content = None


class _FakeChoice:
    message = _FakeMsg()
    finish_reason = "stop"


class _FakeResp:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _CaptureCompletions:
    def __init__(self):
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return _FakeResp()


class _CaptureChat:
    def __init__(self):
        self.completions = _CaptureCompletions()


class _CaptureClient:
    def __init__(self):
        self.chat = _CaptureChat()


def _route_for(provider: str, key: str):
    return [orc.Route(
        provider=provider, key=key,
        base_url="http://127.0.0.1:9/v1", source="test", free_only=False,
    )]


def _make_client(monkeypatch, provider: str, key: str):
    monkeypatch.setattr(orc, "collect_routes",
                        lambda *a, **k: _route_for(provider, key))
    fake = _CaptureClient()
    monkeypatch.setattr(orc.OpenRouterClient, "_client_for",
                        lambda self, route: fake)
    return orc.OpenRouterClient(), fake


@pytest.fixture(autouse=True)
def _isolate_llm_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INFINITY_LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    yield
    monkeypatch.delenv("INFINITY_LLM_CACHE_DIR", raising=False)


def test_flash_thinking_disabled_on_deepseek_route(monkeypatch):
    client, fake = _make_client(monkeypatch, "deepseek", "sk-ds-TEST")
    client.chat("deepseek/deepseek-v4-flash",
                [{"role": "user", "content": "hi"}], max_tokens=50)
    kwargs = fake.chat.completions.kwargs[0]
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    # Flash is no longer thinking-only: temperature is NOT forced to 1.
    assert kwargs.get("temperature") is None or kwargs["temperature"] != 1


def test_pro_still_thinking_only_with_floor(monkeypatch):
    client, fake = _make_client(monkeypatch, "deepseek", "sk-ds-TEST")
    client.chat("deepseek/deepseek-v4-pro",
                [{"role": "user", "content": "hi"}], max_tokens=50)
    kwargs = fake.chat.completions.kwargs[0]
    assert kwargs["temperature"] == 1
    assert kwargs["max_tokens"] >= 4000
    assert "thinking" not in kwargs.get("extra_body", {})


def test_no_thinking_param_on_non_deepseek_route(monkeypatch):
    client, fake = _make_client(monkeypatch, "dashscope", "sk-ws-TEST")
    client.chat("deepseek/deepseek-v4-flash",
                [{"role": "user", "content": "hi"}], max_tokens=50)
    kwargs = fake.chat.completions.kwargs[0]
    assert "thinking" not in kwargs.get("extra_body", {})


def test_flash_not_in_thinking_only_set():
    assert not orc.is_thinking_only("deepseek-v4-flash")
    assert orc.is_thinking_only("deepseek-v4-pro")
    assert orc.is_thinking_only("kimi-k3")
