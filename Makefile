.PHONY: help init up up-live down logs ps reset-cluster reset-admin sandbox-check privilege-check backup restore upgrade test lint web-build openapi check

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

init: ## create .env from the example
	@test -f .env || cp .env.example .env && echo "edit .env, then: make up"

up: init ## start the factory (mode from .env)
	docker compose up -d --build
	@echo "UI  http://localhost:8080   API docs  http://localhost:8080/api/docs (after sign-in)"
	@docker compose exec -T auth sh -c 'test -f /data/initial-admin-password && echo "first sign-in: admin / $$(cat /data/initial-admin-password)  (change it when asked)"' 2>/dev/null || true

up-live: ## start in live mode (real agents + kind)
	FACTORY_MODE=live docker compose up -d --build

down: ## stop everything (keeps cluster, images and data)
	docker compose down

sandbox-check: ## prove the agent sandbox isolation (no secrets, no Docker, egress allowlist)
	docker compose exec -u factory factory agent-factory sandbox check

privilege-check: ## prove least privilege: no root, no capabilities, only dind privileged (ADR-0018)
	scripts/privilege-check.sh

backup: ## consistent snapshot of all factory + auth data into backups/ (safe while running)
	./scripts/backup.sh

restore: ## restore a backup: make restore BACKUP=backups/agent-factory-<timestamp>
	./scripts/restore.sh $(BACKUP)

upgrade: ## back up, pull main, rebuild and prove the stack (see docs/upgrading.md)
	./scripts/backup.sh
	git pull --ff-only
	docker compose up -d --build --wait
	docker compose exec -T -u factory factory agent-factory sandbox check

reset-admin: ## break-glass: print a one-time password for the admin user
	docker compose exec auth factory-auth reset-password $${FACTORY_ADMIN_USER:-admin}

logs: ## follow factory logs
	docker compose logs -f factory

ps:
	docker compose ps

reset-cluster: ## delete and recreate the kind cluster inside dind
	docker compose exec -u factory factory kind delete cluster --name factory || true
	docker compose restart factory

test: ## engine + auth tests (dry-run, no model usage)
	cd engine && uv run --extra dev pytest -q
	cd auth && uv run --extra dev pytest -q

lint:
	cd engine && uv run --extra dev ruff check src tests
	cd auth && uv run --extra dev ruff check src tests

openapi: ## regenerate the API contract and the UI's typed client
	cd engine && uv run agent-factory openapi --out ../web/openapi.json
	cd web && npm run gen:api

web-build:
	cd web && npm ci && npm run build

check: lint test web-build ## everything CI runs
