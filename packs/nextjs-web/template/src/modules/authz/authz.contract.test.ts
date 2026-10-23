import { afterEach, describe, expect, it, vi } from "vitest";

import { setAuth, type Auth, type User } from "@/modules/auth";
import { authorize, authorizePage, authz, createAuthz, setAuthz } from "@/modules/authz";

import { allowAll } from "./adapters/allow-all";
import { roles } from "./adapters/roles";
import { signedIn } from "./adapters/signed-in";
import type { Authz } from "./port";

const admin: User = { id: "1", email: "admin@example.com", role: "admin" };
const member: User = { id: "2", email: "member@example.com", role: "member" };

// The contract every authz adapter keeps: it answers every question with a decision, and a
// refusal is 401 (sign in) or 403 (not allowed) with a reason.
const ADAPTERS: Record<string, () => Authz> = {
  "allow-all": allowAll,
  "signed-in": signedIn,
  roles,
};

describe.each(Object.entries(ADAPTERS))("authz adapter %s", (_name, make) => {
  it("decides every combination", () => {
    const a = make();
    for (const user of [null, admin, member]) {
      for (const role of [undefined, "admin", "member"] as const) {
        const d = a.check(user, role);
        if (!d.ok) {
          expect([401, 403]).toContain(d.status);
          expect(d.reason).toBeTruthy();
        }
      }
    }
    expect(a.check(admin, "admin").ok).toBe(true);
  });
});

describe("adapters", () => {
  it("allow-all lets anyone in", () => {
    expect(allowAll().check(null, "admin").ok).toBe(true);
  });

  it("signed-in needs a user, not a role", () => {
    expect(signedIn().check(null)).toMatchObject({ ok: false, status: 401 });
    expect(signedIn().check(member, "admin").ok).toBe(true);
  });

  it("roles: admins do everything, members only what needs no admin", () => {
    expect(roles().check(null)).toMatchObject({ status: 401 });
    expect(roles().check(member).ok).toBe(true);
    expect(roles().check(member, "member").ok).toBe(true);
    expect(roles().check(member, "admin")).toMatchObject({ ok: false, status: 403 });
    expect(roles().check(admin, "member").ok).toBe(true);
  });

  it("are chosen by name", () => {
    expect(createAuthz("roles").check(member, "admin").ok).toBe(false);
    expect(createAuthz("signed-in").check(null).ok).toBe(false);
    expect(createAuthz("allow-all").check(null).ok).toBe(true);
  });
});

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "session" ? { value: "member-token" } : undefined),
  }),
}));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect ${to}`);
  },
}));

function fakeAuth(users: Record<string, User>): Auth {
  return {
    enabled: true,
    signIn: async () => null,
    session: async (token) => (token ? (users[token] ?? null) : null),
    signOut: async () => undefined,
    createUser: async () => member,
  };
}

describe("guards", () => {
  afterEach(() => {
    setAuth(undefined);
    setAuthz(undefined);
  });

  it("guard route handlers: sign in, role, same origin", async () => {
    setAuth(fakeAuth({ "admin-token": admin, "member-token": member }));
    setAuthz(roles());
    expect(authz().check(null).ok).toBe(false);
    const req = (token?: string, headers: Record<string, string> = {}) =>
      new Request("http://app.test/api/x", {
        method: "POST",
        headers: { ...headers, ...(token ? { cookie: `session=${token}` } : {}) },
      });
    expect((await authorize(req())).denied?.status).toBe(401);
    expect((await authorize(req("member-token"), "admin")).denied?.status).toBe(403);
    const ok = await authorize(req("admin-token"), "admin");
    expect(ok.denied).toBeNull();
    expect(ok.user?.role).toBe("admin");
    const cross = await authorize(
      req("admin-token", { origin: "https://evil.example", host: "app.test" }),
    );
    expect(cross.denied?.status).toBe(403);
    expect(await cross.denied?.json()).toEqual({ error: "cross-site request refused" });
    const read = new Request("http://app.test/api/x", {
      headers: { origin: "https://evil.example" },
    });
    expect((await authorize(read)).denied?.status).toBe(401);
  });

  it("guard pages: anonymous visitors are sent to sign in", async () => {
    setAuth(fakeAuth({ "member-token": member }));
    setAuthz(roles());
    expect(await authorizePage("/books", "admin")).toEqual({ user: member, allowed: false });
    expect(await authorizePage("/books")).toEqual({ user: member, allowed: true });
    setAuth(fakeAuth({}));
    await expect(authorizePage("/books?x=1")).rejects.toThrow(
      "redirect /sign-in?next=%2Fbooks%3Fx%3D1",
    );
  });
});
