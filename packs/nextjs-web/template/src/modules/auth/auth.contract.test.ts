// Test passwords are deliberately low-entropy words, so the secret scan never mistakes them
// for credentials.
import { newDb } from "pg-mem";
import { afterEach, describe, expect, it } from "vitest";

import { setDatabase } from "@/lib/db";
import {
  auth,
  clearedCookie,
  createAuth,
  sameOrigin,
  sessionCookie,
  sessionToken,
  setAuth,
  userFrom,
} from "@/modules/auth";

import { hashPassword, localAuth, SESSION_MS, verifyPassword } from "./adapters/local";
import { noAuth } from "./adapters/none";
import { AuthError, type Auth } from "./port";

function memory() {
  const { Pool } = newDb().adapters.createPg();
  const pool = new Pool();
  return () => Promise.resolve(pool);
}

const ADMIN = { email: "Admin@Example.com", password: "testtesttesttest" };

// The contract every auth adapter keeps: wrong or missing credentials never sign anyone in.
const ADAPTERS: Record<string, () => Auth> = {
  local: () => localAuth({ db: memory(), admin: ADMIN }),
  none: noAuth,
};

describe.each(Object.entries(ADAPTERS))("auth adapter %s", (_name, make) => {
  it("signs nobody in with wrong credentials or without a token", async () => {
    const a = make();
    expect(await a.signIn("nobody@example.com", "wrongwrongwrong")).toBeNull();
    expect(await a.signIn(ADMIN.email, "wrongwrongwrong")).toBeNull();
    expect(await a.session(undefined)).toBeNull();
    expect(await a.session("not-a-session")).toBeNull();
    await expect(a.signOut(undefined)).resolves.toBeUndefined();
  });
});

describe("local accounts", () => {
  it("creates the first admin, signs in and out", async () => {
    const a = localAuth({ db: memory(), admin: ADMIN });
    const s = await a.signIn(" admin@example.com ", ADMIN.password);
    expect(s?.user).toMatchObject({ email: "admin@example.com", role: "admin" });
    expect(await a.session(s?.token)).toMatchObject({ role: "admin" });
    await a.signOut(s?.token);
    expect(await a.session(s?.token)).toBeNull();
  });

  it("adds members, and refuses bad or duplicate accounts", async () => {
    const a = localAuth({ db: memory() });
    const m = await a.createUser("member@example.com", "membermember", "member");
    expect(m.role).toBe("member");
    expect((await a.signIn("member@example.com", "membermember"))?.user.id).toBe(m.id);
    await expect(a.createUser("member@example.com", "membermember", "member")).rejects.toThrow(
      AuthError,
    );
    await expect(a.createUser("not-an-email", "membermember", "member")).rejects.toThrow("email");
    await expect(a.createUser("x@example.com", "short", "member")).rejects.toThrow("12 characters");
    await expect(a.createUser("y@example.com", "membermember", "owner" as never)).rejects.toThrow(
      "role",
    );
  });

  it("creates the admin only when there are no users yet", async () => {
    const db = memory();
    await localAuth({ db }).createUser("first@example.com", "firstfirstfirst", "member");
    expect(await localAuth({ db, admin: ADMIN }).signIn(ADMIN.email, ADMIN.password)).toBeNull();
  });

  it("ends sessions when they expire", async () => {
    let t = 1_000_000;
    const a = localAuth({ db: memory(), admin: ADMIN, now: () => t });
    const s = await a.signIn(ADMIN.email, ADMIN.password);
    expect(s?.expiresAt).toBe(t + SESSION_MS);
    t += SESSION_MS + 1;
    expect(await a.session(s?.token)).toBeNull();
  });

  it("tries the database again after it was down", async () => {
    let up = false;
    const db = memory();
    const a = localAuth({
      db: () => (up ? db() : Promise.reject(new Error("down"))),
      admin: ADMIN,
    });
    await expect(a.signIn(ADMIN.email, ADMIN.password)).rejects.toThrow("down");
    up = true;
    expect(await a.signIn(ADMIN.email, ADMIN.password)).not.toBeNull();
  });

  it("hashes passwords with scrypt and a salt", async () => {
    const h = await hashPassword("testtesttesttest");
    expect(h).toMatch(/^scrypt\$/);
    expect(h).not.toBe(await hashPassword("testtesttesttest"));
    expect(await verifyPassword("testtesttesttest", h)).toBe(true);
    expect(await verifyPassword("otherotherother", h)).toBe(false);
    expect(await verifyPassword("anything", "md5$abc")).toBe(false);
  });
});

describe("no sign-in", () => {
  it("is anonymous and creates no accounts", async () => {
    const a = noAuth();
    expect(a.enabled).toBe(false);
    await expect(a.createUser("a@example.com", "longlonglonglong", "admin")).rejects.toThrow("off");
  });
});

describe("the auth port's helpers", () => {
  afterEach(() => {
    setAuth(undefined);
    setDatabase(null);
  });

  it("picks the adapter, with the admin from the environment", async () => {
    expect(createAuth("none").enabled).toBe(false);
    const { Pool } = newDb().adapters.createPg();
    setDatabase(new Pool());
    const a = createAuth("local", { ADMIN_EMAIL: ADMIN.email, ADMIN_PASSWORD: ADMIN.password });
    expect(await a.signIn(ADMIN.email, ADMIN.password)).not.toBeNull();
    expect(createAuth("local", {}).enabled).toBe(true);
  });

  it("reads the session cookie of a request", async () => {
    const a = localAuth({ db: memory(), admin: ADMIN });
    setAuth(a);
    expect(auth()).toBe(a);
    const s = await a.signIn(ADMIN.email, ADMIN.password);
    const req = new Request("http://app.test/", {
      headers: { cookie: `x=1; session=${s?.token}` },
    });
    expect(sessionToken(req)).toBe(s?.token);
    expect((await userFrom(req))?.role).toBe("admin");
    expect(await userFrom(new Request("http://app.test/"))).toBeNull();
  });

  it("makes httpOnly, SameSite=Lax cookies, Secure behind https", () => {
    const s = {
      token: "t/k",
      expiresAt: Date.now() + 60_000,
      user: { id: "1", email: "a@b.c", role: "admin" as const },
    };
    const plain = sessionCookie(s, new Request("http://app.test/"));
    expect(plain).toMatch(/^session=t%2Fk; Path=\/; HttpOnly; SameSite=Lax; Max-Age=(59|60)$/);
    const proxied = new Request("http://app.test/", { headers: { "x-forwarded-proto": "https" } });
    expect(sessionCookie(s, proxied)).toContain("; Secure");
    expect(clearedCookie(new Request("https://app.test/"))).toBe(
      "session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Secure",
    );
    expect(clearedCookie(new Request("http://app.test/"))).not.toContain("Secure");
  });

  it("accepts mutating requests only from the app's own origin", () => {
    const req = (headers: Record<string, string>) =>
      new Request("http://app.test/api/x", { method: "POST", headers });
    expect(sameOrigin(req({}))).toBe(true);
    expect(sameOrigin(req({ origin: "http://app.test", host: "app.test" }))).toBe(true);
    expect(
      sameOrigin(req({ origin: "https://app.example", "x-forwarded-host": "app.example" })),
    ).toBe(true);
    expect(sameOrigin(req({ origin: "https://evil.example", host: "app.test" }))).toBe(false);
    expect(sameOrigin(req({ origin: "null", host: "app.test" }))).toBe(false);
  });
});
