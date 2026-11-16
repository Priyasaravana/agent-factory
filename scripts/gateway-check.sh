#!/usr/bin/env bash
# The engine answers only the gateway (assessment 2026-11-15, S-01). Run by `make sandbox-check`
# and CI e2e against the running stack. The probe is a container on the engine's own network
# (web, without nginx's proof) claiming to be an admin: the engine must refuse it.
set -uo pipefail
fail=0
pass() { echo "  PASS  $1"; }
flunk() { echo "  FAIL  $1${2:+  ($2)}"; fail=1; }
check() { if [ "$2" = "$3" ]; then pass "$1"; else flunk "$1" "got '$2', want '$3'"; fi; }

# busybox wget prints the status line with -S; the last one is the engine's answer
status() {
  docker compose exec -T web wget -q -S -O /dev/null "$@" 2>&1 | awk '$1 ~ /^HTTP\// { code = $2 } END { print code }'
}
ENGINE=http://factory:8000
check "forged admin headers straight to the engine are refused" \
  "$(status --header 'X-Auth-User: probe' --header 'X-Auth-Role: admin' $ENGINE/api/products)" 401
check "a wrong proof is refused" \
  "$(status --header 'X-Auth-User: probe' --header 'X-Auth-Role: admin' --header 'X-Gateway-Proof: wrong' $ENGINE/api/products)" 401
check "the webhook is not read without the gateway" \
  "$(status --post-data '{}' --header 'X-GitHub-Event: ping' $ENGINE/api/hooks/github)" 401
check "the engine's own health check still answers" "$(status $ENGINE/api/health)" 200
check "the engine's copy of the proof is readable only by its user" \
  "$(docker compose exec -T -u factory factory stat -c '%a %u' /run/gateway-proof/secret | tr -d '\r')" "400 10001"
check "the gateway can't read the engine's copy, only its own config line" \
  "$(docker compose exec -T web sh -c 'ls /etc/nginx/gateway-proof' | tr -d '\r' | xargs)" "proof.conf"

if [ "$fail" = 0 ]; then echo "gateway proof: OK"; else echo "gateway proof: FAILED"; fi
exit "$fail"
