import Link from "next/link";
import type { ReactNode } from "react";

import { APP_NAME, NAV } from "@/app/look";
import { Button } from "@/components/ui/button";
import { type User } from "@/modules/auth";

/** The app's frame on every page: a skip link, the header with the product's name, the main
 * navigation (src/app/look.ts NAV) and the account menu. Server-rendered, no client JavaScript:
 * on a phone the navigation wraps under the name instead of hiding behind a menu button. */
export function AppShell({
  user,
  signInEnabled,
  children,
}: {
  user: User | null;
  signInEnabled: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="border-b bg-card/80 backdrop-blur">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="text-base font-semibold text-foreground no-underline hover:no-underline"
          >
            {APP_NAME}
          </Link>
          <nav aria-label="Main" className="order-3 w-full sm:order-none sm:w-auto">
            <ul className="flex gap-1 overflow-x-auto">
              {NAV.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="block rounded-md px-3 py-1.5 text-sm text-muted-foreground no-underline hover:bg-accent hover:text-accent-foreground hover:no-underline"
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          {(user || signInEnabled) && (
            <div role="group" className="ml-auto flex items-center gap-2" aria-label="Account">
              {user ? (
                <>
                  <span
                    className="hidden text-sm text-muted-foreground sm:inline"
                    data-testid="signed-in-as"
                  >
                    {user.email}
                  </span>
                  <form method="post" action="/api/auth/sign-out">
                    <Button type="submit" variant="outline" size="sm">
                      Sign out
                    </Button>
                  </form>
                </>
              ) : (
                signInEnabled && (
                  <Button asChild variant="outline" size="sm">
                    <Link href="/sign-in" className="no-underline hover:no-underline">
                      Sign in
                    </Link>
                  </Button>
                )
              )}
            </div>
          )}
        </div>
      </header>
      <div className="flex-1">{children}</div>
    </div>
  );
}
