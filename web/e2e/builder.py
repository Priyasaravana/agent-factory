"""Browser check of the workflow builder (ADR-0039 1c) against a dry-run engine (fresh data dir).

Run: engine on :8000 (FACTORY_MODE=dry-run), `npx vite preview --port 4173` in web/,
then `uv run --with playwright python web/e2e/builder.py`.
"""

import os

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:4173")


def box(page, phase):
    return page.locator(f"li.phase-box[aria-label='{phase}']")


def order(page, phase):
    cards = box(page, phase).locator("li[data-testid^=station-]")
    return cards.evaluate_all("els => els.map(e => e.dataset.testid.slice(8))")


def drag(page, src, dst):
    a, b = src.bounding_box(), dst.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 10, a["y"] + a["height"] / 2 + 10, steps=5)
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=15)
    page.wait_for_timeout(150)
    page.mouse.up()


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
    page = b.new_page(viewport={"width": 1400, "height": 1600})
    page.goto(f"{BASE}/workflows/new-product/edit")
    page.wait_for_selector("li.phase-box")

    # 1. a phase box offers only its own stations: Verify has scans, not Build
    box(page, "Verify").get_by_role("button", name="Add station").click()
    menu = page.get_by_role("menu")
    expect(menu).to_contain_text("Secret scan")
    expect(menu).not_to_contain_text("Build")
    menu.get_by_role("menuitem", name="Secret scan finds").click()
    expect(page.get_by_test_id("station-secrets-scan")).to_be_visible()
    assert order(page, "Verify")[-1] == "secrets-scan", order(page, "Verify")

    # 2. reorder inside the box
    before = order(page, "Verify")
    drag(page, page.get_by_role("button", name="Move Secret scan"), page.get_by_test_id("station-test"))
    page.wait_for_timeout(600)
    after = order(page, "Verify")
    assert after.index("secrets-scan") < before.index("secrets-scan"), after

    # 3. the drawer changes how it runs, then removes it
    page.get_by_role("button", name="Open Secret scan").click()
    drawer = page.get_by_role("dialog")
    drawer.get_by_label("If it fails").select_option(label="Stop and wait for a person")
    page.wait_for_timeout(400)
    expect(page.get_by_test_id("station-secrets-scan")).not_to_contain_text("implement")
    drawer.get_by_role("button", name="Remove station").click()
    page.get_by_role("dialog", name="Remove Secret scan?").get_by_role("button", name="Remove station").click()
    expect(page.get_by_test_id("station-secrets-scan")).to_have_count(0)

    # 4. a custom step opens its drawer: its starter prompt blocks publishing until written
    box(page, "Plan").get_by_role("button", name="Add station").click()
    page.get_by_role("menuitem", name="Custom agent step").click()
    expect(page.get_by_role("dialog")).to_contain_text("Write what this step does")
    page.keyboard.press("Escape")
    expect(page.locator(".problems").first).to_contain_text("starter prompt")

    # 5. a preset scopes the lane
    page.get_by_role("radio", name="Test & secure").click()
    page.get_by_role("button", name="Use Test & secure").click()
    expect(page.get_by_role("radio", name="Test & secure")).to_have_attribute("aria-checked", "true")
    expect(box(page, "Develop")).to_have_count(0)
    print("builder OK")
    b.close()
