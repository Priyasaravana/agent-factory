"""What a delivered app may reach from its pods (assessment 2026-11-15, S-02; pure).

An app is agent-written code. Its pods ran with whatever the cluster allows: the open internet,
other products' namespaces, and from the local cluster a route to the factory's own services.
The deploy providers now put one NetworkPolicy in every app namespace before the release goes
in: nothing leaves the namespace except DNS. It belongs to the engine, not to the chart, so a
change to the product's chart can't drop it.

A blueprint whose apps must call something outside says so (`app_egress: open`), deliberately,
and the policy is removed. A cluster whose network plugin doesn't enforce NetworkPolicy keeps
the object and ignores it: the policy is necessary, not proof (kind enforces it from v0.24).

Policies add up, so the engine's policy alone is no ceiling: the chart is also refused when it
ships anything that widens or sidesteps it (`widening`, `installed_widening`)."""

from __future__ import annotations

import json
import shlex
from typing import Any

import yaml

from agent_factory.providers.base import StepResult

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


# ------------------------------------------------- the chart can't widen it --
# Kubernetes adds policies together: a second NetworkPolicy that allows egress would undo the
# engine's, even with the engine's intact (review of #182, 2026-11-16). The chart is agent-written,
# so under `restricted` it may not ship anything that widens or sidesteps the policy. Checked twice:
# on what the chart renders, before anything is installed, and on what the namespace really holds
# after the release (a template can render differently on the cluster, e.g. with `lookup`).
_POLICY_API = "networking.k8s.io"
_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet", "Pod", "ReplicationController"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _allows_egress(doc: dict[str, Any]) -> bool:
    """Whether a networking.k8s.io NetworkPolicy has any say over egress. A policy with an egress
    rule allows something; one that only lists `Egress` denies and is harmless, but nothing the
    engine's policy doesn't already do, so only real rules count."""
    return bool(_dict(doc.get("spec")).get("egress"))


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec = _dict(doc.get("spec"))
    if doc.get("kind") == "Pod":
        return spec
    if doc.get("kind") == "CronJob":
        spec = _dict(_dict(spec.get("jobTemplate")).get("spec"))
    return _dict(_dict(spec.get("template")).get("spec"))


def _touches_policies(rule: dict[str, Any]) -> bool:
    groups, resources = rule.get("apiGroups") or [], rule.get("resources") or []
    verbs = set(rule.get("verbs") or [])
    if not (verbs & {"create", "update", "patch", "delete", "deletecollection", "*"}):
        return False
    return any(g in (_POLICY_API, "*") for g in groups) and any(
        r in ("networkpolicies", "*") or str(r).startswith("networkpolicies/") for r in resources
    )


def widening(rendered: str, live: bool = True) -> list[str]:
    """What a rendered chart (`helm template`, hooks included) ships that would widen or sidestep the
    namespace's egress policy; empty when nothing does (pure). A render that can't be read is itself
    a problem: nothing is installed on a chart the engine couldn't check (`live=False`: dry-run renders
    nothing, so there is nothing to read)."""
    try:
        docs = [d for d in yaml.safe_load_all(rendered) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        if not live:
            return []
        return [f"the rendered chart is not readable YAML ({str(exc).splitlines()[0][:120]})"]
    problems: list[str] = []
    for doc in docs:
        kind = str(doc.get("kind") or "")
        meta = _dict(doc.get("metadata"))
        name = f"{kind} {meta.get('name') or '?'}"
        group = str(doc.get("apiVersion") or "").split("/")[0]
        if kind == "List" and isinstance(doc.get("items"), list):
            docs.extend(i for i in doc["items"] if isinstance(i, dict))
        elif kind.endswith("NetworkPolicy"):
            if group != _POLICY_API:
                problems.append(f"{name} ({group}): only networking.k8s.io NetworkPolicy may be in an app's chart")
            elif _allows_egress(doc):
                problems.append(f"{name} allows egress: policies add up, so it would undo the namespace's policy")
        elif kind in ("Role", "ClusterRole"):
            if any(_touches_policies(r) for r in doc.get("rules") or [] if isinstance(r, dict)):
                problems.append(f"{name} may change NetworkPolicies: the app could remove or widen the policy")
        elif kind in _WORKLOADS and _pod_spec(doc).get("hostNetwork") is True:
            problems.append(f"{name} uses hostNetwork: its pods would not be covered by any NetworkPolicy")
    return problems


def render_command(helm: str, release: str, chart: str, namespace: str, sets: list[str]) -> str:
    """`helm template` with the release's own values, hooks included (its default)."""
    flags = " ".join(f"--set {shlex.quote(x)}" for x in sets)
    return f"{helm} template {shlex.quote(release)} {shlex.quote(chart)} --namespace {namespace} {flags}".rstrip()


def list_command(kubectl: str, namespace: str) -> str:
    return f"{kubectl} -n {namespace} get networkpolicies.{_POLICY_API} -o json"


def installed_widening(policies_json: str) -> list[str] | None:
    """Names of the policies the namespace really holds, besides the engine's, that allow egress (pure).
    None: the answer can't be read."""
    try:
        items = json.loads(policies_json).get("items")
    except (ValueError, AttributeError):
        return None
    if not isinstance(items, list):
        return None
    return sorted(
        str(_dict(i.get("metadata")).get("name"))
        for i in items
        if isinstance(i, dict) and _dict(i.get("metadata")).get("name") != POLICY_NAME and _allows_egress(i)
    )


def refused(problems: list[str]) -> str:
    """What the developer is told when the chart is refused."""
    return (
        "This blueprint's apps may reach DNS and their own namespace only (app_egress: restricted), and the "
        "factory applies that policy itself. The chart must not widen or sidestep it:\n- "
        + "\n- ".join(problems)
        + "\nRemove these from the chart. An ingress-only NetworkPolicy is fine. If the app really must call "
        "something outside, that is a decision for the factory's owner (the blueprint's app_egress), not the chart."
    )


async def after_release(ctx: Any, kubectl: str, ns: str, run: Any) -> StepResult | None:
    """Shared by the deploy providers: None when the namespace holds no policy that widens egress."""
    listed = await run(list_command(kubectl, ns), 60)
    if getattr(ctx.settings, "factory_mode", "live") != "live":
        return None  # dry-run has no cluster to ask
    extra = installed_widening(listed.whole()) if listed.ok else None
    if extra is None:
        return StepResult(
            False, "deployment failed: the namespace's network policies could not be read", listed.output[-1500:]
        )
    if not extra:
        return None
    names = " ".join(shlex.quote(n) for n in extra)
    await run(f"{kubectl} -n {ns} delete networkpolicy {names} --ignore-not-found", 60)
    problems = [
        f"NetworkPolicy {n} allows egress (found in the namespace after the release, and removed)" for n in extra
    ]
    return StepResult(False, "deployment failed: the chart widens the app's egress", refused(problems))
