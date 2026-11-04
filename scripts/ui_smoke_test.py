"""Interactive UI smoke test: drive the chat + runbook reader in a real browser."""

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # Chat: pick the fast model, send a prompt, wait for streamed reply + stats
    page.goto("http://localhost:8080/#chat")
    page.wait_for_selector(".model-card")
    page.click('.model-card[data-model="glm-4.7-flash:latest"]')
    page.fill("#chatInput", "Reply with the single word: ready")
    page.click("#chatSend")
    page.wait_for_selector(".msg.assistant .stats", timeout=180_000)
    print("chat reply:", page.locator(".msg.assistant").inner_text()[:200])
    page.screenshot(path="/tmp/shots/chat_live.png")

    # Runbooks: open the Ollama playbook, confirm markdown rendered
    page.goto("http://localhost:8080/#runbooks")
    page.wait_for_selector(".rb-card")
    page.click('.rb-card[data-slug="ollama"]')
    page.wait_for_selector(".rb-article h1, .rb-article h2")
    print("runbook heading:", page.locator(".rb-article h1, .rb-article h2").first.inner_text())
    page.screenshot(path="/tmp/shots/runbook_reader.png")

    browser.close()
    print("JS errors:", errors if errors else "none")
