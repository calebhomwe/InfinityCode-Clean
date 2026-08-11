# RESEARCH_WAVE0 — Infinity Code Refinement Findings

> 2026-08-10 · Three parallel read-only audits (UI inventory / orchestration / benchmarking).
> This drives the DeepSeek-reviewed overhaul plan. Nothing was modified during research.

## 1. UI inventory (R1) — progressive-disclosure readiness

**No router exists.** Navigation is a `View` union (`"chat" | "assistant" | "build" | "ide" | "browser"`) + ~15 boolean modals, all in `src/App.tsx`. Views stay always-mounted, hidden via `display:none`.

### KEEP (core chat flow)
- `src/components/ChatView.tsx` (2638 L) — composer chrome already close to the target: `+` options, Attach, MCP, Web, Tools, Actions, Export, persona/Mode/Motion behind `+`, ApprovalModePicker, model pill, Steer, send.
- `TabBar.tsx` (multi-chat tabs), `CommandPalette.tsx` (Ctrl+K — natural Pro-drawer hub), `DiffModal.tsx`, `InfinityMark.tsx`, `WindowTitleBar.tsx`, `WindowResizeHandles.tsx`, `SplashScreen.tsx`.

### HIDE (-> Pro drawer / settings)
- `SettingsModal.tsx` (2270 L, 11 tabs), `ModelsView.tsx`, `CreditsView.tsx` (full-screen replace), `VaultBrowser.tsx`, `ScheduledTasks.tsx`, `LongTaskPanel.tsx`, `KnowledgeView.tsx`, `HealthReportModal.tsx`, `VisionPanel.tsx`, `TrainingPanel.tsx`, `BeastPanel.tsx`, `SwarmCouncilPanel.tsx` (mostly command-palette-only "Developer:" entries already — App.tsx:1604-1648).
- Build surface: `OneBox.tsx`, `MissionCard.tsx`, `MissionBrief.tsx`, `SwarmPanel.tsx`, `EvidencePanel.tsx`, `TurboActivity.tsx`, `SwarmDeploymentBadge.tsx`, `DeploymentChips.tsx`.
- `ReplMode.tsx`, `BrowserView.tsx`, `ContextRail.tsx`, `DesignPanel.tsx`, `AgentPicker.tsx` (the 200+ picker to replace in 2b).
- Sidebar utility rows: Theme toggle, Settings, Models, Credits, Browser, `WorkspacePicker.tsx`, `CostTracker.tsx` + `CreditMeter.tsx` footer, `CustomizeSidebar.tsx` (right-click only).

### LEGACY (dead/duplicate -> `src/_legacy`)
- Zero importers: `VoiceDock.tsx` (DESIGN.md:152 bans persistent voice dock), `AIChatHistory.tsx`, `ChatHistoryBrowser.tsx`, `PersonaPicker.tsx` (superseded by AgentPicker).
- Dead affordance: TabBar "other kinds" tabs that "don't force-open the modal" (App.tsx:274-277) — user-visible no-op.

### Design ledger
- `UI_HANDOFF.md` is NOT present. Authority: `DESIGN.md` (Composer section L143-152: footer = `+`, mode, model, send only; "no emoji labels, decorative mode chips, persistent voice dock, or second toolbar"; Sidebar section L207-213: advanced surfaces deliberately excluded from primary nav, reachable via command search — Pro drawer is the sanctioned evolution), `HANDOVER.md`, `AUDIT_MAP.md`, `docs/audit/phase6-uiux-test-plan.md`.

### Startup flow
1. `SplashScreen.tsx` until backend health passes. 2. `src/features/onboarding/OnboardingModal.tsx` — 5-step wizard, gated by `localStorage["infinity-onboarded"]`. 3. Provider banner "No API key is set up yet" (dismissible).

### Emoji mandate violations (fix in Wave 3/4)
- `ScheduledTasks.tsx:112` `⏰`; `VaultBrowser.tsx:155,191` `✓`; `SwarmCouncilPanel.tsx:299` `✓`; `✕` close glyphs (ChatView:2003, KnowledgeView:114, VisionPanel:101, SettingsModal:2034); `⌘` keycaps SettingsModal:2185-2188; `→` labels ChatView:2260. All should be SVG.

### Theme tokens
`src/index.css` `:root` L148-228 (`--c-bg/surface/tx/bd`, `--accent*`, `--status-*`, `--radius-*`, `--space-*`, motion), `[data-tone]`, `[data-contrast]`, `[data-theme="light"]`, `[data-preset="…"]` (kimi/qwen/chatgpt/claude/vscode/neon/paper/oled/pastel). `src/lib/appearance.ts` Appearance store. DESIGN.md default amber `#f0b347` vs shipped default accent `kimi #4a9de8` (appearance.ts:158) — discrepancy to reconcile.

## 2. Orchestration (R2) — dispatcher groundwork

### Agent registry
- `backend/data/agents.json` — **249 agents** (msitarzewski/agency-agents source), divisions with label/color. Loader: `AgentLibrary` in `backend/core/agent_library.py` (`list/get/prompt_for`); instantiated `main.py:1076` -> `application.state.agents` and inside `AgentSwarm.__init__` (`swarm.py:335`).
- Mission roles: `COUNCIL` dict in `backend/core/router.py` — 10 role bindings (architect, engineer, inspector, debugger, eye, artist, creative, worker, longtask_builder, longtask_reviewer). UI comment (`SwarmPanel.tsx:18`) already notes: "the 245 agents in /api/v1/agents are chat personas, not mission roles."

### router.py
- `MODEL_SPECS` (L180) ~20 hardcoded + generated; `ModelSpec` (L135): id, cost_in/out_per_million, context_window, supports_vision, supports_image_gen, fallbacks.
- Lanes `LANE_CHEAP/SMART/VISION/LOCAL/CUSTOM` (L29-34); `ASCENSION_POLICY` (L60); `apply_ascension()` (L70).
- `BFB_OVERRIDES` (L415), `build_council(mode="bfb")` (L443), role lock (L476-483: qwen3.8-max stripped from every non-reviewer chain — "the oracle never drafts").
- `ModelRouter.route(task_type, complexity, has_vision)` (L493): vision->eye, image_gen->artist, architectural->architect, complex->engineer, creative->creative, else worker. `chain_for(role)`, `calculate_cost()`.

### Swarm
- `_swarm/` is an OUTPUT folder only (goal.md, tasks.jsonl, report.md, screenshots/) — no Python.
- Real swarm: `backend/core/swarm.py` (2979 L), `AgentSwarm` (L265). `MAX_CONCURRENT_MISSIONS=8` (L84-94). Effort tiers `_EFFORT_PLAN` (L98): low(1,worker) med(2,engineer) high(3,engineer) xhigh/max/ultracode(4-6,architect) swarm(1,architect) vibe(3,engineer).
- Fan-out: `_attempt_worker_swarm` (L1714) — Director decomposes into <=6 modules, parallel workers via `asyncio.gather(to_thread())` (L1770), compile gate + reviewer pass (L1802). `_attempt_parallel_candidates` (L1634) races 3 engineer candidates. All through `_call_agent` (L617) -> `_provider_chat` (L552) with llm_cache + credits_meter.

### Endpoints
- `backend/routers/chats.py`: GET /api/v1/chat/models, GET/POST /api/v1/chats, POST /api/v1/chats/{id}/stream (SSE, ChatMessageRequest incl. agent_id/tone/mode/tools/assistant), POST /api/v1/chats/{id}/model. Agent persona injection chats.py:353-358.
- `backend/routers/system.py`: GET /api/v1/agents, /api/v1/agents/{id}, /api/v1/swarm/council.
- `backend/routers/missions.py`: POST /api/v1/missions (CreateMissionRequest, agents max 3), GET, approve/reject, WS.

### History migration surface (2b)
- `agent_id` NOT persisted in DB (chat_messages: id, chat_id, role, content, created_at, tools_json). Agents live in frontend state only (ChatView activeAgent, AgentPicker) and per-mission `missions.params_json["agents"]`. **No DB migration needed.**

### Caching / cost
- `backend/core/llm_cache.py`: should_cache (temp <=0.3, no images/tool turns), sha256 cache_key, disk `backend/data/.llm_cache`. Wired at swarm.py:570-603.
- `backend/core/cost_tracker.py`: approve_call reserves against daily AUD budget; per-model at `_call_agent` (642); chat stream gates on remaining_aud (chats.py:265).

### Reusable dispatcher code (jackpot)
- `SmartDefaults.classify_goal(goal)` (`backend/core/smart_defaults.py:75`) — free-text regex -> {mode: video|image|3d|code|auto, effort, lane, predicted}. Called when mode=="auto" (missions.py:56).
- `PredictiveRouter` (`backend/core/predictive.py:47`) — cheap-LLM-gate pattern (GATE_ROLE=worker, max_tokens=8, "complex|simple", conservative fallback) — the template for a cached DeepSeek-Flash task classifier.
- Templated crews already exist: `_run_selected_crew` + `_EFFORT_PLAN` + mode branches (auto/code/image/3d/video/bfb/swarm/vibe).
- **Conclusion: `core/dispatcher.py` maps classifier output -> existing `params` (mode/effort/agents/tools) and reuses `swarm.spawn()` unchanged.**

## 3. Benchmark/testing (R3) — 10-profile plan

### Existing infra
- `backend/core/eval_harness.py` + `backend/data/eval_tasks.jsonl` (77 tasks, 20 profiles incl. swebench/simpleqa/tau_bench/scicode/hhem/mmmu/arc_agi2/gpqa/hle/lmarena/seal/livebench). CLI: `python backend/core/eval_harness.py --lane <l> --profile <p> --model <m> --output <path>`; API POST /eval/run. Reports -> `backend/eval_reports/eval_{lane}_{ts}.json`.
- `backend/core/gauntlet.py` — reads newest report, computes benchmark_gap, feeds Ascension Engine (BLUE at gap>=60).
- `backend/benchmark_curriculum.json` (154 drills, all queued — stalled on dead local endpoint 127.0.0.1:1234).
- `benchmarks/questbench/` — SessionRecorder/BenchmarkRunner/DashboardGenerator; historical qwen3_8b run4: 77 tasks, pass 0.909, $0.
- `benchmarks/browsergym/` — Docker scaffold, NOT agent-wired yet.
- `backend/core/blender_benchmark.py` — /eval/blender/*.

### The 10 profiles (adapt to EvalHarness; 5-8 need small scripts)
1. gen-basic (HumanEval-style, reuse core tasks) — pass >=4/5 with check_run.
2. debug-fix — broken fn + failing assertion; 3/3 run-checks, <=3 attempts.
3. refactor — dedupe 2 fns into helper; behavior preserved.
4. tutorial-follow — implement Player scene from Godot snowboard tutorial excerpt; GDScript parses + required identifiers; bonus headless Godot run.
5. image-to-code — reference PNG -> working HTML/CSS; Playwright smoke (3/5 selectors + screenshot diff tolerance).
6. multi-file-feature — todo-API in scratch repo; pytest green, >=3 files, no stray edits.
7. web-playtest — register->create->delete click-through with screenshots; zero console errors.
8. long-task-loop — mini-game spec, 6 milestones via LongTaskJournal; >=5/6 verified.
9. cheap-quality — local/qwen3:8b gen-basic + image-to-code, with/without vision-assist; assist beats bare by >=2 tasks.
10. cost-per-task — benchmarks profile on cloud lane; <=$0.05/task cheap lane, report $/passing-task.

### Sources
- SWE-bench Verified: swebench.com/verified.html
- Terminal-Bench: tbench.ai, github.com/laude-institute/terminal-bench
- HumanEval: github.com/openai/human-eval
- Vibe Code Bench (Vals AI): arxiv.org/abs/2603.04601
- Design Arena: designarena.ai
- Fellowship of the Benchmark (Cognition, Feb 2025)
- render.com/blog/ai-coding-agents-benchmark (practical reviewer battery)

### Gaps found
- No image-input support in eval harness (needed for profile 5/9).
- BrowserGym not agent-wired.
- Curriculum drills stalled on dead local endpoint.
