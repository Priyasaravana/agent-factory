# Golden path: nextjs-web

Conventions every agent working in this repo must follow. The factory's Verify, Package, Deploy
and Acceptance stations depend on them.

## Layout

- `src/app/` — Next.js App Router. Pages are server components by default; add
  `"use client"` only to components that need state or browser events.
- `src/app/api/<name>/route.ts` — route handlers for the app's own HTTP API. Keep
  `/api/healthz` (liveness), `/api/readyz` (database check) and `/api/metrics` (Prometheus).
- `src/lib/db.ts` — Postgres through `pg` from `DATABASE_URL`; `await db()` gives a pool with the
  tables from `src/lib/schema.ts` created. Plain parameterised SQL (`$1`, `$2`), no ORM.
- `src/modules/<name>/` — cross-cutting concerns as **modules**: a port (the interface, exported
  from `index.ts`), its adapters (`adapters/`, private to the module) and a contract test
  (`<name>.contract.test.ts`) that every adapter passes, the no-op included. App code imports only
  `@/modules/<name>`, never `@/modules/<name>/adapters/…` (`make lint` checks it with
  dependency-cruiser). Which adapter runs is set in one place, `src/modules/config.ts`.
  - `logging` — `logger.info({ ... }, "message")`, never `console.log` (pino JSON; `silent`).
  - `metrics` — `count(route, status)` in each route handler; `/api/metrics` scrapes it
    (Prometheus; `none`).
  - `tracing` — OpenTelemetry, started from `src/instrumentation.ts` (on when
    `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `off`).
  - `auth` — who is signed in: `userFrom(request)` in route handlers, `currentUser()` in server
    components. `local` (email + password, scrypt, httpOnly `SameSite=Lax` session cookie,
    sessions in Postgres, `/sign-in`, `/api/auth/sign-in`, `/api/auth/sign-out`) or `none`
    (everyone is anonymous). The first admin comes from `ADMIN_EMAIL`/`ADMIN_PASSWORD` (the
    chart's `<name>-admin` Secret; `make preview` sets a test admin). Add accounts with
    `auth().createUser(email, password, role)` from an admin-only route.
  - `authz` — what they may do: `const { user, denied } = await authorize(request, "admin");
if (denied) return denied;` in every protected route handler (it also refuses cross-site
    posts), `authorizePage(path, role)` in protected pages. `allow-all`, `signed-in` or `roles`
    (`admin`, `member`).
- `tests/` — vitest. Route handlers and libraries run in Node; a component test starts with
  `// @vitest-environment jsdom` and uses Testing Library. Database tests call
  `memoryDatabase()` from `tests/support/db.ts` (an in-memory Postgres, real SQL).
- `docs/requirements.yaml` — the numbered requirements (R1, R2, …) from Intake; the product spec
  is `docs/spec.md`, the technical design `docs/design.md`, the API contract `docs/openapi.yaml`.
- `tests/acceptance/scenarios.yaml` — acceptance scenarios from Intake, each listing the
  requirements it `covers`. Turn each into at least one test in `tests/acceptance.test.ts(x)`.
- `e2e/` — Playwright tests (`make e2e BASE_URL=…`). The factory runs them twice: against
  `make preview` (the standalone server on an in-memory database, `PREVIEW_DATABASE=memory`)
  before it builds an image, and against the live app after every deploy. `e2e/a11y.spec.ts` scans
  every route in `e2e/routes.ts` with axe.
- `.github/` — CI (same checks as the factory), CODEOWNERS, Dependabot.
- `deploy/chart/` — Helm chart (Deployment + Service + optional Ingress + Postgres).

## Rules

- Design decisions (`docs/decisions.yaml`) choose the adapters: set `DEFAULTS` in
  `src/modules/config.ts` to match — `authentication: local` → `auth: "local"`;
  `authorisation: signed-in` → `authz: "signed-in"`, `roles` → `authz: "roles"`. Protected writes
  (data another person relies on) go through `authorize(...)` on the server, never only by hiding a
  button. With sign-in on, e2e tests sign in with the helpers in `e2e/auth.ts`, and acceptance
  tests show a protected action refused without signing in.
- Traceability: put the requirement ids a test covers in its title, e.g.
  `it("lists open tasks first @R2 @R3", …)` or `test("adds a book @R1", …)`. Every requirement
  needs a scenario and a tagged test.
- Accessibility (WCAG 2.1 AA): every page has `<main>`, one `<h1>`, labelled form controls,
  `alt` on images, enough contrast and visible focus. Add each new page to `e2e/routes.ts`;
  never exclude an axe rule or element to make the check pass.
- Before you report done, run the e2e suite yourself: `make preview &`, then
  `make e2e BASE_URL=http://127.0.0.1:3000`, then stop the preview. The e2e tests must pass on an
  empty database: create the data a test needs inside the test.
- `make verify` must pass: eslint, prettier, `tsc --noEmit`, vitest with coverage >= 80%,
  `npm audit` (high), `next build`.
- This repo starts at agent-readiness Level 3. The factory's Readiness station fails the change
  if a signal goes missing: keep README, AGENTS.md, package-lock.json, Dockerfile, CI workflow,
  CODEOWNERS, pre-commit config, the five modules with their contract tests and
  `.dependency-cruiser.cjs`, `/api/metrics`, `src/instrumentation.ts`,
  `/api/healthz` and `/api/readyz`, the coverage thresholds, acceptance tests, the e2e and a11y
  specs and the docs from Intake/Design. No secrets in the repo (a secret scan runs on every
  delivery).
- Config only from environment variables. No secrets in code or the chart values.
- Keep the runtime image distroless and non-root (`USER nonroot`), `output: "standalone"` and
  the port at 3000. It has no shell or npm: anything the app needs at run time ships in the build.
- Do not change the chart's `image.*`, `service.*`, `ingress.*` or `imagePullSecrets` value
  names: the Deploy station sets them (NodePort locally, ClusterIP + Ingress on a cluster).
- Keep `@playwright/test` at the version in `package.json`: the factory's sandbox provides the
  browser for exactly that version.
- No web fonts or scripts from other hosts (`next/font/google`, CDNs): the build and the tests
  run without them.
- Never run `git push`; the Deliver station publishes.
