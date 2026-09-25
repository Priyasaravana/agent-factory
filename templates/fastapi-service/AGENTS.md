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
- `deploy/chart/` — Helm chart (app Deployment + NodePort Service + Postgres).
- `docs/` — spec, design, openapi, tasks (written by Intake/Design).

## Rules
- `make verify` must pass: ruff, pytest with coverage >= 80%, bandit.
- Create tables on startup with `Base.metadata.create_all` (no migration tool in v0).
- Config only from environment variables. No secrets in code or the chart values.
- Keep the container non-root and the port at 8000.
- Do not change the chart's `service.nodePort` wiring or `image.*` values names —
  the Deploy station sets them.
- Never run `git push`; the Deliver station publishes.
