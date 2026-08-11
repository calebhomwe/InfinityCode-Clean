# SYSTEMS.md — Infinity Code Architecture (Source of Truth)
Version: 0.1.59 | Updated: 2026-08-09 (audit baseline 719b847)

## Stack
- Shell: Tauri v1 (Rust), undecorated window, spawns frozen backend
- Frontend: React 18 + TypeScript + Tailwind + Vite (port 1420)
- Backend: FastAPI + uvicorn (port 8000, localhost-only + bearer token auth)
- Storage: SQLite WAL (per-domain DBs in DATA_DIR)
- Frozen backend: PyInstaller (~88MB with numpy), src-tauri/binaries/infinity-backend.exe

## Data Dir Resolution
- Installed app: %APPDATA%\com.infinitycode.app (INFINITY_DATA_DIR set by Tauri)
- Dev backend: backend/ (repo-local) — DIFFERENT data; see HANDOVER gotcha

## Backend Layout (hub-and-spoke; core/ modules take constructor injection, no core<->core imports)
- main.py — 157 /api/v1/* endpoints + lifespan + auth middleware (SPLIT CANDIDATE: 10 routers)
- core/swarm.py (115KB) — mission engine: plan -> candidates -> tournament -> redteam -> judge
- core/router.py — model council + lanes; config.yaml models: OVERRIDES COUNCIL (drift risk)
- core/providers.py + tools/{dashscope,moonshot,openrouter}_client.py — quota failover chain
- core/tools_registry.py (68KB) — tool dispatch, TOOL_RISK gating, SSRF url_is_blocked
- core/sandbox.py + exec_utils.py — code exec (AST check + -I isolation; NO OS-level jail yet)
- core/ascension.py — 6-tier form state machine (pure transitions, testable)
- core/knowledge.py + local_rag.py + omnibrain.py + memory.py — retrieval systems (ALL WIRED)
- core/longtask/ — quest runtime, spec-mode plan approval, artifacts, SSE
- core/eval_harness.py + gauntlet.py + benchmark_curriculum.py — 13-profile benchmark gauntlet
- core/self_training.py + learn_engine.py + retrain_loop.py + fine_tuner.py + dataset_builder.py — training flywheel
- core/claude_bridge.py + claude_sync.py + ai_chat_sync.py — Claude integration paths (ALL WIRED)

## Databases (WAL, busy_timeout)
knowledge.db (RAG chunks + vectors) | missions.db | longtasks.db | memory.db |
schedules.db | skills.db | wiki.db | ai_chats.db (30 refs), chats.db (108 refs), claude_bridge.db (11 refs) -- ALL LIVE, just no data in installed data dir yet

## Provider Chain (quota failover, never no-response while any key lives)
1. DashScope direct (qwen-turbo/plus/max/coder, FREE quota) via dashscope.key/env
2. Moonshot direct (kimi) via moonshot.key/env (temperature=1 required)
3. OpenRouter (fallback, env OPENROUTER_API_KEY)
4. Local: llama.cpp :8081 (FABLE-MAX), LM Studio :1234, Ollama :11434 (cost 0)

## Frontend Layout
- App.tsx (shell, modes, shortcuts) | ChatView.tsx 2,571 lines | SettingsModal.tsx 2,087 lines
- 10 lazy() code-split panels; vendor-markdown/vendor-react chunks; dist 4.1MB
- Modes: chat | assistant | build | ide (Ctrl+Shift+B cycles)
- Auth: fetches bearer token from GET /api/v1/auth/token on boot

## Security Posture (as of audit 2026-08-09)
- Bearer token auth on /api/* (exempts: /api/v1/health, token endpoint, docs, static)
- Localhost-only middleware for static mounts; SSRF guard on fetch_url/see_image
- Action gating: ApprovalRegistry per-call for side-effect tools; scheduler forces read-only
- OPEN ITEMS: see docs/audit/phase1-security-findings.md (key rotation, OS sandbox, history scrub)

## Test Layout
- backend/tests/: 55 files, pytest; baseline 2026-08-09: 33 pass / 5 fail (ascension cards x3 model roster drift, gauntlet x2 NameError)
- Frontend: NO tests yet (Phase 4.2 gap)
