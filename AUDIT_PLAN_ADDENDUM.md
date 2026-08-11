# Section 9: Addendum — Verified Findings + New Audit Items

Addendum to the Harness Deep Audit Plan (v0.1.59). Every claim below was verified
against the repo at HEAD `4fb8f51` on disk, not from memory.

## 9.0 Claim Verification — the original plan is accurate

| Plan claim | Verified on disk |
|---|---|
| `backend/main.py` = 5,083 lines | Exact match |
| `src/components/ChatView.tsx` = 2,571 | Exact match |
| `src/components/SettingsModal.tsx` = 2,087 | Exact match |
| `src/App.tsx` = 1,394 | Exact match |
| `backend/core/swarm.py` ~115KB | 113KB (2,424 lines) |
| `backend/core/tools_registry.py` ~68KB | 67KB (1,616 lines) |
| 0 frontend tests | Confirmed — zero `.test.*` / `.spec.*` in `src/` |
| 55 backend test files | Confirmed (`backend/tests/`) |
| 12 `@fontsource` packages | Confirmed, all 12 present in `package.json` |
| Tauri v1 EOL | Confirmed — `tauri 1.5`, `tauri-build 1.5`, `@tauri-apps/api ^1.5.0` |
| No CI/CD | Confirmed — no `.github/`, 48 commits, single `master` branch, no tags |
| Sandbox hardening reverted | Confirmed — `backend/core/sandbox.py` is only 194 lines / 7KB (a thin veneer) |

## 9.1 Corrections to the plan (facts it got wrong or left open)

1. **Providers inventory is incomplete.** The plan lists DashScope/Kimi/OpenRouter/local.
   `backend/providers.json` actually holds keys for **DeepSeek, Novita, ElevenLabs, Fal,
   and a ComfyUI URL** — none of which appear in the audit plan's provider surface.
   `backend/tools/` only contains `dashscope_client.py`, `moonshot_client.py`,
   `openrouter_client.py` — meaning DeepSeek/Novita/ElevenLabs/Fal are routed through
   one of these three clients (likely a generic passthrough) and their cost/latency/
   error surfaces are unmeasured. Phase 3.3 and 7.4 must cover all seven providers.

2. **The providers.json open question is answered: plaintext at rest.** Masked in UI,
   plaintext JSON on disk. It IS gitignored (`backend/providers.json` in `.gitignore`),
   and `git grep` of tracked files found zero `sk-*` keys — history is clean at HEAD.
   So: no exfiltration via repo, but **any process running as the user can read every
   key**. On a desktop app that launches arbitrary mission code (sandbox.py), this is
   the single most important key-theft vector. Add to 2.1: "can mission subprocesses
   read `providers.json`? If sandbox only restricts CWD, the answer is yes — file ACLs
   on the providers file are the real fix."

3. **The plan's DB inventory is half wrong.** It names 5 SQLite DBs; there are **10**:
   `ai_chats.db`, `chats.db`, `claude_bridge.db`, `knowledge.db` (5.2MB — real ingested
   RAG content), `longtasks.db`, `memory.db`, `missions.db`, `schedules.db`, `skills.db`,
   `wiki.db`. Two are completely undocumented:
   - **`claude_bridge.db`** — implies a Claude Code / Claude Desktop bridge subsystem
     that appears nowhere in the plan, HANDOVER.md, or SELF_REVIEW.md. Audit it as a
     first-class component: what does it send where, and what secrets does it hold?
   - **`wiki.db`** — separate from knowledge.db; unclear relationship.
   Phase 1.2's "map which modules touch which DB" must enumerate all ten.

4. **No schema versioning or migration tooling exists** (no Alembic, no PRAGMA
   user_version check found). Five of these DBs carry user-authored content
   (knowledge = personal notes from Obsidian/Downloads ingest). An upgrade that adds
   a column silently corrupts the user's RAG brain. Add a plan item: schema-version
   column + migration runner + **backup strategy for knowledge.db** (the user's
   personal data lives in a file the app never snapshots).

5. **The working tree IS the release.** `git status` shows **87 dirty files** at HEAD
   `4fb8f51`. `AUDIT_MAP.md` itself warns: "audit the files on disk, not just the last
   commit." Two consequences the plan misses:
   - Phase 2.1's "check git history" is insufficient — the threat model must treat the
     **untracked/dirty tree as unguarded**: any key accidentally written to a new file
     in the last three days is NOT in git, so `git grep` proves nothing. Secret scan
     must run over the working tree, not the index.
   - Phase 7 (CI) is blocked until the tree is committed; CI on a 48-commit history
     with 87 dirty files would validate a snapshot that doesn't exist. Add a
     "git hygiene gate" step 0 of the CI plan: commit tree, tag `v0.1.59`, then build.

6. **The Rust/Tauri surface is absent from the security phase.** Phase 2 audits the
   Python backend only. But `src-tauri/Cargo.toml` enables `path-all`,
   `fs-write-file`, `fs-read-file`, `fs-exists`, `shell-open`, `system-tray`.
   Tauri v1's filesystem scope is permissive by default and `shell-open` without an
   allowlist can open arbitrary handlers. Add a 2.5 subsection: audit `tauri.conf.json`
   CSP (is the backend `http://127.0.0.1:8000` allowed in CSP? are `unsafe-eval`
   flags present?), fs-plugin scope allowlist, `shell-open` allowlist, and the custom
   protocol handler. The frontend fetches from the local FastAPI — a DOM XSS becomes
   an arbitrary-file read via Tauri fs if the allowlist is wide.

7. **`public/repl.html` (100KB) ships in the build.** The `dist/` folder is otherwise
   near-empty (1KB index.html), meaning the real bundle was consumed by the MSI build —
   but a 100KB standalone REPL page exists in the build output. If that page talks to
   the backend or Tauri APIs, it is an unowned attack surface (it may predate the
   chat UI). Decide: delete, or audit + test it like a first-class view.

8. **Existing red-team artifacts are free regression material.** `backend/outputs/`
   contains benchmark-gauntlet artifacts named `attempt_*/redteam_attack_*.py` —
   the gauntlet already generates sandbox-escape / env-manipulation probes against
   generated code. Phase 2.2 and 4.3 propose building sandbox escape tests from
   scratch; instead, harvest these existing probes into a `tests/security/` corpus.
   They encode real escape attempts the harness has already produced.

9. **`backend/runtime/` is already modular — don't duplicate it in the refactor.**
   `runtime/engine/state_machine.py` (with its own tests) and `runtime/schema/handoff.py`
   show a namespaced pattern exists. The main.py split (Phase 8, 2-3 days) should
   extract into `runtime/`-style subpackages, not create a second pattern. Also note
   `runtime/engine/tests/` proves test-per-module layout precedent — extend it.

10. **Three Python venvs exist**: `venv/` (3.12, dev), `.venv/`, and
    `backend/datasets/.venv-tools/` (which contains the numpy-heavy setuptools stack).
    Phase 3.4's PyInstaller hidden-import investigation should first answer: *which*
    env produces the frozen binary? The 88MB-with-numpy size is likely `.venv-tools`
    bleeding into the freeze. One env, one freeze recipe, one `requirements-lock`.

## 9.2 Phase 0 (NEW — add before Phase 1): Baseline capture

A 7/10 codebase with zero frontend tests is about to undergo a `main.py` split and a
Tauri migration. The #1 risk is refactor-regression with nothing to diff against.
Before Phase 1 begins:

1. **Run the benchmark gauntlet once at 0.1.59 and record scores** (the 13 benchmarks)
   — this is the only numeric "before" you will ever get.
2. **Record the mission flow** (create → swarm → quality gate → result) as a
   Playwright video + transcript — free E2E oracle for later.
3. **Screenshot all major views** (the repo already has `onboarding-check*.png`,
   `step1..6-*.png`, `screen-full.png` from earlier smoke tests — reuse/extend them as
   the visual-regression baseline; Phase 5.4 doesn't need to start from zero).
4. **Copy `knowledge.db` to cold storage.** It is the only copy of the user's RAG
   brain and Phase 1 refactors touch its readers.
5. **Time the full MSI build once** (`build-desktop.ps1`) — Phase 3.4's number, but
   needed now so later changes have a delta.

## 9.3 Per-phase additions

### Phase 1 (Architecture)
- 1.1: Add `backend/runtime/` split target to the router plan; it already models the
  desired pattern (9.1.9).
- 1.2: Expand DB mapping to all 10 DBs (9.1.3). Treat `claude_bridge.db` and `wiki.db`
  as unknown components requiring tracing first.
- 1.3: `react-scan` and `react-doctor` are already devDependencies — Phase 1.3 should
  verify whether react-scan is actually wired into dev mode and confirm it's excluded
  from production bundles; also confirm `repl.html` is not a code-split entry.

### Phase 2 (Security)
- 2.1: Add — **working-tree secret scan** (git grep over `git status` files, not HEAD).
- 2.1: Add — **providers.json file-ACL hardening** (readable only by the user; verify
  mission subprocesses launched with a restricted token cannot read it).
- 2.1: Add — **key-chain shadowing tests**. Commits `4eb8831` (quota failover chain)
  and `4fb8f51` (dead env key no longer shadows live file key) prove the resolution
  chain is evolving logic with real attack surface: test that an attacker-controlled
  `DASHSCOPE_API_KEY`/`MOONSHOT_API_KEY` env var cannot (a) shadow a live file key to
  lock out the user, or (b) be made to leak into error messages. Add to Phase 4.3's
  security regression list.
- 2.1: Add — **HF_TOKEN hygiene**: `backend/tests/test_huggingface_data.py` manipulates
  `HF_TOKEN`; the datasets pipeline (HuggingFace) is another credential surface not in
  the plan. Include it in the env-var audit.
- 2.2: Replace "build sandbox escape tests from scratch" with "harvest
  `backend/outputs/**/redteam_attack_*.py` as the escape-test corpus" (9.1.8).
- NEW 2.5: **Tauri surface audit** — CSP in `tauri.conf.json`, fs-plugin scope
  allowlist (`path-all` + `fs-write-file` is a wide grant), `shell-open` allowlist,
  custom protocol handler, and `public/repl.html` disposition (9.1.6, 9.1.7).

### Phase 3 (Performance)
- 3.4: First identify which venv feeds PyInstaller (9.1.10); check whether
  `backend/datasets/.venv-tools` numpy is being dragged into the freeze.
- 3.2: `knowledge.db` is 5.2MB of real ingested content — the matrix-load benchmark
  can run against real data first, synthetic scales after.
- 3.1: Confirm `react-scan`'s runtime cost in production builds.

### Phase 4 (Testing)
- 4.1: Correct the "no integration tests" framing — `runtime/engine/tests/` and
  `runtime/schema/tests/` exist and set the pattern; the gap is breadth, not zero.
- 4.2: Add a **determinism check for the gauntlet**: record scores at 0.1.59 (Phase 0)
  and require reproducible runs before trusting any later delta.
- 4.3: Add DB-migration test fixture (schema version bumps on the 10 DBs), and the
  key-chain shadowing tests from 2.1.

### Phase 5 (UI/UX)
- 5.4: Seed the visual baseline with the existing `*.png` smoke artifacts (9.2.3).

### Phase 6 (Domain eval)
- 6.2: `backend/datasets/raw/blenderrag/` already contains a Blender-scripting corpus
  (outdoor scenes, condominium, grass) — reuse it as the Blender bpy eval set instead
  of generating new prompts.
- 6.1: The gauntlet's existing `redteam_attack_*` outputs double as an attempt-count
  and cost-per-solution data source — mine them before running fresh benchmarks.

### Phase 7 (CI/CD)
- 7.0 (new step): **Git hygiene gate** — commit the 87 dirty files, tag `v0.1.59`
  before any workflow exists (9.1.5).
- 7.1: Add a **secret-scan workflow** (gitleaks/trufflehog) on push — necessary
  because history hygiene depends on it and the tree is currently 87 files ahead of
  HEAD.
- 7.2: Azure DevOps free tier is the right call for MSI builds (Windows runners);
  GitHub Actions Linux runners for the backend is correct as written.
- 7.3: Add the **DB schema-version check** to release smoke tests (fresh install,
  upgrade-with-migration, knowledge.db preservation).

### Phase 8 (Priorities)
- Keep the five Critical Issues, and add: **git hygiene (87 dirty files)**, and
  **`claude_bridge.db` / Tauri-surface unknowns** as pre-refactor reconnaissance
  (they could invalidate the Phase-1 router split if they touch `main.py`).
- Add to High-Impact: **providers.json ACL hardening** (cheap, high value), and
  **harvest redteam_attack corpus** into the test suite (nearly free).

## 9.4 Summary of what this addendum changes

| # | New/Corrected item | Severity | Cheap to do? |
|---|---|---|---|
| 1 | Working tree (87 dirty files) is the real release; secret scan must cover it | Critical | Yes |
| 2 | Tauri surface (CSP, fs scope, shell-open, repl.html) audited | Critical | Yes |
| 3 | 10 DBs (not 5); claude_bridge.db + wiki.db undocumented | High | Yes |
| 4 | No DB schema versioning/migrations; knowledge.db unbacked | High | Medium |
| 5 | providers.json plaintext confirmed; file-ACL hardening + key-chain shadow tests | High | Yes |
| 6 | Provider inventory incomplete (DeepSeek/Novita/ElevenLabs/Fal/ComfyUI) | Medium | Yes |
| 7 | Phase 0 baseline capture before any refactor | High | Yes |
| 8 | Reuse existing redteam_attack corpus + runtime/ test patterns | Medium | Yes |
| 9 | Three venvs; find which feeds PyInstaller | Medium | Yes |
| 10 | Git hygiene gate + tags before CI | High | Yes |
