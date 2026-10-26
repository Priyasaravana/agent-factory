import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against a running deployment: `make e2e BASE_URL=http://…`.
// The factory runs them after each deploy; they include the accessibility check (e2e/a11y.spec.ts)
// and the screenshots of every route (e2e/screenshots.spec.ts).
const baseURL = process.env.BASE_URL ?? "http://localhost:3000";

// Playwright ignores HTTP_PROXY/HTTPS_PROXY. In the factory's sandbox the only way to the live app
// (e.g. http://dind:8084) is the egress proxy, so non-loopback targets go through it when one is
// set; the preview on 127.0.0.1 is reached directly. Keep this: without it the live run can't
// resolve the app's host.
const loopback = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(baseURL);
const proxyServer = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.http_proxy;
const proxy = !loopback && proxyServer ? { server: proxyServer } : undefined;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL, trace: "off", proxy },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
