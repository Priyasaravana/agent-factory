import { expect, type Page } from "@playwright/test";

import { authEnabled, signIn } from "./auth";
import { SIGNED_IN_ROUTES } from "./routes";

// The phone the phone checks measure on (tap-targets.spec.ts, sideways-scroll.spec.ts): the same
// 390 px the screenshot run uses, so what the UI review sees is what these checks measured.
export const PHONE = { width: 390, height: 844 };

/** Open a route in a look on the configured `page` (signed in for a signed-in route when the app
 * has sign-in), then wait a frame so the look's spacing and type have laid out. */
export async function openInLook(page: Page, route: string, look: string): Promise<void> {
  if (SIGNED_IN_ROUTES.includes(route) && (await authEnabled(page.request))) {
    await signIn(page, route);
  } else {
    await page.goto(route);
  }
  await expect(page.locator("main")).toBeVisible();
  await page.evaluate((l) => {
    document.documentElement.dataset.look = l;
  }, look);
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => done(null))));
}
