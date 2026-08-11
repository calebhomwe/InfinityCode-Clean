# Swarm Report — Infinity Code polish for friend-facing test build (v0.1.60)

## Outcome
- Baseline MSI (v0.1.60) built and smoke-tested green (auth fix included).
- 4-worker polish swarm executed, merged, verified; final MSI rebuilt with polish.

## Workers
| # | Area | Files | Result |
|---|------|-------|--------|
| 1 | First-run onboarding + keyless UX | src/App.tsx, src/components/OneBox.tsx, src/lib/api.ts | DONE — banner, composer hint, keyless guard; tsc 0, vitest 49/49 |
| 2 | Providers & API keys tab polish | src/components/SettingsModal.tsx, src/index.css | DONE — grouped cards, status chips, test-connection, toasts; tsc 0, vitest 49/49 |
| 3 | Backend fresh-machine resilience | backend/main.py, routers/settings.py, core/providers.py, core/router.py, core/model_catalog.py | DONE — accurate configured map, masked-key guard, fast-fail local lanes; repaired 3 BOM-corrupted files; 381/382 cold-cache |
| 4 | Tester docs | README.md (rewrite), docs/TESTING_GUIDE.md (new) | DONE — OpenRouter-era claims removed, friend-facing guide |

## Orchestrator fixes (outside worker specs)
1. backend/main.py: GET /api/v1/auth/token was NOT exempt from the auth middleware -> frontend token bootstrap deadlocked (whole app broken). Exempted.
2. build-desktop.ps1: smoke test predated bearer auth -> 401 on every probe. Now fetches token and sends Authorization header.
3. build-desktop.ps1: venv detection falls back to .venv.
4. Version bumped 0.1.59 -> 0.1.60 (package.json, tauri.conf.json, Cargo.toml).
5. 7 stale "OPENROUTER_API_KEY is not set" messages replaced with Settings > Providers guidance (routers/system.py, learning.py, chats.py, core/swarm.py).
6. SettingsModal DashScope autofocus: rAF cancelled by its own effect re-run (state guard) -> replaced with ref-guarded setTimeout (verified by tsc/vitest).

## Visual verification (vite preview :4175 + Browser agent)
- Screenshots: _swarm/screenshots/01-main-keyless.png, 02-composer-hint.png, 03-providers-tab.png
- Premium dark UI confirmed, no layout defects, banner dismissal persists.
- Note: screenshots taken with a fetch shim because the backend process then on :8000 was a stale build (CORS). Frontend strings verified present in the fresh dist bundle.

## Verification gates
- tsc --noEmit: 0 errors
- vitest run: 49/49
- pytest cold-cache: 384 passed / 14 failed — all 14 failures are pre-existing or from parallel uncommitted WIP (credits suite 12, provider_chat cache-key defect 1, repo_context 1); none in swarm-owned files.
- build-desktop.ps1 smoke: /api/v1/cost + 249 agents + system/info OK (bearer auth)

## Environment notes
- Another agent session (Qoder chat-3 autonomous loop, pid 74780) edits the same repo concurrently (BFB routing, credits, llm_cache landed at 08:02-08:06). Final build includes that WIP per user decision.
- PowerShell .NET file I/O corrupts files (BOM + doubled-quote on line 1) in this environment; use Python byte-level writes. A tree scan found no remaining corruption.

## Swarm: KernelSession v0.2 hardening (2026-08-10)

- Workers: 3 (kernel core / skills bridge / tests) + orchestrator merge
- Result: 32/32 tests passing (was 10); 4 files delivered
- New: backend/core/kernel.py (v0.2), backend/core/kernel_skills.py, backend/tests/test_kernel_hardening.py (16 tests), backend/tests/test_kernel_skills.py (6 tests)
- v0.2 features: shell shim (bang/cmd and %%bash, host-side, allow_shell-gated), shell(), auto-snapshot every N executes, auto-recover on dead kernel (restarted flag), stats(), tool-call protocol (_ToolCall to KernelResult.tool_call + set_tool_result), UTF-8 pipes, thread-safe request serialization (RLock)
- Bugs caught by tests and fixed in merge: Windows UTF-8 mojibake, reply cross-talk under threads, concurrent lazy-spawn race, cmd.exe multi-line drop, _ToolCall shadowing
- Next: longtask kernel-mode adapter + A/B benchmark


## Swarm phase 2: KernelTaskEngine — kernel-mode longtask (2026-08-10)

- Workers: 3 (KernelTaskEngine / engine tests / A/B benchmark CLI) + orchestrator merge
- Result: 76/76 tests passing across the touched domain (kernel + longtask + A/B)
- New: backend/core/longtask/kernel_engine.py (KernelTaskEngine, subclass of LongTaskEngine,
  Python-code protocol instead of JSON actions), backend/tests/test_kernel_engine.py (10 tests),
  Tools/ab_kernel_vs_actions.py (A/B CLI), backend/tests/test_ab_benchmark.py (5 tests)
- KernelTaskEngine: persistent KernelSession per run (allow_shell, auto_recover, snapshot cadence
  seq-N.pkl), tool callables injected into the kernel (read_file/write_file/edit_file/grep/glob/
  list_dir/run_command/update_plan/finish raising _ToolCall), parent dispatches through the same
  PathJail toolset, review + judge gates reused from the parent engine, kernel errors feed back
  to the model for self-correction, protocol-error re-ask then fail.
- Merge fix: the contract's callable spelling (finish(summary)) died with NameError because the
  engine never injected wrappers — preamble injection added at session start.
- A/B CLI: run_ab(builder_factory, tasks, repo, max_steps) pure core; ChatClient for real models;
  --local via LOCAL_BASE_URL/LOCAL_MODEL; table or --json output.
- Next: live A/B run (qwen3:8b local) — the decision gate.


## Live A/B: kernel mode vs JSON actions (qwen3:8b via Ollama, 2026-08-10)

Decision gate run with Tools/ab_kernel_vs_actions.py --local. Model: qwen3:8b, free local.

Short tasks (hello.py; 7x8):
- actions: 2/2 completed, 4 calls, 1000 tokens_in, 22.9s
- kernel:  2/2 completed, 10 calls, 6653 tokens_in, 64.3s

Long task (read base.txt -> transform.py -> reversed output.txt):
- actions: completed, 9 calls, 5190 tokens_in, 58.0s
- kernel:  budget_exceeded (120s wall), 13 calls, 15046 tokens_in, 120.5s

Conclusion (data-backed): for qwen3:8b the JSON-action protocol wins on short and
medium tasks — the model is natively strong at JSON tool calls, while the kernel
protocol costs extra turns of self-correction and a larger system prompt. Do NOT
make kernel mode the default for this model. Kernel mode stays an experimental
lane for strong models (DeepSeek V4 flash, GLM, Qwen Max) and long-context tasks,
where state persistence is the bet. The A/B CLI makes that test one command away.
