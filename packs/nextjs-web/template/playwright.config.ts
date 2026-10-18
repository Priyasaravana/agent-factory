import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against a running deployment: `make e2e BASE_URL=http://…`.
// The factory runs them after each deploy; they include the accessibility check (e2e/a11y.spec.ts).
const baseURL = process.env.BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL, trace: "off" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
