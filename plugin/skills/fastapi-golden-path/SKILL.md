---
name: fastapi-golden-path
description: Use when designing or implementing a service on the fastapi-service product line. Covers layout, data access, testing and the checks `make verify` runs.
---

# FastAPI Golden Path

Read `AGENTS.md` in the repo root first; it is authoritative.

## Implementation checklist
1. Models in `app/models.py` (SQLAlchemy 2.x `Mapped[...]` typed columns) on `app.db.Base`.
2. Pydantic v2 schemas in `app/schemas.py` (`model_config = ConfigDict(from_attributes=True)`).
3. One router per resource in `app/routers/`, included in `app/main.py`.
4. DB access via the `get_session` dependency; commit inside the handler.
5. Return 201 on create, 404 for missing ids, 422 is automatic for bad input.
6. Tests: `tests/test_<resource>.py` using `TestClient(app)` as a context manager
   (so startup creates tables). Cover happy path, not-found and validation.
7. Turn every entry in `tests/acceptance/scenarios.yaml` into a test in
   `tests/test_acceptance.py`.

## Checks (`make verify`)
- `ruff check app tests` — fix, don't suppress.
- `pytest --cov-fail-under=80` with `DATABASE_URL=sqlite://`.
- `bandit -r app`.

## Pitfalls
- SQLite in tests vs Postgres in cluster: avoid Postgres-only SQL; use SQLAlchemy
  constructs (e.g. `ilike`, JSON-free tag tables) that work on both.
- Keep `/healthz` and `/readyz`; Deploy's smoke test calls `/healthz`.
- Add new runtime deps to `pyproject.toml` `[project].dependencies` and run `uv lock`.
