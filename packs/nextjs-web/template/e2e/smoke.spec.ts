import { expect, test } from "@playwright/test";

// Smoke tests against the deployed app (BASE_URL). Acceptance tests that check requirements live
// beside these as e2e/<feature>.spec.ts, each tagged with the requirement ids it covers.

test("liveness and readiness answer", async ({ request }) => {
  expect((await request.get("/api/healthz")).status()).toBe(200);
  expect((await request.get("/api/readyz")).status()).toBe(200);
});

test("metrics are exposed", async ({ request }) => {
  const res = await request.get("/api/metrics");
  expect(res.status()).toBe(200);
  expect(await res.text()).toContain("http_requests_total");
});

test("the home page renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});
