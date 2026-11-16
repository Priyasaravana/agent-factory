"""S-02 (assessment 2026-11-15): an app's pods reach DNS and their own namespace, nothing else, unless
the blueprint says its apps must call out. The policy is the engine's, applied before every release."""

from __future__ import annotations

import yaml
from conftest import PRODUCT, wait_run
from test_cluster_target import EnvExecutor, _use_cluster_target

from agent_factory.config import Blueprint
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.providers.app_network import POLICY_NAME, egress_policy, egress_steps


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
    ex = EnvExecutor(
        outputs={"helm template": "kind: Ingress", "docker --config": "latest: digest: sha256:abc size: 1"}
    )
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
