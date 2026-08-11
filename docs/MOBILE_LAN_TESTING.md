# Testing Infinity Code on your iPhone (LAN web access)

Infinity Code's desktop shell is a **Tauri (Windows)** app — it does **not** run
natively on iOS (a native iOS build needs a Mac + Xcode, which we don't have on
this machine). But the whole UI is a normal React web app talking to a local
FastAPI backend, so you can open it in **Safari on your iPhone** over Wi-Fi.

## One-time
- Phone and PC must be on the **same Wi-Fi network**.
- Allow Node + Python through the Windows Firewall on **Private** networks
  (Windows will prompt the first time; click Allow).

## Run it (two terminals)
```bash
# 1. Backend, exposed on the LAN (not just localhost):
cd C:\Users\caleb\infinity-code
set INFINITY_HOST=0.0.0.0
set INFINITY_DATA_DIR=%APPDATA%\com.infinitycode.app
venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir backend

# 2. Frontend, exposed on the LAN:
npm run dev -- --host
```
Vite prints a **Network:** URL like `http://192.168.1.23:1420/`.

## On the iPhone
Open that `http://<PC-LAN-IP>:1420/` URL in Safari.

The app auto-detects that it was loaded from `192.168.1.23` and points its API +
WebSocket at `192.168.1.23:8000` — no hardcoded `localhost` (see `src/lib/api.ts`).
Add it to the Home Screen for a full-screen, app-like test surface.

## Why this works / gotchas
- `src/lib/api.ts` derives the backend host from `window.location.hostname`, so
  the phone never tries to reach *its own* localhost.
- The backend must bind `0.0.0.0` (env `INFINITY_HOST`) — the default is
  `127.0.0.1` for desktop safety, so remember to set it.
- Don't expose this on an untrusted network; there's no auth on the local API.
- The installed MSI app still binds localhost-only by design; use the dev
  servers above for phone testing.

VS Code: the **"frontend: dev on LAN (phone testing)"** task runs step 2 for you.
