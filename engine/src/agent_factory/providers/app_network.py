"""What a delivered app may reach from its pods (assessment 2026-11-15, S-02; pure).

An app is agent-written code. Its pods ran with whatever the cluster allows: the open internet,
other products' namespaces, and from the local cluster a route to the factory's own services.
The deploy providers now put one NetworkPolicy in every app namespace before the release goes
in: nothing leaves the namespace except DNS. It belongs to the engine, not to the chart, so a
change to the product's chart can't drop it.

A blueprint whose apps must call something outside says so (`app_egress: open`), deliberately,
and the policy is removed. A cluster whose network plugin doesn't enforce NetworkPolicy keeps
the object and ignores it: the policy is necessary, not proof (kind enforces it from v0.24)."""

from __future__ import annotations

import shlex

POLICY_NAME = "agent-factory-egress"


def egress_policy(namespace: str) -> str:
    """Default-deny egress for every pod in `namespace`, with DNS and the namespace itself allowed."""
    return f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {POLICY_NAME}
  namespace: {namespace}
  labels:
    app.kubernetes.io/managed-by: agent-factory
spec:
  podSelector: {{}}
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - podSelector: {{}}
"""


def egress_steps(kubectl: str, namespace: str, mode: str) -> list[str]:
    """Shell steps that make the namespace and set (or, for `open`, remove) its egress policy.
    The manifest travels on stdin; nothing here is a secret."""
    make_ns = f"{kubectl} create namespace {namespace} --dry-run=client -o yaml | {kubectl} apply -f -"
    if mode == "open":
        return [make_ns, f"{kubectl} -n {namespace} delete networkpolicy {POLICY_NAME} --ignore-not-found"]
    return [make_ns, f"printf %s {shlex.quote(egress_policy(namespace))} | {kubectl} apply -f -"]
