import { dbReady } from "@/lib/db";
import { logger } from "@/lib/logger";
import { count } from "@/lib/metrics";

export const dynamic = "force-dynamic";

// Readiness: dependencies (the database, when configured) answer.
export async function GET(): Promise<Response> {
  const ok = await dbReady();
  const status = ok ? 200 : 503;
  if (!ok) logger.warn({ check: "database" }, "not ready");
  count("/api/readyz", status);
  return Response.json({ status: ok ? "ready" : "not ready" }, { status });
}
