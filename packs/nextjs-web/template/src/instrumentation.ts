import { registerOTel } from "@vercel/otel";

// Distributed tracing: on when OTEL_EXPORTER_OTLP_ENDPOINT is set (spans go to that collector).
export function register(): void {
  if (process.env.OTEL_EXPORTER_OTLP_ENDPOINT) {
    registerOTel({ serviceName: process.env.OTEL_SERVICE_NAME ?? "app" });
  }
}
