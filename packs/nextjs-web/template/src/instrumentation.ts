import { startTracing } from "@/modules/tracing";

export async function register(): Promise<void> {
  // Distributed tracing through the tracing module: on when OTEL_EXPORTER_OTLP_ENDPOINT is set.
  startTracing();
  // `make preview`: an in-memory database instead of DATABASE_URL (never set in the chart).
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.PREVIEW_DATABASE === "memory") {
    const { startPreviewDatabase } = await import("@/lib/preview-db");
    await startPreviewDatabase();
  }
}
