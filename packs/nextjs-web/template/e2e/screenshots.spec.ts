import { expect, test } from "@playwright/test";

import { authEnabled, signIn } from "./auth";
import { ROUTES, SIGNED_IN_ROUTES } from "./routes";

// Screenshots of every route, light and dark, desktop and phone (ADR-0044 §4). The factory keeps
// them as evidence of the change and shows them on the change page; its reviewer scores them
// against the pack's UI rubric. They are pictures, not assertions: a page that renders is enough.
const SIZES = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "phone", width: 390, height: 844 },
] as const;
const SCHEMES = ["light", "dark"] as const;
const OUT = process.env.SCREENSHOT_DIR || "e2e-screenshots";

const slug = (route: string) =>
  route.replace(/^\/+|\/+$/g, "").replace(/[^a-z0-9]+/gi, "-") || "home";

for (const route of ROUTES) {
  for (const size of SIZES) {
    for (const scheme of SCHEMES) {
      test(`screenshot ${route} ${size.name} ${scheme}`, async ({ browser, baseURL }) => {
        const context = await browser.newContext({
          baseURL,
          viewport: { width: size.width, height: size.height },
          colorScheme: scheme,
        });
        const page = await context.newPage();
        if (SIGNED_IN_ROUTES.includes(route) && (await authEnabled(page.request))) {
          await signIn(page, route);
        } else {
          await page.goto(route);
        }
        await expect(page.locator("main")).toBeVisible();
        await page.screenshot({
          path: `${OUT}/${slug(route)}-${size.name}-${scheme}.png`,
          fullPage: true,
        });
        await context.close();
      });
    }
  }
}
