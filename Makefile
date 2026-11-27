.PHONY: help init up up-live down logs ps reset-cluster clean-docker local-ingress reset-admin apps app-login app-password sandbox-check privilege-check backup restore upgrade config-split test lint web-build openapi skills-sync check verify test-changed test-quiet mypy-new mypy-baseline secrets-files baselines

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

init: ## create .env from the example
	@test -f .env || cp .env.example .env && echo "edit .env, then: make up"
	@mkdir -m 700 -p .secrets   # token files, mounted read-only at /run/secrets (S-06)

up: init ## start the factory (mode from .env)
	python3 scripts/pin-images.py --check-compose
	docker compose up -d --build
	@echo "UI  http://localhost:8080   API docs  http://localhost:8080/api/docs (after sign-in)"
	@docker compose exec -T auth sh -c 'test -f /data/initial-admin-password && echo "first sign-in: admin / $$(cat /data/initial-admin-password)  (change it when asked)"' 2>/dev/null || true

up-live: ## start in live mode (real agents + kind)
	python3 scripts/pin-images.py --check-compose
	FACTORY_MODE=live docker compose up -d --build

down: ## stop everything (keeps cluster, images and data)
	docker compose down

sandbox-check: ## prove the agent sandbox isolation (no secrets, no Docker, egress allowlist) and that the engine answers only the gateway
	docker compose exec -u factory factory agent-factory sandbox check
	scripts/gateway-check.sh
	PROBE_ENFORCE=1 scripts/sandbox-paths-probe.sh

privilege-check: ## prove least privilege: no root, no capabilities, only dind privileged (ADR-0018)
	scripts/privilege-check.sh

backup: ## consistent snapshot of all factory + auth data into backups/ (safe while running)
	./scripts/backup.sh

restore: ## restore a backup: make restore BACKUP=backups/agent-factory-<timestamp>
	./scripts/restore.sh $(BACKUP)

upgrade: ## back up, pull main, rebuild and prove the stack (see docs/upgrading.md)
	@git diff --quiet HEAD -- .agent-factory/config.yaml || { \
	  echo "refused: .agent-factory/config.yaml has local edits; they belong in the gitignored" >&2; \
	  echo "  .agent-factory/config.local.yaml. Move them once with: make config-split" >&2; exit 2; }
	./scripts/backup.sh
	git pull --ff-only
	python3 scripts/pin-images.py --check-compose
	docker compose up -d --build --wait
	docker compose exec -T -u factory factory agent-factory sandbox check

reset-admin: ## break-glass: print a one-time password for the admin user
	docker compose exec auth factory-auth reset-password $${FACTORY_ADMIN_USER:-admin}

apps: ## list the products: where each lives and its admin's email
	@docker compose exec -T -u factory factory agent-factory apps

app-login: ## sign-in for one app: make app-login APP=<slug>; copies the admin password to the clipboard (macOS)
	@test -n "$(APP)" || { echo "usage: make app-login APP=<slug>   ('make apps' lists them)"; exit 2; }
	@docker compose exec -T -u factory factory agent-factory apps "$(APP)"
	@if command -v pbcopy >/dev/null 2>&1; then \
		pw=$$(docker compose exec -T -u factory factory agent-factory apps "$(APP)" --password-only) \
			|| { echo "  no password copied: the lookup failed (clipboard untouched)"; exit 1; }; \
		[ -n "$$pw" ] || { echo "  no password copied: the lookup returned nothing (clipboard untouched)"; exit 1; }; \
		printf '%s' "$$pw" | pbcopy && echo "  password copied to the clipboard (never printed)"; \
	else \
		echo "  no clipboard here: run 'make app-password APP=$(APP)' to print it"; \
	fi

app-password: ## print one app's admin password: make app-password APP=<slug> (it shows on screen)
	@test -n "$(APP)" || { echo "usage: make app-password APP=<slug>"; exit 2; }
	@docker compose exec -T -u factory factory agent-factory apps "$(APP)" --password-only

logs: ## follow factory logs
	docker compose logs -f factory

ps:
	docker compose ps

reset-cluster: ## delete and recreate the kind cluster inside dind
	docker compose exec -u factory factory kind delete cluster --name factory || true
	docker compose restart factory

clean-docker: ## free Docker disk: build cache, dangling images, stopped containers (never volumes or the cluster)
	@docker compose exec -T -u factory factory docker system df
	docker compose exec -T -u factory factory docker builder prune --all --force
	docker compose exec -T -u factory factory docker image prune --force
	docker compose exec -T -u factory factory docker container prune --force
	@docker compose exec -T -u factory factory docker system df
	@echo "kept: volumes, the kind cluster, tagged images. This cleans the factory's Docker (dind); Docker Desktop's own disk is separate."

local-ingress: ## local trial of the cluster target: registry + ingress-nginx in kind (ADR-0026)
	@docker compose exec -T -u factory factory cat /opt/factory/cluster/local-ingress.sh 2>/dev/null | cmp -s - cluster/local-ingress.sh \
		|| { echo "the running factory image is older than this checkout: run 'make up' first, then 'make local-ingress'"; exit 1; }
	docker compose exec -u factory factory /opt/factory/cluster/local-ingress.sh

test: ## engine + auth tests (dry-run, no model usage)
	cd engine && uv run --extra dev pytest -q
	cd auth && uv run --extra dev pytest -q

lint:
	cd engine && uv run --extra dev ruff check src tests
	cd auth && uv run --extra dev ruff check src tests

skills-sync: ## copy the engineering standards skills from plugin/skills to .claude/skills (ADR-0032)
	@for d in .claude/skills/*/; do n=$$(basename $$d); cp plugin/skills/$$n/SKILL.md .claude/skills/$$n/SKILL.md; done
	@echo "synced: $$(ls .claude/skills | tr '\n' ' ')"

secrets-files: ## move tokens out of .env into .secrets/ files, mounted read-only (S-06, once)
	python3 scripts/secrets-to-files.py --root .

baselines: ## record the G0 baselines from the running factory into docs/outcomes.md (phase A, once)
	@mkdir -p .cache
	docker compose exec -T -u factory factory agent-factory baselines --into - \
	  --install "$${BASELINES_INSTALL:-reference install}" --version "$$(git rev-parse --short HEAD)" \
	  < docs/outcomes.md > .cache/outcomes.md
	mv .cache/outcomes.md docs/outcomes.md
	@git --no-pager diff --stat -- docs/outcomes.md

config-split: ## move your edits in .agent-factory/config.yaml into the gitignored config.local.yaml (once)
	@mkdir -p .cache
	git show HEAD:.agent-factory/config.yaml > .cache/config.base.yaml
	cd engine && uv run agent-factory config split-local --base ../.cache/config.base.yaml \
	  --mine ../.agent-factory/config.yaml --out ../.agent-factory/config.local.yaml

openapi: ## regenerate the API contract and the UI's typed client
	cd engine && uv run agent-factory openapi --out ../web/openapi.json
	cd web && npm run gen:api

web-build:
	cd web && npm ci && npm run build

check: lint test web-build ## everything CI runs

# Quiet variants (scripts/check): full output goes to .cache/check/<step>.log, the terminal
# gets one line per step and only the failing part. Same checks, far less output for agents.
verify: ## quiet gate before a PR: lint, format, mypy (new errors), engine + auth tests, openapi drift, web build
	@scripts/check all

test-changed: ## quiet: only the engine tests touched by this branch (fast dev loop)
	@scripts/check changed

test-quiet: ## quiet: full engine test suite (ARGS="tests/test_x.py -k name" to narrow)
	@scripts/check test $(ARGS)

mypy-new: ## mypy, reporting only errors not in engine/.mypy-baseline.txt
	@scripts/check mypy

mypy-baseline: ## re-record the mypy baseline (after fixing known errors, never to hide new ones)
	@scripts/check mypy-baseline
