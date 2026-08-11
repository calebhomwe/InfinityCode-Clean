# Infinity Code X — Credit-Optimized Autonomous Engineering Swarm

**Status:** Approved design (owner: Munesu Homwe aka Mr X). Supersedes the plain
credit-routing design by fusing it with a deterministic ascension state machine,
an Eyes Module, a Benchmark Gauntlet, owner override authority, and a Council UI.

## 1. Goal

Turn Infinity Code into a credit-optimized engineering swarm: it picks the
cheapest sufficient model set, ascends to heavier forms **only** when measurable
need or owner override requires it, and never switches models randomly. Every
transition, assignment, and override is deterministic, logged, and governed by
the Ascension Engine.

## 2. Principles (from the spec)

1. Free/cheap models are tried first. Escalation only on: task complexity, test
   failures, benchmark gaps, repeated retries, or owner override.
2. Routing is deterministic — approved model lists per form, role locks per
   model, one-step ascension, dwell + cooldown guardrails. No random switching.
3. DeepSeek Flash is the default code executor. Qwen 3.8 Max **only** advises
   and verifies — it never drafts production code.
4. Every ascension, de-escalation, model assignment, and override is logged.
5. No destructive actions without confirmation. No secrets in logs. No role
   lock overrides without Mr X authorization.

## 3. Model registry (live status on this machine)

Availability is resolved at runtime from env key presence. Cards render
`locked` when the key is absent — the engine still knows the model, but the
router must never select a locked model.

| Swarm id | Endpoint | Key | Status | Cost tier |
|---|---|---|---|---|
| Free Router | `deepseek/deepseek-chat:free` | DEEPSEEK_API_KEY | **live** (verified SSE) | $0 |
| DeepSeek Flash 1731 | `deepseek/deepseek-v4-flash` | DEEPSEEK_API_KEY | **live** | cheapest |
| Kimi K3 | `moonshotai/kimi-k3` | MOONSHOT_API_KEY | locked (key lost) | mid |
| MiniMax M3 | `minimax/minimax-m3` | OPENROUTER_API_KEY | locked (key lost) | mid |
| GLM-5.2 | `z-ai/glm-5.2` | OPENROUTER_API_KEY | locked (key lost) | mid |
| Qwen 3.8 Max | `dashscope/qwen3.8-max` | OPENAI_API_KEY (dashscope) | **live** | high — advisory only |
| Local FABLE | `local/fable-max-llamacpp` | n/a | optional (port 8081) | $0 |

Role locks (spec): Free Router = routing/chat/format/syntax/cache only;
DeepSeek Flash 1731 = coding/bugfix/terminal/tests/refactors + diagnosis from
VisualReports; Kimi K3 = research/evidence/swarm tests; MiniMax M3 =
planning/memory/long-context; GLM-5.2 = long-horizon architecture; Qwen 3.8 Max =
advice + verification ONLY (never production code unless owner-authorized).

Media stack (integrated, not routed through ascension): images = Seedance 2 /
Z Image Turbo / GPT Image 2 (fallback `google/gemini-3.1-flash-image` live);
video = MiniMax H3. Selection by task type via the existing media endpoints.

## 4. Ascension forms

| Form | Level | Added models | Purpose |
|---|---|---|---|
| X CODE (BASE) | 0 | Free Router | trivial routing, basic chat, syntax, cache |
| SS1 | 1 | + DeepSeek Flash 1731 | standard coding, debugging, terminal, simple tests |
| SS2 | 2 | + Kimi K3 | coding + research, evidence, swarm testing |
| SS3 | 3 | + MiniMax M3 | planning, memory, long-context coordination |
| BLUE | 4 | + GLM-5.2 + Qwen 3.8 Max | full council, each model in its assigned role |
| MR X FINAL | 5 | all fused | near-max speed + effort scores **and** owner confirmation/override |

Approved model list per form is the union of all forms ≤ current level
(monotone growth, strict subset ordering — this makes "one level at a time"
checkable).

## 5. Ascension Engine rules (backend/core/ascension.py)

- `AscensionState` enum: `X_CODE=0, SS1, SS2, SS3, BLUE, MR_X_FINAL=5`.
- **One-step rule:** automatic escalation moves exactly one level
  (`current + 1`), never a jump. Owner override may jump anywhere.
- **Dwell time:** minimum seconds in a form before ANY state change is
  considered (anti-flicker). Default 45 s, config `ascension.dwell_s`.
- **Cooldown:** after a change, only de-escalation or owner override is
  permitted until `last_change + cooldown_s`. Default 180 s, config
  `ascension.cooldown_s`. Prevents transformation spam.
- **Role lock:** `MODEL_ROLES[model]` — a model may only be assigned to roles
  in its list. The engine rejects any assignment that violates it.
- **Form lock:** `FORM_MODELS[state]` — a model not in the current form's
  approved list cannot be selected by the router.
- **Owner override:** `POST /api/v1/ascension/override` requires the
  passphrase from `ascension.owner_passphrase` (config) or env
  `INFINITY_OWNER_PASSPHRASE`; compared with `hmac.compare_digest`. Wrong
  passphrase → 403, logged. Override bypasses one-step/dwell/cooldown, sets
  `actor: "owner"`.
- **Logging:** append-only JSONL at `backend/data/ascension_log.jsonl`,
  thread-safe, one line per event: `{ts, event, from, to, reason, actor,
  owner_override, speed, effort, models}`. Events: `ascend`, `deescalate`,
  `override`, `assignment`, `blocked`.

### Scores

- `speed_score(tokens_per_second, p95_latency_ms, queue_responsive, model_available, cost_efficiency)` → 0–100.
- `effort_score(failed_tests, retries, task_complexity_0_10, repo_size_bytes, dependency_complexity_0_10, benchmark_gap_0_100, coordination_load_0_10)` → 0–100.
- Each input normalized with per-input weights; deterministic pure functions
  (unit-testable, no I/O).
- Escalation policy: `effort >= 70` or `speed < 40` (sustained) may ascend one
  step; `benchmark_gap >= 60` counts as heavy effort and may ascend to BLUE.
  `MR_X_FINAL` requires `speed >= 85 and effort >= 85` AND owner
  confirmation (passphrase) — the engine blocks it otherwise.

## 6. Router integration (backend/core/router.py)

- New `ASCENSION_POLICY` table: per form → allowed models + allowed lanes.
- `apply_ascension(state, lane, primary)`: if the chosen lane's primary model
  is not in the form's approved list, degrade to the cheapest approved model
  in that lane's chain; if none, fall back to the form's default coder
  (deepseek flash). Never picks a locked model.
- Qwen 3.8 Max is unreachable for drafting: only `longtask_reviewer` /
  `verify` roles may select it (already true in COUNCIL — enforced now).
- Expose `policy_for(state) -> {form, models, lanes, locked}` for the UI and
  for `ModelRouter`/`LaneRouter` call sites (longtask engine, swarm, chat).

## 7. Eyes Module (backend/core/eyes.py + API)

Capture → VisualReport → DeepSeek diagnosis → fix → re-render → before/after
diff → Qwen 3.8 Max verify.

- `POST /api/v1/eyes/capture {url}` → runs the capture tooling (Playwright
  sync API when installed; falls back to Chrome DevTools protocol via the
  existing browser MCP-style client) collecting: screenshot (saved to
  `backend/data/eyes/`), URL, DOM subset, accessibility tree, console errors,
  network failures, selectors, bounding boxes, action history.
- The capture is reduced to a strict `VisualReport` JSON (schema exactly as
  specified) and returned. `elements[].box` = `[x, y, w, h]`.
- `POST /api/v1/eyes/diagnose {visual_report}` → DeepSeek Flash 1731
  (advisory coder) returns `{diagnosis, fix, test_plan, usability}`.
- `POST /api/v1/eyes/verify {before_report, after_report}` → Qwen 3.8 Max
  returns `{verdict, changed, issues_resolved, remaining}` — advisory only.
- Rules: never invent elements/selectors; prefer a11y tree + DOM selectors;
  bounding boxes only as fallback; include broken/hidden/overflow/error states.
- The existing `VisionPanel.tsx` ("Vision Verify") is the UI host for the
  capture/diagnose/verify loop.

## 8. Benchmark Gauntlet (benchmark gaps → effort score)

13 benchmark profiles already exist in `eval_harness.py` (48 tasks:
livebench, arc-agi-2, gpqa-diamond, simpleqa, swe-bench, hle, mmmu, tau-bench,
lmarena-style, scale-seal, scicode, hhem + local fable). New wiring:

- Benchmark gap = `1 - mean_score` per latest report
  (`backend/data/eval_reports/*.json`).
- `benchmark_gap` feeds `effort_score`; gap ≥ 0.6 justifies ascension to BLUE.
- `POST /api/v1/gauntlet/gap` → recompute effort from latest reports.
- Gauntlet button in Council UI → `POST /api/v1/benchmarks/run` (existing
  endpoint, if present) then gap → effort.
- Track: hallucination rate (simpleqa/hhem), tool-use reliability (tau-bench),
  coding (swe-bench/scicode), reasoning (livebench/arc/gpqa/hle), multimodal
  (mmmu).

## 9. Owner recognition (backend/core/owner.py)

- Identity: `OWNER_NAME = "Munesu Homwe"`, `OWNER_ALIAS = "Mr X"`; config
  overridable (`owner.name`, `owner.alias`).
- `owner_line()` → "Infinity Code X is owned and directed by Munesu Homwe —
  Mr X." plus a rotating hint joke (original, anime-flavored, never
  copyrighted): a small tuple cycled deterministically.
- Chat detection: when a user message matches owner queries ("who owns",
  "who built", "mr x", "owner"), the chat system prompt appends `owner_line()`.
- Override authority: the Ascension Engine's passphrase gate IS the owner
  check. The UI's "MR X FINAL" button asks for the passphrase and calls
  `POST /api/v1/ascension/override {target: "MR_X_FINAL"}`.

## 10. Council UI (src/components/SwarmCouncilPanel.tsx + AscensionDial.tsx)

- Ascension Dial: 6 segments (X Code, SS1, SS2, SS3, Blue, Mr X Final),
  current form highlighted, locked forms dimmed (MR X FINAL always gated).
- Swarm Council panel: model cards (name, role, active/locked, cost tier,
  availability) + protection rules strip (one-step, role lock, form lock,
  dwell timer, cooldown, owner-only final).
- Auras (CSS keyframes + WebAudio original chimes, no copyrighted assets):
  - X CODE: soft white pulse
  - SS1: gold aura
  - SS2: electric gold aura
  - SS3: intense gold aura
  - BLUE: calm cyan/blue aura
  - MR X FINAL: violet + gold infinity aura, lightning flicker, subtle screen
    shake (body class applied, removed on leave)
- Data: `GET /api/v1/ascension/state` polled on interval; transitions animate
  only on actual state change (dwell/cooldown remainders shown as timers).
- Visually pleasing coding watch: SwarmDeploymentBadge-style activity feed of
  live ascension log lines (model assignment events streamed via the existing
  SSE/WS channel when available, else polled).

## 11. API surface (backend/main.py)

- `GET /api/v1/ascension/state` → full engine snapshot for the UI.
- `POST /api/v1/ascension/escalate {reason}` → one-step or blocked.
- `POST /api/v1/ascension/deescalate {reason}` → one-step down.
- `POST /api/v1/ascension/override {target, passphrase, reason}` → 403 on bad
  passphrase.
- `GET /api/v1/ascension/log?limit=50` → recent JSONL events.
- `POST /api/v1/ascension/scores {speed: {...}, effort: {...}}` → recompute.
- `POST /api/v1/eyes/capture`, `/api/v1/eyes/diagnose`, `/api/v1/eyes/verify`.
- `POST /api/v1/gauntlet/gap`.
- All under existing CORS (4173 preview) + `/api/v1` prefix conventions.

## 12. Operating loop (as implemented)

1. Understand task → 2. `route()` picks lowest sufficient form (BASE for
   chat/trivia, SS1 for coding, SS2+ on research, SS3 on multi-file, BLUE on
   repeated failures/benchmark gap) → 3. capture context (compress: only
   relevant code/DOM/logs/visual reports) → 4. Eyes capture if UI involved →
   5. plan → 6. DeepSeek Flash 1731 executes → 7. test → 8. re-verify (Eyes or
   harness) → 9. escalate only if verification fails → 10. log → 11. adjust
   rules from outcome. Every step's model assignments are logged and
   form-checked.

## 13. Non-goals / deferred

- No new provider sign-ups: locked models stay locked until keys are
  configured (engine + UI already handle this).
- No copyrighted anime assets: auras and audio are original CSS/WebAudio.
- The harness task set stays at the existing 48 tasks (12+ profiles);
  expanding task counts is a later slice.
