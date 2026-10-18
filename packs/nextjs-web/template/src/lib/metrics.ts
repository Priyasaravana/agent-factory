import { Counter, Registry, collectDefaultMetrics } from "@prometheus-io/client";

// One registry per server process, kept across hot reloads in development.
const g = globalThis as typeof globalThis & { appMetrics?: Registry };

export const registry: Registry = g.appMetrics ?? new Registry();
if (!g.appMetrics) {
  collectDefaultMetrics({ register: registry });
  g.appMetrics = registry;
}

export const requests =
  (registry.getSingleMetric("http_requests_total") as Counter<"route" | "status"> | undefined) ??
  new Counter({
    name: "http_requests_total",
    help: "HTTP requests handled, by route and status",
    labelNames: ["route", "status"] as const,
    registers: [registry],
  });

export function count(route: string, status: number): void {
  requests.inc({ route, status: String(status) });
}
