#!/bin/sh
# factory-init: one-shot, as root, before the factory starts (ADR-0018).
# The only root step in the factory's lifecycle, in its own container with no
# network and only the capabilities it needs (CHOWN, FOWNER, DAC_OVERRIDE):
#   1. copy dind's TLS client certs into the factory-certs volume, owned by uid 10001;
#   2. hand /data (state, product repos, run worktrees) to uid 10001;
#   3. make the gateway proof (assessment 2026-11-15, S-01): one secret, written once, in two
#      volumes: the engine's copy (uid 10001) and nginx's, as a config line (uid 101). nginx adds
#      it to every request it passes to the engine; the engine answers nothing without it.
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

# 3. the gateway proof. Kept across restarts (both sides read it at start); made again only
#    when the engine's copy is missing or malformed. nginx's copy is always rewritten from it.
WEB_UID=101
umask 077
install -d -o "$RUNTIME_UID" -g "$RUNTIME_UID" -m 0700 /gateway-proof/engine
install -d -o "$WEB_UID" -g "$WEB_UID" -m 0700 /gateway-proof/web
proof=""
[ -f /gateway-proof/engine/secret ] && proof=$(cat /gateway-proof/engine/secret)
case "$proof" in
  *[!0-9a-f]*) proof="" ;;
esac
if [ "${#proof}" -ne 64 ]; then
  proof=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  [ "${#proof}" -eq 64 ] || { echo "[init] could not make the gateway proof" >&2; exit 1; }
  echo "[init] gateway proof made"
fi
printf '%s\n' "$proof" > /gateway-proof/engine/secret.tmp
printf 'set $gateway_proof "%s";\n' "$proof" > /gateway-proof/web/proof.conf.tmp
chown "$RUNTIME_UID:$RUNTIME_UID" /gateway-proof/engine/secret.tmp
chown "$WEB_UID:$WEB_UID" /gateway-proof/web/proof.conf.tmp
chmod 0400 /gateway-proof/engine/secret.tmp /gateway-proof/web/proof.conf.tmp
mv -f /gateway-proof/engine/secret.tmp /gateway-proof/engine/secret
mv -f /gateway-proof/web/proof.conf.tmp /gateway-proof/web/proof.conf
echo "[init] gateway proof in place for the engine (uid $RUNTIME_UID) and the gateway (uid $WEB_UID)"
