import { auth, clearedCookie, sameOrigin, sessionToken } from "@/modules/auth";
import { count } from "@/modules/metrics";

export const dynamic = "force-dynamic";

// Sign out: ends the session on the server and clears the cookie. A form post is sent back to
// the home page (303); anything else gets 204.
export async function POST(request: Request): Promise<Response> {
  if (!sameOrigin(request)) {
    count("/api/auth/sign-out", 403);
    return Response.json({ error: "cross-site request refused" }, { status: 403 });
  }
  await auth().signOut(sessionToken(request));
  const form = (request.headers.get("content-type") ?? "").includes("form");
  const headers = new Headers({ "set-cookie": clearedCookie(request) });
  if (form) headers.set("location", "/");
  count("/api/auth/sign-out", form ? 303 : 204);
  return new Response(null, { status: form ? 303 : 204, headers });
}
