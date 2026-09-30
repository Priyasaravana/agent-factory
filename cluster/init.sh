#!/bin/sh
# factory-init: one-shot, as root, before the factory starts (ADR-0018).
# The only root step in the factory's lifecycle, in its own container with no
# network and only the capabilities it needs (CHOWN, FOWNER, DAC_OVERRIDE):
#   1. copy dind's TLS client certs into the factory-certs volume, owned by uid 10001;
#   2. hand /data (state, product repos, run worktrees) to uid 10001.
# The factory itself then runs as uid 10001 with no capabilities at all.
set -eu
RUNTIME_UID=10001

install -d -o "$RUNTIME_UID" -g "$RUNTIME_UID" -m 0700 /factory-certs
for f in ca.pem cert.pem key.pem; do
  if [ -f "/certs/client/$f" ]; then
    install -o "$RUNTIME_UID" -g "$RUNTIME_UID" -m 0600 "/certs/client/$f" "/factory-certs/$f"
  else
    echo "[init] warning: /certs/client/$f missing (is dind running with DOCKER_TLS_CERTDIR?)" >&2
  fi
done

mkdir -p /data
chown -R "$RUNTIME_UID:$RUNTIME_UID" /data
echo "[init] docker client certs and /data handed to uid $RUNTIME_UID"
