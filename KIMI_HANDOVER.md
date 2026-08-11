# Copy-Paste Handover for Kimi K3 (paste this into a fresh session)

You are the primary worker on **Infinity Code**, a Tauri + React + FastAPI
app pivoting into the "Team Engine" — a deterministic multi-agent runtime.

## Repo layout — the files that matter

    C:/Users/caleb/infinity-code/
      docs/
        TEAM_ENGINE_BRIEF.md          # non-negotiables (read this first)
        TOP_TIER_APP_BRIEF.md         # 5-layer product architecture
      backend/
        main.py                       # FastAPI entry — chat stream at ~line 1781
        moonshot.key                  # your Kimi key (auto-loaded, gitignored)
        config.yaml                   # model role map
        tools/
          openrouter_client.py        # provider resolver + normalize_model_id
          moonshot_client.py          # direct Moonshot client used by swarm
        core/                         # existing engines: swarm, critic,
                                      #   redteam, knowledge, memory, router,
                                      #   skill_engine, self_training,
                                      #   speculative, vision_loop,
                                      #   partial_executor (K3 λNK), mcp_router (K3)
        runtime/                      # NEW — Team Engine (deterministic)
          schema/
            handoff.py                # HandoffArtifact v2.0 (SHIPPED)
            tests/test_handoff.py     # 8/8 passing
          engine/
            state_machine.py          # TeamEngine (SHIPPED)
            tests/test_state_machine.py  # 11/11 passing
      src/
        App.tsx                       # top-level shell
        index.css                     # design tokens + 5 theme presets
        lib/
          appearance.ts               # theme/preset/layout
          sidebar.ts                  # sidebar visibility prefs
          handoff.ts                  # TS mirror of HandoffArtifact
        components/
          ChatView.tsx, OneBox.tsx, ComposerMenus.tsx,
          SettingsModal.tsx, TabBar.tsx, CustomizeSidebar.tsx,
          AgentPicker.tsx, EvidencePanel.tsx, DesignPanel.tsx
        hooks/
          useTabs.ts                  # multi-tab shell state

## Working conventions (do these, always)

- **Never call an LLM inside `runtime/engine/`.** State machines only.
- **Every agent-to-agent value must be a `HandoffArtifact`.** Import from
  `runtime.schema.handoff`. Reject bad payloads at the schema level, not
  silently.
- **Small passes.** Each response returns one or two files, complete, in
  this format (nothing else — no preamble, no epilogue):

      FILE: backend/runtime/engine/<file>.py
      ```python
      # full contents
      ```

- **`think_effort=low` by default.** Heavy reasoning is expensive and rarely
  needed for translation tasks. Reserve `high`/`max` for adversarial review.
- **You are thinking-only.** Requires `temperature=1` and `max_tokens ≥ 4000`
  (reasoning eats most of the budget). The client enforces both.

## Non-negotiables (from TEAM_ENGINE_BRIEF)

1. Engine is the product — deterministic, not LLM-planned
2. Agents don't trust each other — typed handoffs, schema rejection
3. Evidence before generation — empty dossier ⇒ "Insufficient evidence"
4. Context is a budget — hot ≤ 8k, warm ≤ 500, cold by ID
5. Adversarial verification is parallel, not linear
6. Confidence < 0.7 blocks output
7. Documents are objects (DOM), not markdown strings
8. BYOK — no AI markup; user's keys, user's spend
9. Shadow-git — every agent action revertable

## Run + test

    cd C:/Users/caleb/infinity-code
    venv/Scripts/python.exe -m uvicorn main:app --port 8000 --app-dir backend
    npm run dev

    cd backend
    ../venv/Scripts/python.exe -m runtime.schema.tests.test_handoff        # 8/8
    ../venv/Scripts/python.exe -m runtime.engine.tests.test_state_machine  # 11/11

## Where the plan lives

    C:/Users/caleb/.claude/plans/compressed-giggling-teacup.md

## In flight / queued

- **J3 (in flight)** — Evidence Dossier + confidence gate on chat path
- **J4** — Red Team parallel to Writer
- **J5** — Document Object Model sketch
- **K1** — BYOK provider matrix (OpenRouter/Anthropic/OpenAI/Google/Ollama)
- **K2** — Shadow-Git checkpoints
- **K3** — MCP Tool Router (dynamic register + parallel + validation) ✅
- **K4** — Cost Router (task complexity → model tier)
- **K5** — Control-center UI (plan viewer, diff panel, checkpoint timeline,
       tool log, cost tracker, model switcher)
- **K6** — Context Engine (filesystem watcher + ChromaDB + .airules)

## The two directive quotes

> Prioritize deterministic orchestration over clever prompts. If you are
> about to solve a coordination problem with an LLM, stop and build a state
> machine instead.

> Cursor executes. Cline frees. Kimi scales. MiniMax feels. My app does all
> four — and you own the keys.
