// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import SignIn from "@/app/sign-in/page";
import { createAuth, setAuth, type Auth, type User } from "@/modules/auth";

const token = { value: undefined as string | undefined };
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => (token.value ? { value: token.value } : undefined) }),
}));

const admin: User = { id: "1", email: "admin@example.com", role: "admin" };
const fake: Auth = {
  enabled: true,
  signIn: async () => null,
  session: async (t) => (t === "ok" ? admin : null),
  signOut: async () => undefined,
  createUser: async () => admin,
};

const page = async (params: { error?: string; next?: string } = {}) =>
  render(await SignIn({ searchParams: Promise.resolve(params) }));

describe("sign-in page", () => {
  afterEach(() => {
    setAuth(undefined);
    token.value = undefined;
  });

  it("has a labelled form that returns to the page asked for", async () => {
    setAuth(fake);
    const { container } = await page({ next: "/books" });
    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText("Email")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
    expect(container.querySelector('input[name="next"]')?.getAttribute("value")).toBe("/books");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says when the credentials were wrong", async () => {
    setAuth(fake);
    await page({ error: "1" });
    expect(screen.getByRole("alert").textContent).toContain("Wrong email or password");
  });

  it("offers sign-out when signed in", async () => {
    setAuth(fake);
    token.value = "ok";
    await page();
    expect(screen.getByText(/signed in as admin@example.com/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  });

  it("explains when the app has no sign-in", async () => {
    setAuth(createAuth("none"));
    await page();
    expect(screen.getByText(/no sign-in/)).toBeTruthy();
  });
});
