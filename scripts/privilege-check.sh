#!/usr/bin/env bash
# Least privilege of the running stack (ADR-0018). Run by `make privilege-check`
# and CI e2e. Reads each service's PID 1 status from inside its container.
set -uo pipefail
fail=0
pass() { echo "  PASS  $1"; }
flunk() { echo "  FAIL  $1${2:+  ($2)}"; fail=1; }
check() { if [ "$2" = "$3" ]; then pass "$1"; else flunk "$1" "got '$2', want '$3'"; fi; }
not_root() { if [ -n "$2" ] && [ "$2" != 0 ]; then pass "$1"; else flunk "$1" "uid '$2'"; fi; }

for svc in factory auth web; do
  st=$(docker compose exec -T "$svc" cat /proc/1/status 2>&1)
  field() { printf '%s\n' "$st" | awk -v k="$1:" '$1 == k { print $2; exit }'; }
  not_root "$svc: main process is not root" "$(field Uid)"
  check "$svc: no effective capabilities" "$(field CapEff)" 0000000000000000
  check "$svc: no capabilities in the bounding set" "$(field CapBnd)" 0000000000000000
  check "$svc: no-new-privileges" "$(field NoNewPrivs)" 1
  not_root "$svc: a shell opened with 'docker compose exec' is not root" \
    "$(docker compose exec -T "$svc" id -u 2>/dev/null | tr -d '\r')"
done

check "factory runs as uid 10001" "$(docker compose exec -T factory id -u | tr -d '\r')" 10001
init=$(docker compose ps -a -q factory-init)
check "factory-init finished successfully" "$([ -n "$init" ] && docker inspect -f '{{.State.ExitCode}}' "$init")" 0
privileged=$(docker compose ps -q | xargs docker inspect \
  -f '{{index .Config.Labels "com.docker.compose.service"}} {{.HostConfig.Privileged}}' \
  | awk '$2 == "true" { print $1 }' | sort | xargs)
check "only dind is privileged (accepted risk, ADR-0018)" "$privileged" dind

if [ "$fail" = 0 ]; then echo "least privilege: OK"; else echo "least privilege: FAILED"; fi
exit "$fail"
