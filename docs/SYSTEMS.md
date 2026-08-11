# SYSTEMS.md — how the moving parts fit together

> Started 2026-07-30 with the local-LLM swarm integration. Add a section here
> whenever a new subsystem ships (per the "organized repeatable systems" rule:
> orphaned unwired files are bugs).

## Local LLM engine (LM Studio) — swarm integration

**What it is.** Missions with `fast=true` (and, since 2026-07-30, ANY role whose
whole cloud chain fails — e.g. OpenRouter 402 out-of-credits) run on LM Studio
at `localhost:1234` instead of cloud APIs. Free, private, and measured at
~234 tok/s aggregate with 4 concurrent requests on the RTX 4080.

**Where.** `backend/core/swarm.py`:
- `_LOCAL_MODEL_PREFERENCE` / `_LOCAL_MODEL_BLOCKLIST` — which local model the
  swarm picks. Never `models[0]`: LM Studio's `/v1/models` lists every
  *downloaded* model and requesting an unloaded one JIT-loads it, so a naive
  pick can pull a 21GB model onto a 16GB card (50-100x slowdown).
- `_local_engine()` — detection + client (max_retries=2 to ride out the JIT
  race where concurrent requests hit a model mid-load and get 400
  "Model is unloaded").
- `_call_agent()` — cloud chain first, then local fallback for any role.
- `_attempt_code()` — local-first engineer with a constrained prompt
  (stdlib+numpy+matplotlib only, Agg backend) and empty-code guard.

**The one rule that matters: reasoning models need a 12000-token budget.**
Qwen3.5 models think for ~3-6k tokens before emitting code. With budgets of
2000-6000 they hit `finish_reason=length` mid-reasoning and return
`content=""` — which used to become an EMPTY main.py that exited 0 and was
recorded as a *successful* mission. Fixed three ways: 12000-token budgets at
both local call sites, empty content raises and falls back, and empty
extracted code is always a failed attempt. `/no_think` and
`chat_template_kwargs {enable_thinking:false}` do NOT work on Qwen3.5 via
LM Studio — don't rediscover this.

**Sandbox note.** `core/sandbox.py` runs generated code with `python -I`
(isolated env, scrubbed keys) but NOT `-S`: `-S` skips site-packages entirely
and silently broke every mission needing numpy/matplotlib. The dev venv
(`venv/`, not `.venv/` which is a bare shell) has numpy+matplotlib installed.

**Standing model layout (shared with FABLE/opencode, see
`C:\AI\fable\FABLE-MINI.bat`):**
| identifier | model | role |
|---|---|---|
| `fable-fast` | Qwen3.5-9B, ctx 32768 | opencode driver + swarm engineer (12k budget) |
| `qwen/qwen3.5-2b` | Qwen3.5-2B, ctx 16384 | opencode titles + swarm last-resort (emits instantly, zero reasoning, but too weak for real codegen — failed every real mission attempt) |

**Measured performance (2026-07-30, RTX 4080 16GB).**
- 3 concurrent fast missions, all-local: engineer ~50s/attempt on the 9B,
  ~5s/attempt on the 2B; LM Studio continuous batching scales 83→234 tok/s
  aggregate from 1→4 concurrent requests.
- Honest quality: simple/medium codegen (physics scatter render) one-shots;
  hard structured codegen (roguelike dungeon with rooms+corridors) exceeds the
  9B's one-shot ceiling — three attempts produced three different real bugs.
  That class of mission wants a cloud model or `mode=code` without `fast`.

**Deploy reality.** The installed desktop app runs a FROZEN backend
(`C:\Program Files\Infinity Code\binaries\infinity-backend.exe`); source
patches only reach it after `build-desktop.ps1`. For development, run the
source backend against the app's real data:
```powershell
$env:INFINITY_DATA_DIR = "C:\Users\caleb\AppData\Roaming\com.infinitycode.app"
cd C:\Users\caleb\infinity-code\backend
..\venv\Scripts\python.exe serve.py
```
(kill the frozen exe first; the desktop UI reconnects to :8000 transparently).

## 2026-07-31 — model saga (see the playbook)

A full night of benchmarking settled which local models are usable on this box.
Definitive reference: **`C:\AI\fable\MODEL-PLAYBOOK.md`** — read it before
changing `_LOCAL_MODEL_PREFERENCE` or reaching for a bigger local model.

Headlines relevant to the swarm:
- Qwen3.5-9B (`fable-fast`) stays the driver: 69 tok/s, reliable tool-calls.
  Qwen3.5-2B stays the emitter-of-last-resort. Nothing bigger is viable locally.
- A model must fit ~12.5GB to be VRAM-resident. Dense spill is death; MoE spill
  via LM Studio layer-split is *also* death (4.9 tok/s — slower than pure CPU).
- Proven-dead list (don't retry): Ornith-9B (destructive edits), Ornith-35B,
  Qwen3.6-27B dense as a driver, Qwen3.5-4B, `/no_think` on Qwen3.5.
- The 12000-token budget rule above is confirmed and generalizes to Qwen3.6.
- Biggest unlock available: 64GB RAM, then an internal SSD for models.
