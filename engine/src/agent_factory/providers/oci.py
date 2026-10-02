"""The `oci` registry provider: push images to any OCI registry (ADR-0026).

GHCR, Docker Hub, ECR (with a token), Harbor, or a local registry container. The
image is built in dind as before; this provider tags and pushes it.

settings:
    repository   where images go, without the app name: ghcr.io/acme/factory,
                 localhost:5001 (the local registry next to kind) …
    username     registry user (with auth.secret_ref holding the password/token)
    insecure     plain HTTP registry (the local one); default false
    check_host   where the readiness check reaches the registry, if not the same address
                 (local: dind:5001, because the push happens inside dind)

The password is resolved just in time and given to `docker login` on stdin from an
environment variable, with a throw-away Docker config directory: it never appears
in a command line, an event or a file that outlives the step.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from agent_factory.providers.base import CheckContext, ImageRef, Readiness, StepResult

if TYPE_CHECKING:
    from agent_factory.engine.stations import StationContext

PASSWORD_ENV = "AF_REGISTRY_PASSWORD"  # noqa: S105 - the name of a variable, not a value


def failed(cmd: str, output: str) -> str:
    return f"`{cmd}` failed:\n{output[-4000:]}"


class OciRegistryProvider:
    kind = "oci"
    capabilities = frozenset({"registry"})

    def __init__(self, name: str, settings: dict[str, Any] | None = None) -> None:
        self.name = name
        self.settings = settings or {}
        repo = str(self.settings.get("repository", "")).strip().rstrip("/")
        if not repo:
            from agent_factory.providers.base import ProviderError

            raise ProviderError(f"integration '{name}' (oci): settings.repository is required")
        self.repository = repo
        self.host = repo.split("/", 1)[0]
        self.username = str(self.settings.get("username", "") or "")
        self.insecure = bool(self.settings.get("insecure", False))
        self.check_host = str(self.settings.get("check_host", "") or self.host)

    @property
    def _ref(self) -> str | None:
        return getattr(getattr(self, "auth", None), "secret_ref", None)

    def _script(self, body: str, logged_in: bool) -> str:
        """A shell script with a private Docker config, removed when it ends."""
        login = (
            f'printf %s "${PASSWORD_ENV}" | docker --config "$D" login {shlex.quote(self.host)} '
            f"-u {shlex.quote(self.username)} --password-stdin >/dev/null; "
            if logged_in
            else ""
        )
        return f"set -e; D=$(mktemp -d); trap 'rm -rf \"$D\"' EXIT; {login}{body}"

    # ------------------------------------------------------------ readiness --
    async def check(self, ctx: CheckContext | None) -> Readiness:
        if ctx is None:
            return Readiness("ready", [f"pushes to {self.repository}"])
        scheme = "http" if self.insecure else "https"
        probe = await ctx.cmd(f"curl -s -o /dev/null -w '%{{http_code}}' {scheme}://{self.check_host}/v2/", timeout=15)
        code = (probe.output.strip().splitlines() or [""])[-1]
        if code not in {"200", "401"}:
            return Readiness("failed", [f"registry {self.host} not reachable ({code or probe.output[-200:]})"])
        reasons = [f"registry {self.host} reachable", f"images go to {self.repository}/<app>"]
        if self._ref and self.username:
            password = ctx.secret(self._ref)
            if password is None:
                return Readiness("degraded", [*reasons, f"credential {self._ref} not available"])
            login = await ctx.cmd(self._script("true", logged_in=True), timeout=30, env={PASSWORD_ENV: password})
            if not login.ok:
                return Readiness("failed", [*reasons, f"login as {self.username} refused: {login.output[-300:]}"])
            reasons.append(f"login as {self.username} works")
        elif code == "401":
            return Readiness("failed", [*reasons, "the registry needs a login: set settings.username and a secret_ref"])
        return Readiness("ready", reasons)

    # -------------------------------------------------------------- registry --
    def image_ref(self, ctx: StationContext, tag: str) -> ImageRef:
        return ImageRef(f"{self.repository}/{ctx.order.product_slug}", tag)

    async def push(self, ctx: StationContext, local_image: str, ref: ImageRef) -> StepResult:
        env: dict[str, str] = {}
        logged_in = False
        if self._ref and self.username:
            password = ctx.secret(self._ref, f"push to {self.host}")
            if password is None:
                return StepResult(False, f"push failed: credential {self._ref} not available")
            env, logged_in = {PASSWORD_ENV: password}, True
        body = f'docker tag {local_image} {ref}; docker --config "$D" push {ref}'
        res = await ctx.cmd(self._script(body, logged_in), timeout=1500, env=env)
        if not res.ok:
            return StepResult(
                False, f"packaging failed at: push to {self.host}", failed(f"docker push {ref}", res.output)
            )
        digest = next((ln.split("digest: ")[1].split()[0] for ln in res.output.splitlines() if "digest: " in ln), None)
        return StepResult(True, f"pushed to {self.host}", data={"image": str(ref), "digest": digest})
