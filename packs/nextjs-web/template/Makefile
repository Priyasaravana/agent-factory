.PHONY: install verify lint test security build e2e run

# npm ci only when the lockfile changed since the last install
node_modules/.package-lock.json: package-lock.json
	npm ci --no-audit --no-fund

install: node_modules/.package-lock.json

lint: install
	npx eslint . --max-warnings=0
	npx prettier --check .
	npx tsc --noEmit

test: install
	npx vitest run --coverage

security: install
	npm audit --omit=dev --audit-level=high

build: install
	NEXT_TELEMETRY_DISABLED=1 npx next build

verify: lint test security build

# end-to-end tests against a running deployment, accessibility included:
#   make e2e BASE_URL=http://localhost:3000
e2e: install
	BASE_URL=$(BASE_URL) npx playwright test

run: install
	npx next dev --port 3000
