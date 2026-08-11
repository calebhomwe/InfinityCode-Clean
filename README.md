# Infinity Code

A self-evolving creative command center. Type a goal into the box; the system
spawns specialized agents that write and run code, generate images, and report
back with evidence - screenshots, exit codes, critique scores - so you can
approve or reject each result.

**Core principle: the system never claims success. It shows evidence.** Every
mission records what actually happened: screenshots, exit codes, critique
scores. You approve or reject.

## Stack

- **Shell:** Tauri v1 (Rust) - a small native Windows app that auto-starts the
  bundled backend and lives in the system tray
- **Frontend:** React 18 + TypeScript + Tailwind + Vite
- **Backend:** FastAPI + SQLite, frozen into a single exe with PyInstaller
- **Installer:** Windows MSI produced by `tauri build` (see `build-desktop.ps1`)
- **Models:** DashScope (Aliyun Model Studio) Qwen free-tier models by default;
  DeepSeek, Moonshot and local llama.cpp lanes are also wired in

## Setup (development, Windows / PowerShell)

Infinity Code is self-hosted: the backend and frontend run locally on your
machine, and the desktop build packs both into one installer.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
npm install
```

API keys are entered inside the app (Settings > Providers & API keys) and are
saved to `providers.json` in the data directory - no `.env` file required.
Environment variables still work as a fallback (`DASHSCOPE_API_KEY`,
`MOONSHOT_API_KEY`, `DEEPSEEK_API_KEY`).

## Run (development)

Terminal 1 - backend:

```powershell
.\venv\Scripts\Activate.ps1
cd backend
uvicorn main:app --reload --port 8000
```

Terminal 2 - frontend:

```powershell
npm run dev
```

Open http://localhost:1420. API docs live at http://127.0.0.1:8000/docs.

## Desktop app

`build-desktop.ps1` does three things:

1. Freezes the FastAPI backend into `src-tauri\binaries\infinity-backend.exe`
   with PyInstaller.
2. Smoke-tests the frozen exe: it must open port 8000, issue a session bearer
   token, and answer the API.
3. Runs `tauri build` to produce an `.msi` installer under
   `src-tauri\target\release\bundle\msi\`.

The Tauri shell starts its own backend on 127.0.0.1:8000 with per-session
bearer auth, shows a splash screen until the port responds, and lives in the
system tray (Show / Hide / Quit). Closing the window hides to the tray; Quit
stops the backend too.

For development, `npm run tauri dev` spawns the venv backend automatically.

## Data

- **Installed builds:** `%APPDATA%\com.infinitycode.app` - missions, chats,
  skills, memory, knowledge, settings, and `providers.json`
- **Development:** `backend\` in the repo
- Generated mission code runs on the system Python (override with the
  `INFINITY_PYTHON` env var)

## First test

Type into the box:

> Generate a Python script that draws a red circle and saves it as a PNG

Start the mission and watch the mission card: the Director plans, the Engineer
writes the script, the Tester runs it, and the output image appears in the
Evidence panel. Approve or reject the result.

## Layout

```
backend/            FastAPI app (main.py) + core engines + missions.db
  core/             router, swarm, critic, cost tracker, learn, providers
  tools/            per-provider LLM clients (dashscope, moonshot, ...)
  config.yaml       model tiers, budgets, paths
src/                React frontend (components, hooks)
src-tauri/          Tauri desktop shell (Rust) + frozen backend binary
```

## Notes

- No API key configured? The app points you to Settings > Providers & API
  keys. Missions that cannot run record the reason in their evidence instead
  of pretending to work.
- Daily spend is capped (default $100 AUD/day, see `backend/config.yaml`);
  calls that would exceed it are refused and downgraded to cheaper models.
- `/api/v1/learn` needs `yt-dlp` and `whisper` on PATH plus ffmpeg for audio.
- Blender renders need Blender on PATH or the `BLENDER_PATH` env var.
- Local GPU models (FABLE, llama.cpp on ports 8081/8082) are optional extras,
  off by default on fresh machines.
