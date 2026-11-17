#!/usr/bin/env python3
"""App pods on a real cluster (assessment 2026-11-15, S-02; #182 security review).

Run against a kind cluster (`kind create cluster`; kindnet enforces NetworkPolicy from kind v0.24),
from the engine's environment:

    cd engine && uv run python ../scripts/cluster-security-check.py

It uses the engine's own code for everything it tests (the policy, the chart check, the read-back),
so it proves what the factory does, not a copy of it. Each check names what it shows; a control
shows the same request succeeding where the control is absent, so a "blocked" is never just a
broken probe. Exit code 0 only when every check passes.

What it covers:
  1. A pod in an app namespace with the engine's policy: no internet (by address and by name), no
     other namespace, no service on the host (where the factory lives), no Kubernetes API; DNS and
     its own namespace still work. The same requests from a namespace without the policy succeed.
  2. An adversarial chart (an allow-all egress policy, a ServiceAccount bound to cluster-admin, a
     hook): the engine's chart check refuses it. Installed anyway, bypassing the check, it really
     does open the internet and lets a pod delete the engine's policy (the threat is real). The
     engine's read-back then finds both, removes them, and the namespace is closed again.
  3. Later changes: a pod with the namespace's own service account can't change the policy (RBAC),
     before and after the attack.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from agent_factory.executor import LocalExecutor
from agent_factory.providers import app_network

IMAGE = os.environ.get("PROBE_IMAGE", "python:3.12-alpine")
APP_NS, OTHER_NS, OPEN_NS, ADV_NS = "app-probe", "probe-other", "probe-open", "app-adversary"
HOST_PORT = 18765
FAILED: list[str] = []


def sh(cmd: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, shell=True, text=True, capture_output=True, input=stdin, timeout=300)  # noqa: S602
    if check and res.returncode != 0:
        raise SystemExit(f"command failed: {cmd}\n{res.stdout}\n{res.stderr}")
    return res


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail and not ok else ''}", flush=True)
    if not ok:
        FAILED.append(name)


# ------------------------------------------------------------------ setup --
def host_address() -> str:
    """The runner as pods reach it: the kind network's gateway (where the factory's own services
    would be, for a cluster in dind)."""
    out = sh("docker network inspect kind -f '{{range .IPAM.Config}}{{.Gateway}} {{end}}'").stdout.split()
    return next(a for a in out if a.count(".") == 3)


def serve_on_host() -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"factory")

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("0.0.0.0", HOST_PORT), H)  # noqa: S104 - a stand-in for the factory, on the kind bridge
    threading.Thread(target=server.serve_forever, daemon=True).start()


def namespace(ns: str) -> None:
    sh(f"kubectl create namespace {ns} --dry-run=client -o yaml | kubectl apply -f -")


def apply_engine_policy(ns: str) -> None:
    """Exactly the steps a deploy runs (`app_network.egress_steps`)."""
    for step in app_network.egress_steps("kubectl", ns, "restricted"):
        sh(step)


def server(ns: str, name: str = "web") -> None:
    manifest = f"""
apiVersion: v1
kind: Pod
metadata: {{name: {name}, namespace: {ns}, labels: {{app: {name}}}}}
spec:
  containers:
    - name: web
      image: {IMAGE}
      command: [python, -m, http.server, "8080"]
      ports: [{{containerPort: 8080}}]
---
apiVersion: v1
kind: Service
metadata: {{name: {name}, namespace: {ns}}}
spec:
  selector: {{app: {name}}}
  ports: [{{port: 8080, targetPort: 8080}}]
"""
    sh("kubectl apply -f -", stdin=manifest)


def client(ns: str, name: str = "client", sa: str | None = None) -> None:
    account = f"serviceAccountName: {sa}\n  " if sa else ""
    manifest = f"""
apiVersion: v1
kind: Pod
metadata: {{name: {name}, namespace: {ns}}}
spec:
  {account}containers:
    - name: c
      image: {IMAGE}
      command: [sleep, "3600"]
"""
    sh("kubectl apply -f -", stdin=manifest)


def ready(ns: str) -> None:
    sh(f"kubectl -n {ns} wait --for=condition=Ready pod --all --timeout=180s")


# --------------------------------------------------------------- requests --
REACH = textwrap.dedent(
    """
    import socket, sys, urllib.request
    kind, target = sys.argv[1], sys.argv[2]
    try:
        if kind == "dns":
            socket.getaddrinfo(target, 80)
        elif kind == "tcp":
            host, port = target.rsplit(":", 1)
            socket.create_connection((host, int(port)), timeout=5).close()
        else:
            urllib.request.urlopen(target, timeout=5).read(1)
        print("REACHED")
    except Exception as exc:
        print("BLOCKED", type(exc).__name__, exc)
    """
)


def reach(ns: str, kind: str, target: str, pod: str = "client") -> bool:
    out = sh(
        f"kubectl -n {ns} exec {pod} -- python -c {shlex.quote(REACH)} {kind} {shlex.quote(target)}", check=False
    ).stdout
    return "REACHED" in out


DELETE_POLICY = textwrap.dedent(
    """
    import json, ssl, sys, urllib.request
    base = "/var/run/secrets/kubernetes.io/serviceaccount"
    token = open(base + "/token").read()
    ns, name = sys.argv[1], sys.argv[2]
    url = f"https://kubernetes.default.svc/apis/networking.k8s.io/v1/namespaces/{ns}/networkpolicies/{name}"
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": "Bearer " + token})
    try:
        urllib.request.urlopen(req, context=ssl.create_default_context(cafile=base + "/ca.crt"), timeout=8)
        print("DELETED")
    except urllib.error.HTTPError as exc:
        print("REFUSED", exc.code)
    except Exception as exc:
        print("UNREACHABLE", type(exc).__name__, exc)
    """
)


def try_delete_policy(ns: str, pod: str) -> str:
    out = sh(
        f"kubectl -n {ns} exec {pod} -- python -c {shlex.quote(DELETE_POLICY)} {ns} {app_network.POLICY_NAME}",
        check=False,
    ).stdout.strip()
    return out.split()[0] if out else "NO-ANSWER"


def has_policy(ns: str) -> bool:
    return sh(f"kubectl -n {ns} get networkpolicy {app_network.POLICY_NAME}", check=False).returncode == 0


def can_i(verb: str, ns: str, sa: str) -> bool:
    res = sh(
        f"kubectl auth can-i {verb} networkpolicies.networking.k8s.io -n {ns} --as=system:serviceaccount:{ns}:{sa}",
        check=False,
    )
    return res.stdout.strip() == "yes"


# ---------------------------------------------------------- the adversary --
ADVERSARY_CHART = {
    "Chart.yaml": "apiVersion: v2\nname: adversary\nversion: 0.1.0\n",
    "templates/app.yaml": f"""
apiVersion: v1
kind: Pod
metadata: {{name: client, namespace: {{{{ .Release.Namespace }}}}}}
spec:
  serviceAccountName: app
  containers:
    - name: c
      image: {IMAGE}
      command: [sleep, "3600"]
""",
    "templates/escape.yaml": """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-all}
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress: [{}]
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: app}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: app-admin}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: cluster-admin}
subjects: [{kind: ServiceAccount, name: app, namespace: "{{ .Release.Namespace }}"}]
""",
    "templates/hook.yaml": """
apiVersion: v1
kind: ConfigMap
metadata:
  name: hooked
  annotations: {"helm.sh/hook": pre-install}
data: {x: "1"}
""",
}


def adversary_chart(root: Path) -> Path:
    chart = root / "adversary"
    for rel, text in ADVERSARY_CHART.items():
        (chart / rel).parent.mkdir(parents=True, exist_ok=True)
        (chart / rel).write_text(text)
    return chart


async def read_back(ns: str):  # noqa: ANN201
    ex = LocalExecutor()
    ctx = SimpleNamespace(settings=SimpleNamespace(factory_mode="live"))

    async def run(cmd: str, timeout: float):  # noqa: ANN202
        return await ex.run(cmd, timeout=timeout)

    return await app_network.after_release(ctx, "kubectl", ns, run)


# ------------------------------------------------------------------- main --
def main() -> int:
    host = host_address()
    serve_on_host()
    print(f"cluster: {sh('kubectl config current-context').stdout.strip()}; host (factory stand-in): {host}")
    for ns in (APP_NS, OTHER_NS, OPEN_NS, ADV_NS):
        namespace(ns)
    apply_engine_policy(APP_NS)
    server(APP_NS)
    client(APP_NS)
    server(OTHER_NS)
    client(OPEN_NS)
    for ns in (APP_NS, OTHER_NS, OPEN_NS):
        ready(ns)
    time.sleep(3)  # let the network plugin program the policy

    print("1. a pod in an app namespace, with the engine's policy (control: the same from a namespace without it)")
    other = f"http://web.{OTHER_NS}.svc.cluster.local:8080/"
    for label, kind, target in (
        ("the internet by address (1.1.1.1:443)", "tcp", "1.1.1.1:443"),
        ("the internet by name (https://example.com)", "http", "https://example.com"),
        ("another namespace's service", "http", other),
        (f"a service on the host, where the factory lives ({host}:{HOST_PORT})", "http", f"http://{host}:{HOST_PORT}/"),
        ("the Kubernetes API", "tcp", "kubernetes.default.svc.cluster.local:443"),
    ):
        check(f"control reaches {label}", reach(OPEN_NS, kind, target))
        check(f"blocked: {label}", not reach(APP_NS, kind, target))
    check(
        "allowed: DNS (kubernetes.default.svc.cluster.local)",
        reach(APP_NS, "dns", "kubernetes.default.svc.cluster.local"),
    )
    check("allowed: DNS for an outside name (the residual channel ADR-0048 names)", reach(APP_NS, "dns", "example.com"))
    check("allowed: its own namespace's service", reach(APP_NS, "http", f"http://web.{APP_NS}.svc.cluster.local:8080/"))

    print("2. an adversarial chart: allow-all egress policy, a ServiceAccount bound to cluster-admin, a hook")
    with tempfile.TemporaryDirectory() as tmp:
        chart = adversary_chart(Path(tmp))
        rendered = sh(app_network.render_command("helm", "adversary", str(chart), ADV_NS, [])).stdout
        problems = app_network.widening(rendered, True, ADV_NS)
        check(
            "the engine's chart check refuses it (the policy and the binding, named)",
            any("allow-all" in p for p in problems) and any("app-admin" in p for p in problems),
            "; ".join(problems) or "nothing refused",
        )
        print("   installed anyway, bypassing the check, to show the threat is real:")
        apply_engine_policy(ADV_NS)
        sh(f"helm install adversary {chart} --namespace {ADV_NS} --wait --timeout 3m")
        ready(ADV_NS)
        time.sleep(3)
        check("the allow-all policy really opens the internet", reach(ADV_NS, "tcp", "1.1.1.1:443"))
        check("the binding really gives the pod the right to delete the policy", can_i("delete", ADV_NS, "app"))
        found = asyncio.run(read_back(ADV_NS))
        detail = found.detail if found else ""
        check(
            "the engine's read-back fails the deploy and names the policy and the binding",
            found is not None and not found.ok and "allow-all" in detail and "app-admin" in detail,
            found.summary if found else "nothing found",
        )
        time.sleep(3)
        check("after the read-back: the internet is blocked again", not reach(ADV_NS, "tcp", "1.1.1.1:443"))
        check("after the read-back: the pod may no longer delete the policy", not can_i("delete", ADV_NS, "app"))
        check("after the read-back: the engine's policy is still there", has_policy(ADV_NS))
        check("a clean namespace reads back clean", asyncio.run(read_back(APP_NS)) is None)

    print("3. later changes: what a pod can do to the policy with the namespace's own account")
    check("RBAC: the default account may not delete the policy", not can_i("delete", APP_NS, "default"))
    check("RBAC: nor patch it", not can_i("patch", APP_NS, "default"))
    attempt = try_delete_policy(APP_NS, "client")
    check(
        "a pod trying it gets nowhere (its policy blocks the API; RBAC would refuse it)", attempt != "DELETED", attempt
    )
    check("the engine's policy is still there", has_policy(APP_NS))
    # the RBAC refusal on its own: a pod whose namespace has no egress policy, so the API is reachable,
    # asks to delete a policy there with its namespace's default account
    sh("kubectl apply -f -", stdin=app_network.egress_policy(OPEN_NS).replace(app_network.POLICY_NAME, "target")
       .replace("policyTypes: [Egress]", "policyTypes: [Ingress]"))  # fmt: skip
    out = sh(
        f"kubectl -n {OPEN_NS} exec client -- python -c {shlex.quote(DELETE_POLICY)} {OPEN_NS} target", check=False
    ).stdout.strip()
    check("with the API reachable, RBAC refuses the delete (403)", out.startswith("REFUSED 403"), out)

    print(f"\n{'all checks passed' if not FAILED else f'{len(FAILED)} FAILED: ' + '; '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    socket.setdefaulttimeout(10)
    sys.exit(main())
