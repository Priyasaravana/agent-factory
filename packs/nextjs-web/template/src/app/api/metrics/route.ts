import { registry } from "@/lib/metrics";

export const dynamic = "force-dynamic";

// Prometheus scrape endpoint.
export async function GET(): Promise<Response> {
  return new Response(await registry.metrics(), {
    headers: { "content-type": registry.contentType },
  });
}
