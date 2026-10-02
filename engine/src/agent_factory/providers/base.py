"""Provider seam: where a delivery step happens, separate from what the station does.

A station (package, deploy, deliver) keeps its evidence contract and calls the
provider bound to the run's environment for each capability:

    registry  make a built image available to the deploy target (kind load, ECR push, ...)
    scan      scan an image for vulnerabilities (Trivy in dind, a scanner service, ...)
    deploy    run the image somewhere reachable and report its URLs (Helm to kind, Argo CD, ...)
    publish   put the product repo somewhere shared (GitHub via gh, a GitHub App, ...)

Providers run in the engine, deterministically. Agents never call them and never
see their credentials (see docs/design/integrations.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from agent_factory.engine.stations import StationContext

Capability = Literal["registry", "scan", "deploy", "publish"]
CAPABILITIES: tuple[Capability, ...] = ("registry", "scan", "deploy", "publish")


@dataclass
class StepResult:
    """Outcome of one provider step, with evidence the station can report."""

    ok: bool
    summary: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageRef:
    repository: str
    tag: str

    def __str__(self) -> str:
        return f"{self.repository}:{self.tag}"


@dataclass
class Readiness:
    state: Literal["ready", "degraded", "failed", "unknown"] = "unknown"
    reasons: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)  # facts later checks use (e.g. mapped app ports)


@dataclass
class CheckContext:
    """What a readiness check may use: config, settings and an executor. There is
    no run: checks must be fast (seconds), read-only and side-effect free."""

    cfg: Any
    settings: Any
    ex: Any
    secrets: Any = None  # SecretResolver; checks may use a credential, never show it

    async def cmd(self, command: str, timeout: float = 15, env: dict[str, str] | None = None) -> Any:
        return await self.ex.run(command, timeout=timeout, env=env)

    def secret(self, ref: str | None) -> str | None:
        if not ref or self.secrets is None:
            return None
        try:
            return self.secrets.resolve(ref)
        except Exception:  # noqa: BLE001 - an unavailable credential is reported by preflight
            return None


class Provider(Protocol):
    """One configured integration. `capabilities` lists what it can do; the
    station calls only the methods for the capability it was bound to."""

    name: str  # the integration id from config
    kind: str  # the provider type, e.g. "local"
    capabilities: frozenset[str]
    # set by Providers from config: IntegrationAuth (a secret *reference* or an identity);
    # resolve it with ctx.secret(ref, purpose) at the moment it is needed
    auth: Any

    async def check(self, ctx: CheckContext | None) -> Readiness: ...


class RegistryProvider(Provider, Protocol):
    def image_ref(self, ctx: StationContext, tag: str) -> ImageRef: ...
    async def push(self, ctx: StationContext, local_image: str, ref: ImageRef) -> StepResult: ...


class ScanProvider(Provider, Protocol):
    async def scan(self, ctx: StationContext, local_image: str) -> StepResult: ...


class DeployProvider(Provider, Protocol):
    uses_node_ports: bool  # True: each order needs one of the product line's node_ports

    def target(self, ctx: StationContext) -> str: ...  # human description, used in repair prompts
    def internal_url(self, ctx: StationContext) -> str: ...  # where the engine/verifier reach the app
    def public_url(self, ctx: StationContext) -> str: ...  # where a person opens it
    async def deploy(self, ctx: StationContext, image: ImageRef) -> StepResult: ...
    async def diagnostics(self, ctx: StationContext) -> str: ...
    async def undeploy(self, ctx: StationContext) -> StepResult: ...  # remove the app (archive)


class PublishProvider(Provider, Protocol):
    async def publish(self, ctx: StationContext) -> StepResult: ...


class ProviderError(Exception):
    """Invalid integration/environment configuration."""


@dataclass
class ProviderSet:
    """The providers bound to one run's environment, one per capability."""

    environment: str
    registry: RegistryProvider
    scan: ScanProvider
    deploy: DeployProvider
    publish: PublishProvider

    def describe(self) -> dict[str, str]:
        return {c: f"{getattr(self, c).name} ({getattr(self, c).kind})" for c in CAPABILITIES}
