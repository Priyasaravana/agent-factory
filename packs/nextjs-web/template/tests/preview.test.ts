import { afterEach, describe, expect, it, vi } from "vitest";

// `make preview` runs the app on an in-memory database so the factory can run the e2e suite
// inside its sandbox before it builds an image.
describe("preview database", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    delete (globalThis as { appPool?: unknown }).appPool;
  });

  it("is used only when PREVIEW_DATABASE=memory in the Node runtime", async () => {
    vi.stubEnv("DATABASE_URL", "");
    vi.stubEnv("OTEL_EXPORTER_OTLP_ENDPOINT", "");
    vi.stubEnv("NEXT_RUNTIME", "nodejs");
    const { register } = await import("@/instrumentation");
    const { pool } = await import("@/lib/db");
    await register();
    expect(pool()).toBeNull();
    vi.stubEnv("PREVIEW_DATABASE", "memory");
    await register();
    expect(pool()).not.toBeNull();
  });

  it("answers queries with the app's tables", async () => {
    const { startPreviewDatabase } = await import("@/lib/preview-db");
    const { db, dbReady } = await import("@/lib/db");
    await startPreviewDatabase();
    expect(await dbReady()).toBe(true);
    const conn = await db();
    await conn.query("insert into app_info (key, value) values ('k', 'v')");
    expect((await conn.query("select value from app_info where key = 'k'")).rows).toEqual([
      { value: "v" },
    ]);
  });
});
