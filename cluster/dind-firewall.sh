#!/bin/sh
# dind's entrypoint (assessment 2026-11-15, S-02, fix 4; ADR-0048). Containers inside dind run
# code the factory doesn't trust: Dockerfile RUN steps, image start checks, scanners, the kind
# nodes and the app pods in them. None of them has any business on the compose network, where
# the engine, the auth service and the gateway live.
#
# The block is two rules that say the same thing, in two tables that are lost independently:
#   mangle FORWARD   put in BEFORE dockerd starts, so no container ever runs without it
#                    (Docker never touches the mangle table);
#   filter DOCKER-USER   the chain Docker keeps for the operator, once dockerd has made it.
# Each drops every packet a container sends on a connection IT opened to the compose subnet
# (conntrack direction ORIGINAL), not only the first packet: a connection that got through
# while a rule was missing is cut the moment the rule is back. Replies to connections that
# came in (the engine calling an app on a published port) are direction REPLY and pass.
# DIND_COMPOSE_ALLOW_PORTS (default 4000, the model gateway) stays reachable: the sandboxes'
# egress proxy routes model calls there (ADR-0036), each with its own virtual key.
#
# A watcher checks both rules every second and puts back what is missing. `check` (the compose
# health check) looks at the real rules and at the watcher itself, never at a marker file:
# a lost rule or a dead watcher makes dind unhealthy.
#
#   dind-firewall.sh [dockerd args]   entrypoint: rules, watcher, then dockerd
#   dind-firewall.sh check            exit 0 only if both rules are in place and the watcher runs
set -eu
ALLOW_PORTS=${DIND_COMPOSE_ALLOW_PORTS:-4000}
PIDFILE=/run/dind-firewall.pid
BEAT=/run/dind-firewall.beat          # touched by the watcher every pass
INTERVAL=1

subnet() { ip -4 route show dev eth0 scope link | awk '{print $1; exit}'; }

# every iptables backend that works here: dockerd picks nft or legacy, and the kernel runs both
backends() {
  for bin in iptables /usr/local/sbin/.iptables-legacy/iptables; do
    if command -v "$bin" >/dev/null 2>&1 && "$bin" -t mangle -nL FORWARD >/dev/null 2>&1; then echo "$bin"; fi
  done
}

drop_rule() { echo "-d $1 -m conntrack --ctdir ORIGINAL -j DROP"; }
allow_rule() { echo "-d $1 -p tcp --dport $2 -m conntrack --ctdir ORIGINAL -j RETURN"; }

# has TABLE CHAIN: the drop rule is there, in some backend
has() {
  net=$(subnet); [ -n "$net" ] || return 1
  for bin in $(backends); do
    # shellcheck disable=SC2046
    if "$bin" -t "$1" -C "$2" $(drop_rule "$net") 2>/dev/null; then return 0; fi
  done
  return 1
}

# put TABLE CHAIN: (re)build the block at the top of the chain, exceptions above the drop
put() {
  net=$(subnet); [ -n "$net" ] || { echo "[dind-firewall] no compose subnet on eth0" >&2; return 1; }
  for bin in $(backends); do
    "$bin" -t "$1" -nL "$2" >/dev/null 2>&1 || continue
    # shellcheck disable=SC2046
    while "$bin" -t "$1" -D "$2" $(drop_rule "$net") 2>/dev/null; do :; done
    # shellcheck disable=SC2046
    "$bin" -t "$1" -I "$2" 1 $(drop_rule "$net")
    for port in $ALLOW_PORTS; do
      # shellcheck disable=SC2046
      while "$bin" -t "$1" -D "$2" $(allow_rule "$net" "$port") 2>/dev/null; do :; done
      # shellcheck disable=SC2046
      "$bin" -t "$1" -I "$2" 1 $(allow_rule "$net" "$port")
    done
    return 0
  done
  return 1
}

chain_exists() { for bin in $(backends); do "$bin" -t filter -nL DOCKER-USER >/dev/null 2>&1 && return 0; done; return 1; }

if [ "${1:-}" = check ]; then
  has mangle FORWARD || { echo "dind-firewall: the mangle FORWARD rule is missing"; exit 1; }
  has filter DOCKER-USER || { echo "dind-firewall: the DOCKER-USER rule is missing"; exit 1; }
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  { [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; } || { echo "dind-firewall: the watcher is not running"; exit 1; }
  # a watcher that is alive but stuck is not watching: its last pass must be recent
  now=$(date +%s); beat=$(stat -c %Y "$BEAT" 2>/dev/null || echo 0)
  [ $((now - beat)) -le 10 ] || { echo "dind-firewall: the watcher's last pass was $((now - beat))s ago"; exit 1; }
  exit 0
fi

rm -f "$PIDFILE" "$BEAT"
# before dockerd, so before any container: without this rule dind does not start at all
put mangle FORWARD || { echo "[dind-firewall] could not install the mangle FORWARD rule; not starting dockerd" >&2; exit 1; }
echo "[dind-firewall] containers can't send on connections they open to $(subnet) (except tcp ports: $ALLOW_PORTS)"

watch() {
  while :; do
    if ! has mangle FORWARD; then
      put mangle FORWARD && echo "[dind-firewall] the mangle FORWARD rule was missing and is back" >&2
    fi
    if chain_exists && ! has filter DOCKER-USER; then
      put filter DOCKER-USER && echo "[dind-firewall] the DOCKER-USER rule is in place" >&2
    fi
    : > "$BEAT"
    sleep "$INTERVAL"
  done
}
watch &
echo $! > "$PIDFILE"
exec dockerd-entrypoint.sh "$@"
