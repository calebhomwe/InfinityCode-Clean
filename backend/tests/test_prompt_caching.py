"""Tests for prompt caching: DashScope session-cache header + OpenRouter
cache_control on the leading system message.

Caching is prefix-based: the static system prompt must stay byte-identical
across turns, so only role=system is annotated and dynamic tail messages
are never touched.
"""
from __future__ import annotations

import pytest

from backend.tools.openrouter_client import OpenRouterClient


class FakeOpenAI:
    """Records construction kwargs so header wiring is observable."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


MSGS = [
    {"role": "system", "content": "static system prompt"},
    {"role": "user", "content": "dynamic tail"},
]


# --------------------------------------------------------------------- #
# _cache_control_messages
# --------------------------------------------------------------------- #

def test_openrouter_marks_first_system_message():
    out = OpenRouterClient._cache_control_messages(MSGS, "openrouter")
    assert out[0]["cache_control"] == {"type": "ephemeral"}
    # Dynamic tail untouched.
    assert out[1] == {"role": "user", "content": "dynamic tail"}
    # Original list is not mutated (callers keep pristine messages).
    assert "cache_control" not in MSGS[0]


def test_openrouter_only_marks_first_system_message():
    msgs = [
        {"role": "system", "content": "first"},
        {"role": "system", "content": "second"},
        {"role": "user", "content": "hi"},
    ]
    out = OpenRouterClient._cache_control_messages(msgs, "openrouter")
    assert out[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in out[1]


def test_no_system_message_leaves_messages_content_equal():
    msgs = [{"role": "user", "content": "hi"}]
    out = OpenRouterClient._cache_control_messages(msgs, "openrouter")
    assert out == msgs
    assert out is not msgs


def test_other_providers_pass_through_untouched():
    for provider in ("dashscope", "deepseek", "moonshot"):
        out = OpenRouterClient._cache_control_messages(MSGS, provider)
        # Same object: no copy, no mutation — they cache at infra level.
        assert out is MSGS


# --------------------------------------------------------------------- #
# Per-provider client headers
# --------------------------------------------------------------------- #

def test_dashscope_client_enables_session_cache(monkeypatch):
    monkeypatch.setattr("backend.tools.openrouter_client.OpenAI", FakeOpenAI)
    client = OpenRouterClient(
        api_key="sk-ws-fake", base_url="https://dashscope.test/v1"
    )
    headers = client._client.kwargs["default_headers"]
    assert headers["x-dashscope-session-cache"] == "enable"


def test_openrouter_client_has_no_session_cache_header(monkeypatch):
    monkeypatch.setattr("backend.tools.openrouter_client.OpenAI", FakeOpenAI)
    client = OpenRouterClient(
        api_key="sk-or-fake", base_url="https://openrouter.test/v1"
    )
    headers = client._client.kwargs["default_headers"]
    assert "x-dashscope-session-cache" not in headers
    # Standard identity headers still present.
    assert headers["X-Title"] == "Infinity Code"
