# Infinity Code — Long-Horizon Coding Agent ("Long Task") Design

Date: 2026-08-05 · Status: approved direction · Target: v0.2.x

## 1. Overview

Give Infinity Code a true agentic, long-horizon coding capability in the style of
the best AI code assistants (Claude Code / Codex / Devin):

`plan → act (tool calls on a real repo) → verify → review by a second model →
continue if needed → finish` — running for hours, surviving restarts, checkpointing
every mutation, and **learning from every run** so it measurably improves over time.

Model stack: builder = Qwen 3.8 Max (DashScope), reviewer = local FABLE (:8081)
with Qwen fallback, all configurable. Zero Claude dependency.

## 2. What we reuse (already in the repo)

| Need | Existing module |
|---|---|
| Model calls + fallback chains | `backend/core/router.py` (LaneRouter), `ai_chat_sync.py`, swarm `_provider_chat` dispatch (Moonshot/DashScope direct) |
| Cost budget reserve/reconcile | `backend/core/cost_tracker.py` |
| Sandboxed command exec | `backend/core/sandbox.py` |
| Code verification | `backend/core/verifier.py`, `quality_gate.py` |
| Plan anchoring (concept) | `backend/core/plan_anchor.py` |
| Embeddings + RAG recall | `backend/core/knowledge.py`, `memory.py` (MemoryStore, `client.embed()`) |
| Skill storage | `backend/core/skill_engine.py`, `backend/skills/` |
| Nightly training slot | `backend/core/self_training.py` |
| Benchmark drills | `backend/core/benchmark_curriculum.py`, `eval_harness.py` |
| WS event streaming + missions UI shell | `backend/core/events.py`, Build view |

New code is a focused engine module + API endpoints + one UI view — not a rewrite.

## 3. The loop — `backend/core/longtask.py` (`LongTaskEngine`)

### 3.1 Cycle
1. **Plan**: builder model writes `PLAN.md` checklist into the task dir.
2. **Act**: each turn the builder emits one or more tool calls (OpenAI-style
   `tool_calls` where the provider supports it; otherwise a strict JSON action
   protocol `{ "action": ..., "args": {...} }` parsed defensively).
3. **Execute**: engine runs tools, appends results to history.
4. **Verify**: after mutating steps, if the repo declares/autodetects a test or
   build command, run it (sandboxed); failures feed back into the loop.
5. **Review gate**: every `N=8` mutating steps and at `finish`, the *reviewer*
   model (different, cheaper) reviews the accumulated diff vs the original goal
   and returns `{pass, issues[]}`. Issues are injected as feedback and the loop
   continues. Two consecutive failed reviews with no progress → escalate model
   tier, then stop with an honest report.
6. **Finish**: model calls `finish(summary)` or budget trips; final result,
   trail, diff, and cost are recorded.

### 3.2 Toolset
`read_file`, `list_dir`, `grep`, `glob`, `edit_file` (exact-match replace),
`write_file`, `run_command`, `update_plan` (check off / revise PLAN.md),
`finish(summary)`.

Constraints:
- File tools are jailed to the target repo subtree (path canonicalization, no
  symlink escape).
- `run_command` goes through `sandbox.py`, hard timeout (default 120s), output
  truncated to 64KB.
- Autonomy modes reuse the existing agent-access picker: in Ask mode, writes
  outside the repo and every shell command require approval; Full Access skips
  prompts but never skips budgets.

### 3.3 Context management (long-horizon survival)
- Persistent on-disk memory in the task dir: `PLAN.md`, `SCRATCHPAD.md`.
- When estimated history > ~48k tokens: compact oldest 50% of turns into a
  summary block; keep system prompt + injected lessons + plan + last N turns.
- Compaction can never lose state that lives on disk.

## 4. Checkpoints, journal, resume

- **Git repos**: engine creates branch `infinity/<task-id>`; every mutating
  step commits with message `longtask(<seq>): <step summary>`. Rollback to any
  step = `git reset --hard <sha>` (exposed as "restore to step").
- **Non-git folders**: incremental snapshots of touched files into
  `<data_dir>/longtask_snapshots/<task-id>/<seq>/`.
- **Journal**: SQLite tables in the app data dir (same DB conventions as
  missions):
  - `longtasks(id, goal, repo_path, branch, status, autonomy, model_builder,
    model_reviewer, budget_json, cost_aud, steps, result, created_at,
    updated_at)`
  - `longtask_steps(id, task_id, seq, kind, tool, args_json, result_json,
    model, cost_aud, duration_ms, ts)`
- **Resume**: on backend start, tasks in `running` state reload journal +
  PLAN.md and continue. Pause / resume / cancel via API + UI. Zombie handling
  mirrors the 0.1.46 mission reconciliation.

## 5. Self-learning layer

1. **Experience journal** — the step journal is the raw record. On task
   completion (success or failure), a cheap extraction call (builder model,
   low temp, capped tokens) reads journal + final diff and emits structured
   lessons: `{kind: failure_fix | pitfall | pattern, trigger, lesson}` →
   table `longtask_lessons(id, task_id, kind, trigger, lesson, embedding,
   uses, created_at)`.
2. **Recall into next run** — at task start, embed the goal, cosine top-k over
   lessons (and playbook triggers); inject as "LESSONS FROM YOUR OWN PAST
   RUNS" in the builder system prompt. Reuses `client.embed()` + MemoryStore
   cosine search from 0.1.28.
3. **Playbook distillation** — when ≥2 successful tasks share a structure,
   the nightly pass distills them into a markdown playbook in
   `backend/skills/longtask/<slug>.md` via `skill_engine.py`; matching tasks
   auto-load the playbook.
4. **Critic calibration** — store each review verdict; when a later step
   proves a flag right/wrong, annotate it; reviewer prompt includes its own
   recent false-positive/miss rate in one line.
5. **Measurable improvement** — `benchmark_curriculum` drills run nightly
   (cheap subset) and weekly (full); scores stored per date. The product
   claim "it gets better" is backed by a score line, not vibes.
6. **Nightly consolidation** (extends existing self-training slot): extract
   pending lessons → dedupe (embedding sim > 0.92 merges) → decay unused
   lessons → distill playbooks → refresh benchmark baseline. Fine-tune
   dataset export (`dataset_builder.py`) stays optional and manual.

## 6. API + events

- `POST /api/v1/longtasks` `{goal, repo_path, autonomy?, budget?{max_steps,
  max_cost_aud, max_wall_min}, model_builder?, model_reviewer?}` → task id.
- `GET /api/v1/longtasks`, `GET /api/v1/longtasks/{id}` (plan, trail, cost).
- `POST /api/v1/longtasks/{id}/pause|resume|cancel|restore/{seq}`.
- WS channel `longtask:<id>` streams: `plan_update`, `step`, `diff`,
  `review_verdict`, `approval_request`, `done`. Frontend drives the view off
  WS (lesson from roadmap item #1: never poll away the live frame).

## 7. Model routing

`backend/config.yaml` `models:` gains (config wins over code council — edit
both per HANDOVER gotcha):
- `longtask_builder`: `qwen/qwen3.8-max` → fallback `qwen/qwen3-coder-480b-a35b-instruct`
- `longtask_reviewer`: `local/fable` (:8081) → fallback builder chain
Thinking-model gotcha honored: `max_tokens >= 3000` for builder calls.

## 8. UI (Build view)

- **Launcher**: goal textarea, repo picker (remembers recent repos), autonomy
  chip (reuses agent-access picker), budget inputs with sane defaults
  (40 steps / $2 / 120 min).
- **Live view**: updating PLAN.md checklist, step trail grouped by phase
  (tool calls, collapsible diffs, command outputs), review verdicts, running
  cost, pause/resume/cancel, restore-to-step on history.

## 9. Build order

- **P1** Engine core: loop, toolset, plan step, journal, API, fake-model test
  harness (backend only, verifiable via HTTP).
- **P2** Review-and-continue gate + git checkpoints + pause/resume/restore.
- **P3** UI launcher + live view + WS events + approval gates.
- **P4** Self-learning: lesson extraction + recall injection; then playbooks +
  critic calibration + nightly consolidation + benchmark tracking.
- **P5** Context compaction + hardening (path escape, zombie tasks, provider
  outages) + version bump to 0.2.0.

## 10. Test plan

- Scripted fake-model client emitting canned tool-call sequences drives the
  engine against a scratch git repo: asserts plan created, edits applied,
  commits per step, finish summary.
- Resume test: kill engine mid-task, restart, assert continuation from journal.
- Security tests: path-escape attempts, symlink escape, command timeout.
- Budget tests: step/cost/wall caps each terminate with honest status.
- Review-gate test: injected bad diff triggers reviewer issues → loop repairs.
- Frontend: `npx tsc -p tsconfig.json --noEmit` exit 0 before any MSI build.

## 11. Risks / accepted trade-offs

- JSON action protocol on non-function-calling providers can produce malformed
  actions → defensive parser with one automatic re-ask before failing the
  step.
- Lesson extraction adds a small per-task cost; capped tokens, cheap model,
  runs after completion (never blocks the user).
- Reviewer quality is bounded by FABLE size; escalation path to Qwen keeps a
  strong-model review available for high-stakes tasks.
