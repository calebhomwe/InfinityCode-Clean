# Harness Audit & Optimization Report — 2026-08-10

Consolidated deliverable for the 5-part harness engagement (Audit, Stress/Failover,
Autonomy Benchmark, Fuzz, Optimization). All claims below are backed by executed
evidence: test runs, shipped builds, and benchmark artifacts.

**Regression baseline:** 566/566 passed across the full `backend/tests/`
suite (fuzz 16, observability 11, failover 14, kernel/engine/cache, rescue,
max_chars, and the pre-existing suites) — re-verified 2026-08-11 after the
0.1.64 source changes. CI (`.github/workflows/ci.yml`) runs the full
`backend/tests/` directory, so every new suite is covered.

---

## Part 1 — Audit & Gap Analysis

### Critical blockers found and FIXED (all shipped)

| ID | Gap | Evidence | Fix |
|----|-----|----------|-----|
| C1 | Free-tier `openrouter/free` meta-slug 404'd on some providers, poisoning failover | Reproduced in failover tests | Free-stage forces the `openrouter/free` slug only inside the free stage; fatal-vs-transient classification in `openrouter_client.py` |
| C2 | Health-probe poisoning: one bad probe marked routes dead for the whole process lifetime | Observed in `_ROUTE_HEALTH` behavior | 300s DEAD TTL + `mark_route_live` revival; now also persisted (see P5) |
| C3 | Route-health dict mutated without locking across concurrent requests | Code review of `_ROUTE_HEALTH` | Guarded mutation + TTL-based expiry; isolated per-test via fixture |
| H3 | **Images disabled the entire tool belt** (`use_tools = ... and not has_images`) — multimodal turns could not call any MCP tool | `backend/routers/chats.py` L660-ish gap | Images are now grounded to Qwen-MM text descriptions via `vision_assist.augment_content_if_needed`; belt stays on (`tools_wanted` at L664). Shipped in the 21:51 build |

### Verified NOT gaps (audited, working)

- **MCP plugin registration & discovery:** `mcp_manager.tool_schemas()` injects
  **69 live schemas** (qwen-mm 15, video-edit 5, video-memory 9, playwright 24,
  browser-use 16) into the function-calling belt; dispatch via
  `mcp_manager.call("mcp__server__tool", args)` with 90s timeout.
- **Multimodal routing:** `backend/core/vision_assist.py` — Ollama-local-first
  (`qwen3-vl:8b` @11434) → cloud fallback (`qwen/qwen3-vl-32b-instruct`, `8b`
  via OpenRouter); sha256 description cache; fail-open everywhere.
- **Streaming:** SSE path intact for both tiers.
- **Cost accounting:** per-call `_record` with per-route pricing; now with
  cost-spike alerting (Part 5).
- **Autonomous loop:** kernel + JSON-actions engines both run plan→act→observe
  loops (benchmarked in Part 3); context management via `test_llm_cache.py` suite.

### Remaining gaps (deferred, user decision required)

1. Live 50-concurrent free-tier blast (will intentionally trigger 429s).
2. Paid-model benchmark cell (DeepSeek flash, ~$0.01).
3. Live multimodal fuzz through the full MCP chain (consumes API quota).

---

## Part 2 — Stress & Failover (SLA matrix)

All executed offline against the real failover chain
(`dashscope → openrouter → deepseek`) with faked transports.

| Test | SLA | Result | Evidence |
|------|-----|--------|----------|
| 429 → route swap | < 2s | **PASS** | `test_failover_on_429_swaps_within_2s` |
| Request dropped during transition | 0 drops | **PASS** | `test_failover_does_not_drop_the_request` |
| Cost attribution post-failover | surviving-route pricing | **PASS** | `test_cost_attribution_after_failover_uses_surviving_route_pricing` |
| Free-stage 429 scope | marks free route dead only | **PASS** | `test_free_stage_429_marks_free_route_dead_only` |
| Transient errors | retry in place first | **PASS** | `test_transient_error_retries_in_place_first` |
| All routes dead | raises with health report | **PASS** | `test_all_routes_dead_raises_with_health` |
| Multimodal decode boundaries | fail-open | **PASS** | 4 vision decode fuzz tests (Part 4) |
| Audio/video attachments | surfaced, never silent | **PASS** (offline) | F7: ingress logs + injects limitation note; live audio through Qwen MM stays quota-gated (owner opt-in) |

**SLA targets (updated):** failover swap ≤ 2s; zero dropped requests within a
walk; total walk capped by `INFINITY_WALK_TIMEOUT_S` (default 120s);
concurrent free-tier latency p50 ≤ 15s / p99 ≤ 60s at the sustainable rate;
free-stage send rate capped at ≤ 0.25 req/s (measured account quota, below).

### 2.1 LIVE free-tier stress (50 concurrent, `openrouter/free`, $0.00) — 2026-08-10

Script: `benchmarks/stress_free_tier.py`, reports in
`backend/data/eval_reports/stress_free_tier_2026-08-10*.json`.
Three prompt-length classes, unique anti-cache suffixes, `max_tokens=64`.

| Run | Conditions | Success | p50 / p95 / p99 | Cost | Verdict |
|-----|-----------|---------|------------------|------|---------|
| v1 (before) | 50 blast, serialized route lock | 49/50 | 129.6 / 250.9 / 252.9s | $0.00 | latency = pure queueing |
| v2 (after lock fix) | same 50 blast, parallel | 16/50 | 3.45 / 10.4 / 13.1s | $0.00 | true upstream rate limit exposed |
| v3 (throttled 0.35 rps) | token bucket 0.35/s | 25/50 | 5.14 / 21.0 / 22.1s | $0.00 | account quota already exhausted by v1+v2 |

**Root causes found (all reproduced, all fixed):**

1. **`_route_lock` serialized every concurrent LLM call.** The lock was held
   for the entire failover walk including the network call — 50 parallel
   requests queued into a single-file line (v1: p50 129.6s vs ~2–12s service
   time). **Fix:** lock now guards route-state mutation only
   (`_prepare_route_kwargs`, `_advance_route`); the wire call runs unlocked
   (v2: p50 3.45s — **37× faster p50, 19× faster p99**). Regression:
   `test_concurrent_calls_do_not_serialize_on_route_lock`.
2. **Upstream-provider errors killed the whole free route.** OpenRouter
   relays upstream failures as 403 "Provider returned error" (observed:
   Novita `NOT_ENOUGH_BALANCE` behind 11× `free-models-per-min` 429s).
   The fatal-marker list saw "403/balance" and marked the aggregator route
   DEAD for 300s. **Fix:** new `is_upstream_provider_error()` — relayed
   upstream failures are transient, retried in place, route stays live.
   Regression: `test_upstream_provider_error_is_transient_not_route_fatal`.
3. **Thinking-model empty-content trap: 30/49 "successful" replies were
   empty** (v1). The `openrouter/free` meta router dispatches to
   thinking-only models that consume the whole small `max_tokens` budget on
   reasoning. **Fix:** `chat()` retries exactly once with a 4000-token floor
   on empty output. Regression: `test_empty_reply_retries_once_with_token_floor`.
4. **Concurrent-failover race:** two workers failing the same route could
   double-advance and kill the next healthy route. **Fix:**
   `_advance_route(..., failed_route=route)` ignores stale failures.
   Regression: `test_advance_route_ignores_stale_concurrent_failure`.
5. **Route-health dict/file were mutated unlocked** once calls went parallel.
   **Fix:** module-level `_HEALTH_LOCK` around `_ROUTE_HEALTH` + persistence.

**Measured free-tier capacity (this account):** ~0.2 req/s sustained
(v1 pacing: 49/50). Bursts above ~0.5 req/s exhaust the
`free-models-per-min` upstream quota; the 3 requests that exhausted all
retries ended at ~22s — the walk deadline fails fast as designed.
**Proposed (not shipped, needs owner sign-off):** client-side admission
limiter on the free stage (token bucket ≤ 0.25 req/s with queuing) so bursts
pace instead of burn quota; requires a product decision on burst UX.

### 2.2 LIVE free→paid failover (20 concurrent, full production chain) — 2026-08-11

Script: `benchmarks/failover_live.py`, reports in
`backend/data/eval_reports/failover_live*_2026-08-11.json`. Chain:
free-router first, then every production route (dashscope ×6,
openrouter-paid, deepseek, moonshot ×2). Spend guard: `max_tokens=48`.

| Run | Result | Verdict |
|-----|--------|---------|
| v1 (N=12, 2-route chain) | 11/12, $0.00, no swap; 1 raw `TypeError` crash | burst too small; crash found |
| v2 (N=20, 2-route chain) | 15/20; OpenRouter paid answered 403 `NOT_ENOUGH_BALANCE` ×3 retries then both routes dead | paid-key balance broken; retry-burn found |
| v3 (N=20, full chain) | 20/20, 0 drops, $0.000062 | failover validated end-to-end |
| **v4 (N=20, full chain, instrumented)** | **20/20, 0 drops, swap gap 1.471s, free $0.00 / paid $0.000185** | **SLA PASS: swap <2s, zero drops, attribution correct** |

**Root causes found this round (all reproduced, all fixed):**

6. **Cost-attribution bug:** free-stage answers were billed at the requested
   priced model instead of the `openrouter/free` ($0) model that served them.
   **Fix:** `_walk_routes` returns `(response, served_model)`; new
   `_billing_model_id(served, requested)` prefers the served model when it is
   in the pricing table, else falls back to the requested id (native provider
   ids are unprefixed and miss the table). Regressions:
   `test_free_stage_bills_zero_even_for_priced_requested_model`,
   `test_paid_stage_bills_served_model_pricing`.
7. **`choices=None` crash under concurrent load (v1 drop):** upstream
   occasionally returns a response whose `choices` is `None`;
   `response.choices[0]` raises `TypeError` past the
   `(AttributeError, IndexError)` guard and escaped `chat()` raw. **Fix:**
   guard widened to include `TypeError` in `chat()` and `chat_with_vision()`
   (`image_gen` already had it). Regression:
   `test_choices_none_yields_empty_text_not_crash`.
8. **Retry-burn on account-balance failures (v2 drops):** `NOT_ENOUGH_BALANCE`
   relayed through OpenRouter was classified transient on every stage, burning
   3 retries + exponential backoff (~6–24s/request) on a route that cannot
   heal within the window. **Fix:** `is_route_fatal(exc, free_only=...)` is now
   stage-aware — relayed provider-balance errors are route-fatal on paid/direct
   stages (chain advances immediately) but stay transient on free-router stages
   (the meta router picks another upstream). Non-balance upstream errors stay
   transient everywhere. Regression:
   `test_provider_balance_error_is_route_fatal_no_retry_burn`.

**Environment findings (not code bugs):** the OpenRouter paid key has no
credits (403 `NOT_ENOUGH_BALANCE`); all six dashscope keys currently fail
auth. Failover around both worked exactly as designed (v4: requests landed on
native deepseek, $0.000185). Recommended owner actions: top up OpenRouter
credits; rotate/verify dashscope keys.

---

## Part 3 — Autonomous Workflow Benchmark ($0 budget, `openrouter/free`)

Run via `Tools/ab_kernel_vs_actions.py` (patched for `LOCAL_API_KEY` env).
Kernel mode = continuous kernel loop; Actions mode = JSON tool-call loop.

| Task | Kernel | Actions | Notes |
|------|--------|---------|-------|
| Debugging (broken main.py + error.log) | ✗ | **✓** | 8 calls, 60.2s; main.py verified fixed, prints 7 |
| Coding (FastAPI image-upload endpoint) | **✓** | ✗ | 9 calls, 246.5s; artifact `app.py` matches spec exactly |
| Research (multimodal RAG papers) | **✓** | ✗ | Kernel wrote a genuine papers.md (run 9, verified on disk); 7 root causes fixed en route |
| Creative / Data | not run | not run | Blocked on paid-model cell (needs user go-ahead) |

**Finding:** the two protocols are complementary, not competing — actions mode
wins tight deterministic fixes; kernel mode wins open-ended construction.
Recommended routing: debugging → actions; generation → kernel.

**Research gap closed (runs 3-6, 2026-08-11):** the original failure was three
stacked harness bugs, not a free-tier ceiling —
(1) `DashScopeClient` read `os.environ` at construct while CLI keys live in
`.env`, killing the provider chain before the walk (fixed: explicit `api_key`
+ per-construct try/except + `openrouter/free` appended as a live zero-cost
fallback); (2) the kernel sandbox forbids `urllib` by design but had no host
`web_fetch` handoff (fixed: `web_fetch` added to `_HOST_TOOLS` for BOTH
engines, http/https only); (3) `finish()` was trusted blindly — a run can now
be downgraded to `claimed_unverified` when the `--expect` artifact is absent.
Run 5 proved genuine research behavior: the kernel self-corrected through a
urllib security violation, bad URLs and 400s to fetch 9 real arXiv multimodal-RAG
papers — but 20K-char fetch blobs bloated history (135K input tokens in 24
steps) and the budget ran out before `papers.md`. Three more root causes fell
in runs 6-9, each fixed with a regression test:
(4) `_exec` dropped the `max_chars` arg the kernel preamble already exposed
(now honored; both prompts advertise small fetches parsed in-kernel);
(5) `result = web_fetch(url)` never binds — the `_ToolCall` raise aborts the
assignment — so models hit NameError refetch loops (`set_tool_result` now
also binds `result`; the prompt teaches one-tool-per-block);
(6) the `openrouter/free` meta router rotated onto a content-safety
classifier slug ("User Safety: safe") — benchmark runs pin a concrete free
slug; (7) builders that deliver every artifact and then stop emitting code
were discarded as errors — both engines now snapshot the repo at start and
end such runs `completed` with an artifact list (rescue-at-protocol-death).
Run 9 closed the cell: the kernel self-corrected through NameErrors, a
forbidden-import attempt and arXiv API errors, then fetched 5 real 2026-08
arXiv papers and wrote a genuine `papers.md` comparison table (verified on
disk) before dying on protocol; with fix (7) that shape reports completed.
Run 10 then delivered the clean scorecard row: kernel `completed`, 15 steps,
22 builder calls, 73K input tokens, 386s, clean `finish()` — a genuine
`papers.md` with 5 linked 2026-08-07 arXiv papers, artifact-gate verified.
(Free-tier ceiling note: actions mode still cannot hold protocol on free
slugs for open-ended research; kernel mode is the right engine for it.)

Baseline comparison: qualitative only (no public per-task numbers for
Replit/Cursor/Devin on these exact prompts); scorecard retained for trend
tracking in `benchmarks/reports/`.

---

## Part 4 — Fuzz Coverage (25 regression tests, all green;
hardened plugin interface spec below)

`backend/tests/test_harness_fuzz.py`:

- **Input:** MCP resolution with unknown server/tool; schema validation with
  wrong-type/missing args; unicode + special-char prompts; empty/null inputs;
  vision decode boundaries — invalid data-URLs, missing files,
  unicode/space/paren paths, corrupt PNG, >4MB file. All fail-open (return
  `None`, never raise).
- **Output:** malformed JSON from model; truncated tool calls; hallucinated
  plugin names (`mcp__ghost__tool` resolves to a clean error, not a crash).
- **State:** registry dispatch with bad args; JSON edge cases (nested, huge,
  duplicate keys).
- **Security regression:** kernel validator rejects `eval` payloads (L1 scan
  false-positive in this file is intentional).

**2026-08-11 state/security fuzz round (3 new P1 findings, all fixed):**

| # | Finding | Severity | Fix | Regression |
|---|---------|----------|-----|------------|
| F1 | `'{"a":' * 5000` raised `RecursionError` out of `parse_actions` (json C scanner recurses per nesting level); engine caught only `ProtocolError` → one hostile builder reply crashed the autonomous task | P1 | `protocol.py`: string-aware `_bounded_nesting()` pre-scan caps candidates at `_MAX_NESTING=64`; `parse_actions` now wraps everything into `ProtocolError` (never raises anything else); engine catch widened | 2 tests (8 hostile payloads, each <2s; valid action recovered beside pathology) |
| F2 | Garbage bytes in the journal DB raised `sqlite3.DatabaseError` out of `LongTaskJournal.__init__` → corrupt state store crashed backend startup | P1 | `journal.py` `_open_or_recover()`: integrity probe on open; corrupt file quarantined to `<name>.corrupt-<ts>` and rebuilt | 1 test |
| F3 | Prompt-injection gap: `t_run_command` runs `shell=True` with no danger gate and `autonomy='full'` bypasses the approval gate → a hostile repo file read into context could lure `rm -rf /` style commands | P1 | `tools.py`: `command_is_destructive()` regex gate (rm -rf, mkfs/diskpart/fdisk, `format X:`, `dd if=`, `del/rmdir /s`, `Remove-Item -Recurse -Force`, `curl\|sh`, fork bomb, shutdown/reboot, `reg delete`) raises `ToolError` inside `t_run_command`, so EVERY caller in EVERY autonomy mode is covered | 1 test (15 hostile blocked, 6 benign incl. `git diff --stat HEAD` allowed) |

**2026-08-11 fuzz round 2 — output/integration vectors (3 findings, fixed):**

| # | Finding | Severity | Fix | Regression |
|---|---------|----------|-----|------------|
| F4 | Context overflow: `compact_history` bounded message COUNT (24→11) but not SIZE — one 200KB `read_file` result inside the 10-message tail could balloon the prompt past the model window → upstream 400, no recovery path | P1 | `engine.py`: per-message cap (`_MAX_MSG_CHARS=4000`) + total-history budget (`_MAX_HISTORY_CHARS=48k`, most-recent-first keep, system prompt always survives) inside `compact_history`, already called every turn | 1 test |
| F5 | Context-overflow 400s matched no fatal marker → the walker retried + walked ALL routes with the same oversized prompt (guaranteed re-fail, retry/backoff burn on every stage) | P2 | `openrouter_client.py`: `_CONTEXT_OVERFLOW_MARKERS` + `is_context_overflow()` → route-fatal on every stage so the walk fails fast with the real reason | 1 test |
| F6 | Conflicting tool selections: one builder reply's byte-identical duplicate action blocks were all executed — step-budget burn + repeated side effects (worst case via hostile output) | P2 | `engine.py`: JSON-signature dedupe before dispatch, dedupe count journaled | 1 test |
| F7 | Audio/video attachments silently dropped: chat ingress kept only `data:image/*` from `request.images`; a `data:audio/*` attachment vanished without a trace, letting the model hallucinate that it heard the content | P2 | `routers/chats.py` `_split_media()`: images pass through, audio/video prefixes collected, WARNING logged, and an explicit system note is injected into the turn so the model states the limitation instead of pretending (2026-08-11) | 1 test |

Infinite reasoning loops: already gated (`_MAX_TURN_FACTOR` turn cap +
step/cost/wall budgets, verified). Memory over 100+ turns: per-turn
compaction bounds history (F4); a 120-turn tracemalloc leak probe showed
flat allocation (-0.5 KiB drift over turns 30-120 incl. 200KB tool results).
Swap models mid-conversation: covered by
`test_model_swap_mid_conversation_never_corrupts_chat` — two live swaps land
exactly, a hostile SQL-injection model id degrades to a valid fallback (no
5xx, parameterized SQL), chat row intact (isolated DB, 2026-08-11).

Severity classification: all observed pre-fix failures were P1 (autonomy
interrupt or machine-safety exposure) or P2 (degraded output); zero P0 remain
open.

### Hardened Plugin Interface Spec (contract as implemented)

The plugin (MCP) interface contract after all Part-4 hardening. Every clause
below is enforced in code and covered by the fuzz regression suite.

**1. Discovery.** `mcp_manager.tool_schemas()` is the single source of truth:
69 live JSON schemas (qwen-mm 15, video-edit 5, video-memory 9, playwright 24,
browser-use 16) are injected into the function-calling belt at chat time.
Schemas are read from the servers themselves, so they cannot drift from the
implementation. `GET /api/v1/mcp/servers` lists registration state.

**2. Addressing.** Tools are addressed `mcp__<server>__<tool>`. Unknown
server or tool names (hallucinated plugins) resolve to a clean textual error
returned to the model - never an exception, never a 5xx
(`test_mcp_call_unknown_tool_returns_error_not_raise`).

**3. Invocation.** `mcp_manager.call(name, args)`:
   - 90s hard per-call timeout; a slow plugin can never stall an autonomous step.
   - Circuit breaker: after `INFINITY_MCP_BREAKER_THRESHOLD` (5) consecutive
     failures the server fails fast for `INFINITY_MCP_BREAKER_COOLDOWN_S` (30s),
     then one probe call decides revival (3 regression tests).
   - Session init bounded at 30s; connection runs on a dedicated daemon thread.

**4. Input validation.** Args are schema-checked before dispatch (wrong type /
missing required -> clean error to the model). Shell-bearing tools pass the
destructive-command gate (`command_is_destructive()`) in every autonomy mode.

**5. Output contract.** Every tool result - MCP or built-in - passes
`sanitize_tool_output()` before the LLM sees it: coerced to `str`,
control-char-stripped, bounded to 6000 chars. Total function: never raises,
never returns a non-string (6 regression tests).

**6. Model-output parsing.** `protocol.parse_actions()` is ProtocolError-only:
any hostile or malformed builder reply degrades to a protocol error, never an
interpreter crash; nesting pre-scan capped at 64 (F1). Duplicate identical
actions in one reply execute once (F6).

**7. Multimodal ingress.** `vision_assist.augment_content_if_needed` is
fail-open: corrupt/oversized/missing media returns `None` (text-only path
continues), never raises; descriptions cached by sha256; local Ollama
`qwen3-vl:8b` first, cloud fallback second (5 decode-boundary fuzz tests).

**8. History safety.** Tool results riding in conversation history are capped
per-message (4000 chars) and in total (48k chars) before the next wire call
(F4); oversized requests that still reach the wire are classified route-fatal
(F5) instead of burning every route.

---

## Part 5 — Optimization & Polish (10:39 PM build + 2026-08-11 stress & failover fixes*)

`backend/tools/openrouter_client.py`:

| Change | Where | Effect |
|--------|-------|--------|
| `max_retries=1` on per-route OpenAI clients | `_client_for` | Retry logic owned by the failover walker, not duplicated by SDK |
| Walk deadline | L584 (`INFINITY_WALK_TIMEOUT_S`, default 120s) checked in route loop AND retry loop | No unbounded failover walks; raises with health report |
| Backoff jitter | L867 `RETRY_BACKOFF_SECONDS * 2**attempt * random.uniform(0.5, 1.5)` | Prevents retry thundering herd |
| Route-health persistence | L417-462: `_health_file()` / `_save_health()` / `load_health()` → `route_health.json` | Health survives restarts; env override `INFINITY_ROUTE_HEALTH_FILE` |
| Cost-spike alerting | L748 (`INFINITY_COST_SPIKE_USD`, default 2.0) | Logs `COST SPIKE` when a single call exceeds threshold |
| MCP circuit breaker | `backend/core/mcp_client.py` `call()` + `_breaker_open`/`_breaker_record` | Flaky plugin servers fail fast after `INFINITY_MCP_BREAKER_THRESHOLD` consecutive failures; auto-probe after `INFINITY_MCP_BREAKER_COOLDOWN_S` (3 regression tests) |
| Trace-ID logging | `backend/main.py` `_TRACE_ID` contextvar + `_TraceFilter` + `_trace_middleware` | Every request gets a 12-char trace id (client may supply `X-Trace-ID`); all log lines carry `[trace]`; response echoes `X-Trace-ID` (4 regression tests) |
| Tool-output validation | `backend/core/tools_registry.py` `sanitize_tool_output()` wired at the chats.py injection point | Every tool result (MCP + built-in) is coerced to str, control-char-stripped, and bounded to 6000 chars before the LLM sees it — total function, never raises (6 regression tests) |
| Dependency health checks | `backend/routers/system.py` `GET /api/v1/health/dependencies` | Read-only report: provider route states (masked keys), MCP server connections, OpenRouter key presence, local model server, data-dir writability; never raises, never leaks secrets (1 regression test) |
| Concurrent LLM calls unlocked | `_walk_routes` lock scope (route-state only; wire call unlocked) + `_HEALTH_LOCK` + `_advance_route(failed_route=...)` | 50-way blast p50 129.6s → 3.45s; no double-advance race; health persistence thread-safe (2 regression tests) |
| Upstream-provider errors transient | `is_upstream_provider_error()` in `is_route_fatal()` | Aggregator-relayed failures (403 "Provider returned error") retry in place instead of killing the route 300s (1 regression test) |
| Empty-reply token-floor retry | `chat()` one bounded retry with max_tokens 4000 | Thinking-only free models can no longer return empty content on small budgets (1 regression test) |
| Served-model cost attribution | `_walk_routes` returns `(response, served_model)`; `_billing_model_id(served, requested)` at all 4 billing sites | Free-stage answers bill $0 (openrouter/free) even when a priced model was requested; native-provider ids fall back to the requested id for pricing (2 regression tests) |
| Malformed-response guards | `chat()`, `chat_with_vision()`, stream loop: except widened to `(AttributeError, IndexError, TypeError)` | Upstream `choices=None` under concurrent load degrades to empty text / skipped chunk instead of a raw `TypeError` crash or aborted stream (2 regression tests) |
| Stage-aware balance classification | `is_route_fatal(exc, free_only=...)` + `_PROVIDER_BALANCE_MARKERS` | Relayed `NOT_ENOUGH_BALANCE` advances the chain immediately on paid stages (no retry-burn) but stays transient on free-router stages where another upstream may answer (live-validated §2.2 v4; 1 regression test) |
| Hostile-builder-output hardening | `protocol.py` `_bounded_nesting()` + `parse_actions` ProtocolError-only contract; `engine.py` catch widened | Deeply-nested/malformed model output degrades to a clean protocol error in <0.05s instead of a RecursionError crash (2 regression tests) |
| Corrupt-state recovery | `journal.py` `_open_or_recover()` | Corrupt longtask journal DB is quarantined + rebuilt at startup instead of crashing the backend (1 regression test) |
| Destructive-command safety gate | `tools.py` `command_is_destructive()` inside `t_run_command` | Irreversible shell (rm -rf, mkfs, format, curl\|sh, fork bomb, ...) is refused in EVERY autonomy mode — closes the prompt-injection → shell vector (1 regression test) |
| Context-overflow protection | `engine.py` size-bounded `compact_history` (`_MAX_MSG_CHARS`/`_MAX_HISTORY_CHARS`) + `openrouter_client.py` `is_context_overflow()` route-fatal classification | Giant tool results can no longer blow the model window; if an oversized request still reaches the wire, the walk fails fast instead of burning every route (2 regression tests) |
| Conflicting-action dedupe | `engine.py` JSON-signature dedupe before dispatch | Duplicate action blocks in one reply execute once; savings journaled (1 regression test) |
| Connection pre-warm | `main.py` `_probe_provider_routes()` (background thread at startup) | One 4-token ping per route builds the HTTP client pool, warms TLS, and seeds route health before the first chat (verified 2026-08-11) |
| Debug mode | `main.py` `INFINITY_DEBUG=1` boot flag + `GET /api/v1/debug/state` + `POST /api/v1/debug/verbose` runtime toggle | Verbose DEBUG logging at boot or flipped at runtime without a restart; state queryable (3 regression tests) |
| Ops endpoints | `POST /api/v1/routes/reset` (`orc.reset_route_health()`) + `POST /api/v1/cache/clear` (`llm_cache.clear()`) | Unstick DEAD routes without waiting the 300s dead-TTL; drop the LLM cache after prompt changes (2 regression tests) |
| CLI ops commands | `Tools/infinity_cli.py`: `health`, `debug on\|off\|status`, `routes-reset`, `cache-clear` (join `status`/`models`/`run`) | Common operations from the terminal against the running backend; stdlib-only, exit codes 0/1/2 (1 parser regression test) |
| Deterministic CI suite | `test_swarm_fanout.py` overlap gauge replaces wall-clock parallelism assert; `test_longtask_live_cancel.py` polls worker cleanup instead of asserting instantly | Both timing-flaky tests hardened: parallelism proven by peak-concurrency counter, cancel cleanup by deadline poll; 547/547 green in 2 consecutive full runs under live app load (2026-08-11) |
| Long-task progress streaming | `routers/longtask.py` `GET /api/v1/longtasks/{id}/events` (SSE) | Partial results stream DURING execution: snapshot frame for late subscribers, live plan/step/review/artifact frames via `_LONGTASK_SUBS` fan-out, heartbeats, auto-terminate on non-running status (3 regression tests incl. fan-out + cleanup) |
| Prompt-template efficiency | `engine.py` `SYSTEM` (~550 chars, action grammar only) + `DESIGN_SYSTEM_PROMPT` appended only on design requests; `_REVIEW_PROMPT_HEAD` minimal JSON contract | Per-turn system overhead stays ~0.5 KB; no dead instruction text (verified 2026-08-11) |

Before/after: failover walk previously unbounded (observed hangs in manual
testing) → hard 120s cap with actionable health report; retry storms possible
→ jittered exponential backoff; concurrent p50 129.6s → 3.45s (§2.1).

*Live rows measured 2026-08-10/11 (§2.1 stress, §2.2 failover) and verified
by 566/566 tests; attribution, choices-guard, balance-classification and
stream-guard fixes shipped in the 2026-08-11 01:00 desktop rebuild; protocol,
journal, command-gate, context-overflow, dedupe hardening and the Part 5 DX
endpoints (debug mode, routes-reset, cache-clear) shipped in the 2026-08-11
02:44 desktop rebuild (MSI 0.1.61; `/api/v1/debug/state` live-verified 200 on
the running app, 13 MCP servers registered); the F7 audio/video ingress fix
shipped in the 03:09 rebuild of the same MSI (full smoke test green: token,
cost, agents, CORS preflight all pass on the frozen exe); MSI 0.1.62 (2026-08-11
03:52) added the owner-pinned plugin set — `qwencloud-model-selector`,
`qwencloud-text`, `qwencloud-vision`, `qwencloud-video-generation`,
`autonomous-coder-loop` seeded with `always: true` and injected every turn via
`SkillEngine.always_on()` — plus the `web_fetch` host tool for both long-task
engines, the `--expect` artifact gate, ChatClient construct hardening and
UTF-8-BOM tolerance in the skill loader and benchmark task files (frozen exe
seeding verified end-to-end in the smoke data dir). The `max_chars` fetch-cap
passthrough, the drag-region/window fixes and the toolresult-alias +
rescue-at-protocol-death engine hardening shipped in MSI 0.1.63 (2026-08-11).
MSI 0.1.64 (2026-08-11 05:40, 566/566 green, tsc clean) added the live-app UX
batch: Models page Back button + real model catalog list (`ModelsView.tsx`
rewritten to render `/chat/models`), MCP quick-connect presets for
Unreal/Unity/Godot/Blender in Settings (`SettingsModal.tsx` `QUICK_MCP`), and
three NVIDIA NIM free models in the chat picker (`CHAT_MODELS` in `main.py` +
alias degradation in all four provider maps of `openrouter_client.py` so the
new ids route or degrade gracefully whichever key is configured).

---

## Monitoring & Alerting Setup

Named deliverable; every channel below exists in the shipped build:

| Signal | Mechanism | Reaction |
|--------|-----------|----------|
| Single-call cost anomaly | `openrouter_client.py` logs `COST SPIKE` when a call exceeds `INFINITY_COST_SPIKE_USD` (default $2.00) | Operator investigates; budget guard still caps daily spend server-side |
| Route degradation | `_ROUTE_HEALTH` persistence (`route_health.json`) + 300s DEAD TTL | Dead routes auto-revive after TTL or via `POST /api/v1/routes/reset`; state survives restarts |
| Dependency status | `GET /api/v1/health/dependencies` | One read-only report: provider routes (masked keys), MCP connections, key presence, local model, data-dir writability |
| MCP server flakiness | Circuit breaker opens after `INFINITY_MCP_BREAKER_THRESHOLD` failures, probes after cooldown | Fail-fast instead of 90s stalls; breaker state logged per server |
| Request tracing | 12-char trace id on every request (`X-Trace-ID` echo) | Correlate logs end-to-end across one user action |
| Verbose diagnostics | `INFINITY_DEBUG=1` / `POST /api/v1/debug/verbose` / `GET /api/v1/debug/state` | Flip DEBUG logging at runtime; state queryable |
| Long-task progress | SSE `/api/v1/longtasks/{id}/events` + journal steps | Live step/review/artifact frames; full forensic history in the journal DB |
| Ops from terminal | `Tools/infinity_cli.py health \| debug \| routes-reset \| cache-clear` | Human-readable status and recovery actions against the running backend |

## Configuration Guide (all new env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `INFINITY_WALK_TIMEOUT_S` | `120` | Max seconds for one failover walk across all routes/retries |
| `INFINITY_COST_SPIKE_USD` | `2.0` | Per-call cost (USD) that triggers a COST SPIKE log |
| `INFINITY_ROUTE_HEALTH_FILE` | `<data dir>/route_health.json` | Override route-health persistence path (used by tests) |
| `INFINITY_MCP_BREAKER_THRESHOLD` | `5` | Consecutive MCP call failures before the server's circuit breaker opens |
| `INFINITY_MCP_BREAKER_COOLDOWN_S` | `30` | Seconds a breaker stays open before a probe call is allowed |
| `INFINITY_LLM_CACHE_DIR` | `<data dir>/.llm_cache` | LLM response cache directory |
| `INFINITY_DEBUG` | `0` | `1` boots the backend with verbose DEBUG logging (runtime alternative: `POST /api/v1/debug/verbose` or `infinity debug on`) |
| `OPENROUTER_API_KEY` | — | Paid tier + vision cloud fallback |
| `LOCAL_API_KEY` / `LOCAL_BASE_URL` / `LOCAL_MODEL` | — | A/B benchmark CLI (`Tools/ab_kernel_vs_actions.py`) local/cloud endpoint |

## Readiness Checklist

- [x] All critical blockers fixed and shipped (C1, C2, C3, H3)
- [x] Failover SLA locked by 23 regression tests
- [x] Fuzz regression suite (25 tests) green; CI covers `backend/tests/`
- [x] Reliability hardening shipped (deadline, jitter, health persistence, cost alerts)
- [x] Developer experience shipped (trace IDs, debug mode, config guide, 7 CLI commands, CI integration tests)
- [x] CI suite deterministic (2 timing flakes hardened; 547/547 twice under load)
- [x] Audit deliverable (this document) in `docs/`
- [x] 50-concurrent free-tier live blast — DONE 2026-08-10, §2.1: $0.00 spend, 3 root causes found & fixed (p50 129.6s → 3.45s)
- [x] LIVE free→paid failover — DONE 2026-08-11, §2.2: swap 1.471s (<2s), 0 drops/20, attribution verified ($0 free / $0.000185 paid); 3 more root causes fixed
- [x] Owner-pinned plugin set always-loaded (MSI 0.1.62, 5 skills, frozen-seeding verified)
- [x] Research-cell CLOSED: kernel wrote a verified papers.md on free tier (run 9); web_fetch host tool, fetch caps, result-alias and rescue-at-protocol-death shipped with regression tests
- [x] MSI 0.1.64 shipped: Models Back button + real model list, MCP quick-connect (Unreal/Unity/Godot/Blender), NVIDIA NIM free models in picker with per-provider alias fallback; 566/566 + tsc clean
- [ ] Paid-model benchmark cell (DeepSeek flash, ~$0.01) — **needs spend approval**
- [ ] Live multimodal fuzz through MCP chain — **needs quota opt-in**
