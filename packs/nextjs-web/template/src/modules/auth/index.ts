import { cookies } from "next/headers";

import { db } from "@/lib/db";
import { moduleConfig } from "@/modules/config";

import { localAuth } from "./adapters/local";
import { noAuth } from "./adapters/none";
import type { Auth, Session, User } from "./port";

export { AuthError, MIN_PASSWORD_LENGTH, ROLES } from "./port";
export type { Auth, Role, Session, User } from "./port";

export const SESSION_COOKIE = "session";

/** The adapter `auth` names in src/modules/config.ts. With local accounts, the first admin comes
 * from ADMIN_EMAIL and ADMIN_PASSWORD (the chart's Secret; `make preview` sets test values). */
export function createAuth(
  adapter = moduleConfig().auth,
  env: Record<string, string | undefined> = process.env,
): Auth {
  if (adapter === "none") return noAuth();
  const admin =
    env.ADMIN_EMAIL && env.ADMIN_PASSWORD
      ? { email: env.ADMIN_EMAIL, password: env.ADMIN_PASSWORD }
      : undefined;
  return localAuth({ db, admin });
}

const g = globalThis as typeof globalThis & { appAuth?: Auth };

/** This process's auth adapter. */
export function auth(): Auth {
  g.appAuth ??= createAuth();
  return g.appAuth;
}

/** For tests: use this adapter (or the configured one again, with undefined) from now on. */
export function setAuth(a: Auth | undefined): void {
  g.appAuth = a;
}

function cookieValue(header: string | null, name: string): string | undefined {
  for (const part of (header ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return undefined;
}

/** The session token a request carries, if any. */
export function sessionToken(request: Request): string | undefined {
  return cookieValue(request.headers.get("cookie"), SESSION_COOKIE);
}

/** The signed-in user of a route handler's request (null: anonymous). */
export async function userFrom(request: Request): Promise<User | null> {
  return auth().session(sessionToken(request));
}

/** The signed-in user in a server component (null: anonymous). */
export async function currentUser(): Promise<User | null> {
  return auth().session((await cookies()).get(SESSION_COOKIE)?.value);
}

function secure(request: Request): boolean {
  return (
    new URL(request.url).protocol === "https:" ||
    request.headers.get("x-forwarded-proto") === "https"
  );
}

/** Set-Cookie for a new session: httpOnly, SameSite=Lax, Secure behind https. */
export function sessionCookie(session: Session, request: Request): string {
  const maxAge = Math.max(0, Math.floor((session.expiresAt - Date.now()) / 1000));
  return [
    `${SESSION_COOKIE}=${encodeURIComponent(session.token)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${maxAge}`,
    ...(secure(request) ? ["Secure"] : []),
  ].join("; ");
}

/** Set-Cookie that ends the session in the browser. */
export function clearedCookie(request: Request): string {
  return [
    `${SESSION_COOKIE}=`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    "Max-Age=0",
    ...(secure(request) ? ["Secure"] : []),
  ].join("; ");
}

/** A mutating request from a browser must come from this app's own pages. Requests without an
 * Origin header (curl, server-to-server) carry no browser cookies by accident, so they pass. */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  const host =
    request.headers.get("x-forwarded-host") ??
    request.headers.get("host") ??
    new URL(request.url).host;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
