"""Typed loader for .agent-factory/config.yaml.

The config is the factory's public extension surface: most adopters change
YAML, templates, prompts and skills — never engine code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

PolicyMode = Literal["auto", "manual", "off"]


class FactorySection(BaseModel):
    name: str = "agent-factory"
    max_concurrent_runs: int = 1
    data_dir: str = "/data"
    public_app_base_url: str = "http://localhost"


class Models(BaseModel):
    judgment: str = "opus"
    default: str = "sonnet"
    fast: str = "haiku"

    def resolve(self, tier_or_alias: str) -> str:
        return getattr(self, tier_or_alias, tier_or_alias)


class ProductLine(BaseModel):
    description: str = ""
    template: str
    workflow_template: str = "workflow-templates/default"  # seeds this product line's workflow
    verify_command: str = "make verify"
    scan_command: str | None = None  # container-image vulnerability scan; {image} placeholder
    chart_path: str = "deploy/chart"
    environment: str = "local"  # where its delivery steps run (see `environments`)
    # Quality gate station: the generated repo must reach this agent-readiness level (1-3)
    min_readiness_level: int = Field(default=3, ge=0, le=3)
    # secret scan of the worktree (runs in dind); {path} = the run worktree
    secret_scan_command: str | None = None
    # software bill of materials for the built image; {image} and {out} (a folder) are filled in
    sbom_command: str | None = None
    service_port: int = 8000
    # what this product line builds with; an order that requires anything else is asked
    # about before any build (stack.py). Empty: no check.
    stack: list[str] = Field(default_factory=list)
    node_ports: list[int] = Field(default_factory=lambda: [30080])

    @field_validator("node_ports", mode="before")
    @classmethod
    def _port_range(cls, v: Any) -> Any:
        """Accept "30080-30099" as well as an explicit list."""
        if isinstance(v, str) and "-" in v:
            lo, hi = (int(x) for x in v.split("-", 1))
            return list(range(lo, hi + 1))
        return v


class Policy(BaseModel, extra="allow"):
    mode: PolicyMode = "manual"


class Policies(BaseModel):
    implement: Policy = Policy(mode="auto")
    deploy: Policy = Policy(mode="auto")
    publish: Policy = Policy(mode="manual")
    merge: Policy = Policy(mode="manual")
    recover: Policy = Policy(mode="manual")


class Gate(BaseModel):
    id: str
    after: str
    kind: Literal["feedback", "approval"] = "feedback"


class Budgets(BaseModel):
    max_attempts_per_station: int = 3
    max_loops_per_run: int = 6
    run_wall_clock_minutes: int = 120


class Limits(BaseModel):
    pause_at_utilization: float = 0.95


class IntegrationAuth(BaseModel):
    """How an integration authenticates. Never a credential itself: a secret
    reference (env://, file://, aws-sm://, vault://) resolved just in time, or a
    workload identity that needs no secret at all."""

    secret_ref: str | None = Field(default=None, validation_alias=AliasChoices("secret_ref", "secretRef"))
    identity: str | None = None  # e.g. "irsa": the pod's own role, no secret

    @field_validator("secret_ref")
    @classmethod
    def _is_reference(cls, v: str | None) -> str | None:
        if v is not None and "://" not in v:
            raise ValueError("secret_ref must be a reference like env://NAME or aws-sm://id#key, never a value")
        return v


class Integration(BaseModel):
    """An external system the factory delivers through. `provider` is the type
    (e.g. "local"); `settings` are non-secret options; `auth` points at the
    credential without containing it."""

    provider: str
    settings: dict[str, Any] = Field(default_factory=dict)
    auth: IntegrationAuth = Field(default_factory=IntegrationAuth)


class Environment(BaseModel):
    """Binds each delivery capability to an integration."""

    registry: str = "local"
    scan: str = "local"
    deploy: str = "local"
    publish: str = "local"


class SkillSource(BaseModel):
    """Skills installed on first start from a GitHub repo, pinned to a commit.
    Changing the pin here is how the default is reviewed (in a PR); after
    install, updates are managed in the Skills page."""

    repo: str  # owner/name
    ref: str = "main"  # followed by "check for update"
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    paths: list[str]  # skill folders (each contains SKILL.md)
    license: str | None = None


DEFAULT_EGRESS = [
    "api.anthropic.com:443",  # the model
    "pypi.org:443",  # Python packages (uv/pip)
    "files.pythonhosted.org:443",
    "dind:8081-8100",  # apps on the local cluster (acceptance calls them over HTTP)
]


class PreflightConfig(BaseModel):
    """Readiness checks (ADR-0015): run at startup, every `interval_minutes`, on
    demand, and before an order or iteration starts (results reused for `max_age_seconds`)."""

    interval_minutes: int = 15
    max_age_seconds: int = 60


class SandboxConfig(BaseModel):
    """Where agent tools and untrusted build/test commands run (ADR-0014).

    container: one throw-away container per agent session / verify command, in
               dind, on an internal network whose only exit is an allowlist proxy.
    off:       run in the factory container (development only: agents can then
               reach engine credentials and the Docker daemon)."""

    mode: Literal["container", "off"] = "container"
    image: str = "agent-factory-sandbox"
    network: str = "factory-sandbox"
    proxy: str = "factory-egress"
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 512
    egress: list[str] = Field(default_factory=lambda: list(DEFAULT_EGRESS))
    # send an allowed destination elsewhere, keeping its host name (sandbox/egress.py)
    routes: list[str] = Field(default_factory=list)


class FactoryConfig(BaseModel):
    version: int = 1
    timezone: str = "UTC"
    factory: FactorySection = FactorySection()
    models: Models = Models()
    product_lines: dict[str, ProductLine]
    policies: Policies = Policies()
    gates: list[Gate] = Field(default_factory=list)
    budgets: Budgets = Budgets()
    limits: Limits = Limits()
    skill_prompts: dict[str, str] = Field(default_factory=dict)
    default_skills: list[SkillSource] = Field(default_factory=list)
    integrations: dict[str, Integration] = Field(default_factory=dict)
    # read-only token for importing templates/skills from private GitHub repos
    skills_github_token_ref: str | None = "env://SKILLS_GITHUB_TOKEN"  # noqa: S105 - a reference, not a value
    environments: dict[str, Environment] = Field(default_factory=dict)
    sandbox: SandboxConfig = SandboxConfig()
    preflight: PreflightConfig = PreflightConfig()

    @model_validator(mode="after")
    def _local_defaults(self) -> FactoryConfig:
        # today's behaviour needs no config: a `local` integration and environment always exist
        self.integrations.setdefault(
            "local",
            Integration(provider="local", auth=IntegrationAuth(secret_ref="env://GITHUB_TOKEN")),  # noqa: S106
        )
        local = self.integrations["local"]
        if local.provider == "local" and not (local.auth.secret_ref or local.auth.identity):
            local.auth.secret_ref = "env://GITHUB_TOKEN"  # noqa: S105 - the pre-phase-2 default, as a reference
        self.environments.setdefault("local", Environment())
        return self

    def skill_overlay(self, skills: list[str]) -> str:
        parts = [
            f"Project guidance for skill `{name}`:\n{self.skill_prompts[name].strip()}"
            for name in skills
            if name in self.skill_prompts
        ]
        return "\n\n".join(parts)


def load_config(path: str | Path) -> FactoryConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return FactoryConfig.model_validate(raw)
