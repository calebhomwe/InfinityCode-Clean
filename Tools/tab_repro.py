#!/usr/bin/env python
"""Repro: tab glitches + can't open multiple tabs. Drives the built frontend
against a sandbox backend and logs tab counts at each step."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "Tools"))
import premium_swarm as ps  # noqa: E402

ps.SANDBOX_API_PORT = 8012
ps.FRONT_PORT = 3000  # 4173 held by the live arc; 3000 is CORS-allowed too


def tabs(page) -> int:
    return page.locator("[role='tab']").count()


def visible_value(page):
    return page.evaluate("""() => {
      const els = [...document.querySelectorAll('[id^="chat-input-"], #chat-input')];
      const vis = els.find(el => el.offsetParent !== null);
      return vis ? vis.value : null;
    }""")


def main() -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = REPO / "Tools" / "tab_repro" / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    print("[repro] building dist for :8010 …")
    ok, out = ps.build_frontend()
    if not ok:
        print("[repro] build failed:", out[-300:])
        sys.exit(1)
    backend, httpd = ps.start_servers(run_dir)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://localhost:{ps.FRONT_PORT}/",
                      wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3500)
            print(f"[repro] initial tabs: {tabs(page)}")

            new_chat = page.locator("aside button", has_text="New chat").first
            new_chat.click()
            page.wait_for_timeout(600)
            print(f"[repro] after New chat #1: {tabs(page)}")
            page.screenshot(path=str(run_dir / "1_one_tab.png"))

            new_chat.click()
            page.wait_for_timeout(600)
            print(f"[repro] after New chat #2: {tabs(page)}")
            page.screenshot(path=str(run_dir / "2_two_tabs.png"))

            # concurrency proof: distinct drafts must survive tab switches
            inp = page.locator("#chat-input-2, [id^='chat-input-']").last
            page.evaluate("""(v) => {
              const els = [...document.querySelectorAll('[id^="chat-input-"], #chat-input')];
              const vis = els.find(el => el.offsetParent !== null);
              if (vis) { const s = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set; s.call(vis, v); vis.dispatchEvent(new Event('input', {bubbles:true})); }
            }""", "draft B")
            page.locator("[role='tab']").first.click()
            page.wait_for_timeout(500)
            page.evaluate("""(v) => {
              const els = [...document.querySelectorAll('[id^="chat-input-"], #chat-input')];
              const vis = els.find(el => el.offsetParent !== null);
              if (vis) { const s = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set; s.call(vis, v); vis.dispatchEvent(new Event('input', {bubbles:true})); }
            }""", "draft A")
            page.locator("[role='tab']").nth(1).click()
            page.wait_for_timeout(500)
            v2 = visible_value(page)
            page.locator("[role='tab']").first.click()
            page.wait_for_timeout(500)
            v1 = visible_value(page)
            print(f"[repro] drafts after switching: tab1={v1!r} tab2={v2!r} "
                  f"{'OK' if v1 == 'draft A' and v2 == 'draft B' else 'LOST'}")

            # type + send in the active (second) tab via its unique composer id
            sent = page.evaluate("""() => {
              const els = [...document.querySelectorAll('[id^="chat-input-"], #chat-input')];
              const vis = els.find(el => el.offsetParent !== null);
              return vis ? vis.id : null;
            }""")
            if sent:
                page.locator(f'[id="{sent}"]').fill("hello there")
                page.locator(f'[id="{sent}"]').press("Enter")
                page.wait_for_timeout(2500)
            print(f"[repro] after send: {tabs(page)}")
            page.screenshot(path=str(run_dir / "3_after_send.png"))

            # click first tab
            first = page.locator("[role='tab']").first
            if first.count():
                first.click()
                page.wait_for_timeout(600)
            print(f"[repro] after clicking tab 1: {tabs(page)} active="
                  f"{page.locator('[role=tab][aria-selected=true]').inner_text() if page.locator('[role=tab][aria-selected=true]').count() else '?'}")
            page.screenshot(path=str(run_dir / "4_tab1.png"))

            # open an existing chat from sidebar (if any listed)
            side = page.locator("aside [class*='chat'], aside button").filter(
                has_text="hello").first
            if side.count():
                side.click()
                page.wait_for_timeout(600)
                print(f"[repro] after sidebar chat click: {tabs(page)}")
            b.close()
        print(f"[repro] shots: {run_dir}")
    finally:
        backend.terminate()
        httpd.shutdown()


if __name__ == "__main__":
    main()
