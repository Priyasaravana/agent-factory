// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { APP_NAME, LOOK, LOOKS, NAV } from "@/app/look";
import { AppShell } from "@/components/app-shell";
import { Page } from "@/components/page";
import { cn } from "@/lib/utils";

describe("app shell", () => {
  it("has a skip link, the product name and the main navigation", () => {
    render(
      <AppShell user={null} signInEnabled={false}>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Skip to content" }).getAttribute("href")).toBe(
      "#main",
    );
    expect(screen.getByRole("link", { name: APP_NAME })).toBeTruthy();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.querySelectorAll("a")).toHaveLength(NAV.length);
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    expect(screen.getByText("content")).toBeTruthy();
  });

  it("offers sign-in when the app has it and nobody is signed in", () => {
    render(
      <AppShell user={null} signInEnabled>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/sign-in");
  });

  it("shows who is signed in and a sign-out form", () => {
    render(
      <AppShell user={{ id: "1", email: "admin@example.test", role: "admin" }} signInEnabled>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByTestId("signed-in-as").textContent).toBe("admin@example.test");
    const signOut = screen.getByRole("button", { name: "Sign out" });
    expect(signOut.closest("form")?.getAttribute("action")).toBe("/api/auth/sign-out");
  });
});

describe("page frame", () => {
  it("gives every page one main landmark, one h1, a description and actions", () => {
    render(
      <Page title="Books" description="Everything on loan" actions={<button>Add book</button>}>
        <p>list</p>
      </Page>,
    );
    expect(screen.getByRole("main").id).toBe("main");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("Everything on loan")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add book" })).toBeTruthy();
  });
});

describe("look", () => {
  it("is one of the token sets in globals.css", () => {
    expect(LOOKS).toContain(LOOK);
  });

  it("merges classes, later Tailwind classes winning", () => {
    expect(cn("px-2 text-sm", false && "hidden", "px-4")).toBe("text-sm px-4");
  });
});
