.PHONY: install verify lint test security build e2e preview run

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
# loopback (make preview) never goes through a proxy; any other BASE_URL is unaffected
e2e: install
	NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost BASE_URL=$(BASE_URL) npx playwright test

# the app on this machine, without the cluster, on an in-memory database (stays in the foreground).
# The factory runs the e2e suite against it before it builds an image; so can you:
#   make preview &   then   make e2e BASE_URL=http://127.0.0.1:3000
# It runs the standalone server the image runs, with the same static files.
preview: build
	rm -rf .next/standalone/.next/static .next/standalone/public
	cp -r .next/static .next/standalone/.next/static
	cp -r public .next/standalone/public
	PREVIEW_DATABASE=memory PORT=3000 HOSTNAME=127.0.0.1 node .next/standalone/server.js

run: install
	npx next dev --port 3000
