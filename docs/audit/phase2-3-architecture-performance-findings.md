# Phase 2+3 Architecture, Bloat & Performance Findings
## Date: 2026-08-09 | Target: v0.1.59 (commit 719b847)

---

## Phase 2 — Architecture

### A2.1 main.py Monolith [HIGH]
- **157 API endpoints** in one file (5,083 -> ~5,800 lines after auth patch)
- All under /api/v1/* — natural split targets: chat, missions/swarm, ascension,
  eyes/vision, longtask, providers, knowledge, settings, scheduler, training
- **Recommendation:** 10 APIRouter files, one per domain; lifespan wiring stays in main.py
- Effort: ~2-3 days with test coverage as safety net (Phase 4.1 baseline exists: 33 pass)

### A2.2 Redundancy Sweep — FINDING CORRECTED [INFO]
Plan assumption that local_rag/omnibrain/claude_* were orphaned is WRONG:
- local_rag: 34 references outside itself (WIRED)
- omnibrain: 37 references (WIRED)
- claude_bridge: 10 references (WIRED)
- claude_sync: 6 references (WIRED)
- ai_chat_sync: 2 references (WIRED, minimal)
- agno: confirmed NOT imported anywhere — safe to remove from requirements
**Action:** Remove agno dependency only. Others stay.

### A2.3 Circular Import Risk — LOW [GOOD]
- swarm.py has ZERO imports from backend/core at module level — everything is
  constructor-injected from main.py. Clean hub-and-spoke architecture.
- router.py likewise has no core imports.
**Verdict:** Dependency graph is healthy; the monolith is main.py, not core/.

### A2.4 Database Audit [MEDIUM]
7 DBs in installed data dir, 10 in dev dir:
| DB | Installed | Dev-only | Notes |
|----|-----------|----------|-------|
| knowledge.db | 5.3MB | 5MB | largest; numpy matrix loads into RAM |
| missions.db | 1MB | 0.4MB | |
| longtasks.db | 0.1MB | 0.1MB | |
| memory.db | 0 | 0.3MB | |
| schedules.db, skills.db, wiki.db | ~0 | ~0 | |
| ai_chats.db, chats.db, claude_bridge.db | absent | ~0 | DEV-ONLY legacy — verify still needed |
- No migration framework — schema changes are ad-hoc CREATE IF NOT EXISTS
**Action:** Document schemas in SYSTEMS.md; decide fate of 3 dev-only DBs.

### A2.5 Config-vs-Code Drift [HIGH]
- config.yaml models: section overrides router.py COUNCIL (confirmed in HANDOVER)
- Dual-source model routing is a live bug class (commit 4fb8f51 fixed one instance)
**Action:** Single source of truth — config.yaml wins, router.py reads it.

---

## Phase 3 — Performance

### P3.1 Frontend Bundle [MEDIUM]
- dist/ total: **4.1 MB**
| Asset | Size | Finding |
|-------|------|---------|
| splash-loop.mp4 | 1,431 KB | eager import in SplashScreen — acceptable (splash is first paint) but has 2.5s fallback timeout, GOOD |
| index.js | 380 KB | main bundle — moderate |
| logo.png | 317 KB | **ChatView imports logo.png (317KB) instead of logo.webp (57KB) — FREE WIN: switch import** |
| vendor-markdown.js | 180 KB | already split — GOOD |
| vendor-react.js | 131 KB | already split — GOOD |
- Code-splitting: **10 lazy() calls present** — panels ARE lazy-loaded. GOOD.
- Vite manualChunks working (vendor-markdown, vendor-react separated). GOOD.

### P3.2 Font Bloat [MEDIUM]
- 11 font families imported in code; package.json lists 12
- **geist-mono: ZERO usage in src/ — dead dependency, remove**
- DESIGN.md says "bundle only Latin subset by default" — verify @fontsource imports use latin subset (default is latin, OK)
- 11 families is still high; consider trimming to the 4 user-selectable ones
  (Geist Sans, Inter, Space Grotesk, JetBrains Mono) + Fira Code for code blocks

### P3.3 Backend [MEDIUM]
- knowledge.py full-matrix M@q: 5.3MB DB is fine today; breaks ~50MB+ corpus
- Embeddings via paid OpenRouter text-embedding-3-small — switch to local Ollama
  embeddings (nomic-embed-text) for zero cost + lower latency
- 157 endpoints, 2 deprecated on_event handlers (should migrate to lifespan)

### P3.4 Python Dependencies [LOW]
- pip-audit: 7 vulns in cryptography 49.0.0 + pip 25.0.1 — upgrade both
- Frozen backend 88MB (numpy) — acceptable for desktop app

---

## Quick Wins Identified (low effort, high value)
1. ChatView.tsx: import logo.webp instead of logo.png (saves 260KB)
2. Remove @fontsource/geist-mono from package.json
3. Remove agno from requirements
4. Upgrade cryptography to 50.0.0 (pip-audit vuln)
5. Migrate 2 deprecated on_event handlers to lifespan

## Requires Dedicated Sprint
1. main.py split into 10 routers (2-3 days)
2. swarm.py internal decomposition (mission FSM / council / crews)
3. Local embeddings migration (Ollama nomic-embed-text)
4. Single-source model config
