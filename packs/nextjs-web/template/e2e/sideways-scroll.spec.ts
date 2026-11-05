import { expect, test } from "@playwright/test";

import { LOOKS } from "../src/app/look";
import { PHONE, openInLook } from "./phone";
import { ROUTES } from "./routes";

// No sideways scrolling on a phone: at 390 px every route fits the screen's width, in every look.
// A page wider than the screen hides its right-hand side (a form's buttons, a table's actions) and
// a person has to pan to find it, so this fails the build instead of leaving it to the UI review.
// A wide table scrolls inside its own box (the `Table` component's wrapper), never the page; and
// hiding the overflow on <html> or <body> only cuts the content off, so that fails too. Fix what
// is too wide (stack the columns, let text wrap, put the table in `Table`); never exempt a route.
const MEASURE = `() => {
  const root = document.documentElement;
  const body = document.body;
  const vw = root.clientWidth;
  const problems = [];
  const wide = Math.max(root.scrollWidth, body.scrollWidth);
  if (wide > vw + 1) problems.push("the page is " + wide + " px wide on a " + vw + " px screen");
  for (const el of [root, body]) {
    const o = getComputedStyle(el).overflowX;
    if (o === "hidden" || o === "clip") {
      problems.push("<" + el.tagName.toLowerCase() + "> has overflow-x: " + o + ", which cuts off what is too wide");
    }
  }
  if (!problems.length) return [];
  // name what sticks out: the outermost elements past either edge that are not inside a box that
  // scrolls sideways on its own
  const scrollsOwn = (el) => {
    for (let p = el.parentElement; p && p !== body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === "auto" || o === "scroll") return true;
    }
    return false;
  };
  const out = [];
  const seen = new Set();
  for (const el of body.querySelectorAll("*")) {
    let parent = el.parentElement;
    while (parent && !seen.has(parent)) parent = parent.parentElement;
    if (parent) continue;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) continue;
    if (box.right <= vw + 1 && box.left >= -1) continue;
    if (scrollsOwn(el)) continue;
    seen.add(el);
    const cls = (el.getAttribute("class") || "").split(/\\s+/).filter(Boolean).slice(0, 3).join(".");
    const text = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\\s+/g, " ");
    out.push("<" + el.tagName.toLowerCase() + (cls ? "." + cls : "") + "> \\"" + text.slice(0, 40) + "\\" " +
      Math.round(box.left) + " to " + Math.round(box.right) + " px");
    if (out.length >= 5) break;
  }
  return problems.concat(out.map((o) => "sticks out: " + o));
}`;

// The configured `page` fixture with phone options, not a context made here: the config's `use`
// (its `proxy` above all: the live app is reachable only through it) then always applies.
test.use({ viewport: PHONE, hasTouch: true });

for (const look of LOOKS) {
  for (const route of ROUTES) {
    test(`${route} fits a phone's width in the ${look} look`, async ({ page }) => {
      await openInLook(page, route, look);
      const problems = (await page.evaluate(`(${MEASURE})()`)) as string[];
      expect(problems, `${route} scrolls sideways on a ${PHONE.width} px phone`).toEqual([]);
    });
  }
}
