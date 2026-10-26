---
name: nextjs-golden-path
description: Use when designing, implementing or repairing a web app on the nextjs-web stack pack. Covers layout, data access, testing, accessibility and the checks `make verify`, `make preview` and `make e2e` run.
---

# Next.js Golden Path

Read `AGENTS.md` in the repo root first; it is authoritative.

## Design
- Pages under `src/app/<route>/page.tsx` (server components); interactive parts as small
  `"use client"` components. Forms post to route handlers or server actions.
- The app's own API is route handlers under `src/app/api/`; describe them in `docs/openapi.yaml`.
- Plan one table per stored entity in `src/lib/schema.ts` (`create table if not exists`).
- Logging, metrics, tracing, sign-in and permissions are modules (`src/modules/<name>`, see
  AGENTS.md): name in `docs/design.md` which adapter each runs, from `docs/decisions.yaml`, and which
  routes and pages need a signed-in user or a role. The user and session tables belong to the
  `auth` module; don't add your own.

## Implementation checklist
1. Data access in `src/lib/<entity>.ts`: functions taking plain values, using `await db()` and
   parameterised SQL. Validate input at the edge (route handler / server action) and return 400
   with a message for bad input, 404 for missing ids, 201 on create.
2. Route handlers call `count(route, status)` (`@/modules/metrics`) and log with `logger`
   (`@/modules/logging`), never `console.log`. Set the adapters in `src/modules/config.ts`
   (`DEFAULTS`) to match `docs/decisions.yaml`. Every protected route handler starts with
   `const { user, denied } = await authorize(request, role); if (denied) return denied;`
   (`@/modules/authz`); protected pages call `authorizePage(path, role)`.
3. Pages read data on the server (`export const dynamic = "force-dynamic"` when they show
   stored data) and render semantic HTML: `<main>`, one `<h1>`, `<label htmlFor>` on every
   input, buttons for actions, lists for lists.
4. Tests in `tests/<feature>.test.ts(x)`: `memoryDatabase()` before each database test; call
   route handlers directly with `new Request(...)`; render components with Testing Library in
   jsdom. Cover the happy path, not-found and validation.
5. Turn every entry in `tests/acceptance/scenarios.yaml` into a test in
   `tests/acceptance.test.ts(x)`, its title tagged with the requirement ids, e.g. `@R1`.
6. Add each new page to `e2e/routes.ts`, and a Playwright spec in `e2e/` for the main journey.

## Checks
- `make verify`: eslint (`--max-warnings=0`), `prettier --check`, `tsc --noEmit`, vitest with
  80% coverage thresholds, `npm audit --audit-level=high`, `next build`. Run `npx prettier
  --write .` before you finish.
- `make e2e BASE_URL=…`: smoke tests plus axe on every route; any serious or critical
  violation fails the change. The factory runs it against `make preview` before it builds an
  image, and against the live app after deploy.
- Before you report done, run it yourself the same way: `make preview > /tmp/preview.log 2>&1 &`,
  wait until `curl -fs --noproxy '*' http://127.0.0.1:3000/api/healthz` answers (the sandbox's
  proxy refuses loopback; `make e2e` already bypasses it), run
  `make e2e BASE_URL=http://127.0.0.1:3000`, then `kill %1`. The preview starts on an empty
  in-memory database each time, so every e2e test creates the data it needs.
- Keep the `proxy` block in `playwright.config.ts`: the live run reaches the app only through the
  sandbox's egress proxy, which Playwright doesn't pick up from `HTTP_PROXY` by itself. A live e2e
  failure such as `getaddrinfo ENOTFOUND` is the environment, not the app: report it, don't change code.

## Pitfalls
- Import a module's port (`@/modules/auth`), never its adapters: `make lint` fails on
  `@/modules/*/adapters/*` outside the module. In tests use `setAuth(createAuth("local"))` with
  `memoryDatabase()`, or a small fake `Auth`; never edit a contract test to make an adapter pass.
- With sign-in on, `make preview` signs in as `admin@preview.test` (password in the Makefile, a
  throw-away preview account); the live run gets the deployment's admin from the factory
  (`E2E_ADMIN_EMAIL`, `E2E_ADMIN_PASSWORD`). Use `signIn(page)` / `signInApi(request)` from
  `e2e/auth.ts`; never write a password into a test. For an account a test creates (a member, a
  second user), take `newCredentials("member")` from `e2e/auth.ts` (a random password each run) and
  create it through the app's admin route; in vitest, build the password the same way
  (`` `pw-${crypto.randomUUID()}` ``). A password literal in a test is held by the change-risk check.
- pg-mem in tests vs Postgres in the cluster: stick to standard SQL (no extensions, no
  `returning *` on multi-statement queries); `serial`/`text`/`integer`/`boolean`/`timestamptz`.
- Never import `src/lib/db.ts` (or anything using `pg`, `pino`) from a `"use client"` file.
- Keep `/api/healthz` and `/api/readyz`; Deploy's smoke test calls `/api/healthz`.
- Add dependencies with `npm install <pkg>` so `package-lock.json` stays in step; never edit
  the lockfile by hand, and don't upgrade `@playwright/test`.
- No `next/font/google` or other remote assets: builds run without internet access to them.
