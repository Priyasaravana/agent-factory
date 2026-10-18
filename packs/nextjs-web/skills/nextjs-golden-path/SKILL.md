---
name: nextjs-golden-path
description: Use when designing, implementing or repairing a web app on the nextjs-web stack pack. Covers layout, data access, testing, accessibility and the checks `make verify` and `make e2e` run.
---

# Next.js Golden Path

Read `AGENTS.md` in the repo root first; it is authoritative.

## Design
- Pages under `src/app/<route>/page.tsx` (server components); interactive parts as small
  `"use client"` components. Forms post to route handlers or server actions.
- The app's own API is route handlers under `src/app/api/`; describe them in `docs/openapi.yaml`.
- Plan one table per stored entity in `src/lib/schema.ts` (`create table if not exists`).

## Implementation checklist
1. Data access in `src/lib/<entity>.ts`: functions taking plain values, using `await db()` and
   parameterised SQL. Validate input at the edge (route handler / server action) and return 400
   with a message for bad input, 404 for missing ids, 201 on create.
2. Route handlers call `count(route, status)` and log with `logger`, never `console.log`.
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
- `make e2e BASE_URL=…` (the factory runs it after deploy): smoke tests plus axe on every route;
  any serious or critical violation fails the change.

## Pitfalls
- pg-mem in tests vs Postgres in the cluster: stick to standard SQL (no extensions, no
  `returning *` on multi-statement queries); `serial`/`text`/`integer`/`boolean`/`timestamptz`.
- Never import `src/lib/db.ts` (or anything using `pg`, `pino`) from a `"use client"` file.
- Keep `/api/healthz` and `/api/readyz`; Deploy's smoke test calls `/api/healthz`.
- Add dependencies with `npm install <pkg>` so `package-lock.json` stays in step; never edit
  the lockfile by hand, and don't upgrade `@playwright/test`.
- No `next/font/google` or other remote assets: builds run without internet access to them.
