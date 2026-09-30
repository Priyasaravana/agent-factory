#!/usr/bin/env bash
set -euo pipefail

# Runs as the unprivileged factory user (uid 10001, ADR-0018). The root steps
# (dind client certs, /data ownership) happen once in the factory-init container.
if [ "$(id -u)" = "0" ] && [ "${AGENT_FACTORY_ALLOW_ROOT:-}" != "1" ]; then
  echo "[entrypoint] refusing to run as root: the factory runs as uid 10001." >&2
  echo "[entrypoint] start it with 'docker compose up' (factory-init prepares certs and /data)." >&2
  exit 1
fi

git config --global init.defaultBranch main
if [ "${FACTORY_MODE:-dry-run}" = "live" ]; then
  git config --global user.name "${GIT_AUTHOR_NAME:-Agent Factory}"
  git config --global user.email "${GIT_AUTHOR_EMAIL:-agent-factory@localhost}"
  /opt/factory/cluster/bootstrap.sh
else
  git config --global user.name "Agent Factory (dry-run)"
  git config --global user.email "dry-run@localhost"
  echo "[entrypoint] dry-run mode: no cluster bootstrap, no model usage"
fi
exec "$@"
