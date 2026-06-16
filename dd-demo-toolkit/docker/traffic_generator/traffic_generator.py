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
        # Wait for `load` (not just DOMContentLoaded) so LCP has actually
        # painted before we start interacting. Then settle the network so the
        # PerformanceObserver has observed final LCP/CLS candidates.
        page.goto(PORTAL_URL, wait_until="load", timeout=25000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(2000)  # final settle for LCP/CLS

        # Real interactions for INP/FID — hover then click so Chromium
        # records a proper first-input interaction. Pace between actions.
        for sel in random.sample(ACTIONS, k=random.randint(2, len(ACTIONS))):
            try:
                page.hover(sel, timeout=3000)
                page.click(sel, timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(int(random.uniform(1200, 3500)))

        page.wait_for_timeout(int(SESSION_LINGER_SEC * 1000))

        # Flush web vitals. The RUM browser SDK batches LCP/CLS and beacons
        # them on `visibilitychange -> hidden` / `pagehide`. Closing the
        # browser context abruptly skips that flush — vitals stay buffered
        # and never reach Datadog. Simulate the tab going hidden, then give
        # the SDK a moment to send via navigator.sendBeacon.
        try:
            page.evaluate("""
                () => {
                  try { Object.defineProperty(document, 'visibilityState', {value:'hidden', configurable:true}); } catch(e){}
                  try { Object.defineProperty(document, 'hidden',         {value:true,     configurable:true}); } catch(e){}
                  document.dispatchEvent(new Event('visibilitychange'));
                  window.dispatchEvent(new Event('pagehide'));
                }
            """)
            page.wait_for_timeout(1000)
        except Exception:
            pass
    except Exception as e:
        print(f"[traffic] session error: {e}", flush=True)
    finally:
        try:
            page.close()
        except Exception:
            pass
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
