import type { Metrics } from "../port";

/** Counts nothing (METRICS=none); /api/metrics answers with an empty page. */
export function noMetrics(): Metrics {
  return {
    count: () => undefined,
    scrape: async () => ({ body: "", contentType: "text/plain; version=0.0.4; charset=utf-8" }),
  };
}
