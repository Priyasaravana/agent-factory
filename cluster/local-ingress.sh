#!/usr/bin/env bash
# The local trial of the cluster target (ADR-0026): a registry next to kind and the
# ingress-nginx controller, so apps are pushed like to a real registry and served at
# http://<app>.localtest.me:8180. Idempotent. Needs a cluster made from the current
# kind-config.yaml (`make reset-cluster` once).
set -euo pipefail
CLUSTER="${CLUSTER_NAME:-factory}"
REG=kind-registry
REG_PORT=5001
INGRESS="${INGRESS_NGINX_VERSION:-controller-v1.12.1}"

node="$(kind get nodes --name "$CLUSTER" | head -1)"
if ! docker exec "$node" grep -q 'certs.d' /etc/containerd/config.toml; then
  echo "[local-ingress] this cluster predates the registry settings: run 'make reset-cluster' first" >&2
  exit 1
fi
if ! docker port "$node" | grep -q '^80/tcp'; then
  echo "[local-ingress] this cluster does not map the ingress port: run 'make reset-cluster' first" >&2
  exit 1
fi

if [ "$(docker inspect -f '{{.State.Running}}' "$REG" 2>/dev/null || true)" != "true" ]; then
  echo "[local-ingress] starting registry $REG on dind:$REG_PORT"
  docker rm -f "$REG" >/dev/null 2>&1 || true
  docker run -d --restart=always --name "$REG" -p "0.0.0.0:$REG_PORT:5000" registry:2 >/dev/null
fi
docker network connect kind "$REG" 2>/dev/null || true

for n in $(kind get nodes --name "$CLUSTER"); do
  docker exec "$n" mkdir -p "/etc/containerd/certs.d/localhost:$REG_PORT"
  printf '[host."http://%s:5000"]\n' "$REG" | docker exec -i "$n" cp /dev/stdin "/etc/containerd/certs.d/localhost:$REG_PORT/hosts.toml"
done
echo "[local-ingress] kind pulls localhost:$REG_PORT/* from $REG"

echo "[local-ingress] installing ingress-nginx $INGRESS"
kubectl apply -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/$INGRESS/deploy/static/provider/kind/deploy.yaml" >/dev/null
kubectl -n ingress-nginx wait --for=condition=ready pod -l app.kubernetes.io/component=controller --timeout=300s
echo "[local-ingress] ready: set a product line's environment to local-ingress; apps at http://<app>.localtest.me:8180"
