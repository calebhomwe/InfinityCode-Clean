# Infinity Code — Fast Handover (2026-07-26)

_Complements the older detailed [HANDOVER.md](HANDOVER.md) with the current
Team-Engine-pivot state. Read this first, that second._

## Where to find what

| Thing | Path |
|---|---|
| **Product briefs** (canonical direction) | [docs/TEAM_ENGINE_BRIEF.md](docs/TEAM_ENGINE_BRIEF.md), [docs/TOP_TIER_APP_BRIEF.md](docs/TOP_TIER_APP_BRIEF.md) |
| **Team Engine runtime scaffold** | [backend/runtime/](backend/runtime/) — `schema/`, `engine/`, more coming |
| **Kimi K3 key** (auto-loads on backend boot, gitignored) | `backend/moonshot.key` |
| **Working plan** | `C:\Users\caleb\.claude\plans\compressed-giggling-teacup.md` |
| **Backend chat entry** | [backend/main.py](backend/main.py) `stream_chat_message` around line 1781 |
| **LLM client** (provider resolver + K3 handling) | [backend/tools/openrouter_client.py](backend/tools/openrouter_client.py) |
| **Frontend shell** | [src/App.tsx](src/App.tsx), [src/components/](src/components/) |
| **Design tokens + themes** | [src/index.css](src/index.css), [src/lib/appearance.ts](src/lib/appearance.ts) |
| **Multi-tab shell** | [src/hooks/useTabs.ts](src/hooks/useTabs.ts), [src/components/TabBar.tsx](src/components/TabBar.tsx) |
| **Customize sidebar dialog** | [src/components/CustomizeSidebar.tsx](src/components/CustomizeSidebar.tsx), [src/lib/sidebar.ts](src/lib/sidebar.ts) |
| **K3 prompt/output scratchpad** | `C:\Users\caleb\AppData\Local\Temp\claude\C--Users-caleb\d92ed19a-1c59-4152-84d2-5ea13b6d5a0b\scratchpad\` |
| **Memory: Alibaba key revoked** | `~/.claude/projects/C--Users-caleb/memory/feedback-alibaba-key-revoked.md` |

## Run it

```bash
cd C:/Users/caleb/infinity-code
venv/Scripts/python.exe -m uvicorn main:app --port 8000 --app-dir backend
```

```bash
cd C:/Users/caleb/infinity-code
npm run dev
```

Or via `.claude/launch.json` names: `infinity-backend`, `infinity-frontend`.

## Run the runtime tests

```bash
cd backend
../venv/Scripts/python.exe -m runtime.schema.tests.test_handoff        # 8/8
../venv/Scripts/python.exe -m runtime.engine.tests.test_state_machine  # 11/11
```

## Kimi K3 essentials (Moonshot direct)

- **Endpoint:** `https://api.moonshot.ai/v1/chat/completions`
- **K3 is thinking-only.** Requires `temperature=1` and needs `max_tokens ≥ ~4000` (reasoning eats most of the budget). The client at `backend/tools/openrouter_client.py::_create_completion` enforces both automatically via `is_thinking_only()`.
- **Provider preference:** `INFINITY_LLM_PROVIDER` env override wins, else `OPENROUTER_API_KEY` > `MOONSHOT_API_KEY` > `DASHSCOPE_API_KEY` (the last is revoked and shouldn't be set).
- Every OpenRouter-style model id (`qwen/qwen3.7-max`, `qwen/qwen3-coder`, etc.) is mapped to a Kimi fallback in `_MOONSHOT_MODEL_ALIASES` so the whole app runs on Kimi.

## "K3 does everything" pattern (used for J1 + J2)

1. Assemble prompt at `SCRATCH/<task>_prompt.txt` — include spec excerpt + existing files it must integrate with + strict OUTPUT FORMAT (`FILE: path\n\`\`\`python\n…\n\`\`\`` blocks)
2. Send with `think_effort="low"` for translation tasks (heavy reasoning is expensive and rarely needed)
3. `max_tokens: 20000` gives ~14k content headroom after reasoning
4. Parse with regex `^FILE:\s*(\S+)\s*$\r?\n\`\`\`[a-zA-Z]*\r?\n(.*?)^\`\`\`\s*$` (MULTILINE|DOTALL)
5. **Sandbox writes** to an allow-listed directory (parser rejects out-of-bound paths)
6. Run tests → if fail, feed the diff back to K3 with a compact "here's the failure, fix only this" prompt

## Current position

**Shipped this session:**
- Phase A + B (theme presets, sound, tabs, sidebar prefs, cmdk/sonner/howler, status tokens, layout modes)
- Phase E1 + E2 (sidebar to 4 items, composer to 3 buttons, Kimi restraint)
- **J1** — HandoffArtifact schema (Python + TS mirror), 8/8 tests
- **J2** — Team Engine state machine, 11/11 tests
- **FIX** — app's chat path wired to Kimi K3 (was broken after DashScope revocation)

**In flight:**
- **J3** — Evidence Dossier + confidence gate on the chat path. Prompt already assembled at `SCRATCH/j3_prompt.txt`; needs re-run in **small passes** with `think_effort=low`.

**Queued (order):** J4 (Red Team parallel), J5 (DOM sketch), K1 (BYOK matrix), K2 (Shadow-Git checkpoints), K3 (MCP Tool Router), K4 (Cost Router), K5 (Control-center UI), K6 (Context Engine), J-review + K-review.

## Non-negotiables (from the two briefs)

1. **The Engine is the Product** — no LLM in orchestration code
2. **Agents Must Not Trust Each Other** — every handoff validated via HandoffArtifact
3. **Evidence Before Generation** — no factual claim without dossier
4. **Context Is a Budget** — hot ≤ 8k, warm ≤ 500, cold by ID
5. **Adversarial Verification Is Parallel** — Red Team alongside Writer
6. **Human Sovereignty With Predictive Support** — user is CEO
7. **Confidence Is a Hard Gate** — < 0.7 blocks output
8. **Documents Are Objects** — DOM, not markdown strings
9. **BYOK** — support OpenRouter/Anthropic/OpenAI/Google/Ollama; zero AI markup
10. **Shadow-Git Checkpoints** — every agent action revertable
11. **Cost Routing** — cheap-fast for coding, expensive-smart only for planning/verification

## Two directive quotes to remember

> "Prioritize deterministic orchestration over clever prompts. If you are about to solve a coordination problem with an LLM, stop and build a state machine instead."

> "Cursor executes. Cline frees. Kimi scales. MiniMax feels. My app does all four — and you own the keys."
