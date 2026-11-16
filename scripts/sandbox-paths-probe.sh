#!/usr/bin/env bash
# What agent-written code can reach when it runs OUTSIDE the agent sandbox (assessment 2026-11-15,
# S-02): a Dockerfile's RUN step, the image start check and app pods run in dind, not on the
# sandbox's internal network. This probe stands where they stand and says what it reaches.
# Run against the running stack (`make up`), after `make sandbox-check` (it uses the sandbox image).
#
#   scripts/sandbox-paths-probe.sh           report only (exit 0)
#   PROBE_ENFORCE=1 scripts/sandbox-paths-probe.sh   fail when a path that must be closed is open
#   PROBE_PROVE_RULE=1 ...   CI only: also lift dind's firewall rule for a moment and show the path opens
set -uo pipefail
IMAGE=${SANDBOX_IMAGE:-}   # default: the sandbox image the factory built in dind (repository agent-factory-sandbox)
NET=${SANDBOX_NETWORK:-factory-sandbox}
fail=0
say() { printf '  %-5s %s%s\n' "$1" "$2" "${3:+  ($3)}"; }
# want_closed NAME RESULT(open|closed) [DETAIL]
want_closed() { if [ "$2" = closed ]; then say PASS "$1"; else say OPEN "$1" "${3:-}"; fail=1; fi; }
want_open() { if [ "$2" = open ]; then say PASS "$1"; else say FAIL "$1" "${3:-}"; fail=1; fi; }
# known_open NAME RESULT: a path this probe reports but nothing closes yet (roadmap, S-02 "builds and the internet")
known_open() { if [ "$2" = closed ]; then say PASS "$1"; else say KNOWN "$1" "open; not closed yet, see the roadmap"; fi; }
dind() { docker compose exec -T dind "$@"; }
ip_of() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q "$1")" 2>/dev/null; }
# reach WHERE-ARGS... -- URL  → open|closed, from a throw-away container inside dind
reach() {
  local args=(); while [ "$1" != -- ]; do args+=("$1"); shift; done; shift
  if dind docker run --rm "${args[@]}" "$IMAGE" curl -s -o /dev/null --max-time 6 "$1" >/dev/null 2>&1; then echo open; else echo closed; fi
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
want_open   "the firewall rule is in place (dind is healthy only with it)" \
  "$(dind test -f /run/dind-firewall.ok && echo open || echo closed)"

if [ "${PROBE_PROVE_RULE:-0}" = 1 ]; then
  # CI only: lift the rule for a moment to show it is what closes the path (never on a factory in use)
  SUBNET=$(dind sh -c "ip -4 route show dev eth0 scope link" | awk '{print $1; exit}')
  rule="DOCKER-USER -d $SUBNET -m conntrack --ctstate NEW -j DROP"
  if dind iptables -D $rule 2>/dev/null; then
    want_open "without the rule the same container reaches the engine (the rule is what closes it)" \
      "$(reach -- "http://$FACTORY:8000/api/health")"
    dind sh -c "iptables -A $rule" || { echo "could not put the rule back"; exit 2; }
    want_closed "with the rule back, the engine's port" "$(reach -- "http://$FACTORY:8000/api/health")"
  else
    say FAIL "the rule could not be lifted for the proof"; fail=1
  fi
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
say INFO "iptables in dind: $(dind sh -c 'iptables -S DOCKER-USER 2>&1 | head -3' | tr -d '\r' | tr '\n' ';')"
say INFO "dind routes: $(dind sh -c 'ip -4 route' | tr -d '\r' | tr '\n' ';')"
say INFO "dind bridges: $(dind docker network ls --format '{{.Name}}:{{.Driver}}' | tr -d '\r' | tr '\n' ' ')"
dind rm -rf "$ctx" >/dev/null 2>&1; rm -f "/tmp/probe-build.$$"

if [ "$fail" = 0 ]; then echo "paths around the sandbox: closed"; else echo "paths around the sandbox: OPEN (see above)"; fi
[ "${PROBE_ENFORCE:-0}" = 1 ] && exit "$fail"
exit 0
