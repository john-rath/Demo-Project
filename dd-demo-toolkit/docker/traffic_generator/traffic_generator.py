"""Real-browser RUM traffic generator (Playwright/Chromium).

Drives the care-portal with a real headless browser, so the Datadog RUM Browser
SDK actually loads and emits genuine sessions, views, and actions — which link
through `allowedTracingUrls` to the backend APM trace and the AI Care
Companion's LLM Observability spans. This is the hands-off way to populate RUM
without a Synthetics private location (and more "real" than a synthetic check).

Each loop iteration is one RUM session: open a fresh browser context, load the
portal, click around (Ask the Care Companion / Locate / Refresh) with think
time, linger, then close. Concurrency + pacing via env.
"""
from __future__ import annotations

import os
import random
import time

from playwright.sync_api import sync_playwright

PORTAL_URL = os.getenv("PORTAL_URL", "http://care-portal:8080")
SESSION_GAP_SEC = float(os.getenv("SESSION_GAP_SEC", "4"))
SESSION_LINGER_SEC = float(os.getenv("SESSION_LINGER_SEC", "12"))
ACTIONS = ["#btn-ask", "#btn-locate", "#btn-summary", "#btn-ask", "#btn-ask"]


def run_session(browser) -> None:
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    try:
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=20000)
        # Let the RUM SDK initialize and the page's auto-actions fire.
        page.wait_for_timeout(3000)
        for sel in random.sample(ACTIONS, k=random.randint(2, len(ACTIONS))):
            try:
                page.click(sel, timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(int(random.uniform(1200, 3500)))
        page.wait_for_timeout(int(SESSION_LINGER_SEC * 1000))
    except Exception as e:
        print(f"[traffic] session error: {e}", flush=True)
    finally:
        ctx.close()  # ends the RUM session


def main() -> None:
    print(f"[traffic] driving real browser sessions at {PORTAL_URL}", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        n = 0
        while True:
            n += 1
            print(f"[traffic] session #{n}", flush=True)
            run_session(browser)
            time.sleep(SESSION_GAP_SEC)


if __name__ == "__main__":
    main()
