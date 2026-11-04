import { expect, test } from "@playwright/test";

import { LOOKS } from "../src/app/look";
import { PHONE, openInLook } from "./phone";
import { ROUTES } from "./routes";

// Phone-ready controls: on a 390 px phone every control a person taps is at least 44 × 44 px
// (WCAG 2.5.5, the pack's UI rubric), in every look. Compact looks shrink spacing and type, so a
// control that is big enough in one look can be too small in another: this measures them all and
// fails the build instead of leaving it to the UI review. Fix the control (the base components
// carry `phone:min-h-[44px]`); never exempt one to pass. Signed-in routes are measured signed in.
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

// The configured `page` fixture with phone options, not a context made here: the config's `use`
// (its `proxy` above all: the live app is reachable only through it) then always applies.
test.use({ viewport: PHONE, hasTouch: true });

for (const look of LOOKS) {
  for (const route of ROUTES) {
    test(`${route} has phone-sized tap targets in the ${look} look`, async ({ page }) => {
      await openInLook(page, route, look);
      const small = (await page.evaluate(`(${MEASURE})(${MIN})`)) as string[];
      expect(small, `controls under ${MIN} px on a ${PHONE.width} px phone`).toEqual([]);
    });
  }
}
