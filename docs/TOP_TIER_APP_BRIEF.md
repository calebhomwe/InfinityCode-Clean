# Top-Tier General AI App — Architecture Brief

_Authored by product owner (Caleb), 2026-07-25. Companion to
[TEAM_ENGINE_BRIEF.md](TEAM_ENGINE_BRIEF.md). Same non-negotiable weight:
if an implementation choice conflicts with either document, the document wins
unless Caleb explicitly overrules it._

## Positioning line

> "Cursor executes. Cline frees. Kimi scales. MiniMax feels. Our app does
> all four — and you own the keys."

## What each winner really sells

| App | Real product | Lie users believe |
|---|---|---|
| Cursor | Autonomous execution — edits 50 files while you drink coffee | "It's just a better autocomplete" |
| Cline | Freedom — open-source, BYOK, no subscription tax | "It's just a VS Code extension" |
| Kimi | Scale — 300 agents for the price of one GPT call | "It's just a chatbot" |
| MiniMax | Presence — AI that feels like a person, not a terminal | "It's just text generation" |

## The 5-Layer Architecture (build in order)

### Layer 1 — Context Engine

Ingest the user's world; keep it hot.
- **Project indexing:** watch filesystem, index into vector DB + tree structure, <100 ms queries
- **Session memory:** SQLite/Redis that persists across restarts
- **Rules engine:** `.airules` per project — user writes rule once, agent obeys forever
- Stack: `watchdog` + `chromadb` + `sqlite`

### Layer 2 — Agent Kernel (state machine, not chat loop)

Pattern: `Plan → Act → Verify → Checkpoint`
1. Planner (expensive smart model) breaks task into subtasks
2. User approves plan
3. Executor (cheap fast model) runs subtasks in parallel
4. Verifier (smart model) checks vs rules + tests
5. Checkpoint: git commit / snapshot
6. User reviews diff

Cheap model for execution, expensive model only for planning + verification.
Route dynamically.

### Layer 3 — Tool Mesh (MCP Backbone)

Every capability is an MCP server; app is the router.

Default servers every user gets:
- `filesystem` — read/write user files
- `fetch` — scrape web, pull docs
- `browser` — Puppeteer: click, screenshot, fill forms
- `sqlite` — persistent memory, logs, user data
- `github` — repo ops, PRs, issues
- `terminal` — execute shell in sandbox

Tool Router:
1. Register MCP servers dynamically (user adds → works instantly)
2. Parallelize independent calls (filesystem + fetch + sqlite at once)
3. Validate outputs (retry or escalate on garbage)

### Layer 4 — Swarm Controller (4-agent minimum)

| Agent | Model | Job |
|---|---|---|
| Planner | Smart (Claude / GPT / K3) | Break tasks, set strategy |
| Coder | Fast (Qwen3-30B / Kimi K2.7-code) | Write code, edit files |
| Critic | Smart | Review, find bugs, enforce rules |
| DevOps | Fast (Qwen3-8B) | Run tests, commit, deploy |

Planner outputs a task DAG. Swarm executes independents in parallel.
**Insight from Kimi:** 4 well-orchestrated agents > 1 monolithic agent >
300 marketing-agents.

### Layer 5 — Interface (control center, not chat window)

Required UI elements:
- **Plan viewer** — show plan before execution; approve / edit / reject
- **Diff panel** — side-by-side before/after for every file change
- **Checkpoint timeline** — visual timeline of agent actions; click to revert
- **Tool call log** — expandable trace of every MCP call: name, args, return, duration
- **Cost tracker** — real-time $ spent per session
- **Model switcher** — one-click swap between providers, no lock-in

## The 5 Non-Negotiables

1. **BYOK** — support OpenRouter, Anthropic, OpenAI, Google, Ollama/vLLM.
   User's key, user's spend. Zero AI markup. This is Cline's moat.
2. **Checkpoints / Shadow Git** — before every agent action:
   `git stash && git checkout -b agent-session-{uuid}`. One-click revert.
3. **Multimodal I/O** — image in (describe/debug), voice in (transcribe+act),
   preview out (render HTML/3D/Markdown live).
4. **Cost Routing** — task complexity → model tier, explicitly:
   - `planning` → smart ($$$, rare)
   - `coding` → cheap fast ($, common)
   - `verification` → cheap-good-enough ($)
5. **Speed Budgets** — <200 ms simple ops, <2 s file edits, <10 s multi-file.
   Stream progress if longer; never freeze UI.

## Suggested 4-week build order (from brief)

| Week | Deliverable | Stack |
|---|---|---|
| 1 | Context Engine + Filesystem MCP | Python/Node, Chroma, SQLite, Watchdog |
| 2 | Plan/Act Agent Kernel + Diff UI | React/Vue, FastAPI, OpenRouter |
| 3 | 4-Agent Swarm + Parallel Execution | asyncio, task DAG, multi-model routing |
| 4 | Tool Mesh (GitHub, Browser, Terminal MCP) + Checkpoints | MCP SDK, GitPython, shadow-git |

## How this reconciles with the Team Engine brief

Both briefs align on the core principle: **deterministic orchestration, agents
that don't chat, evidence-first, human sovereignty**. This brief adds the
practical stack + shipping shape:

| Team Engine (previous) | Top-Tier App (this brief) | Reconciliation |
|---|---|---|
| Deterministic state machine (XState / Temporal) | Plan → Act → Verify → Checkpoint | Same shape. Team Engine's `producing→verifying→red_team→retry→done` **is** Plan→Act→Verify→Checkpoint. |
| Typed HandoffArtifact protocol | (unspoken but assumed) | Team Engine schema (J1) is the wire format the 4-agent swarm uses. |
| Evidence Dossier + confidence gate | (unspoken but assumed) | Applies inside the Verifier / Critic step. |
| Red Team parallel to Writer | Critic in the 4-agent swarm | Critic + Red Team can be the same agent (or Red Team as a specialist Critic). |
| Document Object Model | (unspoken) | Powers the Diff panel + Plan viewer render. |
| — | BYOK, Cost Routing, Shadow Git, MCP Backbone, Control-Center UI | New concrete requirements to add to the task list. |
| — | 4-week build order | Suggested milestone shape for the pivot. |

**Merged direction:** J1–J5 (schema, state machine, evidence, red-team,
DOM) build the *invisible engine*. New K-series adds the *shipping shape*:
BYOK provider matrix, shadow-git checkpoints, MCP tool router with
validation, cost router, control-center UI (plan viewer, diff panel,
checkpoint timeline, tool log, cost tracker, model switcher).
