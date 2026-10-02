"""The `helm` deploy provider: Helm to any Kubernetes cluster, reached through an
ingress host per app (ADR-0026).

Each app is served at `<app>.<ingress_domain>`: no node ports, so no 20-app limit.
The product's chart must render an Ingress (the golden path ships one); a chart
without one fails deploy with that evidence, so the devops agent adds it.

settings:
    ingress_domain   apps live at <app>.<domain> (required), e.g. apps.example.com,
                     or localtest.me for the local ingress (resolves to 127.0.0.1)
    ingress_class    the cluster's IngressClass (default nginx)
    public_scheme    https (default) or http
    public_port      only if not the scheme's default (e.g. 8180 locally)
    tls              serve TLS from a per-app secret (default: scheme is https)
    cluster_issuer   cert-manager ClusterIssuer that creates that secret (optional)
    connect_via      host:port where the ENGINE reaches the ingress, when <app>.<domain>
                     does not resolve to it from the factory (local: dind:8180)
    kube_context     a context in the kubeconfig (optional)
    namespace_prefix default app-
    pull_secret_ref  secret reference to a dockerconfigjson, when the cluster needs a
                     login to pull (private GHCR …); becomes the app's imagePullSecret
auth.secret_ref      the kubeconfig (optional; default: the factory's own kubeconfig)

Acceptance runs in the sandbox, whose only exit is the egress allowlist: add
`*.<domain>:<port>` to sandbox.egress (readiness warns when it is missing), and for
the local ingress a route (sandbox.routes) to where the ingress listens.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from agent_factory.providers.base import CheckContext, ImageRef, ProviderError, Readiness, StepResult

if TYPE_CHECKING:
    from agent_factory.engine.stations import StationContext

KUBECONFIG_ENV = "AF_KUBECONFIG"
PULL_ENV = "AF_PULL_SECRET"
PULL_SECRET_NAME = "factory-pull"  # noqa: S105 - a Kubernetes object name


class HelmProvider:
    kind = "helm"
    capabilities = frozenset({"deploy"})
    uses_node_ports = False

    def __init__(self, name: str, settings: dict[str, Any] | None = None) -> None:
        self.name = name
        s = settings or {}
        self.domain = str(s.get("ingress_domain", "")).strip().strip(".").lower()
        if not self.domain:
            raise ProviderError(f"integration '{name}' (helm): settings.ingress_domain is required")
        self.ingress_class = str(s.get("ingress_class", "nginx"))
        self.scheme = str(s.get("public_scheme", "https")).lower()
        if self.scheme not in {"http", "https"}:
            raise ProviderError(f"integration '{name}' (helm): public_scheme must be http or https")
        self.port = int(s.get("public_port") or (443 if self.scheme == "https" else 80))
        self.tls = bool(s.get("tls", self.scheme == "https"))
        self.cluster_issuer = s.get("cluster_issuer")
        self.connect_via = s.get("connect_via")
        self.kube_context = s.get("kube_context")
        self.namespace_prefix = str(s.get("namespace_prefix", "app-"))
        self.pull_secret_ref = s.get("pull_secret_ref")
        self.settings = s

    # ----------------------------------------------------------- addresses --
    def host(self, ctx: StationContext) -> str:
        return f"{ctx.order.product_slug}.{self.domain}"

    def namespace(self, ctx: StationContext) -> str:
        return f"{self.namespace_prefix}{ctx.order.product_slug}"[:63]

    def _origin(self, host: str) -> str:
        default = 443 if self.scheme == "https" else 80
        return f"{self.scheme}://{host}" + ("" if self.port == default else f":{self.port}")

    def target(self, ctx: StationContext) -> str:
        return f"namespace {self.namespace(ctx)} via Helm ({self.name}), ingress host {self.host(ctx)}"

    def public_url(self, ctx: StationContext) -> str:
        return self._origin(self.host(ctx))

    def internal_url(self, ctx: StationContext) -> str:
        return self.public_url(ctx)  # the sandbox reaches it through the egress proxy

    def egress_rule(self) -> str:
        return f"*.{self.domain}:{self.port}"

    # ------------------------------------------------------------- commands --
    @property
    def _kube_ref(self) -> str | None:
        return getattr(getattr(self, "auth", None), "secret_ref", None)

    def _flags(self, tool: str) -> str:
        if not self.kube_context:
            return ""
        return (
            f" --kube-context {shlex.quote(self.kube_context)}"
            if tool == "helm"
            else (f" --context {shlex.quote(self.kube_context)}")
        )

    def _kube(self, body: str, kubeconfig: bool) -> str:
        """Wrap commands with a private kubeconfig file, removed when they end."""
        if not kubeconfig:
            return body
        return (
            f"set -e; K=$(mktemp); trap 'rm -f \"$K\"' EXIT; "
            f'printf %s "${KUBECONFIG_ENV}" > "$K"; export KUBECONFIG="$K"; {body}'
        )

    def _env(self, ctx: Any, purpose: str) -> tuple[dict[str, str], bool] | None:
        if not self._kube_ref:
            return {}, False
        value = ctx.secret(self._kube_ref, purpose) if hasattr(ctx, "run") else ctx.secret(self._kube_ref)
        return ({KUBECONFIG_ENV: value}, True) if value is not None else None

    def _curl(self, url: str) -> str:
        connect = ""
        if self.connect_via:
            host = url.split("://", 1)[1].split("/", 1)[0].split(":")[0]
            connect = f" --connect-to {host}:{self.port}:{self.connect_via}"
        return f"curl -fsS{connect} {url}"

    # ------------------------------------------------------------ readiness --
    async def check(self, ctx: CheckContext | None) -> Readiness:
        import shutil

        missing = [t for t in ("helm", "kubectl") if shutil.which(t) is None]
        if missing:
            return Readiness("failed", [f"{', '.join(missing)} not installed in the factory image"])
        if ctx is None:
            return Readiness("ready", [f"apps at <app>.{self.domain}"])
        env = self._env(ctx, "readiness")
        if env is None:
            return Readiness("failed", [f"kubeconfig {self._kube_ref} not available"])
        kenv, kfile = env
        api = await ctx.cmd(self._kube(f"kubectl{self._flags('kubectl')} get --raw=/readyz", kfile), 15, kenv)
        if not api.ok:
            return Readiness("failed", [f"cluster API not ready: {api.output.strip()[-300:]}"])
        reasons = ["cluster API ready"]
        cls = await ctx.cmd(
            self._kube(f"kubectl{self._flags('kubectl')} get ingressclass {shlex.quote(self.ingress_class)}", kfile),
            15,
            kenv,
        )
        if not cls.ok:
            return Readiness(
                "failed", [*reasons, f"IngressClass '{self.ingress_class}' not found: install an ingress controller"]
            )
        reasons.append(f"IngressClass {self.ingress_class}; apps at {self._origin('<app>.' + self.domain)}")
        from agent_factory.sandbox.egress import Allowlist

        sandbox = getattr(ctx.cfg, "sandbox", None)
        rules = list(getattr(sandbox, "egress", []) or [])
        if (
            sandbox is not None
            and getattr(sandbox, "mode", "off") != "off"
            and not Allowlist(rules).allows(f"probe.{self.domain}", self.port)
        ):
            return Readiness(
                "degraded",
                [*reasons, f"acceptance can't reach the apps: add {self.egress_rule()} to sandbox.egress"],
            )
        return Readiness("ready", reasons)

    # --------------------------------------------------------------- deploy --
    async def deploy(self, ctx: StationContext, image: ImageRef) -> StepResult:
        env = self._env(ctx, "deploy")
        if env is None:
            return StepResult(False, f"deployment failed: kubeconfig {self._kube_ref} not available")
        kenv, kfile = env
        ns, slug, host = self.namespace(ctx), ctx.order.product_slug, self.host(ctx)
        chart = ctx.product_line.chart_path
        h, k = f"helm{self._flags('helm')}", f"kubectl{self._flags('kubectl')}"
        sets = [
            f"image.repository={image.repository}",
            f"image.tag={image.tag}",
            "service.type=ClusterIP",
            "ingress.enabled=true",
            f"ingress.host={host}",
            f"ingress.className={self.ingress_class}",
            f"ingress.tls={'true' if self.tls else 'false'}",
        ]
        render = await ctx.cmd(
            f"helm template {slug} {chart} " + " ".join(f"--set {shlex.quote(x)}" for x in sets),
            timeout=120,
        )
        live = getattr(ctx.settings, "factory_mode", "live") == "live"  # dry-run renders nothing
        if not render.ok or (live and "kind: Ingress" not in render.output):
            return StepResult(
                False,
                "deployment failed: the chart has no Ingress",
                f"`helm template` of {chart} with ingress.enabled=true rendered no Ingress. The '{self.name}' "
                "target serves apps through an ingress host: add templates/ingress.yaml (values ingress.enabled, "
                "ingress.host, ingress.className, ingress.tls, ingress.annotations) and support service.type="
                f"ClusterIP and imagePullSecrets.\n{render.output[-1500:]}",
            )
        steps: list[str] = [f"{k} create namespace {ns} --dry-run=client -o yaml | {k} apply -f -"]
        if self.pull_secret_ref:
            pull = ctx.secret(str(self.pull_secret_ref), "image pull secret")
            if pull is None:
                return StepResult(False, f"deployment failed: pull secret {self.pull_secret_ref} not available")
            kenv = {**kenv, PULL_ENV: pull}
            steps.append(
                f'printf %s "${PULL_ENV}" | {k} -n {ns} create secret generic {PULL_SECRET_NAME} '
                "--type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/dev/stdin "
                f"--dry-run=client -o yaml | {k} apply -f -"
            )
            sets.append(f"imagePullSecrets[0].name={PULL_SECRET_NAME}")
        if self.cluster_issuer:
            sets.append(f"ingress.annotations.cert-manager\\.io/cluster-issuer={self.cluster_issuer}")
        steps.append(
            f"{h} upgrade --install {slug} {chart} --namespace {ns} "
            + " ".join(f"--set {shlex.quote(x)}" for x in sets)
            + " --wait --timeout 5m"
        )
        res = await ctx.cmd(self._kube("; ".join(["set -e", *steps]), kfile), timeout=420, env=kenv)
        if not res.ok:
            return StepResult(False, "deployment failed", res.output[-2500:])
        url = self.public_url(ctx)
        smoke = await ctx.cmd(
            f"for i in $(seq 1 30); do {self._curl(url + '/healthz')} && exit 0; sleep 2; done; exit 1", timeout=90
        )
        if not smoke.ok:
            return StepResult(
                False, f"deployed, but {url}/healthz did not answer through the ingress", smoke.output[-2500:]
            )
        return StepResult(True, f"deployed {image} to {ns}; {url}/healthz ok", data={"url": url})

    async def diagnostics(self, ctx: StationContext) -> str:
        env = self._env(ctx, "diagnostics")
        if env is None:
            return "kubeconfig not available"
        kenv, kfile = env
        ns, slug, k = self.namespace(ctx), ctx.order.product_slug, f"kubectl{self._flags('kubectl')}"
        diag = await ctx.cmd(
            self._kube(
                f"{k} -n {ns} get pods,svc,ingress -o wide; "
                f"{k} -n {ns} get events --sort-by=.lastTimestamp | tail -20; "
                f"{k} -n {ns} describe pods | tail -60; "
                f"{k} -n {ns} describe ingress | tail -30; "
                f"{k} -n {ns} logs -l app.kubernetes.io/instance={slug} --tail=80 --all-containers",
                kfile,
            ),
            timeout=60,
            env=kenv,
        )
        return diag.output[-3500:]

    async def undeploy(self, ctx: StationContext) -> StepResult:
        env = self._env(ctx, "archive")
        if env is None:
            return StepResult(False, f"could not archive: kubeconfig {self._kube_ref} not available")
        kenv, kfile = env
        ns, slug = self.namespace(ctx), ctx.order.product_slug
        cmd = self._kube(
            f"helm{self._flags('helm')} uninstall {slug} --namespace {ns} --ignore-not-found --wait --timeout 2m && "
            f"kubectl{self._flags('kubectl')} delete namespace {ns} --ignore-not-found --wait=false",
            kfile,
        )
        res = await ctx.cmd(cmd, timeout=180, env=kenv)
        if not res.ok:
            return StepResult(False, f"could not remove {slug} from {ns}", res.output[-2500:])
        return StepResult(True, f"removed {slug} and namespace {ns} ({self.name})")
