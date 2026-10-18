import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { ROUTES } from "./routes";

// The `a11y` readiness signal (usability pillar): every route has no serious or critical
// WCAG 2.x A/AA violation. Fix the page; never exclude a rule or an element to pass.
for (const route of ROUTES) {
  test(`${route} has no serious accessibility violations`, async ({ page }) => {
    await page.goto(route);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(blocking.map((v) => `${v.id}: ${v.help} (${v.nodes.length} element(s))`)).toEqual([]);
  });
}
