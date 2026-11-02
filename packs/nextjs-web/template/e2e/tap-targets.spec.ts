import { expect, test, type Page } from "@playwright/test";

import { LOOKS } from "../src/app/look";
import { authEnabled, signIn } from "./auth";
import { ROUTES, SIGNED_IN_ROUTES } from "./routes";

// Phone-ready controls: on a 390 px phone every control a person taps is at least 44 × 44 px
// (WCAG 2.5.5, the pack's UI rubric), in every look. Compact looks shrink spacing and type, so a
// control that is big enough in one look can be too small in another: this measures them all and
// fails the build instead of leaving it to the UI review. Fix the control (the base components
// carry `phone:min-h-[44px]`); never exempt one to pass.
const PHONE = { width: 390, height: 844 };
const MIN = 44;

// Links inside running text are exempt (WCAG 2.5.5); so are controls hidden until focused (the
// skip link) and anything not rendered.
const MEASURE = `(min) => {
  const SELECTOR =
    "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], [role=tab], [role=checkbox], [role=switch]";
  const small = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") continue;
    let box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) continue; // sr-only until focused
    if (el.tagName === "A" && style.display === "inline" && el.closest("p")) continue;
    if (el.matches("input[type=checkbox], input[type=radio]")) {
      // the label is the target: a small box inside a big enough label passes
      const label = el.closest("label") || (el.id && document.querySelector('label[for="' + el.id + '"]'));
      if (label) box = label.getBoundingClientRect();
    }
    if (box.height + 0.5 < min || box.width + 0.5 < min) {
      const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("name") || "").trim();
      small.push(el.tagName.toLowerCase() + ' "' + name.slice(0, 40) + '" ' +
        Math.round(box.width) + "×" + Math.round(box.height) + " px");
    }
  }
  return small;
}`;

async function open(page: Page, route: string, look: string): Promise<void> {
  if (SIGNED_IN_ROUTES.includes(route) && (await authEnabled(page.request))) {
    await signIn(page, route);
  } else {
    await page.goto(route);
  }
  await expect(page.locator("main")).toBeVisible();
  await page.evaluate((l) => {
    document.documentElement.dataset.look = l;
  }, look);
}

for (const look of LOOKS) {
  for (const route of ROUTES) {
    test(`${route} has phone-sized tap targets in the ${look} look`, async ({
      browser,
      baseURL,
    }) => {
      const context = await browser.newContext({ baseURL, viewport: PHONE, hasTouch: true });
      const page = await context.newPage();
      await open(page, route, look);
      const small = (await page.evaluate(`(${MEASURE})(${MIN})`)) as string[];
      expect(small, `controls under ${MIN} px on a ${PHONE.width} px phone`).toEqual([]);
      await context.close();
    });
  }
}
