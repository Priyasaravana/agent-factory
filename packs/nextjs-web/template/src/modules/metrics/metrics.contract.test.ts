import { Registry } from "@prometheus-io/client";
import { describe, expect, it } from "vitest";

import { createMetrics } from "@/modules/metrics";

import { noMetrics } from "./adapters/none";
import { prometheusMetrics } from "./adapters/prometheus";
import type { Metrics } from "./port";

// The contract every metrics adapter keeps: counting never throws, and a scrape answers with
// a text body and its content type.
const ADAPTERS: Record<string, () => Metrics> = {
  prometheus: () => prometheusMetrics(new Registry()),
  none: noMetrics,
};

describe.each(Object.entries(ADAPTERS))("metrics adapter %s", (_name, make) => {
  it("counts and scrapes", async () => {
    const m = make();
    expect(() => m.count("/api/x", 200)).not.toThrow();
    const scrape = await m.scrape();
    expect(typeof scrape.body).toBe("string");
    expect(scrape.contentType).toContain("text/plain");
  });
});

describe("prometheus adapter", () => {
  it("exposes request counts by route and status", async () => {
    const m = prometheusMetrics(new Registry());
    m.count("/api/x", 201);
    m.count("/api/x", 201);
    expect((await m.scrape()).body).toContain('http_requests_total{route="/api/x",status="201"} 2');
  });

  it("is the default; none on request", async () => {
    expect((await createMetrics("none").scrape()).body).toBe("");
    expect((await createMetrics("prometheus").scrape()).body).toContain("http_requests_total");
  });
});
