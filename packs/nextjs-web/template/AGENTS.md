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
- `src/lib/logger.ts` — pino JSON logs; `logger.info({ ... }, "message")`, never `console.log`.
- `src/lib/metrics.ts` — the Prometheus registry; `count(route, status)` in each route handler.
- `src/instrumentation.ts` — OpenTelemetry tracing (on when `OTEL_EXPORTER_OTLP_ENDPOINT` is set).
- `tests/` — vitest. Route handlers and libraries run in Node; a component test starts with
  `// @vitest-environment jsdom` and uses Testing Library. Database tests call
  `memoryDatabase()` from `tests/support/db.ts` (an in-memory Postgres, real SQL).
- `docs/requirements.yaml` — the numbered requirements (R1, R2, …) from Intake; the product spec
  is `docs/spec.md`, the technical design `docs/design.md`, the API contract `docs/openapi.yaml`.
- `tests/acceptance/scenarios.yaml` — acceptance scenarios from Intake, each listing the
  requirements it `covers`. Turn each into at least one test in `tests/acceptance.test.ts(x)`.
- `e2e/` — Playwright tests against a running deployment (`make e2e BASE_URL=…`), run by the
  factory after every deploy. `e2e/a11y.spec.ts` scans every route in `e2e/routes.ts` with axe.
- `.github/` — CI (same checks as the factory), CODEOWNERS, Dependabot.
- `deploy/chart/` — Helm chart (Deployment + Service + optional Ingress + Postgres).

## Rules

- Traceability: put the requirement ids a test covers in its title, e.g.
  `it("lists open tasks first @R2 @R3", …)` or `test("adds a book @R1", …)`. Every requirement
  needs a scenario and a tagged test.
- Accessibility (WCAG 2.1 AA): every page has `<main>`, one `<h1>`, labelled form controls,
  `alt` on images, enough contrast and visible focus. Add each new page to `e2e/routes.ts`;
  never exclude an axe rule or element to make the check pass.
- `make verify` must pass: eslint, prettier, `tsc --noEmit`, vitest with coverage >= 80%,
  `npm audit` (high), `next build`.
- This repo starts at agent-readiness Level 3. The factory's Readiness station fails the change
  if a signal goes missing: keep README, AGENTS.md, package-lock.json, Dockerfile, CI workflow,
  CODEOWNERS, pre-commit config, pino logging, `/api/metrics`, `src/instrumentation.ts`,
  `/api/healthz` and `/api/readyz`, the coverage thresholds, acceptance tests, the e2e and a11y
  specs and the docs from Intake/Design. No secrets in the repo (a secret scan runs on every
  delivery).
- Config only from environment variables. No secrets in code or the chart values.
- Keep the container non-root (`USER node`), `output: "standalone"` and the port at 3000.
- Do not change the chart's `image.*`, `service.*`, `ingress.*` or `imagePullSecrets` value
  names: the Deploy station sets them (NodePort locally, ClusterIP + Ingress on a cluster).
- Keep `@playwright/test` at the version in `package.json`: the factory's sandbox provides the
  browser for exactly that version.
- No web fonts or scripts from other hosts (`next/font/google`, CDNs): the build and the tests
  run without them.
- Never run `git push`; the Deliver station publishes.
