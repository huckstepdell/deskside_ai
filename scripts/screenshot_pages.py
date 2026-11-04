"""Visual verification: screenshot every dashboard page (dev tool, not part of the app)."""

import sys
from playwright.sync_api import sync_playwright

PAGES = ["overview", "chat", "vision", "imagegen", "rag", "runbooks"]
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://localhost:8080/#overview")
    page.wait_for_timeout(6000)  # let telemetry charts fill in
    for name in PAGES:
        page.goto(f"http://localhost:8080/#{name}")
        page.wait_for_timeout(1500)
        page.screenshot(path=f"{OUT}/{name}.png")
        print(f"shot: {name}")
    browser.close()
    print("JS errors:", errors if errors else "none")
