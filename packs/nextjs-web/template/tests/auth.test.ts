// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET as session } from "@/app/api/auth/session/route";
import { POST as signIn } from "@/app/api/auth/sign-in/route";
import { POST as signOut } from "@/app/api/auth/sign-out/route";
import { setDatabase } from "@/lib/db";
import { createAuth, setAuth } from "@/modules/auth";

import { memoryDatabase } from "./support/db";

const ADMIN = { email: "admin@example.com", password: "testtesttesttest" };

function memoryAuth() {
  memoryDatabase();
  return createAuth("local", { ADMIN_EMAIL: ADMIN.email, ADMIN_PASSWORD: ADMIN.password });
}

const form = (fields: Record<string, string>, headers: Record<string, string> = {}) =>
  new Request("http://app.test/api/auth/sign-in", {
    method: "POST",
    body: new URLSearchParams(fields),
    headers,
  });
const json = (body: unknown) =>
  new Request("http://app.test/api/auth/sign-in", {
    method: "POST",
    body: typeof body === "string" ? body : JSON.stringify(body),
    headers: { "content-type": "application/json" },
  });

function cookieOf(res: Response): string {
  return (res.headers.get("set-cookie") ?? "").split(";")[0] ?? "";
}

describe("sign-in and sign-out", () => {
  beforeEach(() => setAuth(memoryAuth()));
  afterEach(() => {
    setAuth(undefined);
    setDatabase(null);
  });

  it("signs in from the form and comes back to the page asked for", async () => {
    const res = await signIn(form({ ...ADMIN, next: "/books" }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/books");
    expect(res.headers.get("set-cookie")).toMatch(/HttpOnly; SameSite=Lax/);
    const who = await session(
      new Request("http://app.test/api/auth/session", { headers: { cookie: cookieOf(res) } }),
    );
    expect(await who.json()).toMatchObject({
      enabled: true,
      user: { email: ADMIN.email, role: "admin" },
    });
  });

  it("never redirects to another site", async () => {
    for (const next of ["//evil.example", "https://evil.example", "/\\evil.example"]) {
      expect((await signIn(form({ ...ADMIN, next }))).headers.get("location")).toBe("/");
    }
  });

  it("answers JSON callers with JSON", async () => {
    const res = await signIn(json(ADMIN));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ user: { role: "admin" } });
    expect((await signIn(json({ email: ADMIN.email, password: "wrongwrongwrong" }))).status).toBe(
      401,
    );
    expect((await signIn(json({ email: ADMIN.email }))).status).toBe(400);
    expect((await signIn(json("{not json"))).status).toBe(400);
  });

  it("sends a wrong password back to the form with an error", async () => {
    const res = await signIn(form({ email: ADMIN.email, password: "wrongwrongwrong", next: "/x" }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/sign-in?error=1&next=%2Fx");
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("refuses a sign-in posted from another site", async () => {
    const res = await signIn(form(ADMIN, { origin: "https://evil.example", host: "app.test" }));
    expect(res.status).toBe(403);
  });

  it("signs out: the session ends and the cookie is cleared", async () => {
    const cookie = cookieOf(await signIn(json(ADMIN)));
    const res = await signOut(
      new Request("http://app.test/api/auth/sign-out", {
        method: "POST",
        headers: { cookie, "content-type": "application/x-www-form-urlencoded" },
      }),
    );
    expect(res.status).toBe(303);
    expect(res.headers.get("set-cookie")).toContain("Max-Age=0");
    const who = await session(
      new Request("http://app.test/api/auth/session", { headers: { cookie } }),
    );
    expect(await who.json()).toEqual({ enabled: true, user: null });
    expect(
      (await signOut(new Request("http://app.test/api/auth/sign-out", { method: "POST" }))).status,
    ).toBe(204);
    const cross = new Request("http://app.test/api/auth/sign-out", {
      method: "POST",
      headers: { origin: "https://evil.example", host: "app.test" },
    });
    expect((await signOut(cross)).status).toBe(403);
  });
});

describe("without sign-in", () => {
  beforeEach(() => setAuth(createAuth("none")));
  afterEach(() => setAuth(undefined));

  it("has no sign-in and says so", async () => {
    expect((await signIn(json(ADMIN))).status).toBe(404);
    expect((await signIn(form(ADMIN))).headers.get("location")).toBe("/sign-in");
    const who = await session(new Request("http://app.test/api/auth/session"));
    expect(await who.json()).toEqual({ enabled: false, user: null });
  });
});

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));
