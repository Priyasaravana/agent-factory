#!/usr/bin/env bash
# The engine answers only the gateway (assessment 2026-11-15, S-01), and the gateway keeps the UI
# and the apps on different sites (S-04) and sends the security headers (S-10). Run by `make sandbox-check`
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
check "a forged account id (ADR-0052) straight to the engine is refused" \
  "$(status --header 'X-Auth-User: probe' --header 'X-Auth-Role: member' --header 'X-Auth-Uid: 00000000000000000000000000000000' $ENGINE/api/products)" 401
check "the webhook is not read without the gateway" \
  "$(status --post-data '{}' --header 'X-GitHub-Event: ping' $ENGINE/api/hooks/github)" 401
check "the engine's own health check still answers" "$(status $ENGINE/api/health)" 200
check "the engine's copy of the proof is readable only by its user" \
  "$(docker compose exec -T -u factory factory stat -c '%a %u' /run/gateway-proof/secret | tr -d '\r')" "400 10001"
check "the gateway can't read the engine's copy, only its own config line" \
  "$(docker compose exec -T web sh -c 'ls /etc/nginx/gateway-proof' | tr -d '\r' | xargs)" "proof.conf"

# S-04: the UI and the apps are different sites, and another site's page can't write with a session
UI=${UI_URL:-http://localhost:8080}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
check "the UI opened on the apps' host (127.0.0.1) is sent to localhost" \
  "$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "${UI/localhost/127.0.0.1}/products")" "308 $UI/products"
check "a write to the API from an app's page is refused" \
  "$(code -X POST -H 'Origin: http://127.0.0.1:8081' -d '{}' "$UI/api/products")" 403
check "a sign-in posted from another site is refused" \
  "$(code -X POST -H 'Origin: https://elsewhere.example' -H 'Content-Type: application/json' -d '{}' "$UI/auth/login")" 403
check "the same request from the UI's own page reaches the gateway's sign-in check" \
  "$(code -X POST -H "Origin: $UI" -d '{}' "$UI/api/products")" 401

# S-10: security headers on pages and on refusals (a sign-in rate-limit probe would lock CI's own
# sign-ins out for a minute, so the limit is checked from the config in tests/test_static_ops.py)
header() { local h=$1; shift; curl -s -o /dev/null -D - "$@" | tr -d '\r' | awk -v h="$h" 'tolower($0) ~ "^"h":" { sub(/^[^:]*: */, ""); print; exit }'; }
check "the UI page can't be framed" "$(header x-frame-options "$UI/products")" "DENY"
check "the UI page isn't sniffed" "$(header x-content-type-options "$UI/")" "nosniff"
check "the UI page refuses inline scripts" \
  "$(header content-security-policy "$UI/" | grep -o "script-src 'self';")" "script-src 'self';"
check "an API refusal carries the headers too" "$(header x-frame-options "$UI/api/products")" "DENY"

if [ "$fail" = 0 ]; then echo "gateway proof: OK"; else echo "gateway proof: FAILED"; fi
exit "$fail"
