"""The Pod Security level an app's namespace enforces (assessment 2026-11-15, S-05; pure).

An app's chart is agent-written. `risk.py`'s `sec-privileged` rule reads the diff for literal strings,
so `hostPath`, `hostPID`, an added capability or a value templated through `values.yaml` pass it, and a
privileged pod on a kind node is root on a privileged dind. The cluster is the control now: every
deploy labels the app's namespace for Kubernetes' Pod Security admission, which refuses a pod the
level doesn't allow whatever the chart or its values say. The regex stays as the early warning.

The level is decided from what the chart really renders (`helm template`, hooks included):
- **restricted** when every pod meets it (the packs' charts do from fastapi-service 1.2.1 and
  nextjs-web 1.6.1);
- **baseline** when a pod meets only baseline: a product generated before those templates keeps
  deploying, its namespace still refuses privileged pods, host namespaces, host paths and added
  capabilities (the path to root on dind), and `warn`/`audit` name what restricted would refuse;
- refused before anything is installed when a pod doesn't meet baseline.

The level only goes up by itself: a namespace that enforces restricted is never lowered by a chart
(`decide`). Lowering it is a person's decision, made on the cluster, and the deploy says how.

The rules follow the Pod Security Standards (kubernetes.io/docs/concepts/security/pod-security-standards,
`latest`). The check is an early, explained refusal, not the control: if it ever passes a pod the cluster
refuses, the release fails with the cluster's reason (`warn` makes Helm print it)."""

from __future__ import annotations

import json
import shlex
from typing import Any, Literal

import yaml

Level = Literal["baseline", "restricted"]
LABEL = "pod-security.kubernetes.io"
_RANK = {"privileged": 0, "baseline": 1, "restricted": 2}
_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"}

# baseline
_BASELINE_CAPS = frozenset(
    "AUDIT_WRITE CHOWN DAC_OVERRIDE FOWNER FSETID KILL MKNOD NET_BIND_SERVICE SETFCAP SETGID SETPCAP SETUID "
    "SYS_CHROOT".split()
)
_SAFE_SYSCTLS = frozenset(
    {
        "kernel.shm_rmid_forced",
        "net.ipv4.ip_local_port_range",
        "net.ipv4.ip_unprivileged_port_start",
        "net.ipv4.tcp_syncookies",
        "net.ipv4.ping_group_range",
        "net.ipv4.ip_local_reserved_ports",
        "net.ipv4.tcp_keepalive_time",
        "net.ipv4.tcp_fin_timeout",
        "net.ipv4.tcp_keepalive_intvl",
        "net.ipv4.tcp_keepalive_probes",
    }
)
_SELINUX_TYPES = frozenset({"", "container_t", "container_init_t", "container_kvm_t", "container_engine_t"})
# restricted
_VOLUME_TYPES = frozenset(
    {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected", "secret"}
)
_SECCOMP_OK = frozenset({"RuntimeDefault", "Localhost"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec = _dict(doc.get("spec"))
    if doc.get("kind") == "Pod":
        return spec
    if doc.get("kind") == "CronJob":
        spec = _dict(_dict(spec.get("jobTemplate")).get("spec"))
    return _dict(_dict(spec.get("template")).get("spec"))


def _containers(pod: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        c
        for key in ("initContainers", "containers", "ephemeralContainers")
        for c in _list(pod.get(key))
        if isinstance(c, dict)
    ]


def _baseline(pod: dict[str, Any]) -> list[str]:
    """What in one pod spec the baseline level refuses."""
    found: list[str] = []
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(key) is True:
            found.append(f"{key}: true")
    psc = _dict(pod.get("securityContext"))
    if _dict(psc.get("windowsOptions")).get("hostProcess") is True:
        found.append("hostProcess: true")
    for vol in _list(pod.get("volumes")):
        if isinstance(vol, dict) and "hostPath" in vol:
            found.append(f"hostPath volume {vol.get('name') or '?'}")
    for sysctl in _list(psc.get("sysctls")):
        if isinstance(sysctl, dict) and sysctl.get("name") not in _SAFE_SYSCTLS:
            found.append(f"sysctl {sysctl.get('name')}")
    contexts = [("the pod", psc)] + [
        (f"container {c.get('name') or '?'}", _dict(c.get("securityContext"))) for c in _containers(pod)
    ]
    for where, sc in contexts:
        if _dict(sc.get("seccompProfile")).get("type") == "Unconfined":
            found.append(f"{where}: seccompProfile Unconfined")
        if _dict(sc.get("appArmorProfile")).get("type") == "Unconfined":
            found.append(f"{where}: appArmorProfile Unconfined")
        se = _dict(sc.get("seLinuxOptions"))
        if str(se.get("type") or "") not in _SELINUX_TYPES or se.get("user") or se.get("role"):
            found.append(f"{where}: seLinuxOptions")
    for c in _containers(pod):
        name, sc = c.get("name") or "?", _dict(c.get("securityContext"))
        if sc.get("privileged") is True:
            found.append(f"container {name}: privileged")
        if _dict(sc.get("windowsOptions")).get("hostProcess") is True:
            found.append(f"container {name}: hostProcess")
        if sc.get("procMount") not in (None, "Default"):
            found.append(f"container {name}: procMount {sc.get('procMount')}")
        added = [str(a) for a in _list(_dict(sc.get("capabilities")).get("add"))]
        if extra := sorted(set(added) - _BASELINE_CAPS):
            found.append(f"container {name}: adds {', '.join(extra)}")
        for port in _list(c.get("ports")):
            if isinstance(port, dict) and port.get("hostPort") not in (None, 0):
                found.append(f"container {name}: hostPort {port.get('hostPort')}")
    return found


def _restricted(pod: dict[str, Any]) -> list[str]:
    """What in one pod spec, on top of baseline, the restricted level refuses."""
    found: list[str] = []
    psc = _dict(pod.get("securityContext"))
    for vol in _list(pod.get("volumes")):
        if isinstance(vol, dict) and (kinds := set(vol) - {"name"}) and not kinds <= _VOLUME_TYPES:
            found.append(f"volume {vol.get('name') or '?'} is {', '.join(sorted(kinds - _VOLUME_TYPES))}")
    if psc.get("runAsUser") == 0:
        found.append("the pod: runAsUser 0")
    pod_seccomp = _dict(psc.get("seccompProfile")).get("type")
    for c in _containers(pod):
        name, sc = c.get("name") or "?", _dict(c.get("securityContext"))
        if sc.get("allowPrivilegeEscalation") is not False:
            found.append(f"container {name}: allowPrivilegeEscalation is not false")
        non_root = sc.get("runAsNonRoot", psc.get("runAsNonRoot"))
        if non_root is not True:
            found.append(f"container {name}: runAsNonRoot is not true")
        if sc.get("runAsUser") == 0:
            found.append(f"container {name}: runAsUser 0")
        if _dict(sc.get("seccompProfile")).get("type", pod_seccomp) not in _SECCOMP_OK:
            found.append(f"container {name}: seccompProfile is not RuntimeDefault")
        caps = _dict(sc.get("capabilities"))
        if "ALL" not in [str(d) for d in _list(caps.get("drop"))]:
            found.append(f"container {name}: capabilities don't drop ALL")
        if extra := sorted({str(a) for a in _list(caps.get("add"))} - {"NET_BIND_SERVICE"}):
            found.append(f"container {name}: adds {', '.join(extra)}")
    return found


def assess(rendered: str, live: bool = True) -> tuple[list[str], list[str]]:
    """(what baseline refuses, what restricted refuses on top), each `<Kind> <name>: <what>` (pure).
    Dry-run renders nothing (`live=False`); live, a render that can't be read is a baseline problem,
    so nothing goes in on a chart the engine couldn't check."""
    try:
        docs = [d for d in yaml.safe_load_all(rendered) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        return (
            ([], [])
            if not live
            else ([f"the rendered chart is not readable YAML ({str(exc).splitlines()[0][:120]})"], [])
        )
    base: list[str] = []
    strict: list[str] = []
    for doc in docs:
        if doc.get("kind") == "List":
            docs.extend(i for i in _list(doc.get("items")) if isinstance(i, dict))
            continue
        if doc.get("kind") not in _WORKLOADS:
            continue
        name = f"{doc.get('kind')} {_dict(doc.get('metadata')).get('name') or '?'}"
        pod = _pod_spec(doc)
        base += [f"{name}: {p}" for p in _baseline(pod)]
        strict += [f"{name}: {p}" for p in _restricted(pod)]
    return base, strict


def read_command(kubectl: str, namespaces: list[str]) -> str:
    """The namespaces as they are: absent ones are left out, an unreachable cluster fails."""
    names = " ".join(shlex.quote(n) for n in dict.fromkeys(namespaces))
    return f"{kubectl} get namespace {names} --ignore-not-found -o json"


def enforced(listed: str) -> str | None:
    """The highest level any of the listed namespaces enforces: '' when none does (or none exists yet),
    None when the answer can't be read (pure). A namespace with no label enforces nothing."""
    if not listed.strip():
        return ""
    try:
        doc = json.loads(listed)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    items = _list(doc.get("items")) if doc.get("kind") == "List" else [doc]
    levels = [str(_dict(_dict(i.get("metadata")).get("labels")).get(f"{LABEL}/enforce") or "") for i in items]
    known = [lv for lv in levels if lv in _RANK]
    return max(known, key=_RANK.__getitem__) if known else ""


def decide(current: str | None, base: list[str], strict: list[str]) -> tuple[Level | None, str]:
    """(the level to enforce, why not) (pure). None means refuse before anything is installed."""
    if base:
        return None, (
            "The app's pods would get privileges the factory never gives an app (Pod Security baseline):\n- "
            + "\n- ".join(base)
            + "\nRemove these from the chart (templates and values). A privileged pod, host namespaces, host "
            "paths, host ports or added capabilities are refused by the cluster whatever the chart says."
        )
    if current is None:
        return None, "the namespace's Pod Security level could not be read, so nothing was installed"
    if not strict:
        return "restricted", ""
    if current == "restricted":
        return None, (
            "The namespace enforces Pod Security restricted, and this chart's pods would no longer meet it:\n- "
            + "\n- ".join(strict)
            + "\nFix the chart: each container sets allowPrivilegeEscalation: false, runAsNonRoot: true, "
            "capabilities.drop: [ALL] and seccompProfile.type: RuntimeDefault (pod or container), and uses only "
            "configMap, secret, emptyDir, PVC, projected, downwardAPI, csi or ephemeral volumes. The factory never "
            "lowers a namespace's level; a person who decides to does it on the cluster "
            f"(`kubectl label namespace <ns> --overwrite {LABEL}/enforce=baseline`)."
        )
    return "baseline", ""


def label_step(kubectl: str, namespace: str, level: Level) -> str:
    """Enforce `level` on the namespace; warn and audit at restricted, so what it would refuse is shown."""
    labels = " ".join(
        f"{LABEL}/{mode}={value}"
        for mode, value in (
            ("enforce", level),
            ("enforce-version", "latest"),
            ("warn", "restricted"),
            ("warn-version", "latest"),
            ("audit", "restricted"),
            ("audit-version", "latest"),
        )
    )
    return f"{kubectl} label namespace {namespace} --overwrite {labels}"


def floor_step(kubectl: str, namespace: str) -> str:
    """For an app already running when the factory starts: baseline, only where the namespace enforces
    nothing yet (never lowers a level, never refuses its running pods: admission checks new pods only)."""
    current = (
        f"{kubectl} get namespace {namespace} -o jsonpath='{{.metadata.labels.pod-security\\.kubernetes\\.io/enforce}}'"
    )
    return f'[ -n "$({current})" ] || {label_step(kubectl, namespace, "baseline")}'


def baseline_note(strict: list[str]) -> str:
    """Said when a chart runs at baseline: what an upkeep change would fix to reach restricted."""
    shown = strict[:6] + ([f"… and {len(strict) - 6} more"] if len(strict) > 6 else [])
    return (
        "The app runs under Pod Security baseline, not restricted: its chart was made before the template "
        "set restricted's settings. What restricted would refuse:\n- " + "\n- ".join(shown)
    )


async def prepare(
    ctx: Any, kubectl: str, namespace: str, rendered: str, run: Any, also: tuple[str, ...] = ()
) -> tuple[str | None, str, str]:
    """Shared by the deploy providers (the one part here that acts): (the label step to run before the
    release, why the deploy is refused instead, a note for the change when it runs at baseline). `also`
    are namespaces whose level counts too (production, while a change validates in its own)."""
    live = getattr(ctx.settings, "factory_mode", "live") == "live"
    base, strict = assess(rendered, live)
    listed = await run(read_command(kubectl, [namespace, *also]), 60)
    current = (enforced(listed.whole()) if listed.ok else None) if live else ""
    level, why = decide(current, base, strict)
    if level is None:
        return None, why, ""
    return label_step(kubectl, namespace, level), "", baseline_note(strict) if level == "baseline" else ""
