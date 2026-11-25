"""S-05 (assessment 2026-11-15): the cluster, not a regex, refuses pods with more than the app needs.
Every deploy labels the app's namespace for Pod Security admission at the level its rendered chart meets:
restricted for the packs' charts, baseline for a chart made before them, refused below baseline. A
namespace that enforces restricted is never lowered by a chart."""

from __future__ import annotations

import json
from types import SimpleNamespace

from conftest import PRODUCT, wait_run
from test_cluster_target import EnvExecutor, _use_cluster_target

from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.providers.pod_security import (
    LABEL,
    assess,
    decide,
    enforced,
    floor_step,
    label_step,
    prepare,
    read_command,
)
from agent_factory.risk import check_diff, parse_diff

# what the packs' charts render after this change (fastapi-service 1.2.1, nextjs-web 1.6.1), trimmed
RESTRICTED = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: kudos}
spec:
  template:
    spec:
      securityContext: {runAsNonRoot: true, runAsUser: 65532, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: web
          image: kudos:2
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
          volumeMounts: [{name: tmp, mountPath: /tmp}]
      volumes: [{name: tmp, emptyDir: {}}]
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: kudos-postgres}
spec:
  template:
    spec:
      securityContext:
        {runAsNonRoot: true, runAsUser: 70, runAsGroup: 70, fsGroup: 70, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: postgres
          image: postgres:16-alpine
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
---
apiVersion: v1
kind: Service
metadata: {name: kudos}
"""
# what they rendered before it: non-root, no escalation, but no seccomp and no drop ALL; postgres as root
BEFORE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: kudos}
spec:
  template:
    spec:
      securityContext: {runAsNonRoot: true, runAsUser: 65532}
      containers:
        - name: web
          image: kudos:1
          securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true}
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: kudos-postgres}
spec:
  template:
    spec:
      containers: [{name: postgres, image: "postgres:16-alpine"}]
"""


def pod(spec: str, kind: str = "Deployment") -> str:
    return f"apiVersion: apps/v1\nkind: {kind}\nmetadata: {{name: x}}\nspec:\n  template:\n    spec:\n{spec}"


def test_the_packs_charts_meet_restricted():
    assert assess(RESTRICTED) == ([], [])


def test_a_chart_from_before_meets_only_baseline_and_says_what_restricted_refuses():
    base, strict = assess(BEFORE)
    assert base == [], "nothing there gives a pod privileges: it keeps deploying"
    said = "\n".join(strict)
    assert "Deployment kudos: container web: seccompProfile is not RuntimeDefault" in strict
    assert "Deployment kudos: container web: capabilities don't drop ALL" in strict
    assert "StatefulSet kudos-postgres: container postgres: runAsNonRoot is not true" in said
    assert "allowPrivilegeEscalation is not false" in said


def test_everything_that_would_give_a_pod_root_on_the_node_is_refused():
    cases = {
        "privileged": "      containers: [{name: a, securityContext: {privileged: true}}]\n",
        "hostPID: true": "      hostPID: true\n      containers: [{name: a}]\n",
        "hostNetwork: true": "      hostNetwork: true\n      containers: [{name: a}]\n",
        "hostIPC: true": "      hostIPC: true\n      containers: [{name: a}]\n",
        "hostPath volume root": "      containers: [{name: a}]\n      volumes: [{name: root, hostPath: {path: /}}]\n",
        "adds SYS_ADMIN": "      containers: [{name: a, securityContext: {capabilities: {add: [SYS_ADMIN]}}}]\n",
        "hostPort 2375": "      containers: [{name: a, ports: [{containerPort: 80, hostPort: 2375}]}]\n",
        "seccompProfile Unconfined": "      securityContext: {seccompProfile: {type: Unconfined}}\n"
        "      containers: [{name: a}]\n",
        "procMount Unmasked": "      containers: [{name: a, securityContext: {procMount: Unmasked}}]\n",
        "sysctl kernel.core_pattern": "      securityContext: {sysctls: [{name: kernel.core_pattern, value: x}]}\n"
        "      containers: [{name: a}]\n",
        "seLinuxOptions": "      containers: [{name: a, securityContext: {seLinuxOptions: {type: spc_t}}}]\n",
    }
    for what, spec in cases.items():
        base, _ = assess(pod(spec))
        assert any(what in p for p in base), f"{what}: {base}"
    # init containers count too, and so do pods in Jobs, CronJobs and bare Pods (hooks render them)
    init = "      initContainers: [{name: i, securityContext: {privileged: true}}]\n      containers: [{name: a}]\n"
    assert assess(pod(init))[0] == ["Deployment x: container i: privileged"]
    cron = (
        "apiVersion: batch/v1\nkind: CronJob\nmetadata: {name: c}\nspec:\n  jobTemplate:\n    spec:\n"
        "      template:\n        spec:\n          hostPID: true\n          containers: [{name: a}]\n"
    )
    assert assess(cron)[0] == ["CronJob c: hostPID: true"]
    bare = "apiVersion: v1\nkind: Pod\nmetadata: {name: p}\nspec:\n  hostNetwork: true\n  containers: [{name: a}]\n"
    assert assess(bare)[0] == ["Pod p: hostNetwork: true"]
    listed = "apiVersion: v1\nkind: List\nitems:\n  - " + bare.replace("\n", "\n    ").rstrip()
    assert assess(listed)[0] == ["Pod p: hostNetwork: true"]


def test_baseline_allows_what_an_ordinary_app_uses():
    ordinary = (
        "      containers: [{name: a, ports: [{containerPort: 8080}], securityContext: "
        "{capabilities: {add: [NET_BIND_SERVICE, CHOWN]}}}]\n"
        "      volumes: [{name: d, persistentVolumeClaim: {claimName: d}}, {name: c, configMap: {name: c}}]\n"
    )
    assert assess(pod(ordinary))[0] == []


def test_restricted_refuses_what_baseline_lets_through():
    _, strict = assess(
        pod("      containers: [{name: a, securityContext: {capabilities: {drop: [ALL], add: [CHOWN]}}}]\n")
    )
    assert "Deployment x: container a: adds CHOWN" in strict
    _, strict = assess(pod("      securityContext: {runAsUser: 0}\n      containers: [{name: a}]\n"))
    assert "Deployment x: the pod: runAsUser 0" in strict
    _, strict = assess(pod("      containers: [{name: a}]\n      volumes: [{name: g, gitRepo: {repository: r}}]\n"))
    assert "Deployment x: volume g is gitRepo" in strict
    # a container may not undo the pod's runAsNonRoot; a container's own seccomp counts
    spec = (
        "      securityContext: {runAsNonRoot: true, seccompProfile: {type: RuntimeDefault}}\n"
        "      containers: [{name: a, securityContext: {runAsNonRoot: false, allowPrivilegeEscalation: false, "
        "capabilities: {drop: [ALL]}, seccompProfile: {type: Localhost, localhostProfile: p.json}}}]\n"
    )
    assert assess(pod(spec))[1] == ["Deployment x: container a: runAsNonRoot is not true"]


def test_a_value_templated_through_values_yaml_is_caught_where_the_diff_rule_is_not():
    """The finding itself: `risk.py` reads the diff; `privileged: {{ .Values.x }}` passes it."""
    diff = (
        "diff --git a/deploy/chart/templates/deployment.yaml b/deploy/chart/templates/deployment.yaml\n"
        "--- a/deploy/chart/templates/deployment.yaml\n+++ b/deploy/chart/templates/deployment.yaml\n"
        "@@ -1,1 +1,2 @@\n+          securityContext: {privileged: {{ .Values.debug.privileged }}}\n"
    )
    assert not [f for f in check_diff(parse_diff(diff)) if f.rule == "sec-privileged"], "the early warning misses it"
    literal = diff.replace("{{ .Values.debug.privileged }}", "true")
    assert [f for f in check_diff(parse_diff(literal)) if f.rule == "sec-privileged"], "control: it sees a literal"
    rendered = pod("      containers: [{name: a, securityContext: {privileged: true}}]\n")
    assert assess(rendered)[0] == ["Deployment x: container a: privileged"], "the render shows it"


def test_a_render_that_cant_be_read_is_refused_live_only():
    base, _ = assess("kind: [", live=True)
    assert base and "not readable YAML" in base[0]
    assert assess("kind: [", live=False) == ([], []), "dry-run renders nothing to read"


def test_the_level_only_goes_up_by_itself():
    restricted_gaps = ["Deployment x: container a: capabilities don't drop ALL"]
    assert decide("", [], []) == ("restricted", "")
    assert decide("", [], restricted_gaps) == ("baseline", "")
    assert decide("baseline", [], []) == ("restricted", ""), "a chart that meets restricted raises it"
    assert decide("privileged", [], restricted_gaps) == ("baseline", ""), "a hand-set privileged is raised"
    level, why = decide("restricted", [], restricted_gaps)
    assert level is None and "never lowers" in why and "drop ALL" in why and f"{LABEL}/enforce=baseline" in why
    level, why = decide("restricted", ["Deployment x: hostPID: true"], [])
    assert level is None and "hostPID" in why and "baseline" in why
    assert decide(None, [], [])[0] is None, "a level that can't be read installs nothing"


def test_the_level_is_read_from_the_cluster_and_an_unreadable_answer_is_not_none():
    ns = {"kind": "Namespace", "metadata": {"name": "a", "labels": {f"{LABEL}/enforce": "restricted"}}}
    bare = {"kind": "Namespace", "metadata": {"name": "b"}}
    assert enforced("") == "", "no namespace yet"
    assert enforced(json.dumps(ns)) == "restricted"
    assert enforced(json.dumps(bare)) == ""
    base = {**bare, "metadata": {"name": "c", "labels": {f"{LABEL}/enforce": "baseline"}}}
    assert enforced(json.dumps({"kind": "List", "items": [bare, base, ns]})) == "restricted", "the highest counts"
    assert enforced(json.dumps({"kind": "List", "items": [bare, base]})) == "baseline"
    assert enforced("error: the server is unavailable") is None and enforced("[]") is None
    assert read_command("kubectl", ["a", "a", "b"]) == "kubectl get namespace a b --ignore-not-found -o json"


def test_the_label_enforces_the_level_and_warns_and_audits_at_restricted():
    step = label_step("kubectl --context c", "app-kudos", "baseline")
    assert step.startswith("kubectl --context c label namespace app-kudos --overwrite ")
    for label in ("enforce=baseline", "warn=restricted", "audit=restricted", "enforce-version=latest"):
        assert f"{LABEL}/{label}" in step
    floor = floor_step("kubectl", "app-kudos")
    assert (
        floor.startswith('[ -n "$(kubectl get namespace app-kudos -o jsonpath=')
        and f"{LABEL}/enforce=baseline" in floor
    )
    assert floor.index("||") < floor.index("label namespace"), "only where it enforces nothing yet"


async def test_prepare_reads_production_too_and_refuses_a_lowering():
    ex = FakeExecutor()
    live = SimpleNamespace(settings=SimpleNamespace(factory_mode="live"))
    prod = {"kind": "Namespace", "metadata": {"name": "app-kudos", "labels": {f"{LABEL}/enforce": "restricted"}}}
    ex.scan_outputs["get namespace"] = json.dumps({"kind": "List", "items": [prod]})

    async def run(cmd: str, timeout: float):
        return await ex.run(cmd, timeout=timeout)

    label, why, _ = await prepare(live, "kubectl", "app-kudos-c1", BEFORE, run, also=("app-kudos",))
    assert label is None and "never lowers" in why, "a temporary environment can't pass what production refuses"
    assert "kubectl get namespace app-kudos-c1 app-kudos --ignore-not-found -o json" in ex.calls
    label, why, note = await prepare(live, "kubectl", "app-kudos-c1", RESTRICTED, run, also=("app-kudos",))
    assert label is not None and "enforce=restricted" in label and why == note == ""
    ex.scan_outputs["get namespace"] = ""
    label, _, note = await prepare(live, "kubectl", "app-kudos", BEFORE, run)
    assert label is not None and "enforce=baseline" in label and "baseline, not restricted" in note
    ex.fail_on.append("get namespace")
    label, why, _ = await prepare(live, "kubectl", "app-kudos", RESTRICTED, run)
    assert label is None and "could not be read" in why, "fail closed"


async def test_the_local_cluster_labels_the_namespace_before_the_release(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs["helm template"] = RESTRICTED
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    label = next(i for i, c in enumerate(ex.calls) if f"{LABEL}/enforce=restricted" in c)
    release = next(i for i, c in enumerate(ex.calls) if c.startswith("helm upgrade --install"))
    assert label < release and "label namespace app-bookmarks-service" in ex.calls[label]


async def test_a_chart_from_before_still_deploys_at_baseline_and_says_so(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs["helm template"] = BEFORE
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert any(f"{LABEL}/enforce=baseline" in c for c in ex.calls)
    notes = [e for e in f.store.list_events(change.id) if (e.data or {}).get("pod_security") == "baseline"]
    assert notes and "capabilities don't drop ALL" in notes[0].message


async def test_a_privileged_pod_is_never_installed(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs["helm template"] = pod("      containers: [{name: a, securityContext: {privileged: true}}]\n")
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) != ChangeStatus.awaiting_feedback, "not delivered"
    assert not any(c.startswith("helm upgrade --install") for c in ex.calls), "nothing was installed"
    said = " ".join(f"{e.message} {e.data}" for e in f.store.list_events(change.id))
    assert "need more than Pod Security allows" in said and "privileged" in said


async def test_a_cluster_target_labels_every_namespace_it_releases_into(make_factory, cfg):
    _use_cluster_target(cfg)
    ingress = "apiVersion: networking.k8s.io/v1\nkind: Ingress\n---\n"
    ex = EnvExecutor(
        outputs={"helm template": ingress + RESTRICTED, "docker --config": "latest: digest: sha256:abc size: 1"}
    )
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    releases = [c for c in ex.calls if "helm upgrade --install" in c]
    assert len(releases) >= 2, "the change's temporary environment, then production"
    for cmd in releases:
        ns = cmd.split("--namespace ", 1)[1].split()[0]
        assert f"label namespace {ns} --overwrite {LABEL}/enforce=restricted" in cmd
        assert cmd.index("label namespace") < cmd.index("upgrade --install"), "before the release"
