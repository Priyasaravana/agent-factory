# Golden path: fastapi-service

Conventions every agent working in this repo must follow. The factory's
Verify, Package and Deploy stations depend on them.

## Layout
- `app/main.py` — FastAPI app. Keep `/healthz` (liveness) and `/readyz` (DB check).
- `app/db.py` — SQLAlchemy 2.x engine/session from `DATABASE_URL`.
- `app/` — add routers as `app/routers/<resource>.py`, models in `app/models.py`,
  schemas (pydantic v2) in `app/schemas.py`.
- `tests/` — pytest; tests run against SQLite (`DATABASE_URL=sqlite://`).
- `tests/acceptance/scenarios.yaml` — acceptance scenarios from Intake. Turn each
  into at least one test in `tests/test_acceptance.py`.
- `app/observability.py` — JSON logs with request ids, Prometheus `/metrics`,
  OpenTelemetry tracing (on when `OTEL_EXPORTER_OTLP_ENDPOINT` is set). Keep it wired
  in `app/main.py`; log with `logging.getLogger(__name__)`, never `print`.
- `tests/e2e/` — smoke tests against a running deployment (`E2E_BASE_URL`).
- `.github/` — CI (same checks as the factory), CODEOWNERS, Dependabot.
- `deploy/chart/` — Helm chart (app Deployment + NodePort Service + Postgres).
- `docs/` — spec, design, openapi, tasks (written by Intake/Design).

## Rules
- `make verify` must pass: ruff (lint + format), pytest with coverage >= 80%, bandit.
- This repo starts at agent-readiness Level 3. The factory's Readiness station fails the
  run if a signal goes missing: keep README, AGENTS.md, lockfile, Dockerfile, CI workflow,
  CODEOWNERS, pre-commit config, JSON logging, `/metrics`, tracing hook, `/healthz` and
  `/readyz`, acceptance tests and the docs from Intake/Design. No secrets in the repo
  (a secret scan runs on every delivery).
- Create tables on startup with `Base.metadata.create_all` (no migration tool in v0).
- Config only from environment variables. No secrets in code or the chart values.
- Keep the container non-root and the port at 8000.
- Do not change the chart's `service.nodePort` wiring or `image.*` values names —
  the Deploy station sets them.
- Never run `git push`; the Deliver station publishes.
