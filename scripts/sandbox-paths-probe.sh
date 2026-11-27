#!/usr/bin/env bash
# What agent-written code can reach when it runs OUTSIDE the agent sandbox (assessment 2026-11-15,
# S-02): a Dockerfile's RUN step, the image start check and app pods run in dind, not on the
# sandbox's internal network. This probe stands where they stand and says what it reaches.
# Run against the running stack (`make up`), after `make sandbox-check` (it uses the sandbox image).
#
#   scripts/sandbox-paths-probe.sh           report only (exit 0)
#   PROBE_ENFORCE=1 scripts/sandbox-paths-probe.sh   fail when a path that must be closed is open
#   PROBE_RESTART=1 ...      CI only: also restart dind and check everything is closed again
#   PROBE_PROVE_RULE=1 ...   CI only: also take dind's firewall rules away and show what each is worth,
#                            that a connection opened meanwhile is cut, and that a stuck watcher is unhealthy
set -uo pipefail
IMAGE=${SANDBOX_IMAGE:-}   # default: the sandbox image the factory built in dind (repository agent-factory-sandbox)
NET=${SANDBOX_NETWORK:-factory-sandbox}
fail=0
say() { printf '  %-5s %s%s\n' "$1" "$2" "${3:+  ($3)}"; }
# want_closed NAME RESULT(open|closed) [DETAIL]
want_closed() { if [ "$2" = closed ]; then say PASS "$1"; else say OPEN "$1" "${3:-}"; fail=1; fi; }
want_open() { if [ "$2" = open ]; then say PASS "$1"; else say FAIL "$1" "${3:-}"; fail=1; fi; }
# known_open NAME RESULT: a path this probe reports but nothing closes yet (roadmap, S-02 "builds and the internet")
check() { if [ "$2" = "$3" ]; then say PASS "$1"; else say FAIL "$1" "got '$2', want '$3'"; fail=1; fi; }
known_open() { if [ "$2" = closed ]; then say PASS "$1"; else say KNOWN "$1" "open; not closed yet, see the roadmap"; fi; }
dind() { docker compose exec -T dind "$@"; }
ip_of() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q "$1")" 2>/dev/null; }
# reach WHERE-ARGS... -- URL  → open|closed, from a throw-away container inside dind
reach() {
  local args=(); while [ "$1" != -- ]; do args+=("$1"); shift; done; shift
  if dind docker run --rm ${args[@]+"${args[@]}"} "$IMAGE" curl -s -o /dev/null --max-time 6 "$1" >/dev/null 2>&1; then echo open; else echo closed; fi
}

[ -n "$IMAGE" ] || IMAGE=$(dind docker images agent-factory-sandbox --format '{{.Repository}}:{{.Tag}}' | tr -d '\r' | head -1)
# controls: a "closed" below means the request failed, so first show the probe itself works
if [ -z "$IMAGE" ] || ! dind docker run --rm --network none "$IMAGE" curl --version >/dev/null 2>&1; then
  echo "probe not usable: no sandbox image with curl in dind (run \`make sandbox-check\` first; image '$IMAGE')"; exit 2
fi
echo "probe image: $IMAGE"
FACTORY=$(ip_of factory); AUTH=$(ip_of auth); WEB=$(ip_of web)
if [ -z "$FACTORY" ] || [ -z "$AUTH" ]; then echo "probe not usable: the stack's addresses can't be read"; exit 2; fi
echo "compose addresses: factory=$FACTORY auth=$AUTH web=$WEB"
echo "dind: $(dind docker version -f 'server {{.Server.Version}}' 2>&1 | tr -d '\r'), builder: $(dind docker buildx version 2>&1 | head -1 | tr -d '\r')"

echo "a container on dind's default bridge (where a start check runs today):"
want_closed "the engine's port"            "$(reach -- "http://$FACTORY:8000/api/health")"
want_closed "the auth service"             "$(reach -- "http://$AUTH:8001/auth/me")"
want_closed "the gateway (UI) from inside" "$(reach -- "http://$WEB:8080/")"
known_open  "the open internet"            "$(reach -- https://example.com)"
want_open   "dind's own check passes: both rules really in place and their watcher running" \
  "$(dind /usr/local/bin/dind-firewall.sh check >/dev/null 2>&1 && echo open || echo closed)"

if [ "${PROBE_PROVE_RULE:-0}" = 1 ]; then
  # CI only (never on a factory in use): take the rules away and show what each is worth.
  SUBNET=$(dind sh -c "ip -4 route show dev eth0 scope link" | awk '{print $1; exit}')
  RULE="-d $SUBNET -m conntrack --ctdir ORIGINAL -j DROP"
  fw_check() { dind /usr/local/bin/dind-firewall.sh check >/dev/null 2>&1 && echo open || echo closed; }
  # a connection held open: connect, send half a request, wait, send the rest, say what came back
  LONG='import socket,sys,time
s=socket.create_connection((sys.argv[1],8000),5); s.sendall(b"GET /api/health HTTP/1.0\r\n"); print("connected",flush=True)
time.sleep(float(sys.argv[2]))
try:
    s.sendall(b"Host: probe\r\n\r\n"); s.settimeout(5); print("answered" if s.recv(16) else "closed by the engine")
except OSError as exc:
    print("cut:", type(exc).__name__)'
  WATCHER=$(dind cat /run/dind-firewall.pid | tr -d '\r')
  dind kill -STOP "$WATCHER"   # hold the watcher still, so the rules stay away while we look
  dind sh -c "iptables -t filter -D DOCKER-USER $RULE" || { say FAIL "the DOCKER-USER rule could not be lifted"; fail=1; }
  want_closed "one rule gone: dind's check fails (it reads the rules, not a marker)" "$(fw_check)"
  want_closed "one rule gone: the engine's port is still closed (the other rule holds)" "$(reach -- "http://$FACTORY:8000/api/health")"
  dind sh -c "iptables -t mangle -D FORWARD $RULE" || { say FAIL "the mangle rule could not be lifted"; fail=1; }
  want_open "both rules gone: the same container reaches the engine (the rules are what closes it)" \
    "$(reach -- "http://$FACTORY:8000/api/health")"
  ctl=$(dind docker run --rm "$IMAGE" python3 -c "$LONG" "$FACTORY" 1 2>&1 | tr -d '\r' | tail -1)
  want_open "control: a held connection is answered while the rules are away" "$([ "$ctl" = answered ] && echo open || echo closed)" "$ctl"
  dind docker rm -f probe-long >/dev/null 2>&1
  dind docker run -d --name probe-long "$IMAGE" python3 -c "$LONG" "$FACTORY" 8 >/dev/null
  for _ in $(seq 1 10); do dind docker logs probe-long 2>&1 | grep -q connected && break; sleep 1; done
  dind kill -CONT "$WATCHER"   # the watcher runs again: both rules must be back within seconds
  for _ in $(seq 1 10); do [ "$(fw_check)" = open ] && break; sleep 1; done
  want_open "the watcher put both rules back by itself" "$(fw_check)"
  want_closed "with the rules back, the engine's port is closed again" "$(reach -- "http://$FACTORY:8000/api/health")"
  dind docker wait probe-long >/dev/null 2>&1
  long=$(dind docker logs probe-long 2>&1 | tr -d '\r' | tail -1); dind docker rm -f probe-long >/dev/null 2>&1
  want_closed "a connection opened while the rules were away is cut when they return" \
    "$(case "$long" in cut:*) echo closed;; *) echo open;; esac)" "$long"
  # a watcher that is alive but not watching must not look healthy
  dind kill -STOP "$WATCHER"; sleep 12
  want_closed "a stuck watcher: dind's check fails though the rules are in place" "$(fw_check)"
  dind kill -CONT "$WATCHER"; sleep 2
  want_open "the watcher running again: dind's check passes" "$(fw_check)"
fi

echo "a Dockerfile RUN step (default build network):"
ctx=$(dind mktemp -d | tr -d '\r')
build() { # build NAME EXTRA-ARGS... ; the RUN step curls $URL
  local url="$1"; shift
  dind sh -c "printf 'FROM $IMAGE\nARG URL\nARG BUST\nRUN curl -s -o /dev/null --max-time 6 \"\$URL\"\n' > $ctx/Dockerfile"
  if dind docker build -q --build-arg "URL=$url" --build-arg "BUST=$RANDOM$RANDOM" "$@" "$ctx" >/dev/null 2>"/tmp/probe-build.$$"; then echo open; else echo closed; fi
}
if [ "$(build "file:///etc/hostname")" != open ]; then
  echo "probe not usable: a plain build fails in dind: $(tr -d '\r' < "/tmp/probe-build.$$" | tail -3 | tr '\n' ' ')"; exit 2
fi
want_closed "the engine's port"  "$(build "http://$FACTORY:8000/api/health")"
want_closed "the gateway (UI)"   "$(build "http://$WEB:8080/")"
known_open  "the open internet"  "$(build https://example.com)"

echo "the same RUN step with --network none:"
want_closed "the engine's port"  "$(build "http://$FACTORY:8000/api/health" --network none)"
want_closed "the open internet"  "$(build https://example.com --network none)"

echo "what a fix can use (facts, not verdicts):"
r=$(build "http://$FACTORY:8000/api/health" --network "$NET"); err=$(tr -d '\r' < "/tmp/probe-build.$$" | tail -2 | tr '\n' ' ')
say INFO "docker build --network $NET (the sandbox's internal network): RUN reached the engine: $r" "$err"
PROXY=$(dind docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" factory-egress 2>/dev/null | tr -d '\r')
say INFO "egress proxy on $NET: ${PROXY:-not running}"
if [ -n "$PROXY" ]; then
  r=$(build https://pypi.org/simple/ --network "$NET" --build-arg "https_proxy=http://$PROXY:3128" --build-arg "HTTPS_PROXY=http://$PROXY:3128")
  say INFO "RUN on $NET through the egress proxy reaches an allowed host (pypi.org): $r" "$(tr -d '\r' < "/tmp/probe-build.$$" | tail -2 | tr '\n' ' ')"
  r=$(build https://example.com --network "$NET" --build-arg "https_proxy=http://$PROXY:3128" --build-arg "HTTPS_PROXY=http://$PROXY:3128")
  say INFO "RUN on $NET through the egress proxy reaches a host not allowed (example.com): $r"
fi
# can a start check live on an internal network and still be probed on a published port?
dind docker network inspect probe-internal >/dev/null 2>&1 || dind docker network create --internal probe-internal >/dev/null
dind docker rm -f probe-start >/dev/null 2>&1
dind docker run -d --rm --name probe-start --network probe-internal -p 8000 --cap-drop ALL --security-opt no-new-privileges \
  --memory 1g --pids-limit 256 "$IMAGE" python3 -m http.server 8000 >/dev/null 2>&1
sleep 2
hp=$(dind docker port probe-start 8000 2>/dev/null | tr -d '\r' | head -1 | sed 's/.*://')
say INFO "start check on an --internal network: published port '${hp:-none}'; the engine reaches it at dind:$hp: $(docker compose exec -T -u factory factory curl -s -o /dev/null --max-time 5 "http://dind:${hp:-1}/" && echo yes || echo no)"
say INFO "  the same container reaches the internet: $(dind docker exec probe-start curl -s -o /dev/null --max-time 5 https://example.com && echo yes || echo no); the engine: $(dind docker exec probe-start curl -s -o /dev/null --max-time 5 "http://$FACTORY:8000/api/health" && echo yes || echo no)"
say INFO "  a sibling on the same network reaches it by name: $(dind docker run --rm --network probe-internal "$IMAGE" curl -s -o /dev/null --max-time 5 http://probe-start:8000/ && echo yes || echo no)"
dind docker rm -f probe-start >/dev/null 2>&1; dind docker network rm probe-internal >/dev/null 2>&1
say INFO "a container on $NET reaches the engine: $(reach --network "$NET" -- "http://$FACTORY:8000/api/health")"
say INFO "iptables in dind: $(dind sh -c 'iptables -S DOCKER-USER 2>&1 | head -3; iptables -t mangle -S FORWARD 2>&1 | head -4' | tr -d '\r' | tr '\n' ';')"
say INFO "dind routes: $(dind sh -c 'ip -4 route' | tr -d '\r' | tr '\n' ';')"
say INFO "dind bridges: $(dind docker network ls --format '{{.Name}}:{{.Driver}}' | tr -d '\r' | tr '\n' ' ')"

echo "the image start check, run for real with the engine's own commands (base_images.start_check):"
# an image that answers on 8000, built in dind from the sandbox image (no network needed)
dind sh -c "printf 'FROM $IMAGE\nCMD [\"python3\", \"-m\", \"http.server\", \"8000\"]\n' | docker build -q -t probe-app:1 - >/dev/null" \
  || { echo "probe not usable: the start-check image can't be built"; exit 2; }
plan=$(docker compose exec -T -u factory factory python -c "
import json; from agent_factory import base_images as b
start, probe = b.start_check('probe-start', 'probe-app:1', 8000, '/', '$IMAGE')
print(json.dumps({'start': ' && '.join(start), 'probe': probe}))" | tr -d '\r')
start_cmd=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["start"])')
probe_cmd=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["probe"])')
dind docker rm -f probe-start >/dev/null 2>&1
if dind sh -c "$start_cmd" >/dev/null 2>&1 && dind sh -c "$probe_cmd" >/dev/null 2>&1; then r=open; else r=closed; fi
want_open "the started image answers the engine's probe, asked from a sandbox-image container" "$r"
check "nothing is published on dind for it" "$(dind docker port probe-start 2>/dev/null | tr -d '\r' | wc -l | tr -d ' ')" 0
inside() { if dind docker exec probe-start python3 -c "import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5)" "$@" >/dev/null 2>&1; then echo open; else echo closed; fi; }
want_open "from the started image (control): its own port answers" "$(inside 127.0.0.1 8000)"
want_closed "from the started image: the internet (1.1.1.1:443)" "$(inside 1.1.1.1 443)"
want_closed "from the started image: the engine ($FACTORY:8000)" "$(inside "$FACTORY" 8000)"
want_closed "from the started image: the auth service" "$(inside "$AUTH" 8001)"
caps=$(dind docker inspect -f '{{.HostConfig.CapDrop}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}} {{.HostConfig.SecurityOpt}}' probe-start | tr -d '\r')
check "it runs without capabilities, bounded, with no-new-privileges" "$caps" "[ALL] 1073741824 256 [no-new-privileges]"
dind docker rm -f probe-start >/dev/null 2>&1

if [ "${PROBE_RESTART:-0}" = 1 ]; then
  echo "dind restarted (cold start of its daemon; CI only):"
  docker compose restart dind >/dev/null 2>&1
  for _ in $(seq 1 90); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q dind)")" = healthy ] && break; sleep 2
  done
  check "dind is healthy again after the restart" "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q dind)")" healthy
  ctx=$(dind mktemp -d | tr -d '\r')   # dind's /tmp went with the restart
  # controls first: a "closed" below must mean blocked, not a probe that no longer works
  want_open "after the restart (control): a container can still be run" \
    "$(dind docker run --rm --network none "$IMAGE" true >/dev/null 2>&1 && echo open || echo closed)"
  want_open "after the restart (control): a plain build still works" "$(build "file:///etc/hostname")"
  want_closed "after the restart: a container reaches the engine's port" "$(reach -- "http://$FACTORY:8000/api/health")"
  want_closed "after the restart: a RUN step reaches the engine's port" "$(build "http://$FACTORY:8000/api/health")"
fi
dind rm -rf "$ctx" >/dev/null 2>&1; rm -f "/tmp/probe-build.$$"

if [ "$fail" = 0 ]; then echo "paths around the sandbox: closed"; else echo "paths around the sandbox: OPEN (see above)"; fi
[ "${PROBE_ENFORCE:-0}" = 1 ] && exit "$fail"
exit 0
