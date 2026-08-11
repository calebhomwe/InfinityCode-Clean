# Swarm Goal: Polish Infinity Code for a friend-facing test build (v0.1.60)

Ship a polished, tester-friendly build of Infinity Code (Tauri + React + FastAPI,
multi-provider LLM command center) to a friend who will install the MSI on a FRESH
Windows machine. The friend has NO API keys, NO local GPU models, and no repo.

Polish priorities (in order):
1. First-run experience: a keyless user must be guided to Settings > Providers,
   never stuck on silent failures.
2. Providers & API keys settings tab: clear, grouped, configured-status visible,
   test-connection feedback.
3. Backend resilience on fresh machines: friendly errors when no provider key is
   configured; local-model lanes degrade gracefully instead of hanging.
4. Tester documentation: README refresh + friend-facing TESTING_GUIDE.md.

Hard conventions (from project rules - do not violate):
- NO emojis anywhere in UI text or docs. Typography-first, premium dark aesthetic.
- Use the design tokens in src/index.css (CSS variables); no ad-hoc colors.
- Do NOT change API response shapes consumed by the frontend (ProvidersInfo,
  missions, chat). Additive fields only.
- One writer per file - files listed in a worker spec belong ONLY to that worker.
- Do NOT touch version numbers, tauri.conf.json, or the build pipeline.
- Do NOT commit. The orchestrator merges and verifies.

Baseline: uncommitted work on branch audit/2026-08-09 (BrowserView, model_catalog,
browser router, glm/minimax clients) is part of the tree - preserve it.