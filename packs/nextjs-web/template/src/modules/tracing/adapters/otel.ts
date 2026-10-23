import { registerOTel } from "@vercel/otel";

import type { Tracing } from "../port";

/** OpenTelemetry: spans go to the collector at OTEL_EXPORTER_OTLP_ENDPOINT. */
export function otelTracing(register: typeof registerOTel = registerOTel): Tracing {
  return {
    enabled: true,
    start(serviceName) {
      register({ serviceName });
    },
  };
}
