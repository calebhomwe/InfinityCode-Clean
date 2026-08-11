"""Infinity Code API router: in-app browser proxy.

The Browser view needs an iframe-friendly window onto the open web. Direct
iframes hit X-Frame-Options / CSP walls on most sites, so this router fetches
pages server-side, injects a <base> so subresources load from the original
site, rewrites <a href> navigations back through the proxy (every click stays
same-origin with the API, so the iframe sandbox never needs allow-same-origin),
and injects a tiny script that reports navigation to the parent.

`/text` extracts readable text for the "send this page to the agent" flow.
Every outbound fetch is SSRF-guarded (no loopback/private/link-local targets,
redirect targets re-checked) so a remote page can never use the proxy to poke
at localhost services.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import socket
from html import escape as html_escape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger("infinity.browser")

router = APIRouter(prefix="/browser", tags=["browser"])

MAX_BYTES = 2_000_000
TIMEOUT = 15.0
TEXT_CAP = 120_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Tests swap this in to avoid real network + DNS calls.
_TEST_TRANSPORT: Optional[httpx.BaseTransport] = None

_HTML_TYPES = ("text/html", "application/xhtml+xml")


def _resolve(host: str, port: int) -> List[Any]:
    """Indirection so tests can fake DNS without patching the socket module."""
    return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)


def _private_ip(ip: Any) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _block_reason(url: str) -> Optional[str]:
    """Why this URL must not be fetched (None = allowed)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "Only http/https URLs are allowed."
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        return "URL has no host."
    if host == "localhost" or host.endswith(".local"):
        return "Local hosts are not allowed."
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _private_ip(literal):
        return "Private/loopback addresses are not allowed."
    try:
        addrs = _resolve(host, parsed.port or 80)
    except socket.gaierror:
        return "Could not resolve host."
    for entry in addrs:
        try:
            candidate = ipaddress.ip_address(entry[4][0])
        except ValueError:
            continue
        if _private_ip(candidate):
            return "Private/loopback addresses are not allowed."
    return None


class _RedirectBlocked(Exception):
    """A redirect hop was rejected by the SSRF guard."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


MAX_REDIRECTS = 10


def _fetch(url: str) -> httpx.Response:
    """Fetch with per-hop SSRF re-checks (redirects are not auto-followed).

    httpx's automatic redirect following would connect to every hop before
    we could vet it; instead each hop's URL is validated against the SSRF
    guard before the request is made.
    """
    client = httpx.Client(
        transport=_TEST_TRANSPORT,
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            resp = client.get(current)
            if not resp.is_redirect:
                return resp
            location = resp.headers.get("location", "")
            nxt = urljoin(current, location) if location else ""
            if not nxt:
                raise _RedirectBlocked("Redirect without a location.")
            reason = _block_reason(nxt)
            if reason:
                raise _RedirectBlocked(f"Redirect target rejected: {reason}")
            current = nxt
        raise _RedirectBlocked("Too many redirects.")
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# HTML rewriting
# --------------------------------------------------------------------------- #

_HREF_DQ = re.compile(r'<a\s+[^>]*?\bhref\s*=\s*"([^"]*)"[^>]*>', re.IGNORECASE)
_HREF_SQ = re.compile(r"<a\s+[^>]*?\bhref\s*=\s*'([^']*)'[^>]*>", re.IGNORECASE)

_NAV_BRIDGE = (
    "<script>window.addEventListener('load',function(){"
    "try{parent.postMessage({__infinityNav:location.href},'*')}catch(e){}});</script>"
)


def rewrite_html(doc: str, base_url: str) -> str:
    """Make a remote page iframe-friendly when served from our own origin.

    - A <base> tag resolves relative subresources (img/css/js) against the
      original site — cross-origin subresource loads are not blocked by
      X-Frame-Options, so pages render normally.
    - <a href> navigations are rewritten through the proxy, so every click
      stays same-origin with the API; the sandboxed iframe never needs
      allow-same-origin (which would let the page reach the host app).
    - The nav-bridge script tells the parent where the page navigated so the
      URL bar stays truthful across clicks and SPA pushState.
    """
    parsed = urlparse(base_url)
    base = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    escaped_base = html_escape(base, quote=True)
    # Strip any existing <base> so ours wins.
    doc = re.sub(r"<base[^>]*>", "", doc, flags=re.IGNORECASE)

    def _rewrite_a(match: re.Match[str]) -> str:
        tag = match.group(0)
        href = match.group(1).strip()
        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:", "data:", "about:", "file:")
        ):
            return tag
        resolved = urljoin(base, href)
        if urlparse(resolved).scheme not in ("http", "https"):
            return tag
        proxied = "/api/v1/browser/fetch?url=" + quote(resolved, safe="")
        return re.sub(
            r'href\s*=\s*["\'][^"\']*["\']',
            f'href="{proxied}"',
            tag,
            count=1,
            flags=re.IGNORECASE,
        )

    def _with_base(match: re.Match[str]) -> str:
        return (
            f"<head{match.group(1)}>{_NAV_BRIDGE}"
            f'<base href="{escaped_base}">'
        )

    doc = re.sub(r"<head([^>]*)>", _with_base, doc, count=1, flags=re.IGNORECASE)
    if "<base" not in doc.lower():
        doc = f"{_NAV_BRIDGE}<base href=\"{escaped_base}\">" + doc
    doc = _HREF_DQ.sub(_rewrite_a, doc)
    doc = _HREF_SQ.sub(_rewrite_a, doc)
    return doc


# --------------------------------------------------------------------------- #
# Readable-text extraction (stdlib only — no bs4 dependency)
# --------------------------------------------------------------------------- #

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "object", "embed"}
_BLOCK_TAGS = {
    "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
    "tr", "td", "th", "section", "article", "br", "table", "ul", "ol",
    "header", "footer", "nav", "aside",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title = ""
        self.description = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "meta":
            meta = dict(attrs)
            if meta.get("name", "").lower() == "description":
                self.description = (meta.get("content") or "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS and self.parts:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = " ".join(data.split())
            return
        if self._skip:
            return
        if data.strip():
            self.parts.append(data)


def extract_text(html: str, cap: int = TEXT_CAP) -> Dict[str, str]:
    """Return {title, description, text} for a fetched page."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML must not 500 the proxy
        logger.warning("text extraction aborted on malformed HTML")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > cap:
        text = text[: max(cap - 2, 0)].rsplit(" ", 1)[0] + " …"
    return {"title": parser.title, "description": parser.description, "text": text}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/fetch")
def browser_fetch(
    url: str = Query(..., min_length=4, max_length=4096),
    max_bytes: int = Query(MAX_BYTES, ge=10_000, le=8_000_000),
) -> Any:
    """Fetch a page and return iframe-friendly rewritten HTML."""
    reason = _block_reason(url)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    try:
        resp = _fetch(url)
    except _RedirectBlocked as exc:
        raise HTTPException(status_code=403, detail=exc.reason) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Timed out fetching page.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc
    reason = _block_reason(str(resp.url))
    if reason:
        raise HTTPException(status_code=403, detail=f"Redirect target rejected: {reason}")
    if len(resp.content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Page exceeds {max_bytes} bytes.")
    ctype = resp.headers.get("content-type", "").lower()
    if not any(t in ctype for t in _HTML_TYPES):
        return {
            "mode": "asset",
            "url": str(resp.url),
            "content_type": ctype,
            "size": len(resp.content),
        }
    rewritten = rewrite_html(resp.text, str(resp.url))
    return HTMLResponse(rewritten, media_type="text/html; charset=utf-8")


@router.get("/text")
def browser_text(url: str = Query(..., min_length=4, max_length=4096)) -> Dict[str, Any]:
    """Extract readable text from a page (the "send to agent" payload)."""
    reason = _block_reason(url)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    try:
        resp = _fetch(url)
    except _RedirectBlocked as exc:
        raise HTTPException(status_code=403, detail=exc.reason) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Timed out fetching page.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc
    reason = _block_reason(str(resp.url))
    if reason:
        raise HTTPException(status_code=403, detail=f"Redirect target rejected: {reason}")
    extracted = extract_text(resp.text)
    return {
        "url": str(resp.url),
        **extracted,
        "char_count": len(extracted["text"]),
    }


@router.get("/status")
def browser_status(request: Request) -> Dict[str, Any]:
    """Readiness of the agent-side browsing tools (no secrets).

    The MCP servers themselves are managed in Settings → MCP servers; this is
    the Browser tab's status chip: are the launchers present and the servers
    enabled?
    """
    mcp = getattr(request.app.state, "mcp", None)
    servers = mcp.config_public().get("servers", {}) if mcp else {}

    def pack(server_id: str, launcher: str) -> Dict[str, Any]:
        cfg = servers.get(server_id, {})
        return {
            "configured": server_id in servers,
            "enabled": bool(cfg.get("enabled", False)),
            "launcher_ready": shutil.which(launcher) is not None,
        }

    return {
        "playwright": pack("playwright", "npx"),
        "browser_use": pack("browser-use", "uvx"),
        "node": shutil.which("node") is not None,
    }


__all__ = [
    "router",
    "rewrite_html",
    "extract_text",
    "_block_reason",
    "_TEST_TRANSPORT",
    "_resolve",
]
