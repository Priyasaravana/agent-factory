#!/usr/bin/env bash
set -euo pipefail

# Phase 1 (root): hand the dind TLS client certs and /data to the factory user, then drop privileges.
if [ "$(id -u)" = "0" ]; then
  if [ -d /certs/client ]; then
    mkdir -p /home/factory/.docker/certs
    cp /certs/client/*.pem /home/factory/.docker/certs/ 2>/dev/null || true
    chown -R factory:factory /home/factory/.docker
    chmod 600 /home/factory/.docker/certs/key.pem 2>/dev/null || true
  fi
  mkdir -p /data && chown -R factory:factory /data
  exec setpriv --reuid=10001 --regid=10001 --init-groups "$0" "$@"
fi

# Phase 2 (factory user)
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
