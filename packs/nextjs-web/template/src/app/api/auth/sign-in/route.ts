import { auth, sameOrigin, sessionCookie } from "@/modules/auth";
import { logger } from "@/modules/logging";
import { count } from "@/modules/metrics";

export const dynamic = "force-dynamic";

const ROUTE = "/api/auth/sign-in";

/** Only paths inside this app: never `//host` or a full URL (no open redirect). */
function safeNext(next: unknown): string {
  return typeof next === "string" &&
    next.startsWith("/") &&
    !next.startsWith("//") &&
    !next.startsWith("/\\")
    ? next
    : "/";
}

// Sign in with email and password. The sign-in page posts a form and is redirected (303); a JSON
// body ({"email", "password"}) gets JSON back. Either way the session is an httpOnly cookie.
export async function POST(request: Request): Promise<Response> {
  const json = (request.headers.get("content-type") ?? "").includes("application/json");
  const reply = (status: number, body: object, location?: string, cookie?: string): Response => {
    count(ROUTE, status);
    const headers = new Headers();
    if (cookie) headers.set("set-cookie", cookie);
    if (location && !json) {
      headers.set("location", location);
      return new Response(null, { status: 303, headers });
    }
    return Response.json(body, { status, headers });
  };
  if (!sameOrigin(request)) return reply(403, { error: "cross-site request refused" });
  if (!auth().enabled) return reply(404, { error: "sign-in is off for this app" }, "/sign-in");
  let fields: Record<string, unknown>;
  try {
    fields = json
      ? ((await request.json()) as Record<string, unknown>)
      : Object.fromEntries(await request.formData());
  } catch {
    return reply(400, { error: "send email and password" }, "/sign-in?error=1");
  }
  const { email, password } = fields;
  const next = safeNext(fields.next);
  if (typeof email !== "string" || typeof password !== "string" || !email || !password) {
    return reply(
      400,
      { error: "send email and password" },
      `/sign-in?error=1&next=${encodeURIComponent(next)}`,
    );
  }
  const session = await auth().signIn(email, password);
  if (!session) {
    logger.info({ route: ROUTE }, "sign-in refused");
    return reply(
      401,
      { error: "wrong email or password" },
      `/sign-in?error=1&next=${encodeURIComponent(next)}`,
    );
  }
  logger.info({ route: ROUTE, user: session.user.id }, "signed in");
  return reply(200, { user: session.user }, next, sessionCookie(session, request));
}
