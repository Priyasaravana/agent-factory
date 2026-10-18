import { afterEach, describe, expect, it, vi } from "vitest";

describe("database pool", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    delete (globalThis as { appPool?: unknown }).appPool;
  });

  it("is absent without DATABASE_URL", async () => {
    vi.stubEnv("DATABASE_URL", "");
    const { pool } = await import("@/lib/db");
    expect(pool()).toBeNull();
  });

  it("creates the tables once and answers queries", async () => {
    const { db, setDatabase } = await import("@/lib/db");
    const { memoryDatabase } = await import("./support/db");
    memoryDatabase();
    const first = await db();
    await first.query("insert into app_info (key, value) values ('k', 'v')");
    const again = await db();
    expect((await again.query("select value from app_info where key = 'k'")).rows).toEqual([
      { value: "v" },
    ]);
    setDatabase(null);
    await expect(db()).rejects.toThrow("no database");
  });

  it("is created once from DATABASE_URL", async () => {
    vi.stubEnv("DATABASE_URL", "postgres://user:pw@db.invalid:5432/app");
    const { pool } = await import("@/lib/db");
    const first = pool();
    expect(first).not.toBeNull();
    expect(pool()).toBe(first);
    await (first as { end?: () => Promise<void> } | null)?.end?.();
  });
});

describe("tracing", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    vi.doUnmock("@vercel/otel");
  });

  it("registers only when a collector is configured", async () => {
    const registerOTel = vi.fn();
    vi.doMock("@vercel/otel", () => ({ registerOTel }));
    const { register } = await import("@/instrumentation");
    vi.stubEnv("OTEL_EXPORTER_OTLP_ENDPOINT", "");
    register();
    expect(registerOTel).not.toHaveBeenCalled();
    vi.stubEnv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318");
    register();
    expect(registerOTel).toHaveBeenCalledWith({ serviceName: "app" });
  });
});

describe("logger", () => {
  it("writes JSON with the service name", async () => {
    const { logger } = await import("@/lib/logger");
    expect(logger.bindings()).toMatchObject({ service: "app" });
  });
});
