"""Keep the public demo awake on Streamlit Community Cloud.

Free Community Cloud apps go to sleep after a while without visitors, and a visitor then has to click a
"wake up" button. This script opens the app in a headless browser; if the sleep screen is showing, it clicks
the wake-up button and waits for the app to load. Run on a schedule by .github/workflows/keep-alive.yml.

Usage: APP_URL=https://<your-app>.streamlit.app python scripts/keep_alive.py
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

WAKE_TEXT = "get this app back up"   # Streamlit's sleep-screen button
READY_TEXT = "Suraksha"               # shown in the app's top bar once it has loaded


def main() -> int:
    url = os.environ.get("APP_URL", "").strip()
    if not url:
        print("APP_URL is not set; nothing to do.")
        return 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(15_000)
        wake = page.locator(f"button:has-text('{WAKE_TEXT}')")
        if wake.count() > 0:
            print("App was asleep - waking it up.")
            wake.first.click()
        else:
            print("App was awake.")
        try:
            # The app itself runs inside an iframe on *.streamlit.app; check both the page and its frames.
            page.wait_for_function(
                f"""() => [document, ...Array.from(document.querySelectorAll('iframe'))
                        .map(f => {{ try {{ return f.contentDocument; }} catch (e) {{ return null; }} }})]
                        .some(d => d && d.body && d.body.innerText.includes('{READY_TEXT}'))""",
                timeout=180_000,
            )
            print("App is up.")
        except PlaywrightTimeout:
            print("App did not show its title within 3 minutes (it may still be starting).")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
