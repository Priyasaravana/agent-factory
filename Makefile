.PHONY: help init up up-live down logs ps reset-cluster test lint web-build openapi check

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

init: ## create .env from the example
	@test -f .env || cp .env.example .env && echo "edit .env, then: make up"

up: init ## start the factory (mode from .env)
	docker compose up -d --build
	@echo "UI  http://localhost:8080   API docs  http://localhost:8000/api/docs"

up-live: ## start in live mode (real agents + kind)
	FACTORY_MODE=live docker compose up -d --build

down: ## stop everything (keeps cluster, images and data)
	docker compose down

logs: ## follow factory logs
	docker compose logs -f factory

ps:
	docker compose ps

reset-cluster: ## delete and recreate the kind cluster inside dind
	docker compose exec factory kind delete cluster --name factory || true
	docker compose restart factory

test: ## engine tests (dry-run, no model usage)
	cd engine && uv run --extra dev pytest -q

lint:
	cd engine && uv run --extra dev ruff check src tests

openapi: ## regenerate the API contract and the UI's typed client
	cd engine && uv run agent-factory openapi --out ../web/openapi.json
	cd web && npm run gen:api

web-build:
	cd web && npm ci && npm run build

check: lint test web-build ## everything CI runs
