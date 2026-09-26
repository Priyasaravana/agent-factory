"""Browser check of the skills import against a dry-run engine, fetching a real
public skill (BuilderIO/skills). Run like lane_builder.py."""

import os

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:4173")

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
    page = b.new_page(viewport={"width": 1300, "height": 1400})
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{BASE}/skills")
    page.get_by_label("skill repo").fill("BuilderIO/skills")
    page.get_by_label("skill path").fill("skills/plan-arbiter")
    page.get_by_role("button", name="Review").click()
    review = page.get_by_role("region", name="Skill review")
    expect(review).to_be_visible(timeout=30000)
    expect(review).to_contain_text("plan-arbiter")
    expect(review.locator("pre").first).to_be_visible()
    box = review.get_by_role("checkbox")
    if box.count():
        expect(review.get_by_role("button", name="Install")).to_be_disabled()
        box.check()
    review.get_by_role("button", name="Install").click()
    row = page.get_by_test_id("skill-plan-arbiter")
    expect(row).to_be_visible(timeout=30000)
    print("installed:", row.inner_text().splitlines()[0:2])

    row.get_by_role("button", name="Check for update").click()
    expect(page.get_by_role("region", name="Skill review")).to_contain_text("Already installed", timeout=30000)
    page.get_by_role("button", name="Close").click()

    # the picker in the agent editor offers it, pinned
    page.goto(f"{BASE}/workflows/fastapi-service/edit")
    page.get_by_role("button", name="Edit").first.click()
    expect(page.get_by_text("plan-arbiter")).to_be_visible()
    expect(page.locator("label", has_text="plan-arbiter").locator(".pill")).to_contain_text("github @")

    page.goto(f"{BASE}/skills")
    page.get_by_test_id("skill-plan-arbiter").get_by_role("button", name="Remove").click()
    expect(page.get_by_test_id("skill-plan-arbiter")).to_have_count(0)
    print("skills e2e OK")
    b.close()
