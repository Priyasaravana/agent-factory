import { expect, test } from "@playwright/test";

import { authEnabled, signIn } from "./auth";

// Sign-in works end to end (only when the app has sign-in: AUTH=local).
test("the admin signs in and out", async ({ page, request }) => {
  test.skip(!(await authEnabled(request)), "this app has no sign-in");
  await signIn(page);
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { level: 1, name: "Signed in" })).toBeVisible();
  // the header has a sign-out button too: use the page's own
  await page.getByRole("main").getByRole("button", { name: "Sign out" }).click();
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
});

test("a wrong password is refused", async ({ page, request }) => {
  test.skip(!(await authEnabled(request)), "this app has no sign-in");
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrongwrongwrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Wrong email or password");
});
