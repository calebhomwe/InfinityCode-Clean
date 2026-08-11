# Infinity Code — Handover (continue WITHOUT Claude)

_Written 2026-08-02. Purpose: hand off active work so you can keep going on your
OWN models (Qwen / Kimi / DeepSeek / local FABLE / Codex) instead of spending
Claude credits._

Repo: `C:\Users\caleb\infinity-code`  ·  Installed app: `C:\Program Files\Infinity Code\`
Current shipped version: **0.1.59** (audit baseline; see SYSTEMS.md + docs/audit/) (MSI in `src-tauri\target\release\bundle\msi\`).

---

## HOW TO CONTINUE WITHOUT CLAUDE

You already have a re-runnable dev swarm that uses YOUR models, not Claude:

```bash
cd C:\Users\caleb\infinity-code
# PLAN(kimi-k3) -> BUILD(kimi-k2.7-code) -> REVIEW(kimi-k2.6). Writes proposals
# to Tools\proposals\<slug>\ — it never edits the repo, so it's safe + cheap.
venv\Scripts\python.exe Tools\dev_swarm.py "<feature to build>" --context src/App.tsx backend/main.py
```

Other non-Claude options:
- **DashScope Qwen direct** (fast, cheap): `qwen3-coder-480b-a35b-instruct` ~1.5s.
  Key/endpoint in `~/.env` (`DASHSCOPE_API_KEY`, `DASHSCOPE_BASE_URL`, workspace maas URL).
- **Local FABLE stack** (zero cost, on your 4080): llama.cpp on `:8081` — see config.yaml `local:`.
- **Codex** (`~/.codex`) for larger edits.
- The app itself can now build features via its Build view (missions), running on Qwen/Kimi.

To edit dev_swarm's models, see `PLANNER/BUILDER/REVIEWER` at the top of `Tools\dev_swarm.py`.

---

## BUILD / INSTALL / VERIFY (all local, no Claude)

```powershell
# 1. Build the MSI (freezes backend + Tauri). ~3-4 min.
cd C:\Users\caleb\infinity-code ; .\build-desktop.ps1
#    GOTCHA: run it plainly. Piping its output can make PyInstaller's stderr
#    trip exit 255 — that's a false alarm; just re-run without the pipe.

# 2. Install (elevated) + launch
$msi = Get-ChildItem "src-tauri\target\release\bundle\msi\*.msi" | Select -Last 1
Start-Process msiexec.exe -ArgumentList "/i","`"$($msi.FullName)`"","/qn","/norestart" -Verb RunAs -Wait
Start-Process "C:\Program Files\Infinity Code\Infinity Code.exe"
```

Verify a mission end-to-end (backend is on `http://127.0.0.1:8000`):
```powershell
# create a code mission, poll /api/v1/missions until status=completed
```
Typecheck frontend: `npx tsc -p tsconfig.json --noEmit` (must be exit 0 before building).

**Version bump** = edit FOUR files: `package.json`, `src-tauri/tauri.conf.json`,
`src-tauri/Cargo.toml`, `backend/main.py` (the `version="..."` in FastAPI()).

---

## KEY FACTS / GOTCHAS (each cost real time to learn)

- **DATA DIR**: the installed app reads/writes `C:\Users\caleb\AppData\Roaming\com.infinitycode.app\`
  (set via `INFINITY_DATA_DIR`). That's the REAL missions.db / knowledge.db / learned/ + the
  `moonshot.key`, `dashscope.key`, `dashscope.base` files. A dev backend started WITHOUT that env
  var uses a separate repo-local DB — you'll think data is missing when it's just the wrong DB.
- **config.yaml `models:` OVERRIDES the code COUNCIL.** To change a role's model you must edit
  BOTH `backend/config.yaml` (models:) AND `backend/core/router.py` (COUNCIL) or config wins.
- **effort → role**: `low`=worker (gemini-flash-lite), `med/high`=engineer, `xhigh/max`=architect.
  A low-effort test will NOT exercise the engineer model.
- **Thinking models (kimi-k2.6/k3, some qwen) need generous max_tokens** (~3000). At 700 they burn
  the budget reasoning and return EMPTY, silently falling back to a pricier model. Short test
  prompts don't reproduce it — test at realistic prompt size.
- **Kimi via Moonshot direct requires temperature=1** (clamped in moonshot_client). DashScope Qwen
  accepts normal temps.
- Sandbox (`backend/core/sandbox.py`) runs mission code with `python -I` (isolated) — NOTE a
  teammate reverted the Job-Object hardening; current version keeps PATH so numpy/matplotlib work.
- `is_kimi()` / `is_dashscope()` in router.py decide which direct provider a model id uses;
  dispatch is in `swarm._provider_chat`.

---

## WHAT'S DONE (this session, all shipped + verified)

0.1.46 reconciliation of zombie missions + Cancel/Retry + killed always-on blur ·
0.1.47 sounds+notifications, TurboActivity Composer panel, adaptive polling, live screenshot,
media compression + code-split, LAN/iPhone web access, VS Code workspace ·
0.1.49 self-training loop (live web → K3 distil → RAG, nightly) ·
0.1.50 direct Moonshot Kimi API ·
0.1.52 real per-agent model routing + fallback chains + goal-fit acceptance gate ·
0.1.53 sandboxed exec + concurrency cap (3) ·
0.1.54 genuine tournament diversity (different models per candidate + goal-fit judge) ·
0.1.55 direct DashScope Qwen (engineer=qwen3-coder-480b, architect=qwen3.7-max, eye=qwen3-vl-plus).

Roadmap + audit: `C:\Users\caleb\infinity-code\docs\ROADMAP_v0.1.47.md`.

---

## WHAT'S STILL QUEUED (pick these up with dev_swarm / Codex / local)

1. **Compose-your-crew agent picker** (the last item from the original amp-up list).
   Let users choose which agents run a mission + combine with the built-in swarm.
   Over the 245-agent library (`backend/data/agents.json`). Frontend: a picker in the Build
   composer; backend: accept a `agents:[...]` param on mission-create and feed into the swarm.
2. **Per-step checkpoints + restore** (snapshot mission dir before each agent write; roll back one
   agent's output without nuking the rest).
3. **NEEDS YOUR EYES/EARS** (I can't verify these headlessly): completion sounds quality/volume,
   desktop notifications actually firing on Windows, the TurboActivity "turbo" feel, the toggle
   fix looking right, and a real iPhone hitting the LAN URL.

To build #1 cheaply:
```bash
venv\Scripts\python.exe Tools\dev_swarm.py "Add a compose-your-crew agent picker: a control in the Build composer that lists agents from backend/data/agents.json, lets the user select several and toggle 'combine with default swarm', passes agents:[ids] on POST /api/v1/missions, and the swarm runs the chosen agents. Keep the existing default-swarm behaviour when nothing is picked." --context src/components/OneBox.tsx backend/main.py backend/core/swarm.py
# then review Tools\proposals\<slug>\PLAN.md + BUILD.md + REVIEW.md and apply the good parts.
```

---

## LEDGER OF KEYS (already on this machine — do not re-paste anywhere public)

- OpenRouter: `~/.env` / `backend/config.yaml` `${OPENROUTER_API_KEY}`
- Moonshot (Kimi direct, FUNDED): `AppData\Roaming\com.infinitycode.app\moonshot.key`
- DashScope (Qwen direct): `AppData\Roaming\com.infinitycode.app\dashscope.key` + `.base`


---

## LONG TASK HARNESS - boost notes (P2)

**Free mode** (`backend/config.yaml` -> `longtask.free_mode: true`): forces both
builder and reviewer onto local llama.cpp - zero cloud spend. Gotcha: free_mode
uses local llama.cpp only; start the server on :8081 first, otherwise tasks fail
with "no local llama.cpp server".

**Cost honesty:** every `GET /api/v1/longtasks/{id}` response includes `cost_aud`
(journal persists it per run) and the Long Tasks panel shows it live.

**Sellability boost checklist:**
1. Reviews are already free - the reviewer runs on local FABLE
   (`longtask_reviewer`), so every task gets a second opinion at zero cost.
2. DashScope/Moonshot direct clients avoid OpenRouter markup on the most-used
   models; token-priced replies get cost estimates from MODEL_SPECS.
3. Nightly self-training distills journaled lessons into playbooks
   (`backend/skills/frontend-vibe-learned.json`), so quality compounds without spend.
