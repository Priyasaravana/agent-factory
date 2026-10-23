import { moduleConfig } from "@/modules/config";

import { noMetrics } from "./adapters/none";
import { prometheusMetrics } from "./adapters/prometheus";
import type { Metrics } from "./port";

export type { Metrics, Scrape } from "./port";

/** The adapter `metrics` names in src/modules/config.ts. */
export function createMetrics(adapter = moduleConfig().metrics): Metrics {
  return adapter === "none" ? noMetrics() : prometheusMetrics();
}

export const metrics: Metrics = createMetrics();

/** Count one answered request: call it in every route handler. */
export function count(route: string, status: number): void {
  metrics.count(route, status);
}
