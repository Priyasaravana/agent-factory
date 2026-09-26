"""Browser check of the lane builder against a dry-run engine (fresh data dir).

Run: engine on :8000 (FACTORY_MODE=dry-run), `npx vite preview --port 4173` in web/,
then `uv run --with playwright python web/e2e/lane_builder.py`.
"""

import os

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:4173")


def drag(page, src, dst, dx=0):
    a = src.bounding_box()
    b = dst.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(
        a["x"] + a["width"] / 2 + 10, a["y"] + a["height"] / 2 + 10, steps=5
    )
    page.mouse.move(b["x"] + b["width"] / 2 + dx, b["y"] + b["height"] / 2, steps=15)
    page.wait_for_timeout(150)
    page.mouse.up()


def lane(page):
    cards = page.locator("ol.lane > li[data-testid^=station-]")
    return cards.evaluate_all("els => els.map(e => e.dataset.testid.slice(8))")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
    page = b.new_page(viewport={"width": 1400, "height": 1800})
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{BASE}/workflows/fastapi-service/edit")
    page.wait_for_selector("ol.lane")
    before = lane(page)
    print("before", before)

    # 1. drag a custom agent step onto 'verify' -> inserted at verify's position
    drag(
        page,
        page.get_by_test_id("palette-agent"),
        page.get_by_test_id("station-verify"),
    )
    form = page.get_by_role("dialog", name="Add station")
    expect(form).to_be_visible()
    form.get_by_label("station id").fill("security-review")
    form.get_by_label("new station agent").select_option("verifier")
    form.get_by_label("new station on fail").select_option("build")
    form.get_by_role("button", name="Add station").click()
    expect(page.get_by_test_id("station-security-review")).to_be_visible()
    after = lane(page)
    print("after add", after)
    assert after.index("security-review") == before.index("verify"), after

    # 2. reorder: drag security-review's grip onto 'package'
    grip = page.get_by_role("button", name="Move security-review", exact=True)
    drag(page, grip, page.get_by_test_id("station-package"))
    page.wait_for_timeout(600)
    moved = lane(page)
    print("after move", moved)
    assert moved.index("security-review") > moved.index("verify"), moved

    # 3. bad order is flagged live: put deploy before package
    drag(
        page,
        page.get_by_role("button", name="Move deploy", exact=True),
        page.get_by_test_id("station-package"),
    )
    page.wait_for_timeout(600)
    print("after bad move", lane(page))
    expect(page.locator(".problems")).to_contain_text("must come before")
    drag(
        page,
        page.get_by_role("button", name="Move package", exact=True),
        page.get_by_test_id("station-deploy"),
    )
    page.wait_for_timeout(600)
    print("fixed", lane(page))
    expect(page.locator(".problems")).to_have_count(0)

    # 4. edit a route and remove a station
    page.get_by_label("security-review on fail").select_option("design")
    page.wait_for_timeout(400)
    expect(page.get_by_label("security-review on fail")).to_have_value("design")
    page.get_by_role("button", name="Remove deploy_fix", exact=True).click()
    expect(page.get_by_test_id("station-deploy_fix")).to_have_count(0)

    # 5. click-to-add a check at the end, then remove it
    page.get_by_test_id("palette-check-verify").click()
    form.get_by_role("button", name="Add station").click()
    expect(page.get_by_test_id("station-verify-2")).to_be_visible()
    assert lane(page)[-1] == "verify-2"
    page.get_by_role("button", name="Remove verify-2", exact=True).click()
    expect(page.get_by_test_id("station-verify-2")).to_have_count(0)

    # 6. publish
    page.get_by_placeholder("What changed?", exact=False).fill(
        "lane: security review, no deploy_fix"
    )
    page.get_by_role("button", name="Publish").click()
    page.wait_for_url("**/workflows/fastapi-service")
    expect(page.locator(".strip")).to_contain_text("security-review")
    print("published OK:", page.locator("h2").first.inner_text())
    b.close()
