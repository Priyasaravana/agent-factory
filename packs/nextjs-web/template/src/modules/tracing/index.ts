import { moduleConfig } from "@/modules/config";

import { noTracing } from "./adapters/off";
import { otelTracing } from "./adapters/otel";
import type { Tracing } from "./port";

export type { Tracing } from "./port";

/** The adapter `tracing` names in src/modules/config.ts (otel when a collector is configured). */
export function createTracing(adapter = moduleConfig().tracing): Tracing {
  return adapter === "otel" ? otelTracing() : noTracing();
}

/** Start tracing for this server process; called from src/instrumentation.ts. */
export function startTracing(tracing: Tracing = createTracing()): void {
  tracing.start(process.env.OTEL_SERVICE_NAME ?? "app");
}
