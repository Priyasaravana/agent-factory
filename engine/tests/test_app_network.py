"""S-02 (assessment 2026-11-15): an app's pods reach DNS and their own namespace, nothing else, unless
the blueprint says its apps must call out. The policy is the engine's, applied before every release."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml
from conftest import PRODUCT, wait_run
from test_cluster_target import EnvExecutor, _use_cluster_target

from agent_factory.config import Blueprint
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.providers.app_network import (
    APP_KINDS,
    POLICY_NAME,
    after_release,
    egress_policy,
    egress_steps,
    installed_grants,
    installed_widening,
    widening,
)

ROOT = Path(__file__).resolve().parents[2]
# what `helm template` prints for an Ingress (a real render always carries apiVersion)
INGRESS = "apiVersion: networking.k8s.io/v1\nkind: Ingress\n"


def test_the_policy_denies_everything_but_dns_and_the_namespace_itself():
    policy = yaml.safe_load(egress_policy("app-kudos"))
    assert (policy["kind"], policy["metadata"]["name"], policy["metadata"]["namespace"]) == (
        "NetworkPolicy",
        POLICY_NAME,
        "app-kudos",
    )
    spec = policy["spec"]
    assert spec["podSelector"] == {} and spec["policyTypes"] == ["Egress"], "every pod; ingress is left alone"
    dns, own = spec["egress"]
    assert dns["to"][0]["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "kube-system"}
    assert dns["to"][0]["podSelector"]["matchLabels"] == {"k8s-app": "kube-dns"}
    assert sorted((p["protocol"], p["port"]) for p in dns["ports"]) == [("TCP", 53), ("UDP", 53)]
    assert own == {"to": [{"podSelector": {}}]}, "its own namespace only: no namespaceSelector, no ipBlock"
    assert "ipBlock" not in egress_policy("app-kudos"), "no address range is ever allowed"


def test_restricted_is_the_default_and_open_is_said_out_loud():
    assert Blueprint(target="repo").app_egress == "restricted"
    ns, apply = egress_steps("kubectl", "app-kudos", "restricted")
    assert ns.startswith("kubectl create namespace app-kudos ") and apply.endswith("| kubectl apply -f -")
    assert "kind: NetworkPolicy" in apply
    ns, drop = egress_steps("kubectl --context c", "app-kudos", "open")
    assert drop == f"kubectl --context c -n app-kudos delete networkpolicy {POLICY_NAME} --ignore-not-found"


async def test_the_local_cluster_gets_the_policy_before_the_release(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    policy = next(i for i, c in enumerate(ex.calls) if "kind: NetworkPolicy" in c)
    release = next(i for i, c in enumerate(ex.calls) if c.startswith("helm upgrade --install"))
    assert policy < release and "namespace: app-bookmarks-service" in ex.calls[policy]


async def test_a_cluster_target_gets_it_in_every_namespace_it_releases_into(make_factory, cfg):
    _use_cluster_target(cfg)
    ex = EnvExecutor(outputs={"helm template": INGRESS, "docker --config": "latest: digest: sha256:abc size: 1"})
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    releases = [c for c in ex.calls if "helm upgrade --install" in c]
    assert len(releases) >= 2, "the change's temporary environment, then production"
    for cmd in releases:
        ns = cmd.split("--namespace ", 1)[1].split()[0]
        assert f"namespace: {ns}\n" in cmd, f"{ns} has the policy"
        assert cmd.index("kind: NetworkPolicy") < cmd.index("upgrade --install"), "before the release"


async def test_a_blueprint_that_opens_egress_removes_the_policy(make_factory, cfg):
    for bp in cfg.blueprints.values():
        bp.app_egress = "open"
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert not any("kind: NetworkPolicy" in c for c in ex.calls)
    assert any(f"delete networkpolicy {POLICY_NAME} --ignore-not-found" in c for c in ex.calls)


# ------------------------------------------ the chart can't widen the policy --
APP = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: kudos}
spec:
  template:
    spec:
      containers: [{name: app, image: "kudos:1"}]
---
apiVersion: v1
kind: Service
metadata: {name: kudos}
---
# an ingress-only policy narrows who may call the app: fine
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: only-the-ingress}
spec:
  podSelector: {}
  policyTypes: [Ingress]
  ingress: [{from: [{namespaceSelector: {}}]}]
"""
ALLOW_ALL = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-all}
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress: [{}]
"""


def test_an_ordinary_chart_is_not_refused():
    assert widening(APP) == []
    assert widening("") == [] and widening(INGRESS) == []
    deny = ALLOW_ALL.replace("egress: [{}]", "egress: []")
    assert widening(APP + deny) == [], "a policy that only denies egress adds nothing to allow"


def test_a_chart_may_not_ship_a_policy_that_allows_egress():
    """Review of #182 (2026-11-16): Kubernetes adds policies together, so an allow-all policy in the chart
    would undo the engine's with the engine's intact."""
    [problem] = widening(APP + ALLOW_ALL)
    assert problem.startswith("NetworkPolicy allow-all allows egress")
    # the same without policyTypes (Egress is implied by the rule), to one address, and as a Helm hook
    implied = ALLOW_ALL.replace("  policyTypes: [Egress]\n", "")
    one = ALLOW_ALL.replace("egress: [{}]", "egress: [{to: [{ipBlock: {cidr: 203.0.113.7/32}}]}]")
    hook = ALLOW_ALL.replace("{name: allow-all}", '{name: allow-all, annotations: {"helm.sh/hook": pre-install}}')
    for chart in (implied, one, hook):
        assert len(widening(APP + chart)) == 1, chart
    wrapped = yaml.safe_dump({"apiVersion": "v1", "kind": "List", "items": [yaml.safe_load(ALLOW_ALL.strip("-\n"))]})
    assert len(widening(wrapped)) == 1, "inside a List too"


def test_a_chart_may_not_sidestep_the_policy_either():
    other = "apiVersion: cilium.io/v2\nkind: CiliumNetworkPolicy\nmetadata: {name: c}\nspec: {egress: [{}]}\n"
    assert "not an ordinary app object" in widening(other)[0], "another plugin's policy kind"
    host = APP.replace("    spec:\n      containers", "    spec:\n      hostNetwork: true\n      containers")
    assert "hostNetwork" in widening(host)[0], "a pod on the node's network is covered by no policy"
    assert "not readable YAML" in widening("kind: [unclosed")[0], "a render the engine can't read is refused"
    assert widening("[dry-run] ok: helm template x", live=False) == [], "dry-run renders nothing"


# Re-review of #182 (2026-11-17): a binding to a role that already exists gives the app's pods the right to
# delete the policy later, after every deploy-time check. The chart may grant no API rights at all.
SA = "apiVersion: v1\nkind: ServiceAccount\nmetadata: {name: app}\n"
BIND_ADMIN = """
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: app-admin}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: cluster-admin}
subjects: [{kind: ServiceAccount, name: app, namespace: app-kudos}]
"""


def test_a_chart_may_not_grant_its_pods_api_rights():
    assert widening(APP + SA) == [], "a ServiceAccount alone has no rights"
    [problem] = widening(APP + SA + BIND_ADMIN)
    assert problem.startswith("RoleBinding app-admin") and "grants API rights" in problem
    cluster = BIND_ADMIN.replace("kind: RoleBinding", "kind: ClusterRoleBinding")
    assert "grants API rights" in widening(cluster)[0], "cluster-wide, the broader variant"
    to_edit = BIND_ADMIN.replace("name: cluster-admin", "name: edit")
    assert len(widening(to_edit)) == 1, "a binding to any existing role, not only cluster-admin"
    harmless_role = (
        "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {name: r}\n"
        "rules: [{apiGroups: [''], resources: [configmaps], verbs: [get]}]\n"
    )
    assert len(widening(harmless_role)) == 1, "no RBAC at all: what a role grants can change with its binding"
    aggregate = "apiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\nmetadata:\n  name: x\n  labels:\n"
    aggregate += "    rbac.authorization.k8s.io/aggregate-to-edit: 'true'\n"
    aggregate += "rules: [{apiGroups: ['*'], resources: ['*'], verbs: ['*']}]\n"
    assert len(widening(aggregate)) == 1, "a ClusterRole that would add itself to `edit`"


def test_only_ordinary_app_objects_in_the_releases_own_namespace():
    for api, kind in (
        ("admissionregistration.k8s.io/v1", "MutatingWebhookConfiguration"),
        ("admissionregistration.k8s.io/v1", "ValidatingAdmissionPolicy"),
        ("apps/v1", "DaemonSet"),
        ("v1", "Namespace"),
        ("apiextensions.k8s.io/v1", "CustomResourceDefinition"),
        ("v1", "Endpoints"),
    ):
        doc = f"apiVersion: {api}\nkind: {kind}\nmetadata: {{name: x}}\n"
        assert "not an ordinary app object" in widening(doc)[0], kind
    elsewhere = APP.replace("metadata: {name: kudos}", "metadata: {name: kudos, namespace: kube-system}", 1)
    assert "is for namespace kube-system" in widening(elsewhere, namespace="app-kudos")[0]
    assert widening(APP.replace("{name: kudos}", "{name: kudos, namespace: app-kudos}", 1), namespace="app-kudos") == []
    # the shipped packs' charts stay inside the allowlist
    for chart in sorted(ROOT.glob("packs/*/template/deploy/chart/templates/*.yaml")):
        kinds = {line.split(":", 1)[1].strip() for line in chart.read_text().splitlines() if line.startswith("kind:")}
        assert kinds <= {k for _, k in APP_KINDS}, (chart, kinds)


def test_what_the_namespace_really_holds_is_read_after_the_release():
    ours = yaml.safe_load(egress_policy("app-kudos"))
    extra = yaml.safe_load(ALLOW_ALL.strip("-\n"))
    ingress_only = {"metadata": {"name": "only-the-ingress"}, "spec": {"policyTypes": ["Ingress"]}}
    binding = {"kind": "RoleBinding", "metadata": {"name": "app-admin"}, "roleRef": {"name": "cluster-admin"}}
    assert installed_widening(json.dumps({"items": [ours, binding]})) == [], "a binding is not a policy"
    crb = {"metadata": {"name": "x"}, "subjects": [{"kind": "ServiceAccount", "name": "app", "namespace": "app-kudos"}]}
    other = {"metadata": {"name": "kube"}, "subjects": [{"kind": "ServiceAccount", "namespace": "kube-system"}]}
    nothing = json.dumps({"items": []})
    assert installed_grants(json.dumps({"items": [ours]}), json.dumps({"items": [other]}), "app-kudos") == ([], [])
    assert installed_grants(
        json.dumps({"items": [ours, binding]}), json.dumps({"items": [crb, other]}), "app-kudos"
    ) == (
        ["app-admin"],
        ["x"],
    )
    assert installed_grants("", nothing, "app-kudos") is None and installed_grants(nothing, "nope", "a") is None
    assert installed_widening(json.dumps({"items": [ours, ingress_only]})) == []
    assert installed_widening(json.dumps({"items": [ours, extra]})) == ["allow-all"]
    assert installed_widening("") is None and installed_widening("{}") is None, "unreadable is not 'none'"


async def test_a_chart_with_an_allow_all_policy_is_never_installed(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs["helm template"] = APP + ALLOW_ALL
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) != ChangeStatus.awaiting_feedback, "not delivered"
    assert not any(c.startswith("helm upgrade --install") for c in ex.calls), "nothing was installed"
    said = " ".join(f"{e.message} {e.data}" for e in f.store.list_events(change.id))
    assert "the chart widens the app's egress" in said and "allow-all" in said


async def test_an_ordinary_chart_is_rendered_checked_and_delivered(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs["helm template"] = APP
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    template = next(i for i, c in enumerate(ex.calls) if c.startswith("helm template"))
    release = next(i for i, c in enumerate(ex.calls) if c.startswith("helm upgrade --install"))
    assert template < release, "rendered and checked before the release"
    assert any("get networkpolicies" in c for c in ex.calls[release:]), "and the namespace is read after it"


async def test_a_cluster_target_refuses_the_same_chart(make_factory, cfg):
    _use_cluster_target(cfg)
    outputs = {"helm template": INGRESS + ALLOW_ALL, "docker --config": "latest: digest: sha256:abc size: 1"}
    ex = EnvExecutor(outputs=outputs)
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) != ChangeStatus.awaiting_feedback
    assert not any("upgrade --install" in c for c in ex.calls), "not even into the temporary environment"


async def test_a_policy_found_only_on_the_cluster_is_removed_and_the_deploy_fails(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs["helm template"] = APP  # the render was clean: it appeared at install (e.g. `lookup`)
    held = [yaml.safe_load(egress_policy("app-kudos")), yaml.safe_load(ALLOW_ALL.strip("-\n"))]
    ex.scan_outputs["get networkpolicies"] = json.dumps({"items": held})
    live = SimpleNamespace(settings=SimpleNamespace(factory_mode="live"))

    async def run(cmd: str, timeout: float):
        return await ex.run(cmd, timeout=timeout)

    refusal = await after_release(live, "kubectl", "app-kudos", run)
    assert refusal is not None and not refusal.ok and "allow-all" in refusal.detail
    assert "kubectl -n app-kudos delete networkpolicy allow-all --ignore-not-found" in ex.calls
    # nothing extra: no refusal; an answer that can't be read: refused, never taken as "none"
    ex.scan_outputs["get networkpolicies"] = json.dumps({"items": held[:1]})
    assert await after_release(live, "kubectl", "app-kudos", run) is None
    # a binding that only appeared on the cluster: removed, the deploy fails (a pod could change the policy later)
    binding = {"kind": "RoleBinding", "metadata": {"name": "app-admin"}}
    crb = {
        "metadata": {"name": "app-cluster-admin"},
        "subjects": [{"kind": "ServiceAccount", "namespace": "app-kudos"}],
    }
    ex.scan_outputs["get networkpolicies"] = json.dumps({"items": [*held[:1], binding]})
    ex.scan_outputs["get clusterrolebindings"] = json.dumps({"items": [crb]})
    granted = await after_release(live, "kubectl", "app-kudos", run)
    assert granted is not None and "app-admin" in granted.detail and "app-cluster-admin" in granted.detail
    assert "kubectl -n app-kudos delete rolebinding app-admin --ignore-not-found" in ex.calls
    assert "kubectl delete clusterrolebinding app-cluster-admin --ignore-not-found" in ex.calls
    ex.scan_outputs["get clusterrolebindings"] = json.dumps({"items": []})
    ex.scan_outputs["get networkpolicies"] = "error: the server is unavailable"
    unread = await after_release(live, "kubectl", "app-kudos", run)
    assert unread is not None and "could not be read" in unread.summary


async def test_apps_already_running_get_the_policy_at_start_not_at_their_next_deploy(make_factory, cfg):
    """Review of #182: an upgrade must not leave delivered apps without the control."""
    ex = FakeExecutor()
    f = make_factory(ex)
    running = f.manager.create_product(PRODUCT)
    running.app_url = "http://127.0.0.1:8081"
    f.store.save_product(running)
    not_yet = f.manager.create_product(PRODUCT.model_copy(update={"title": "Not delivered yet"}))
    ex.calls.clear()
    assert await f.manager.secure_app_namespaces() == ([], []), "dry-run has no cluster"
    assert ex.calls == []

    f.manager.settings.factory_mode = "live"
    secured, failed = await f.manager.secure_app_namespaces()
    assert (secured, failed) == (["app-bookmarks-service"], [])
    [cmd] = ex.calls
    assert "kind: NetworkPolicy" in cmd and "namespace: app-bookmarks-service" in cmd
    assert f"app-{not_yet.slug}" not in cmd, "nothing to secure before a first deploy (the deploy does it)"

    # a blueprint that opens egress on purpose is left alone; a cluster that refuses is reported
    ex.calls.clear()
    ex.fail_on.append("kubectl create namespace")
    assert await f.manager.secure_app_namespaces() == ([], ["app-bookmarks-service"])
    cfg.blueprints[running.blueprint].app_egress = "open"
    ex.calls.clear()
    assert await f.manager.secure_app_namespaces() == ([], []) and ex.calls == []
