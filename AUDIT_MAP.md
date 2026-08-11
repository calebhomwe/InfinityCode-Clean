# Infinity Code — Audit Map (for external review)

**Repo root:** `C:\Users\caleb\infinity-code`
**Current version:** 0.1.36 · **Git HEAD:** `55f5877` (⚠️ the v0.2 work is in the
**working tree** — several new files are untracked/uncommitted; audit the files on
disk, not just the last commit).

## What it is
A Windows desktop AI app: **Tauri v1** shell (Rust) + **React/TS/Tailwind** frontend +
**FastAPI/SQLite** backend (Python), models via **OpenRouter**. Three modes: Chat,
Executive Assistant (gated PC tools), Build/Code (agent swarm + a quality-gated loop).
v0.2 added a self-learning knowledge base (RAG over the user's notes), a free local
self-test, a 245-persona agent library, and scheduled tasks.

## How to run it (dev)
```
# backend (port 8000)
cd C:\Users\caleb\infinity-code
venv\Scripts\python.exe backend\serve.py
# frontend (port 1420, Vite strictPort)
npm run dev
# full desktop build -> MSI
powershell -File build-desktop.ps1   # PyInstaller freeze + Tauri MSI + smoke test
```
Python venv: `venv\` (3.12). Node: `C:\Users\caleb\nodejs\node-v24.18.0-win-x64`.

## Layout
```
backend/
  main.py                 (~2160 lines — ALL FastAPI routes + lifespan wiring)
  serve.py                (uvicorn entrypoint; DATA_DIR resolution)
  config.yaml             (budget, paths)
  core/                   (engines — see "v0.2 additions" below)
  tools/openrouter_client.py   (OpenRouter chat/stream/embed; auto-continuation)
  data/agents.json        (245 bundled agent personas, from agency-agents MIT)
  data/seed_skills/       (bundled reference skills seeded into the vault)
src/
  App.tsx                 (shell: sidebar, 3-mode routing, modals, shortcuts)
  main.tsx                (entry; initNativeFeel)
  components/*.tsx        (ChatView is the big one ~1700 lines; AgentPicker,
                           ScheduledTasks, SettingsModal, VaultBrowser, OneBox, …)
  lib/                    (appearance.ts, nativeFeel.ts, artifact.ts, composer.ts)
  hooks/                  (useChats, useMissions, useCost)
src-tauri/
  src/main.rs             (tray, single-instance, spawns frozen backend, INFINITY_DATA_DIR)
  tauri.conf.json         (window, bundle, decorations:true)
  binaries/infinity-backend.exe   (PyInstaller-frozen backend, ~88MB with numpy)
Tools/or_swarm.py         (reusable N-wide OpenRouter parallel swarm harness)
build-desktop.ps1         (freeze + MSI + smoke test)
design/concept.html       (approved visual-direction mockup, not the app)
dist/                     (vite production build output)
```

## v0.2 additions to focus the audit on (backend/core/)
- **knowledge.py** — `KnowledgeStore` (`knowledge.db`): chunking + numpy matrix cosine
  (`M@q`), ingest of the user's vaults + a Downloads→Inbox collector. Reindex is O(files);
  search loads the whole matrix into RAM.
- **loop_engine.py** — the `/build` loop: plan→generate→quality-gate→retry/escalate,
  grounded + anti-hallucination. SSE via `/api/v1/loop/run`.
- **quality_gate.py** — deterministic PASS/RETRY/ESCALATE/REJECT + tiered model router.
- **selftest.py** — free gap-analysis via local LM Studio (:1234); logs gaps to `knowledge.db`.
- **scheduler.py** — stdlib minute-tick scheduled tasks; runner FORCES `allow_actions=false`.
- **agent_library.py** — loads `data/agents.json`; `agent_id` becomes the chat system prompt.
- **memory.py** — semantic memory (float32 BLOBs, pure-python/numpy cosine). *Note: the
  chat stream stores the memory text embedded with its own vector (fixed in v0.2).*
- **tools_registry.py** — the tool dispatch + `TOOL_RISK` gating (`ApprovalRegistry` in
  main.py). Assistant actions require per-call approval.
- Older swarm path: `swarm.py`, `tournament.py`, `redteam.py`, `router.py`,
  `critic_engine.py`, `cost_tracker.py`, `predictive.py`.

## Security / correctness notes for the auditor
- **Secrets are NOT in the repo.** `OPENROUTER_API_KEY` is in the Windows **User env**;
  `FAL_KEY`/`NOVITA_API_KEY`/etc. live in `D:\genesis\infra\.env`. Provider keys entered in
  the app persist to `providers.json` in the data dir (masked in UI).
- **Data dir** (SQLite DBs + user config): dev = `backend\`; **installed** =
  `%APPDATA%\com.infinitycode.app` (set by Tauri via `INFINITY_DATA_DIR`). DBs:
  `missions.db`, `skills.db`, `memory.db`, `knowledge.db`, `schedules.db` (WAL).
- **Action gating**: Executive-Assistant side-effect/paid tools are per-call approval-gated
  (`ApprovalRegistry`, pending/approve/skip SSE). Scheduled runs are hard-coded read-only.
  MCP tools + `read_file`/`review_screen` gating was tightened in v0.2 — worth verifying.
- **SSRF guard** on `fetch_url`/`see_image` (`url_is_blocked` in tools_registry.py).
- **`run_python`** executes in a sandboxed temp dir with a timeout — review the sandbox.
- **Knowledge ingest** reads the user's real folders (LLM WIKI, Obsidian Vault, Downloads)
  and copies stray Downloads notes into a vault Inbox — verify path handling.

## Known limitations (fair game for the audit)
- `main.py` is a 2100-line monolith of routes (candidate for splitting).
- Chat model catalog in `main.py` has some placeholder IDs masked by `CHAT_FALLBACK_MODELS`.
- Full visual overhaul (`design/concept.html`) is only partially applied (shell tokens only).
- Repo has build-log cruft at root (`build-0.1.*.log`) and 22 historical MSIs under
  `src-tauri/target/release/bundle/msi/`.
- Frozen backend is ~88MB (numpy). PyInstaller hidden-imports in `build-desktop.ps1`.

## Reference / knowledge corpus (grounds "AAA")
User's Obsidian vault `…\Documents\Obsidian Vault\Reference\GameDev\` — 15 grounded
Blender/UE/AAA reference docs the app retrieves against (indexed into `knowledge.db`).
