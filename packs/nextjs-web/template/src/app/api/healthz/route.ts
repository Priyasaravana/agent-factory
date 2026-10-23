import { count } from "@/modules/metrics";

export const dynamic = "force-dynamic";

// Liveness: the process answers.
export function GET(): Response {
  count("/api/healthz", 200);
  return Response.json({ status: "ok" });
}
