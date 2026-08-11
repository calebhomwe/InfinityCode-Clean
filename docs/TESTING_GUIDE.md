# Testing Infinity Code on Windows

## What it is

Infinity Code is a desktop app that turns a one-line goal into working code
and images. It spawns a small "swarm" of AI agents - a Director that plans, an
Engineer that writes, a Tester that runs - and shows you the real output
(screenshots, exit codes) as evidence. You stay in control: every result is
yours to approve or reject.

## Installing the app

1. Double-click the MSI installer (it is unsigned, so Windows will warn you).
2. If SmartScreen shows a blue "Windows protected your PC" dialog, click
   **More info**, then **Run anyway**.
3. The app runs on the Microsoft Edge WebView2 runtime. On Windows 11 it is
   already installed; on Windows 10 the installer fetches and installs it if
   needed (this needs an internet connection).
4. Launch Infinity Code from the Start menu.

## First-run setup: add an API key

The app needs one free API key to talk to the AI models.

1. Open the Aliyun Bailian / Model Studio console (DashScope) and create a
   free account.
2. In the console, create an API key (DashScope API keys start with `sk-`).
   The free tier includes the Qwen chat, coding and vision models the app
   uses by default.
3. Open the app and go to **Settings > Providers & API keys**.
4. Paste your key into the DashScope (Qwen) field.
5. Click **Test connection** - it should report success.
6. Click **Save**.

That is the whole setup. The key is stored locally on your machine.

## What works out of the box

- **Chat** - conversation with the Qwen free-tier models, with optional
  grounding in your notes (OmniBrain, on by default).
- **Missions** - type a goal such as "draw a red circle and save it as a
  PNG"; the swarm plans, writes, runs and shows you the result.
- **Build** - code generation and execution against the free-tier models.
- **Settings > Providers & API keys** - add more providers (DeepSeek,
  Moonshot) whenever you want.

## What needs extra setup (off by default)

These features are off on a fresh machine because they need local GPU models
you do not have yet:

- **Local models (FABLE / llama.cpp)** - run a local model server on ports
  8081 and 8082 for zero-cost, on-device inference.
- **Long-task reviewer** - long-horizon coding sessions route their review
  step to a local model by default; they only work with a local server
  running.
- **Self-improve loops** - automated evaluation and self-patching are
  local-model-only and fail closed, so they stay off until local models are
  added.
- **OmniBrain** - this one IS on by default: chat answers can be grounded in
  your own notes through a hosted brain service.

## Where your data lives

- Everything is stored under `%APPDATA%\com.infinitycode.app` (press
  Win+R, paste that path, press Enter).
- This folder holds your missions, chats, skills, memory and settings,
  including your API keys.

**Backup:** quit the app (right-click its tray icon > Quit), then copy the
whole folder somewhere safe.

**Reset:** quit the app, then delete the folder. The next launch starts
fresh - you will need to re-enter your API key.

## Uninstalling

- Windows Settings > Apps > Installed apps > Infinity Code > Uninstall, or
- Run the MSI again and choose Remove.
- Either method leaves the data folder in `%APPDATA%` behind; delete it if
  you want a complete removal.

## Reporting a bug

Helpful reports contain:

1. What you clicked or typed (and in which view: Chat, Missions, IDE...).
2. What you expected to happen.
3. What actually happened (exact error text if any).
4. A screenshot.
5. The app version - open **Settings > About** and note the version number.

Send these to the person who gave you the installer.
