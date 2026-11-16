#!/bin/sh
# dind's entrypoint (assessment 2026-11-15, S-02, fix 4). Containers inside dind run code the
# factory doesn't trust: Dockerfile RUN steps, image start checks, scanners, the kind nodes and
# the app pods in them. None of them has any business on the compose network, where the engine,
# the auth service and the gateway live. One rule in DOCKER-USER (the chain Docker keeps for the
# operator) drops every NEW connection forwarded from a container to the compose subnet, so the
# block doesn't depend on each `docker run` or `docker build` passing the right flag.
#
#   - replies to connections that came in (the engine calling an app on a published port) pass:
#     only NEW connections are dropped;
#   - DIND_COMPOSE_ALLOW_PORTS (default 4000, the model gateway) stays reachable: the sandboxes'
#     egress proxy routes model calls there (ADR-0036), each with its own virtual key;
#   - /run/dind-firewall.ok exists only while the rule is in place; the compose health check
#     needs it, so the factory doesn't start on a dind without the rule.
set -eu
ALLOW_PORTS=${DIND_COMPOSE_ALLOW_PORTS:-4000}
OK=/run/dind-firewall.ok
rm -f "$OK"

ipt() { # the backend dockerd chose (nft, or legacy on kernels without nf_tables)
  for bin in iptables /usr/local/sbin/.iptables-legacy/iptables; do
    if command -v "$bin" >/dev/null 2>&1 && "$bin" -nL DOCKER-USER >/dev/null 2>&1; then echo "$bin"; return 0; fi
  done
  return 1
}

apply() {
  bin=$(ipt) || return 1
  subnet=$(ip -4 route show dev eth0 scope link | awk '{print $1; exit}')
  [ -n "$subnet" ] || { echo "[dind-firewall] no compose subnet on eth0" >&2; return 1; }
  if ! "$bin" -C DOCKER-USER -d "$subnet" -m conntrack --ctstate NEW -j DROP 2>/dev/null; then
    "$bin" -I DOCKER-USER 1 -d "$subnet" -m conntrack --ctstate NEW -j DROP
    for port in $ALLOW_PORTS; do
      "$bin" -I DOCKER-USER 1 -d "$subnet" -p tcp --dport "$port" -m conntrack --ctstate NEW -j RETURN
    done
    echo "[dind-firewall] containers can no longer open connections to $subnet (except tcp ports: $ALLOW_PORTS)"
  fi
  : > "$OK"
}

watch() {
  n=0
  while :; do
    apply || rm -f "$OK"
    # every second while dockerd comes up (the chain appears with it), then every 15 s in case it is flushed
    n=$((n + 1)); if [ "$n" -lt 120 ] && [ ! -f "$OK" ]; then sleep 1; else sleep 15; fi
  done
}
watch &
exec dockerd-entrypoint.sh "$@"
