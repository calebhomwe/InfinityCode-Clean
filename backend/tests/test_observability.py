"""Part 5 observability & output-validation suite.

Covers:
- Trace IDs: per-request generation, client-supplied X-Trace-ID inheritance,
  response header echo, and log-record propagation via the Filter.
- sanitize_tool_output: total function — coercion, control-char stripping,
  truncation, never raises.

Deterministic, offline.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core.tools_registry import sanitize_tool_output  # noqa: E402


# --- trace IDs --------------------------------------------------------------- #

def _client():
    return TestClient(main.app)


def test_trace_header_generated_and_echoed():
    r = _client().get("/api/v1/health")
    assert r.status_code == 200
    tid = r.headers.get("x-trace-id")
    assert tid and tid != "-"
    assert len(tid) == 12


def test_trace_header_inherits_client_supplied_id():
    r = _client().get("/api/v1/health", headers={"X-Trace-ID": "my-trace-42"})
    assert r.headers.get("x-trace-id") == "my-trace-42"


def test_trace_filter_sets_record_attribute():
    filt = main._TraceFilter()
    token = main._TRACE_ID.set("abc123")
    try:
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
        assert filt.filter(rec) is True
        assert rec.trace == "abc123"
    finally:
        main._TRACE_ID.reset(token)
    # Outside a request the default applies.
    rec2 = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    filt.filter(rec2)
    assert rec2.trace == "-"


def test_trace_ids_are_unique_per_request():
    c = _client()
    a = c.get("/api/v1/health").headers["x-trace-id"]
    b = c.get("/api/v1/health").headers["x-trace-id"]
    assert a != b


# --- tool output validation -------------------------------------------------- #

def test_sanitize_passes_through_clean_strings():
    assert sanitize_tool_output("hello\nworld\t!") == "hello\nworld\t!"


def test_sanitize_coerces_non_strings():
    assert sanitize_tool_output(42) == "42"
    assert sanitize_tool_output({"a": 1}) == '{"a": 1}'
    out = sanitize_tool_output(None)
    assert isinstance(out, str) and out == "null"


def test_sanitize_strips_control_chars_but_keeps_newline_tab():
    dirty = "a\x00b\x07c\nd\te\x1f"
    assert sanitize_tool_output(dirty) == "abc\nd\te"


def test_sanitize_truncates_overlong_output():
    out = sanitize_tool_output("x" * 20000)
    assert out.endswith("[truncated]")
    assert len(out) <= 6000 + len("\n[truncated]")


def test_sanitize_never_raises_on_weird_objects():
    class Weird:
        def __repr__(self):
            raise RuntimeError("no repr")
    out = sanitize_tool_output(Weird())
    assert isinstance(out, str) and len(out) > 0


def test_sanitize_unicode_preserved():
    assert sanitize_tool_output("héllo — 日本語 🎵") == "héllo — 日本語 🎵"


# --- dependency health endpoint ---------------------------------------------- #

def test_health_dependencies_shape():
    """Read-only dependency report: shape is stable, values degrade safely."""
    r = _client().get(
        "/api/v1/health/dependencies",
        headers={"Authorization": f"Bearer {main._API_TOKEN}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert isinstance(body["provider_routes"], list)
    assert isinstance(body["openrouter_key"], bool)
    assert isinstance(body["mcp"], list)
    assert isinstance(body["local_model_server"], bool)
    assert isinstance(body["data_dir_writable"], bool)
    # Secrets must never leak: only masked keys in the route report.
    for entry in body["provider_routes"]:
        assert set(entry) == {"provider", "source", "key", "stage", "state"}

# --- Part 5 DX: debug mode + ops endpoints ----------------------------------- #

def _auth():
    return {"Authorization": f"Bearer {main._API_TOKEN}"}


def test_debug_mode_runtime_toggle():
    """DX 2026-08-11: debug mode must be flippable at runtime (no restart)
    and report its state; restore the prior level afterwards."""
    root = logging.getLogger()
    prior = root.level
    try:
        c = _client()
        on = c.post("/api/v1/debug/verbose", json={"enabled": True},
                    headers=_auth())
        assert on.status_code == 200 and on.json()["debug"] is True
        assert root.level == logging.DEBUG
        state = c.get("/api/v1/debug/state", headers=_auth())
        assert state.json()["debug"] is True
        off = c.post("/api/v1/debug/verbose", json={"enabled": False},
                     headers=_auth())
        assert off.json()["debug"] is False
        assert root.level == logging.INFO
        toggle = c.post("/api/v1/debug/verbose", json={}, headers=_auth())
        assert toggle.json()["debug"] is True  # omitted -> toggle
    finally:
        root.setLevel(prior)


def test_routes_reset_clears_dead_routes(tmp_path, monkeypatch):
    """DX 2026-08-11: ops must be able to unstick DEAD routes without
    waiting out the dead-TTL; reset clears and persists an empty state."""
    try:
        from backend.tools import openrouter_client as orc
    except ImportError:
        from tools import openrouter_client as orc  # type: ignore[no-redef]
    monkeypatch.setenv("INFINITY_ROUTE_HEALTH_FILE",
                       str(tmp_path / "route_health.json"))
    with orc._HEALTH_LOCK:
        orc._ROUTE_HEALTH["fake/dead"] = ("dead", 0.0)
    r = _client().post("/api/v1/routes/reset", headers=_auth())
    assert r.status_code == 200
    assert r.json()["cleared"] >= 1
    assert "fake/dead" not in orc._ROUTE_HEALTH


def test_cache_clear_endpoint(tmp_path, monkeypatch):
    """DX 2026-08-11: cache-clear drops the LLM response cache and reports
    the entry count (never raises)."""
    monkeypatch.setenv("INFINITY_LLM_CACHE_DIR", str(tmp_path / "cache"))
    r = _client().post("/api/v1/cache/clear", headers=_auth())
    assert r.status_code == 200
    assert isinstance(r.json()["cleared"], int)


def test_cli_parser_covers_dx_commands():
    """DX 2026-08-11: the CLI exposes health/debug/routes-reset/cache-clear."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "infinity_cli",
        Path(__file__).resolve().parents[2] / "Tools" / "infinity_cli.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    for fn in ("cmd_health", "cmd_debug", "cmd_routes_reset", "cmd_cache_clear"):
        assert callable(getattr(cli, fn)), fn
