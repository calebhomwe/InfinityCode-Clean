# Spec: In-App Browser (Browser View + Agent Browsing Tools)

## Objective

Give Infinity Code a good browser, two halves:

1. **In-app Browser view** — browse the open web inside Infinity: URL bar,
   back/forward/refresh, open-in-system-browser, and **Send to agent** (the
   page's readable text becomes a new chat's context).
2. **Agent browsing tools that work out of the box** — Playwright MCP and
   Browser Use MCP are already seeded + enabled in mcp_servers.json; add a
   `/browser/status` endpoint so the UI can report their readiness
   (npx/uvx/node present on this machine), and verify the MCP tab shows them.

Direct iframes hit X-Frame-Options / CSP walls on most sites, so the proxy
fetches pages server-side, strips nothing header-wise (we serve from our own
origin), injects a `<base>` for subresources, rewrites `<a href>` clicks back
through the proxy, and injects a tiny script that reports navigation to the
parent (truthful URL bar without allow-same-origin).

## Tech Stack

- Backend: FastAPI router, `httpx` 0.28 (already in venv), stdlib
  `html.parser` / `ipaddress` / `socket` (no new deps).
- Frontend: React/TS `src/components/BrowserView.tsx`, wired into App.tsx
  (`View` union + sidebar button), composer injection via a pending-compose
  bridge consumed by ChatView on mount.
- Tests: `backend/tests/test_browser_router.py` (offline — mocked transport
  + DNS).

## Commands

- Backend tests: `python -m pytest backend/tests -q`
- Frontend build: `npm run build` (includes tsc typecheck)
- Run: `python backend/main.py` (API on :8000) + `npm run dev` (UI on :1420)

## Project Structure

```
docs/BROWSER.md                    → this spec
backend/routers/browser.py         → proxy router (fetch / text / status)
backend/main.py                    → auth exemption + router registration
backend/tests/test_browser_router.py → offline unit tests
src/components/BrowserView.tsx     → browser UI
src/App.tsx                        → View union, sidebar button, container
src/components/ChatView.tsx        → consumes __infinityPendingCompose
```

## Code Style

Follow the repo conventions exactly: `APIRouter(prefix="/browser")` with the
`/api/v1` prefix applied at registration, `Dict[str, Any]` return types,
`HTTPException` for errors, `logging.getLogger("infinity.browser")`, no
`from main import ...` (the router is standalone — it takes `Request` to read
`request.app.state.mcp`). Frontend: function components, existing tailwind
tokens (text-tx / surface-2 / light-sweep-control), no new npm deps.

## Testing Strategy

- SSRF guard unit tests: 127.0.0.1, localhost, ::1, 10.x, 172.16.x,
  169.254.169.254 (metadata), ftp:// scheme, DNS-based block via monkeypatched
  `_resolve`, redirect-target re-check.
- `rewrite_html`: base injected, `<a href>` proxied, javascript:/mailto:/#
  untouched, relative links resolved+proxied, existing `<base>` stripped.
- `extract_text`: title/description captured, script/style skipped, blocks
  newline-separated, cap applied.
- Endpoint tests via `httpx.MockTransport` (no network): /fetch returns
  rewritten HTML, /fetch on non-HTML returns asset JSON, /text shape,
  /status shape with monkeypatched shutil.which.
- Full suite + `npm run build` must stay green.

## Boundaries

- Always: SSRF-guard every outbound fetch (loopback/private/link-local/
  multicast/reserved + localhost/.local names), re-check redirect targets,
  cap response at 2 MB and text at 120k chars, 15s timeout.
- Always: sandboxed iframe WITHOUT allow-same-origin; keep /api/v1/browser/*
  the only auth-exempt prefix beyond health/docs.
- Ask first: new Python/JS deps, changing the sandbox flags, proxying POST /
  form submissions, cookies/auth forwarding to proxied sites.
- Never: forward Authorization headers or provider keys to fetched sites;
  allow javascript:/data: navigations through the proxy.

## Success Criteria

- `GET /api/v1/browser/fetch?url=https://example.com` → rewritten HTML with
  a `<base>` tag, proxied links, and the nav-bridge script; no 401 (auth
  exemption works for iframe navigation).
- `GET /api/v1/browser/text?url=...` → {url, title, description, text,
  char_count}.
- `GET /api/v1/browser/status` → playwright/browser_use/node readiness
  (npx + uvx present on this machine → ready).
- BrowserView: URL bar, back/forward/refresh, open in system browser, Send to
  agent opens a new chat with the page text preloaded.
- `pytest backend/tests -q` and `npm run build` green.
