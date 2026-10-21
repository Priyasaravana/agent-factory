import { registerOTel } from "@vercel/otel";

export async function register(): Promise<void> {
  // Distributed tracing: on when OTEL_EXPORTER_OTLP_ENDPOINT is set (spans go to that collector).
  if (process.env.OTEL_EXPORTER_OTLP_ENDPOINT) {
    registerOTel({ serviceName: process.env.OTEL_SERVICE_NAME ?? "app" });
  }
  // `make preview`: an in-memory database instead of DATABASE_URL (never set in the chart).
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.PREVIEW_DATABASE === "memory") {
    const { startPreviewDatabase } = await import("@/lib/preview-db");
    await startPreviewDatabase();
  }
}
