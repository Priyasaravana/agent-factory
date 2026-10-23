import { Counter, Registry, collectDefaultMetrics } from "@prometheus-io/client";

import type { Metrics } from "../port";

// One registry per server process, kept across hot reloads in development.
const g = globalThis as typeof globalThis & { appMetrics?: Registry };

function processRegistry(): Registry {
  if (!g.appMetrics) {
    g.appMetrics = new Registry();
    collectDefaultMetrics({ register: g.appMetrics });
  }
  return g.appMetrics;
}

/** Prometheus text format; `registry` is the process's own unless a test passes a fresh one. */
export function prometheusMetrics(registry: Registry = processRegistry()): Metrics {
  const requests =
    (registry.getSingleMetric("http_requests_total") as Counter<"route" | "status"> | undefined) ??
    new Counter({
      name: "http_requests_total",
      help: "HTTP requests handled, by route and status",
      labelNames: ["route", "status"] as const,
      registers: [registry],
    });
  return {
    count(route, status) {
      requests.inc({ route, status: String(status) });
    },
    async scrape() {
      return { body: await registry.metrics(), contentType: registry.contentType };
    },
  };
}
