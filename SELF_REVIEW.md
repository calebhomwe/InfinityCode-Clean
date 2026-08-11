# Infinity Code — Self-Review Findings (v0.2)

A proactive review of the v0.2 code I authored, for the external audit.
**Update:** findings #1, #2, #3 are now **FIXED + verified** (see status tags); the rest
remain documented for the reviewer.

## 1. [BUG · low-moderate] ✅ FIXED — `/build` loop budget guard was a no-op
`backend/core/loop_engine.py:98`
```python
if getattr(self.cost_tracker, "remaining_budget_aud", 1.0) <= 0:
```
`CostTracker` has **no** `remaining_budget_aud` attribute (only `daily_budget_aud` and
private `_spent_today_aud`). So `getattr(..., 1.0)` always returns `1.0` → the guard
never trips → the loop's per-daily-budget cap is **not enforced**. Cost is still bounded
by `max_iter`, so it's not unbounded, but the budget check is dead.
**Fix:** add a public `remaining_aud` on `CostTracker` (`daily_budget_aud -
_spent_today_aud`) and check that; or inline `daily_budget_aud - _spent_today_aud <= 0`.

**Fixed:** added `CostTracker.remaining_aud` property; `loop_engine.py` now reads it. Verified.

## 2. [LATENT · medium] ✅ MITIGATED — Embedding-dimension coupling
`backend/core/knowledge.py` (`_load_matrix`/`search`), `backend/core/memory.py`.
Vectors are stored as raw `float32` blobs and `np.stack`ed into one matrix; search does
`matrix @ query`. This assumes **every** stored vector and the query share one dimension
(currently 1536 from `text-embedding-3-small`). If the embedding model is ever changed
(e.g. to local LM Studio embeddings — a stated future goal), old 1536-dim rows + a new-dim
query will raise on `np.stack`/matmul, silently breaking retrieval until a full re-embed.
**Fix:** persist the embed model + dim in the DB; on change, clear + re-embed all chunks
(and reject mixed-dim rows in `_load_matrix`).

**Mitigated:** `search()` now checks matrix-dim == query-dim and fails soft (empty + log)
instead of crashing on `np.stack`/matmul. Full fix (persist model+dim, auto-rebuild) still
open. Verified: a 768-dim query against 1536-dim store returns empty, no crash.

## 3. [CONCURRENCY · low] ✅ FIXED — Unlocked knowledge matrix cache
`backend/core/knowledge.py` — `_matrix`/`_chunk_index` are mutated by `reindex()`
(`_invalidate`) and read by `search()` with no lock. FastAPI runs sync endpoints in a
threadpool, so a search concurrent with a reindex could observe a half-loaded matrix or
double-load. Low severity (reindex is a manual, rare action).
**Fix:** guard `_load_matrix`/`_invalidate`/`search` reads with a `threading.Lock`.

**Fixed:** `threading.Lock` guards `_load_matrix`/`_invalidate`/`search` matrix access.
Verified search still returns correct hits.

## 4. [STYLE] Scheduler event-loop acquisition
`backend/core/scheduler.py` — `asyncio.get_event_loop().create_task(...)`. Fine because
it's called from the lifespan's running loop, but `asyncio.create_task(...)` (or accepting
the loop explicitly) is the modern, deprecation-safe form.

## 5. [MAINTAINABILITY] `backend/main.py` is a ~2160-line monolith
All routes + lifespan + helpers in one file. Candidate for splitting into routers
(chat, knowledge, agents, schedules, loop, settings) behind an `APIRouter` per concern.

## 6. [HOUSEKEEPING] Repo cruft
Root has `build-0.1.*.log` (16 files) and `src-tauri/target/release/bundle/msi/` holds
22 historical MSIs (~2GB). Safe to gitignore/prune. `data/agents.json` (3.5MB) and the
frozen backend (88MB with numpy) are intentional.

---
### Verified-good (spot-checked, no issue found)
- Action gating: side-effect/paid tools per-call approval-gated; scheduled runs hard-force
  `allow_actions=false`; SSRF guard on `fetch_url`/`see_image`.
- Auto-continuation, quality gate (PASS/RETRY/ESCALATE/REJECT), and the `or_swarm.py`
  kimi-k3 reasoning-token handling are unit-verified.
- Memory now stores each memory embedded with **its own** text vector (prior mismatch fixed).
- DBs use WAL + busy_timeout; indexes on chat_id/source_id.
