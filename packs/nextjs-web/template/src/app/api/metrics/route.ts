import { metrics } from "@/modules/metrics";

export const dynamic = "force-dynamic";

// Prometheus scrape endpoint (the metrics module's adapter decides what it holds).
export async function GET(): Promise<Response> {
  const { body, contentType } = await metrics.scrape();
  return new Response(body, { headers: { "content-type": contentType } });
}
