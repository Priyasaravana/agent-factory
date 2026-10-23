import { describe, expect, it } from "vitest";

import { ADAPTERS, DEFAULTS, moduleConfig } from "@/modules/config";

describe("module configuration", () => {
  it("uses the defaults, with tracing only when a collector is configured", () => {
    expect(moduleConfig({})).toEqual({ ...DEFAULTS, tracing: "off" });
    expect(moduleConfig({ OTEL_EXPORTER_OTLP_ENDPOINT: "http://collector:4318" }).tracing).toBe(
      "otel",
    );
    expect(
      moduleConfig({ OTEL_EXPORTER_OTLP_ENDPOINT: "http://c:4318", TRACING: "off" }).tracing,
    ).toBe("off");
  });

  it("lets the environment choose each adapter", () => {
    const env = {
      AUTH: "local",
      AUTHZ: "roles",
      LOGGING: "silent",
      METRICS: "none",
      TRACING: "otel",
    };
    expect(moduleConfig(env)).toEqual({
      auth: "local",
      authz: "roles",
      logging: "silent",
      metrics: "none",
      tracing: "otel",
    });
  });

  it("refuses unknown adapters and authorisation without sign-in", () => {
    expect(() => moduleConfig({ AUTH: "oidc" })).toThrow(
      "auth=oidc is not an adapter (one of none, local)",
    );
    expect(() => moduleConfig({ AUTH: "none", AUTHZ: "roles" })).toThrow("needs a way to sign in");
  });

  it("names a no-op adapter for every module", () => {
    expect(Object.keys(ADAPTERS).sort()).toEqual([
      "auth",
      "authz",
      "logging",
      "metrics",
      "tracing",
    ]);
  });
});
