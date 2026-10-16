.PHONY: install verify lint test security run

install:
	uv sync --quiet

lint: install
	uv run ruff check app tests
	uv run ruff format --check app tests

test: install
	DATABASE_URL=sqlite:// uv run pytest -q --cov=app --cov-fail-under=80

security: install
	uv run bandit -q -r app

verify: lint test security

run: install
	uv run uvicorn app.main:app --reload --port 8000
