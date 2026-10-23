import { redirect } from "next/navigation";

import { currentUser, sameOrigin, userFrom, type Role, type User } from "@/modules/auth";
import { moduleConfig } from "@/modules/config";

import { allowAll } from "./adapters/allow-all";
import { roles } from "./adapters/roles";
import { signedIn } from "./adapters/signed-in";
import type { Authz, Decision } from "./port";

export type { Authz, Decision } from "./port";

/** The adapter `authz` names in src/modules/config.ts. */
export function createAuthz(adapter = moduleConfig().authz): Authz {
  if (adapter === "roles") return roles();
  if (adapter === "signed-in") return signedIn();
  return allowAll();
}

const g = globalThis as typeof globalThis & { appAuthz?: Authz };

export function authz(): Authz {
  g.appAuthz ??= createAuthz();
  return g.appAuthz;
}

/** For tests: use this adapter (or the configured one again, with undefined) from now on. */
export function setAuthz(a: Authz | undefined): void {
  g.appAuthz = a;
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

/** Guard a route handler: `const { user, denied } = await authorize(request, "admin");
 * if (denied) return denied;`. A mutating request from another site's page is refused too. */
export async function authorize(
  request: Request,
  role?: Role,
): Promise<{ user: User | null; denied: Response | null }> {
  if (!SAFE.has(request.method) && !sameOrigin(request)) {
    return {
      user: null,
      denied: Response.json({ error: "cross-site request refused" }, { status: 403 }),
    };
  }
  const user = await userFrom(request);
  const d: Decision = authz().check(user, role);
  return { user, denied: d.ok ? null : Response.json({ error: d.reason }, { status: d.status }) };
}

/** Guard a page: anonymous visitors go to /sign-in and come back to `path` afterwards;
 * `allowed` is false for a signed-in user without the role (show them a message). */
export async function authorizePage(
  path: string,
  role?: Role,
): Promise<{ user: User | null; allowed: boolean }> {
  const user = await currentUser();
  const d = authz().check(user, role);
  if (!d.ok && d.status === 401) redirect(`/sign-in?next=${encodeURIComponent(path)}`);
  return { user, allowed: d.ok };
}
