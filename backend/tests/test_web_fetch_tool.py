"""Regression suite for the longtask web_fetch action (network-free)."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.longtask import tools as lt  # noqa: E402
from core.longtask.tools import PathJail, ToolError, t_web_fetch  # noqa: E402


@pytest.fixture
def jail(tmp_path):
    return PathJail(tmp_path)


class _FakeResp(io.BytesIO):
    def __init__(self, body: bytes, url: str, status: int = 200):
        super().__init__(body)
        self._url = url
        self.status = status

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def test_non_http_schemes_refused(jail):
    for bad in ("file:///c:/windows/system32", "ftp://example.com/x",
                "javascript:alert(1)", "", None, "not a url"):
        with pytest.raises(ToolError):
            t_web_fetch(jail, bad)


def test_private_and_loopback_refused(jail, monkeypatch):
    monkeypatch.delenv("INFINITY_WEB_FETCH_ALLOW_LOCAL", raising=False)
    # Literal IPs skip DNS but still hit the classifier.
    for bad in ("http://127.0.0.1/admin", "http://10.0.0.5/",
                "http://169.254.169.254/latest/meta-data",
                "http://192.168.1.1/"):
        with pytest.raises(ToolError):
            t_web_fetch(jail, bad)


def test_allow_local_env_override(jail, monkeypatch):
    monkeypatch.setenv("INFINITY_WEB_FETCH_ALLOW_LOCAL", "1")
    monkeypatch.setattr(lt.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(b"local ok",
                                                       "http://127.0.0.1/"))
    out = t_web_fetch(jail, "http://127.0.0.1/")
    assert out["content"] == "local ok" and out["status"] == 200


def test_fetch_returns_body_and_caps_output(jail, monkeypatch):
    # Public literal IP: getaddrinfo resolves it locally (no DNS round-trip),
    # the classifier passes it, and the mocked urlopen serves the body.
    big = b"x" * (lt.MAX_WEB_CHARS + 5000)
    monkeypatch.setattr(lt.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(
                            big, "https://93.184.216.34/a"))
    out = t_web_fetch(jail, "https://93.184.216.34/a")
    assert len(out["content"]) == lt.MAX_WEB_CHARS
    assert out["truncated"] is True
    assert out["url"] == "https://93.184.216.34/a"


def test_dns_failure_is_tool_error(jail, monkeypatch):
    def _boom(host, port):
        import socket as _s
        raise _s.gaierror("no such host")
    monkeypatch.setattr(lt.socket, "getaddrinfo", _boom)
    with pytest.raises(ToolError):
        t_web_fetch(jail, "https://no-such-host.invalid/")


def test_network_error_is_tool_error(jail, monkeypatch):
    def _boom(req, timeout):
        raise OSError("connection reset")
    monkeypatch.setattr(lt.urllib.request, "urlopen", _boom)
    with pytest.raises(ToolError):
        t_web_fetch(jail, "https://93.184.216.34/")


def test_engine_dispatches_web_fetch(jail, monkeypatch):
    """The action name reaches the tool through the engine dispatch."""
    from core.longtask.engine import LongTaskEngine
    from core.longtask.journal import LongTaskJournal

    monkeypatch.setattr(lt.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(
                            b"dispatched", "https://93.184.216.34/"))
    eng = LongTaskEngine(builder=object(),
                         journal=LongTaskJournal(jail.root / "j.db"))
    out = eng._exec(jail, {"action": "web_fetch",
                           "args": {"url": "https://93.184.216.34/"}})
    assert out["content"] == "dispatched"


def test_system_prompt_advertises_web_fetch():
    from core.longtask.engine import SYSTEM
    assert "web_fetch{url" in SYSTEM
