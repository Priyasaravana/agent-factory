"""End-to-end smoke test of the whole stack in dry-run (no model usage).

Runs in CI after `docker compose up` (see .github/workflows/ci.yml, job `e2e`) and
locally against any running factory:

    BASE_URL=http://localhost:8080 FACTORY_ADMIN_PASSWORD=... \\
      uv run --with playwright python web/e2e/smoke.py

Covers the path every change must keep working: sign-in through the auth gateway,
readiness, creating a product, the station timeline to delivery, feedback starting a
second iteration, the Outcomes page, archiving, and no errors in the browser console.
"""

from __future__ import annotations

import os
import re
import sys
import time

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:8080")
USER = os.environ.get("FACTORY_ADMIN_USER", "admin")
PASSWORD = os.environ["FACTORY_ADMIN_PASSWORD"]
SHOTS = os.environ.get("SMOKE_SCREENSHOTS", "smoke-screenshots")
DELIVERED = "delivered · awaiting feedback"


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: m.type == "error" and "401" not in m.text and errors.append(f"console: {m.text}"),
        )
        # the console only says "status of 500"; record which request it was
        page.on(
            "response",
            lambda r: r.status >= 500 and errors.append(f"server {r.status}: {r.request.method} {r.url}"),
        )
        try:
            step("sign in")
            page.goto(BASE + "/")
            page.get_by_label("Username").fill(USER)
            page.get_by_label("Password").fill(PASSWORD)
            page.get_by_role("button", name="Sign in").click()
            expect(page.get_by_role("heading", name="Products", exact=True)).to_be_visible(timeout=20_000)

            step("readiness")
            page.goto(BASE + "/integrations")
            expect(page.get_by_role("heading", name=re.compile("Readiness"))).to_be_visible()
            page.get_by_role("button", name=re.compile("Check now")).click()
            expect(page.locator("[data-testid^=preflight-]").first).to_contain_text("Free app port")

            step("product → delivered")
            title = f"Smoke {int(time.time())}"
            page.goto(BASE + "/?new=1")
            page.get_by_label("Title").fill(title)
            page.get_by_label("Requirements").fill("A tiny API with GET /ping returning a counter that increments.")
            page.get_by_role("button", name=re.compile("Create product")).click()
            expect(page.get_by_role("heading", name="Iteration 1")).to_be_visible(timeout=20_000)
            expect(page.get_by_text(DELIVERED).first).to_be_visible(timeout=120_000)
            expect(page.locator(".strip")).to_contain_text("Quality gate")
            expect(page.get_by_text(re.compile(r"agent readiness: Level 3"))).to_be_visible()
            page.screenshot(path=f"{SHOTS}/delivered.png", full_page=True)

            step("evidence: sealed, intact, downloadable")
            ev = page.get_by_test_id("evidence")
            expect(ev).to_contain_text("intact", timeout=30_000)
            ev.locator("summary").click()
            with page.expect_download() as dl:
                ev.get_by_role("button", name=re.compile("Download bundle")).click()
            assert dl.value.suggested_filename.startswith("evidence-"), dl.value.suggested_filename

            step("feedback → iteration 2")
            page.get_by_placeholder(re.compile("pagination")).fill("Add a reset endpoint")
            page.get_by_role("button", name=re.compile("Send feedback")).click()
            expect(page.get_by_role("tab", name="iteration 2")).to_be_visible(timeout=20_000)
            expect(page.get_by_text(DELIVERED).first).to_be_visible(timeout=120_000)

            step("outcomes")
            page.get_by_role("link", name="Outcomes").click()
            expect(page.get_by_role("heading", name="Outcomes")).to_be_visible()
            kpis = page.get_by_test_id("kpis")
            expect(kpis).to_contain_text("Deliveries")
            # two delivered iterations, no unplanned human help
            expect(kpis.get_by_text("100%").first).to_be_visible()
            expect(page.get_by_test_id("waiting")).to_contain_text("Nobody is blocking")
            quality = page.get_by_test_id("quality")
            expect(quality).to_contain_text("uncovered")  # pillars with no signals are never shown as 100%
            expect(quality.get_by_test_id("quality-by-app")).to_contain_text(title)
            page.screenshot(path=f"{SHOTS}/outcomes.png", full_page=True)
            page.go_back()

            step("archive")
            page.get_by_role("button", name="Archive").click()
            page.get_by_role("button", name="Archive product").click()
            expect(page.get_by_text(re.compile("^Archived"))).to_be_visible(timeout=30_000)
            page.screenshot(path=f"{SHOTS}/archived.png", full_page=True)
        except Exception:
            page.screenshot(path=f"{SHOTS}/failure.png", full_page=True)
            raise
        finally:
            browser.close()
    if errors:
        print("browser errors:\n  " + "\n  ".join(errors))
        return 1
    print("smoke: OK")
    return 0


def step(name: str) -> None:
    print(f"== {name}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
