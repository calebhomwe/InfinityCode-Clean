# Phase 7 — Reliability & Ops Checklist (post-audit state)

## Completed in this audit
- [x] Version drift fixed (4 files at 0.1.59) + Tools/check_version.ps1 gate in CI
- [x] requirements.txt created + pinned (107 pkgs), pip-audit now CLEAN
- [x] GitHub CI: backend pytest + frontend typecheck/test/build on every push/PR
- [x] GitHub build.yml: MSI artifact on version tags
- [x] Bearer token auth on all /api/* endpoints
- [x] Supply chain: npm audit 0 vulns, pip-audit 0 vulns
- [x] Test baseline: 54/54 offline backend tests, 8/8 frontend tests

## Owner Action Required (cannot be automated)
1. ROTATE DashScope key (backend/dashscope.key) — leaked in git history (commit 133a2a8)
2. ROTATE Moonshot key (backend/moonshot.key) — same exposure
3. Scrub git history: git filter-repo --invert-paths --path backend/dashscope.key --path backend/moonshot.key (backup first; force-push)
4. Review dev-only DBs (ai_chats.db, chats.db, claude_bridge.db) — keep or migrate

## Deferred Sprints (documented, sequenced)
1. OS-level sandbox: Windows AppContainer / restricted token for exec surfaces
2. main.py split into 10 APIRouter modules (~2-3 days)
3. swarm.py internal decomposition (mission FSM / council / crews)
4. Single-source model config (config.yaml wins over router.py COUNCIL)
5. Local embeddings (Ollama nomic-embed-text) to cut cost/latency
6. SQLite migration framework (replace ad-hoc CREATE IF NOT EXISTS)
7. E2E Playwright suite + axe-core a11y scans
8. Cloud benchmark runs (RunPod/GitHub Actions for LLM evals with live keys via secrets)
