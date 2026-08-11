"""Unit tests for the in-app browser proxy (backend/routers/browser.py).

All tests are offline: outbound HTTP goes through httpx.MockTransport and
DNS resolution is monkeypatched. Covers the SSRF guard, HTML rewriting,
readable-text extraction, and endpoint shapes.
"""
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.routers import browser as br  # noqa: E402

SAMPLE_HTML = (
    '<html><head><title>Sample</title><meta name="description" content="d">'
    '<style>x{}</style></head><body><p>Hello <a href="https://example.com/x">link</a></p>'
    '<a href="/rel">rel</a><a href="#frag">frag</a>'
    '<a href="javascript:void(0)">js</a><a href="mailto:a@b.c">mail</a>'
    '<script>bad()</script></body></html>'
)

BLOCKED_URLS = (
    "http://127.0.0.1:8000/api/v1/health",
    "http://localhost/x",
    "http://[::1]/x",
    "http://10.0.0.5/x",
    "http://172.16.0.1/x",
    "http://192.168.1.1/x",
    "http://169.254.169.254/latest/meta-data",
    "ftp://example.com/x",
    "file:///etc/passwd",
)


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_block_reason_blocks_private_targets(url):
    assert br._block_reason(url)


def test_block_reason_allows_public_url(monkeypatch):
    def fake_resolve(host, port):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(br, "_resolve", fake_resolve)
    assert br._block_reason("http://example.com/x") is None


def test_block_reason_blocks_dns_resolved_private(monkeypatch):
    def fake_resolve(host, port):
        return [(2, 1, 6, "", ("10.1.2.3", port))]

    monkeypatch.setattr(br, "_resolve", fake_resolve)
    assert br._block_reason("http://example.com/x") is not None


def test_block_reason_blocks_unknown_scheme():
    assert br._block_reason("gopher://example.com/") is not None


# --------------------------------------------------------------------------- #
# HTML rewriting
# --------------------------------------------------------------------------- #


def test_rewrite_injects_base_and_proxies_links():
    out = br.rewrite_html(SAMPLE_HTML, "https://example.com/")
    assert 'base href="https://example.com/"' in out
    assert "/api/v1/browser/fetch?url=https%3A%2F%2Fexample.com%2Fx" in out
    assert "/api/v1/browser/fetch?url=https%3A%2F%2Fexample.com%2Frel" in out


def test_rewrite_leaves_safe_schemes_alone():
    out = br.rewrite_html(SAMPLE_HTML, "https://example.com/")
    assert 'href="#frag"' in out
    assert "javascript:void(0)" in out
    assert "mailto:a@b.c" in out


def test_rewrite_strips_existing_base():
    doc = '<html><head><base href="https://old.example/"></head><body></body></html>'
    out = br.rewrite_html(doc, "https://new.example/page")
    assert 'base href="https://new.example/page"' in out
    assert "old.example" not in out


def test_rewrite_handles_missing_head():
    out = br.rewrite_html('<a href="https://example.com/x">x</a>', "https://example.com/")
    assert 'base href="https://example.com/"' in out


def test_rewrite_resolves_relative_against_page_url():
    # Browsers resolve against the page's directory: "next" on /a/b -> /a/next.
    doc = '<a href="next">next</a>'
    out = br.rewrite_html(doc, "https://example.com/a/b")
    assert "/api/v1/browser/fetch?url=https%3A%2F%2Fexample.com%2Fa%2Fnext" in out


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #


def test_extract_captures_title_and_body():
    ext = br.extract_text(SAMPLE_HTML)
    assert ext["title"] == "Sample"
    assert ext["description"] == "d"
    assert "Hello" in ext["text"]
    assert "link" in ext["text"]


def test_extract_skips_script_and_style():
    ext = br.extract_text(SAMPLE_HTML)
    assert "bad()" not in ext["text"]
    assert "x{}" not in ext["text"]


def test_extract_caps_length():
    long_html = "<p>" + "word " * 10_000 + "</p>"
    ext = br.extract_text(long_html, cap=1000)
    assert len(ext["text"]) <= 1000


def test_extract_handles_malformed_html():
    ext = br.extract_text("<p>ok" * 5000)  # unclosed tags pile up
    assert isinstance(ext["text"], str)


# --------------------------------------------------------------------------- #
# Endpoints (offline via MockTransport)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def proxy_client(monkeypatch):
    def handler(request):
        if request.url.path == "/page":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=(
                    "<html><head><title>Mock</title></head><body><p>Body text</p>"
                    '<a href="http://example.com/next">next</a></body></html>'
                ),
            )
        if request.url.path == "/img.png":
            return httpx.Response(
                200, headers={"content-type": "image/png"}, content=b"PNGDATA"
            )
        if request.url.path == "/secret":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><head><title>Secret</title></head><body>leak</body></html>",
            )
        raise AssertionError(f"unexpected fetch {request.url}")

    def fake_resolve(host, port):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(br, "_resolve", fake_resolve)
    monkeypatch.setattr(br, "_TEST_TRANSPORT", httpx.MockTransport(handler))
    app = FastAPI()
    app.include_router(br.router, prefix="/api/v1")
    return TestClient(app)


def test_fetch_returns_rewritten_html(proxy_client):
    r = proxy_client.get("/api/v1/browser/fetch", params={"url": "http://example.com/page"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'base href="http://example.com/page"' in r.text
    assert "/api/v1/browser/fetch?url=http%3A%2F%2Fexample.com%2Fnext" in r.text


def test_fetch_asset_mode(proxy_client):
    r = proxy_client.get("/api/v1/browser/fetch", params={"url": "http://example.com/img.png"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "asset"
    assert body["content_type"] == "image/png"


def test_fetch_rejects_loopback(proxy_client):
    r = proxy_client.get("/api/v1/browser/fetch", params={"url": "http://127.0.0.1:8000/x"})
    assert r.status_code == 403


def test_fetch_rejects_redirect_to_private(proxy_client, monkeypatch):
    def handler(request):
        return httpx.Response(
            302, headers={"location": "http://10.0.0.9/evil"}
        )

    def fake_resolve(host, port):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(br, "_resolve", fake_resolve)
    monkeypatch.setattr(br, "_TEST_TRANSPORT", httpx.MockTransport(handler))
    app = FastAPI()
    app.include_router(br.router, prefix="/api/v1")
    client = TestClient(app)
    r = client.get("/api/v1/browser/fetch", params={"url": "http://example.com/start"})
    assert r.status_code == 403


def test_text_endpoint_shape(proxy_client):
    r = proxy_client.get("/api/v1/browser/text", params={"url": "http://example.com/page"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Mock"
    assert "Body text" in body["text"]
    assert body["char_count"] == len(body["text"])


def test_status_endpoint_shape(proxy_client, monkeypatch):
    monkeypatch.setattr(br.shutil, "which", lambda name: "C:/fake/" + name)
    r = proxy_client.get("/api/v1/browser/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"playwright", "browser_use", "node"}
    assert body["playwright"]["launcher_ready"] is True
    assert body["browser_use"]["launcher_ready"] is True
    assert body["node"] is True
