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
# engine's, even with the engine's intact (review of #182, 2026-11-16). And a chart that grants its
# own pods rights (a RoleBinding to `cluster-admin`, say) lets a pod delete the policy later, after
# every deploy-time check (re-review, 2026-11-17). So under `restricted` the chart is held to an
# **allowlist**: ordinary app objects in the release's own namespace, nothing else. RBAC, bindings,
# webhooks and anything cluster-wide are refused by kind, whatever they contain. Checked on what the
# chart renders, before anything is installed, and on what the namespace really holds after the
# release (a template can render differently on the cluster, e.g. with `lookup`).
_POLICY_API = "networking.k8s.io"
_RBAC_API = "rbac.authorization.k8s.io"
# (API group, kind) an app's chart may render. Pods never get API rights from the chart: with no
# binding, a ServiceAccount here has none.
APP_KINDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("", "Service"),
        ("", "ConfigMap"),
        ("", "Secret"),
        ("", "ServiceAccount"),
        ("", "PersistentVolumeClaim"),
        ("", "Pod"),
        ("apps", "Deployment"),
        ("apps", "StatefulSet"),
        ("batch", "Job"),
        ("batch", "CronJob"),
        (_POLICY_API, "Ingress"),
        (_POLICY_API, "NetworkPolicy"),  # and then only without egress rules
        ("autoscaling", "HorizontalPodAutoscaler"),
        ("policy", "PodDisruptionBudget"),
    }
)
_WORKLOADS = {"Deployment", "StatefulSet", "Job", "CronJob", "Pod"}


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


def widening(rendered: str, live: bool = True, namespace: str | None = None) -> list[str]:
    """What a rendered chart (`helm template`, hooks included) ships beyond an ordinary app in its own
    namespace; empty when nothing (pure). A render that can't be read is itself a problem: nothing is
    installed on a chart the engine couldn't check (`live=False`: dry-run renders nothing)."""
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
        api = str(doc.get("apiVersion") or "")
        group = api.split("/")[0] if "/" in api else ""
        if kind == "List" and isinstance(doc.get("items"), list):
            docs.extend(i for i in doc["items"] if isinstance(i, dict))
            continue
        if (group, kind) not in APP_KINDS:
            why = "grants API rights" if group == _RBAC_API else "is not an ordinary app object"
            problems.append(f"{name} ({api or 'no apiVersion'}) {why}: an app's chart may not ship it")
            continue
        theirs = meta.get("namespace")
        if theirs and namespace and theirs != namespace:
            problems.append(f"{name} is for namespace {theirs}: an app's chart stays in its own ({namespace})")
        if kind == "NetworkPolicy" and _allows_egress(doc):
            problems.append(f"{name} allows egress: policies add up, so it would undo the namespace's policy")
        if kind in _WORKLOADS and _pod_spec(doc).get("hostNetwork") is True:
            problems.append(f"{name} uses hostNetwork: its pods would not be covered by any NetworkPolicy")
    return problems


def render_command(helm: str, release: str, chart: str, namespace: str, sets: list[str]) -> str:
    """`helm template` with the release's own values, hooks included (its default)."""
    flags = " ".join(f"--set {shlex.quote(x)}" for x in sets)
    return f"{helm} template {shlex.quote(release)} {shlex.quote(chart)} --namespace {namespace} {flags}".rstrip()


def list_command(kubectl: str, namespace: str) -> str:
    return f"{kubectl} -n {namespace} get networkpolicies.{_POLICY_API},rolebindings.{_RBAC_API} -o json"


def cluster_bindings_command(kubectl: str) -> str:
    return f"{kubectl} get clusterrolebindings.{_RBAC_API} -o json"


def _items(listed: str) -> list[dict[str, Any]] | None:
    try:
        items = json.loads(listed).get("items")
    except (ValueError, AttributeError):
        return None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else None


def installed_widening(policies_json: str) -> list[str] | None:
    """Names of the policies the namespace really holds, besides the engine's, that allow egress (pure).
    None: the answer can't be read."""
    items = _items(policies_json)
    if items is None:
        return None
    return sorted(
        str(_dict(i.get("metadata")).get("name"))
        for i in items
        if i.get("kind", "NetworkPolicy") == "NetworkPolicy"
        and _dict(i.get("metadata")).get("name") != POLICY_NAME
        and _allows_egress(i)
    )


def installed_grants(namespace_json: str, cluster_json: str, namespace: str) -> tuple[list[str], list[str]] | None:
    """(RoleBindings in the namespace, ClusterRoleBindings with a subject in it) (pure). The factory makes
    none, so any is the app's: a way for its pods to change the policy later. None: an answer can't be read."""
    here, cluster = _items(namespace_json), _items(cluster_json)
    if here is None or cluster is None:
        return None
    role_bindings = sorted(str(_dict(i.get("metadata")).get("name")) for i in here if i.get("kind") == "RoleBinding")
    cluster_bindings = sorted(
        str(_dict(b.get("metadata")).get("name"))
        for b in cluster
        if any(_dict(s).get("namespace") == namespace for s in b.get("subjects") or [])
    )
    return role_bindings, cluster_bindings


def refused(problems: list[str]) -> str:
    """What the developer is told when the chart is refused."""
    return (
        "This blueprint's apps may reach DNS and their own namespace only (app_egress: restricted), and the "
        "factory applies that policy itself. The chart must not widen or sidestep it, and may ship only "
        "ordinary app objects in its own namespace (no RBAC, bindings, webhooks or cluster-wide objects):\n- "
        + "\n- ".join(problems)
        + "\nRemove these from the chart. An ingress-only NetworkPolicy is fine. If the app really must call "
        "something outside, that is a decision for the factory's owner (the blueprint's app_egress), not the chart."
    )


async def after_release(ctx: Any, kubectl: str, ns: str, run: Any) -> StepResult | None:
    """Shared by the deploy providers: None when the namespace holds nothing that widens egress or grants
    its pods rights. Anything found is removed and the deploy fails; an answer that can't be read fails it."""
    listed = await run(list_command(kubectl, ns), 60)
    bindings = await run(cluster_bindings_command(kubectl), 60)
    if getattr(ctx.settings, "factory_mode", "live") != "live":
        return None  # dry-run has no cluster to ask
    extra = installed_widening(listed.whole()) if listed.ok else None
    grants = installed_grants(listed.whole(), bindings.whole(), ns) if listed.ok and bindings.ok else None
    if extra is None or grants is None:
        unread = (listed if not listed.ok or extra is None else bindings).output[-1500:]
        return StepResult(
            False, "deployment failed: the namespace's network policies and bindings could not be read", unread
        )
    role_bindings, cluster_bindings = grants
    if not (extra or role_bindings or cluster_bindings):
        return None
    problems: list[str] = []
    if extra:
        await run(f"{kubectl} -n {ns} delete networkpolicy {' '.join(map(shlex.quote, extra))} --ignore-not-found", 60)
        problems += [f"NetworkPolicy {n} allows egress (found after the release, and removed)" for n in extra]
    if role_bindings:
        names = " ".join(map(shlex.quote, role_bindings))
        await run(f"{kubectl} -n {ns} delete rolebinding {names} --ignore-not-found", 60)
        problems += [
            f"RoleBinding {n} grants the app's pods API rights (found after the release, and removed)"
            for n in role_bindings
        ]
    if cluster_bindings:
        names = " ".join(map(shlex.quote, cluster_bindings))
        await run(f"{kubectl} delete clusterrolebinding {names} --ignore-not-found", 60)
        problems += [
            f"ClusterRoleBinding {n} grants the app's pods cluster rights (found after the release, and removed)"
            for n in cluster_bindings
        ]
    return StepResult(False, "deployment failed: the chart widens the app's egress", refused(problems))
