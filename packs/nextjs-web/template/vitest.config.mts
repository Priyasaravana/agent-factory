import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    // route handlers and libraries run in Node; a component test opts in to jsdom with
    // `// @vitest-environment jsdom` as its first line
    environment: "node",
    env: { LOGGING: "silent" }, // no log lines between test results
    // tests/ for the app; each module's contract tests sit beside its adapters (src/modules/*/)
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx", "src/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/app/layout.tsx"], // markup only: the e2e suite covers it
      thresholds: { lines: 80, functions: 80, branches: 80, statements: 80 },
    },
  },
});
