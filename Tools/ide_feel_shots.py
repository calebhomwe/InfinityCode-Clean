#!/usr/bin/env python
"""Capture IDE-feel proof shots of the current Infinity Code build:
settled shell, Ctrl+K palette, sidebar hover, council panel, chip hover."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "Tools"))
import premium_swarm as ps  # noqa: E402

ps.SANDBOX_API_PORT = 8010


def main() -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = REPO / "Tools" / "ide_feel" / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    print("[ide] building dist against :8010 …")
    ok, out = ps.build_frontend()
    if not ok:
        print("[ide] build failed:", out[-400:])
        sys.exit(1)

    backend, httpd = ps.start_servers(run_dir)
    try:
        from playwright.sync_api import sync_playwright
        shots = run_dir / "shots"
        shots.mkdir(exist_ok=True)
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1440, "height": 900},
                              device_scale_factor=1)
            page.goto(f"http://localhost:{ps.FRONT_PORT}/",
                      wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            page.screenshot(path=str(shots / "01_shell.png"))

            page.keyboard.press("Control+k")
            page.wait_for_timeout(700)
            page.screenshot(path=str(shots / "02_palette.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

            council = page.locator("aside button, aside [role='button']",
                                   has_text="Council").first
            if council.count():
                council.hover()
                page.wait_for_timeout(350)
                page.screenshot(path=str(shots / "03_hover_council.png"))
                council.click()
                page.wait_for_timeout(1200)
                page.screenshot(path=str(shots / "04_council.png"))
                close = page.locator("button", has_text="Close").first
                if close.count():
                    close.click()
                    page.wait_for_timeout(400)

            chip = page.locator("button", has_text="Refactor a function").first
            if chip.count():
                chip.hover()
                page.wait_for_timeout(350)
                page.screenshot(path=str(shots / "05_hover_chip.png"))
            b.close()
        print(f"[ide] shots in {shots}")
    finally:
        backend.terminate()
        httpd.shutdown()


if __name__ == "__main__":
    main()
