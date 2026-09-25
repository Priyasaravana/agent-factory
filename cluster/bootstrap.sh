#!/usr/bin/env bash
# Idempotent: create the kind cluster inside dind and write a kubeconfig the
# factory container can use. Safe to run on every start.
set -euo pipefail
CLUSTER="${CLUSTER_NAME:-factory}"
KCFG="${KUBECONFIG:-/data/kube/config}"
mkdir -p "$(dirname "$KCFG")"

echo "[bootstrap] waiting for docker (dind)..."
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker info >/dev/null 2>&1 || { echo "[bootstrap] docker not reachable at $DOCKER_HOST"; exit 1; }

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "[bootstrap] creating kind cluster '$CLUSTER'"
  kind create cluster --name "$CLUSTER" --config "$(dirname "$0")/kind-config.yaml" --wait 180s
fi

# kind writes 0.0.0.0:6443 — point it at the dind service name instead.
kind get kubeconfig --name "$CLUSTER" | sed -E "s#server: https://[^:]+:6443#server: https://${DIND_HOST:-dind}:6443#" > "$KCFG"
kubectl get nodes -o wide
echo "[bootstrap] cluster ready"
