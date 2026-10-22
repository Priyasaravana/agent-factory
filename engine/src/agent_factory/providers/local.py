"""The `local` provider: today's laptop behaviour, unchanged.

registry  `kind load` the image into the local cluster
scan      the blueprint's `scan_command` (Trivy in dind)
deploy    Helm to kind, NodePort, curl the pack's liveness path (/healthz)
publish   `gh` with GITHUB_TOKEN from .env (moves to secret references in phase 2)
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory.executor import CommandResult
from agent_factory.models import EventKind, Product
from agent_factory.providers.base import CAPABILITIES, CheckContext, ImageRef, Readiness, StepResult

if TYPE_CHECKING:
    from agent_factory.engine.stations import StationContext


def failed_detail(cmd: str, res: CommandResult) -> str:
    return f"`{cmd}` exited {res.returncode}:\n{res.output[-4000:]}"


def namespace(product: Product) -> str:
    return f"app-{product.slug}"[:63]


class LocalProvider:
    kind = "local"
    capabilities = frozenset(CAPABILITIES)
    uses_node_ports = True

    def __init__(self, name: str = "local", settings: dict[str, Any] | None = None) -> None:
        self.name = name
        self.settings = settings or {}

    async def check(self, ctx: CheckContext | None) -> Readiness:
        """dind reachable, cluster API ready, and which app ports the cluster maps."""
        missing = [tool for tool in ("docker", "kind", "helm", "kubectl") if shutil.which(tool) is None]
        if missing:
            return Readiness("failed", [f"{', '.join(missing)} not installed in the factory image"])
        if ctx is None:
            return Readiness("ready", ["tools installed"])
        reasons: list[str] = []
        docker = await ctx.cmd("docker info --format '{{.ServerVersion}}'", timeout=15)
        if not docker.ok:
            return Readiness("failed", [f"Docker-in-Docker not reachable: {docker.output.strip()[-300:]}"])
        version = (docker.output.strip().splitlines() or ["reachable"])[-1]
        reasons.append(f"Docker-in-Docker {version}")
        api = await ctx.cmd("kubectl get --raw=/readyz", timeout=15)
        if not api.ok:
            return Readiness(
                "failed", [*reasons, f"kind cluster API not ready: {api.output.strip()[-300:]} (make reset-cluster)"]
            )
        reasons.append("kind cluster API ready")
        ports = await ctx.cmd(f"docker port {ctx.settings.cluster_name}-control-plane", timeout=15)
        mapped = sorted({int(m) for m in re.findall(r"^(\d+)/tcp", ports.output, re.M)} - {6443})
        reasons.append(f"{len(mapped)} app ports mapped to the host")
        return Readiness("ready", reasons, data={"mapped_node_ports": mapped})

    # ------------------------------------------------------------ registry --
    def image_ref(self, ctx: StationContext, tag: str) -> ImageRef:
        return ImageRef(ctx.product.slug, tag)

    async def push(self, ctx: StationContext, local_image: str, ref: ImageRef) -> StepResult:
        cmd = f"kind load docker-image {local_image} --name {ctx.settings.cluster_name}"
        res = await ctx.cmd(cmd, timeout=1500)
        if not res.ok:
            return StepResult(False, "packaging failed at: kind load", failed_detail(cmd, res))
        return StepResult(True, "loaded", data={"image": str(ref)})

    # ---------------------------------------------------------------- scan --
    async def scan(self, ctx: StationContext, local_image: str) -> StepResult:
        template = ctx.blueprint.scan_command
        if not template:
            await ctx.emit(EventKind.decision, "security scan NOT run: no scan_command configured")
            return StepResult(True, "not scanned")
        import shlex
        import tempfile

        cmd = template.format(image=local_image)
        # The report is stdout, captured whole in a private temp file (the executor keeps only the
        # tail of stdout+stderr). The Build station parses and judges it (ADR-0042); the raw report
        # is never kept, only normalised findings.
        with tempfile.TemporaryDirectory(prefix="image-scan-") as tmp:
            report_file = Path(tmp) / "report.json"
            res = await ctx.ex.run(f"{cmd} > {shlex.quote(str(report_file))}", cwd=ctx.worktree, timeout=1500)
            stdout = report_file.read_text(errors="replace") if report_file.is_file() else ""
        report = stdout if stdout.strip() else res.output  # a fake executor answers on its output
        await ctx.emit(
            EventKind.command,
            f"{'✓' if res.ok else '✗'} {cmd} (exit {res.returncode}, {len(report)} bytes of report)",
            {"returncode": res.returncode, "output": "" if stdout.strip() else res.output[-3000:]},
        )
        data = {"report": report, "returncode": res.returncode, "timed_out": res.timed_out}
        if res.timed_out:
            return StepResult(False, "packaging failed at: image scan (timed out)", failed_detail(cmd, res), data)
        return StepResult(True, "scanned", failed_detail(cmd, res) if not res.ok else "", data)

    # -------------------------------------------------------------- deploy --
    def target(self, ctx: StationContext) -> str:
        return f"namespace {namespace(ctx.product)} on the local kind cluster"

    def internal_url(self, ctx: StationContext) -> str:
        return f"http://{ctx.settings.dind_host}:{ctx.product.host_port}"

    def public_url(self, ctx: StationContext) -> str:
        return f"{ctx.cfg.factory.public_app_base_url}:{ctx.product.host_port}"

    async def deploy(self, ctx: StationContext, image: ImageRef) -> StepResult:
        ns, slug = namespace(ctx.product), ctx.product.slug
        helm = (
            f"helm upgrade --install {slug} {ctx.blueprint.chart_path} --namespace {ns} --create-namespace "
            f"--set image.repository={image.repository} --set image.tag={image.tag} "
            f"--set service.nodePort={ctx.product.node_port} --wait --timeout 5m"
        )
        res = await ctx.cmd(helm, timeout=420)
        if res.ok:
            live = f"{self.internal_url(ctx)}{ctx.blueprint.health_path}"  # the pack's liveness path
            smoke = await ctx.cmd(
                f"for i in $(seq 1 30); do curl -fsS {live} && exit 0; sleep 2; done; exit 1",
                timeout=90,
            )
            if smoke.ok:
                return StepResult(True, f"deployed {image} to {ns}; {ctx.blueprint.health_path.lstrip('/')} ok")
            res = smoke
        return StepResult(False, "deployment failed", res.output[-2500:])

    async def diagnostics(self, ctx: StationContext) -> str:
        ns, slug = namespace(ctx.product), ctx.product.slug
        diag = await ctx.cmd(
            f"kubectl -n {ns} get pods -o wide; "
            f"kubectl -n {ns} get events --sort-by=.lastTimestamp | tail -20; "
            f"kubectl -n {ns} describe pods | tail -60; "
            f"kubectl -n {ns} logs -l app.kubernetes.io/instance={slug} --tail=80 --all-containers",
            timeout=60,
        )
        return diag.output[-3500:]

    async def undeploy(self, ctx: StationContext) -> StepResult:
        ns, slug = namespace(ctx.product), ctx.product.slug
        cmd = (
            f"helm uninstall {slug} --namespace {ns} --ignore-not-found --wait --timeout 2m && "
            f"kubectl delete namespace {ns} --ignore-not-found --wait=false"
        )
        res = await ctx.cmd(cmd, timeout=180)
        if not res.ok:
            return StepResult(False, f"could not remove {slug} from {ns}", failed_detail(cmd, res))
        return StepResult(True, f"removed {slug} and namespace {ns} from the local cluster")

    # ------------------------------------------------------------- publish --
    async def publish(self, ctx: StationContext) -> StepResult:
        ws = ctx.ws
        repo = ws.product_dir(ctx.product.slug)
        ref = getattr(getattr(self, "auth", None), "secret_ref", None) or "env://GITHUB_TOKEN"
        token = ctx.secret(ref, "publish", "open-pr")
        if not token:
            await ctx.emit(EventKind.decision, f"publish NOT done: no GitHub token ({ref} not available)")
            return StepResult(True, f"NOT pushed ({ref} not available)")
        pub = ctx.cfg.policies.publish
        owner = self.settings.get("owner", getattr(pub, "owner", "")) or ""
        visibility = self.settings.get("visibility", getattr(pub, "visibility", "private"))
        env = {"GH_TOKEN": token}
        name = f"{owner}/{ctx.product.slug}" if owner else ctx.product.slug
        if not (await ws.git("remote get-url origin", repo)).ok:
            vis = "--private" if visibility == "private" else "--public"
            c = await ctx.cmd(  # not safe to repeat: a create that timed out may have created it (ADR-0043)
                f"gh repo create {name} {vis} --source . --remote origin", env=env, cwd=repo, timeout=120, retry=False
            )
            if not c.ok:
                return StepResult(False, "GitHub repo creation failed", c.output)
        p = await ctx.cmd(
            "gh auth setup-git && git push -u origin main --tags --force-with-lease",
            env={**env, **ws.git_env_for(repo)},
            cwd=repo,
            timeout=300,
        )
        if not p.ok:
            return StepResult(False, "push to GitHub failed", p.output)
        url = await ctx.cmd("gh repo view --json url -q .url", env=env, cwd=repo, timeout=60)
        lines = url.output.strip().splitlines() if url.ok else []
        repo_url = next((ln.strip() for ln in lines if ln.strip().startswith("https://")), None)
        return StepResult(True, f"pushed to {repo_url or name}", data={"repo_url": repo_url})
