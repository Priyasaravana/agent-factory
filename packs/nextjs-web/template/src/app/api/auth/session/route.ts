import { auth, userFrom } from "@/modules/auth";
import { count } from "@/modules/metrics";

export const dynamic = "force-dynamic";

// Who is signed in: {"enabled": false} when the app has no sign-in, else the user or null.
export async function GET(request: Request): Promise<Response> {
  count("/api/auth/session", 200);
  return Response.json({ enabled: auth().enabled, user: await userFrom(request) });
}
