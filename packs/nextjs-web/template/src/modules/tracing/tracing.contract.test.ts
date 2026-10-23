import { describe, expect, it, vi } from "vitest";

import { createTracing, startTracing } from "@/modules/tracing";

import { noTracing } from "./adapters/off";
import { otelTracing } from "./adapters/otel";
import type { Tracing } from "./port";

// The contract every tracing adapter keeps: it says whether it is on, and starting never throws.
const ADAPTERS: Record<string, () => Tracing> = {
  otel: () => otelTracing(vi.fn()),
  off: noTracing,
};

describe.each(Object.entries(ADAPTERS))("tracing adapter %s", (_name, make) => {
  it("starts without throwing", () => {
    const t = make();
    expect(typeof t.enabled).toBe("boolean");
    expect(() => t.start("app")).not.toThrow();
  });
});

describe("otel adapter", () => {
  it("registers OpenTelemetry with the service name", () => {
    const register = vi.fn();
    startTracing(otelTracing(register));
    expect(register).toHaveBeenCalledWith({ serviceName: "app" });
  });

  it("is chosen by name; off does nothing", () => {
    expect(createTracing("otel").enabled).toBe(true);
    expect(createTracing("off").enabled).toBe(false);
  });
});
