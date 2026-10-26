// Every page a person can open. The accessibility check (a11y.spec.ts) scans each one and the
// screenshot run (screenshots.spec.ts) captures each one, so add a route here when you add a page.
export const ROUTES: string[] = ["/", "/sign-in"];

// The routes that need a signed-in person: the screenshot run signs in before it opens them (when
// the app has sign-in), so the evidence shows the page, not the sign-in redirect.
export const SIGNED_IN_ROUTES: string[] = [];
