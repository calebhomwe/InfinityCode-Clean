"""PyInstaller entry point for the Infinity Code backend.

Imports the FastAPI app object directly (no uvicorn string-import machinery,
which does not survive freezing) and serves it on 127.0.0.1:8000.
"""

from __future__ import annotations

import os
import sys

# Some modules import as `backend.core.X` and others as `core.X`. Most guard
# with try/except, but new files keep landing with the hard `backend.` form and
# crash startup (auto_fix, speculative, vision_loop all did on 2026-07-31).
# Putting the PROJECT ROOT on sys.path makes `backend.*` resolve everywhere, so
# either style works and this stops being whack-a-mole.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
for _p in (_PROJECT_ROOT, _BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uvicorn

from main import app


def run() -> None:
    # Localhost-only by default (desktop-safe). Set INFINITY_HOST=0.0.0.0 to
    # expose the backend on the LAN for phone/tablet testing — pair with
    # `vite --host`; the frontend derives the backend host from the page URL.
    host = os.environ.get("INFINITY_HOST", "127.0.0.1")
    try:
        uvicorn.run(app, host=host, port=8000, log_level="info")
    except Exception as exc:  # noqa: BLE001 - surface startup failures plainly
        print(f"Infinity Code backend failed to start: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
