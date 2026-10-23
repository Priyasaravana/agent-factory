import { expect, type APIRequestContext, type Page } from "@playwright/test";

// Signing in for e2e tests. Against the live app the factory passes the deployment's admin
// (E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD: from the chart's Secret, redacted in its logs); against
// `make preview` they are unset and the preview's test admin is used (PREVIEW_ADMIN_* in the
// Makefile). Never write a real password into a test.
export const ADMIN = {
  email: process.env.E2E_ADMIN_EMAIL || "admin@preview.test",
  password: process.env.E2E_ADMIN_PASSWORD || "preview-preview",
};

/** Whether this app has sign-in (the auth module's adapter is not `none`). */
export async function authEnabled(request: APIRequestContext): Promise<boolean> {
  const res = await request.get("/api/auth/session");
  return ((await res.json()) as { enabled: boolean }).enabled;
}

/** Sign in through the sign-in page as the admin; the page's cookies keep the session. */
export async function signIn(page: Page, next = "/"): Promise<void> {
  await page.goto(`/sign-in?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
}

/** Sign in an API client (request fixture) as the admin. */
export async function signInApi(request: APIRequestContext): Promise<void> {
  const res = await request.post("/api/auth/sign-in", { data: ADMIN });
  expect(res.status(), "the admin can sign in").toBe(200);
}
