# Spec: AI Connectivity — More Providers + Read All Chats

## Objective

Make Infinity Code ready for day-to-day use by the owner:

1. **More providers** — add OpenRouter, Kimi (Moonshot), Qwen (DashScope), MiniMax and GLM (Zhipu) as first-class connectable LLM providers in Settings, alongside the existing DeepSeek/DashScope entries. Each key is saved locally (providers.json, masked), tested with a live call, and wired into the model dispatch so `openrouter/*`, `kimi/*`, `dashscope/*`, `minimax/*`, `glm/*` model ids route to the right backend.
2. **Read all chats on this PC** — import conversation history from every AI tool that stores chats locally: Claude, Codex, Qoder, OpenCode. Detect-and-report (not fake-import) for ChatGPT, Kimi and Qwen, which keep chats server-side on this machine.

## What exists today (verified)

- `backend/core/providers.py` — ProviderManager (providers.json): comfyui, fal, novita, elevenlabs, deepseek, dashscope keys; `test()` per provider; `public()` masks keys.
- `backend/main.py` — `_load_saved_provider_keys()` loads deepseek/dashscope from providers.json into env; startup builds OpenRouterClient, MoonshotClient (env/key-file), DashScopeClient; `_rebuild_llm_clients()` rewires them.
- `backend/core/router.py` — `is_kimi()`, `is_dashscope()`, `MODEL_SPECS` pricing registry, council binding.
- `backend/core/provider_chat.py` + `backend/core/swarm.py::_provider_chat` — dispatch kimi → Moonshot, dashscope → DashScope, local → llama.cpp, else OpenRouter.
- `backend/core/ai_chat_sync.py` — unified AI-chats DB (ai_chats.db); Claude + partial/legacy Codex + stub OpenAI sync; `/ai-chats*` endpoints in routers/integrations.py; frontend `AIChatHistory.tsx` tabs all/claude/codex/gpt.

## Chat source map on this PC (verified 2026-08-09)

| Source | Location | Format | Status |
|---|---|---|---|
| Claude | `~/.claude/history.jsonl`, `~/.claude/projects/*/*.jsonl` | JSONL | already synced |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, `~/.codex/session_index.jsonl`, `~/.codex/archived_sessions/` | JSONL (session_meta / response_item / event_msg) | broken — parser reads legacy format only |
| Qoder | `~/.qoder/cache/projects/*/conversation-history/*/task-*.jsonl` | JSONL `{role, message:{content:[{type,text}]}}` | new |
| OpenCode | `~/.local/share/opencode/opencode.db` | SQLite (session/message/part) | new |
| ChatGPT | no local store on this PC (`AppData\Local\OpenAI` = Codex extension only) | — | detect & report |
| Kimi | no local store (only `~/.kimi/kimi-claw` agent config) | — | detect & report |
| Qwen | `AppData\Roaming\Qwen\IndexedDB` LevelDB contains no extractable chat text (server-side) | — | detect & report |

## Key changes

### Providers (backend)
- New `backend/tools/minimax_client.py`, `backend/tools/glm_client.py` — OpenAI-compatible thin clients (same shape as DashScopeClient: `chat()` → `{text, input_tokens, output_tokens}`).
- `providers.py` DEFAULTS + `public()` + `configured` + `test()`: `openrouter_key`, `moonshot_key`, `minimax_key`, `glm_key`.
- `main.py`: load all six LLM keys from providers.json → env (`OPENROUTER_API_KEY`, `MOONSHOT_API_KEY`, `MINIMAX_API_KEY`, `GLM_API_KEY`); build `app.state.minimax` / `app.state.glm`; extend `_rebuild_llm_clients`.
- `router.py`: `is_minimax()`, `is_glm()` + `MODEL_SPECS` entries (`minimax/*`, `glm/*`).
- `provider_chat.py` + `swarm.py::_provider_chat`: route `minimax/*`, `glm/*` to direct clients.
- `routers/settings.py`: `ProvidersUpdate` new key fields; rebuild triggers on any LLM key.

### Providers (frontend)
- `SettingsModal.tsx`: ProvidersInfo + provider list rows: OpenRouter, Kimi (Moonshot), Qwen/DashScope (exists), MiniMax, GLM, DeepSeek (exists) — each with Save & test.

### Chat sync (backend)
- `ai_chat_sync.py`:
  - Fix Codex: import `~/.codex/sessions/**/rollout-*.jsonl` + `archived_sessions/` + titles from `session_index.jsonl`; store real messages.
  - Add Qoder parser (`~/.qoder/cache/projects/*/conversation-history/*/*.jsonl`).
  - Add OpenCode parser (SQLite: session/message/part, epoch-ms timestamps).
  - Add `discover_ai_sources()` → per-source `{available, count, paths, note}` for Claude/Codex/Qoder/OpenCode/ChatGPT/Kimi/Qwen.
  - `sync_all_sources()` returns per-source result + availability.
- `routers/integrations.py`: `/ai-chats/sources` (GET) + richer `/ai-chats/sync` + stats unchanged.

### Chat sync (frontend)
- `AIChatHistory.tsx`: tabs all/claude/codex/qoder/opencode (+ status line for gpt/kimi/qwen showing "not stored locally"); source icons/colors; footer with real paths; "Sync All" already exists.

## Commands

- Backend tests: `cd C:\Users\caleb\infinity-code && .\.venv\Scripts\python.exe -m pytest backend/tests -q` (currently 323 pass)
- Frontend build: `cd C:\Users\caleb\infinity-code && npm run build`
- Dev server: `cd C:\Users\caleb\infinity-code\backend && ..\.venv\Scripts\python.exe main.py` (port 8000)

## Testing strategy

- pytest for: new client construction without keys; provider_chat dispatch for minimax/glm/kimi ids (fake clients); providers.public() masking + configured flags; Codex/Qoder/OpenCode parsers against fixture files/temp sqlite DBs (tmp_path).
- `npm run build` must stay green.

## Boundaries

- Always: keep existing routes byte-compatible; masked-key round-trips must not clobber saved keys; source files read-only.
- Ask first: new dependencies (none planned — stdlib + existing `openai` SDK only).
- Never: store keys in the repo; modify Claude/Codex/Qoder/OpenCode source files.

## Success criteria

- Settings → Providers shows 6 LLM providers; saving any key rebuilds clients without restart (log line "LLM clients rebuilt").
- `/ai-chats/sources` reports available: claude, codex, qoder, opencode; gpt/kimi/qwen reported with a clear note.
- `POST /ai-chats/sync` imports real messages from all four readable sources; frontend tabs show counts.
- 323+ backend tests pass; `npm run build` passes.
