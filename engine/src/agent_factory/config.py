"""Typed loader for .agent-factory/config.yaml.

The config is the factory's public extension surface: most adopters change
YAML, templates, prompts and skills — never engine code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, PrivateAttr, field_validator, model_validator

if TYPE_CHECKING:
    from agent_factory.packs import PackBinding
    from agent_factory.readiness import StackRules

PolicyMode = Literal["auto", "manual", "off"]


class FactorySection(BaseModel):
    name: str = "agent-factory"
    max_concurrent_changes: int = Field(
        default=1, validation_alias=AliasChoices("max_concurrent_changes", "max_concurrent_runs")
    )
    data_dir: str = "/data"
    public_app_base_url: str = "http://localhost"


class Models(BaseModel):
    judgment: str = "opus"
    default: str = "sonnet"
    fast: str = "haiku"

    def resolve(self, tier_or_alias: str) -> str:
        return getattr(self, tier_or_alias, tier_or_alias)


class Blueprint(BaseModel):
    description: str = ""
    # what the blueprint works on (ADR-0030/0031): `new` builds from its pack's template;
    # `repo` works on a team's existing repository (no template, nothing deployed)
    target: Literal["new", "repo"] = "new"
    # the stack pack it builds with (ADR-0041): the pack gives template, verify command, stack,
    # port and chart; a field set here as well overrides the pack's and is recorded as evidence
    pack: str | None = None
    template: str | None = None
    verify_command: str = "make verify"
    # container-image vulnerability scan; {image} placeholder. Trivy's JSON report (`--format json`)
    # is parsed into findings the Build station routes by origin (ADR-0042); any other output falls
    # back to the command's exit code.
    scan_command: str | None = None
    scan_fail_on: Literal["critical", "high"] = "critical"  # lower severities are recorded, never block
    chart_path: str = "deploy/chart"
    environment: str = "local"  # where its delivery steps run (see `environments`)
    # Quality gate station: the generated repo must reach this agent-readiness level (1-3)
    min_readiness_level: int = Field(default=3, ge=0, le=3)
    # secret scan of the worktree (runs in dind); {path} = the change worktree
    secret_scan_command: str | None = None
    # software bill of materials for the built image; {image} and {out} (a folder) are filled in
    sbom_command: str | None = None
    service_port: int = 8000
    # what this blueprint builds with; a product that requires anything else is asked
    # about before any build (stack.py). Empty: no check.
    stack: list[str] = Field(default_factory=list)
    node_ports: list[int] = Field(default_factory=lambda: [30080])

    _pack: PackBinding | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _template_for_new(self) -> Blueprint:
        if self.target == "new" and not (self.template or self.pack):
            raise ValueError("a blueprint that builds new products needs a `pack` (or a `template`)")
        if self.target == "repo":
            self.node_ports = []  # nothing is deployed
        return self

    def bind_pack(self, binding: PackBinding) -> None:
        """Set by `packs.bind` when the factory starts."""
        self._pack = binding

    @property
    def pack_binding(self) -> PackBinding | None:
        """The installed pack this blueprint builds with (None: no pack, or not bound yet)."""
        return self._pack

    @property
    def health_path(self) -> str:
        """The liveness path the deploy smoke check calls: the pack's, else `/healthz`."""
        return self._pack.health_live if self._pack else "/healthz"

    @property
    def e2e_command(self) -> str | None:
        """The pack's end-to-end command against the deployed app ({url}), if it has one."""
        return self._pack.e2e if self._pack else None

    @property
    def pack_egress(self) -> tuple[str, ...]:
        """Egress this blueprint's products' sandboxes get on top of `sandbox.egress`."""
        return self._pack.egress if self._pack else ()

    @property
    def readiness_rules(self) -> StackRules | None:
        """How this blueprint's stack proves the stack-specific readiness signals (ADR-0041 §3)."""
        return self._pack.rules if self._pack else None

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
    max_loops_per_change: int = Field(
        default=6, validation_alias=AliasChoices("max_loops_per_change", "max_loops_per_run")
    )
    # time budget per run of a change; it restarts when a person approves, answers or resumes it
    change_wall_clock_minutes: int = Field(
        default=120, validation_alias=AliasChoices("change_wall_clock_minutes", "run_wall_clock_minutes")
    )


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
    "registry.npmjs.org:443",  # Node packages (ADR-0033)
    "dind:8081-8100",  # apps on the local cluster (acceptance calls them over HTTP)
]


class PreflightConfig(BaseModel):
    """Readiness checks (ADR-0015): run at startup, every `interval_minutes`, on
    demand, and before a product or iteration starts (results reused for `max_age_seconds`)."""

    interval_minutes: int = 15
    max_age_seconds: int = 60
    # warn (never block) when the Docker disk is this full; `make clean-docker` frees it
    docker_disk_warn_percent: int = Field(default=85, ge=1, le=100)


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
    # where agents reach the model; set to the gateway when one is configured (ADR-0036)
    model_endpoint: str = "api.anthropic.com:443"


class AcceptableUseRule(BaseModel):
    """An operator's own acceptable-use rule (ADR-0027). Adds to the shipped ones;
    those can't be removed or turned off."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    title: str
    pattern: str  # regular expression, case-insensitive
    unless: str | None = None  # defensive work that matches `pattern` but is fine

    @field_validator("pattern", "unless")
    @classmethod
    def _compiles(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v


class ChangeRiskConfig(BaseModel):
    """Trust and change-risk policy (ADR-0027)."""

    acceptable_use: list[AcceptableUseRule] = Field(default_factory=list)
    # a risky change needs an admin other than the requester; on a single-admin install
    # the requester may approve their own after a cooling-off delay (flagged as break-glass)
    break_glass: bool = True
    cooling_off_minutes: int = Field(default=60, ge=0, le=7 * 24 * 60)
    # outside hosts generated apps may call without a hold (fnmatch patterns)
    allowed_hosts: list[str] = Field(default_factory=list)


class ExistingReposConfig(BaseModel):
    """Onboarding teams' existing repositories (ADR-0031)."""

    # hosts a repository URL may point at (https only, never credentials in the URL)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["github.com"])
    # the blueprint new repo products use
    blueprint: str = "existing-repo"
    clone_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    # pushing a branch and opening a pull request (ADR-0033): a reference, never a value.
    # A fine-grained token with Contents and Pull requests read/write on the repos.
    write_token_ref: str | None = "env://REPO_GITHUB_TOKEN"  # noqa: S105 - a reference, not a value
    pr_branch_prefix: str = Field(default="factory/", pattern=r"^[A-Za-z0-9._/-]{1,40}$")

    @field_validator("write_token_ref")
    @classmethod
    def _is_reference(cls, v: str | None) -> str | None:
        if v is not None and "://" not in v:
            raise ValueError("write_token_ref must be a reference like env://NAME, never a value")
        return v


CheckName = Literal["secrets", "code", "dependencies", "config"]


class ChecksConfig(BaseModel):
    """Who answers each scan station's check (ADR-0035). The station is named after the
    check; the provider is the team's choice, made once here."""

    # local (open-source tool, the default) | github | snyk | agent
    providers: dict[CheckName, Literal["local", "github", "snyk", "agent"]] = Field(default_factory=dict)
    # a different catalogue tool for `local`, e.g. {code: my-scanner} (defined under `tools:`)
    local_tools: dict[CheckName, str] = Field(default_factory=dict)
    # when the provider can't answer (no token, not a repository, the tool didn't run)
    fallback: Literal["agent", "none"] = "agent"
    # references, never values: resolved for the step, passed through the environment
    snyk_token_ref: str | None = "env://SNYK_TOKEN"  # noqa: S105 - a reference, not a value
    github_token_ref: str | None = "env://REPO_GITHUB_TOKEN"  # noqa: S105 - a reference, not a value

    @field_validator("snyk_token_ref", "github_token_ref")
    @classmethod
    def _is_reference(cls, v: str | None) -> str | None:
        if v is not None and "://" not in v:
            raise ValueError("token references look like env://NAME, never a value")
        return v

    def token_ref(self, provider: str) -> str | None:
        return {"snyk": self.snyk_token_ref, "github": self.github_token_ref}.get(provider)


class ModelGatewayConfig(BaseModel):
    """Where agents reach the model (ADR-0036).

    direct:  agents call the model provider with the engine's credential (today's behaviour).
    litellm: agents call a LiteLLM gateway with a virtual key minted per agent call;
             `credential: api_key` keeps the provider key in the gateway only (anything
             others run), `subscription` forwards the agent's own Claude login per request."""

    provider: Literal["direct", "litellm"] = "direct"
    # as the engine and the sandboxes reach it (compose service name; sandboxes get an egress route)
    url: str = "http://model-gateway:4000"
    credential: Literal["api_key", "subscription"] = "api_key"
    # the gateway's admin (master) key: a reference, never a value
    admin_key_ref: str | None = "env://LITELLM_MASTER_KEY"  # noqa: S105 - a reference, not a value
    call_budget_usd: float = Field(default=5.0, gt=0)  # hard cap per agent call, enforced by the gateway
    change_budget_usd: float | None = Field(default=None, gt=0)  # the engine refuses new calls past this
    key_minutes: int = Field(default=180, ge=5, le=24 * 60)  # a virtual key's lifetime, even if never revoked
    allowed_models: list[str] = Field(default_factory=list)  # empty: whatever the gateway serves

    @field_validator("admin_key_ref")
    @classmethod
    def _is_reference(cls, v: str | None) -> str | None:
        if v is not None and "://" not in v:
            raise ValueError("admin_key_ref must be a reference like env://NAME, never a value")
        return v

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("model_gateway.url must be http(s)://host:port")
        return v


class OrgColumn(BaseModel):
    """An org column between built-in phases (ADR-0039 §2): look-and-decide only."""

    id: str
    title: str = Field(min_length=2, max_length=60)
    after: str  # the built-in phase it follows
    allows: list[str] = Field(default_factory=lambda: ["read-repo"])
    gives: list[str] = Field(default_factory=lambda: ["report"])


class PhasesConfig(BaseModel):
    """The org's phase registry (ADR-0039 §2): the seven built-ins always, plus look-and-decide
    columns an admin adds, plus the phases of packs an admin installed (appended after operate)."""

    columns: list[OrgColumn] = Field(default_factory=list)
    # read as an alias of the top-level `packs:` (ADR-0041 §1) and folded into it
    packs: list[str] = Field(default_factory=list)


class FactoryConfig(BaseModel):
    version: int = 1
    timezone: str = "UTC"
    factory: FactorySection = FactorySection()
    models: Models = Models()
    # how each kind of app is built and run (ADR-0029; `product_lines:` before)
    blueprints: dict[str, Blueprint] = Field(validation_alias=AliasChoices("blueprints", "product_lines"))
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
    change_risk: ChangeRiskConfig = ChangeRiskConfig()
    existing_repos: ExistingReposConfig = ExistingReposConfig()
    # tool stations (ADR-0034): add tools or override the built-in ones (image, command …),
    # e.g. pin a reviewed digest: {semgrep: {image: "semgrep/semgrep@sha256:…"}}
    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # which provider answers each check (ADR-0035): local tool, GitHub, Snyk or an agent
    checks: ChecksConfig = ChecksConfig()
    # where agents reach the model (ADR-0036): direct, or a gateway with a virtual key per call
    model_gateway: ModelGatewayConfig = ModelGatewayConfig()
    # org roles a station may name as its owner, besides the built-in ones (ADR-0037):
    # product-owner, tech-lead, security, qa. Id -> title, e.g. {release-manager: Release manager}
    roles: dict[str, str] = Field(default_factory=dict)
    # the factory default lanes (ADR-0039): id -> the lane template each is seeded from. A change
    # runs the lane its product is assigned for its kind (an org workflow), else the default lane
    # for (works_on, kind). Defaults are always there: edits become new versions, and "back to
    # factory default" restores the template. `use_factory_defaults` ignores every assignment.
    workflows: dict[str, str] = Field(
        default_factory=lambda: {
            lane: f"workflow-templates/standard/lanes/{lane}.yaml"
            for lane in ("new-product", "product-change", "repo-assessment", "repo-change")
        }
    )
    use_factory_defaults: bool = False
    phases: PhasesConfig = PhasesConfig()
    # the packs an admin installed, after reviewing them (ADR-0039 §2, ADR-0041): folders with a
    # pack.yaml, relative to the factory home. `phases.packs` is read as an alias.
    packs: list[str] = Field(default_factory=list)

    _stack_packs: dict[str, PackBinding] = PrivateAttr(default_factory=dict)

    def bind_stack_packs(self, packs: dict[str, PackBinding]) -> None:
        """Set by `packs.install`: every installed stack pack, by id, for team repos (ADR-0041 §5)."""
        self._stack_packs = dict(packs)

    @property
    def stack_packs(self) -> dict[str, PackBinding]:
        """The installed stack packs by id (empty until `packs.install` runs)."""
        return self._stack_packs

    @model_validator(mode="after")
    def _fold_pack_alias(self) -> FactoryConfig:
        if self.phases.packs:
            self.packs = list(dict.fromkeys([*self.packs, *self.phases.packs]))
            self.phases.packs = []
        return self

    @field_validator("roles")
    @classmethod
    def _role_ids(cls, v: dict[str, str]) -> dict[str, str]:
        from agent_factory.contract import ROLE_ID

        bad = [r for r in v if not ROLE_ID.match(r)]
        if bad:
            raise ValueError(f"role ids {bad} must be lowercase letters, digits and dashes")
        return v

    def role_ids(self) -> list[str]:
        from agent_factory.contract import BUILTIN_ROLES

        return [*BUILTIN_ROLES, *(r for r in self.roles if r not in BUILTIN_ROLES)]

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

    # ---------------------------------------------------------- workflows --
    CHANGE_KINDS: ClassVar[tuple[str, ...]] = ("feature", "bug", "upkeep")

    def workflow_templates(self) -> dict[str, str]:
        """The factory default lanes and the template each is seeded from."""
        return dict(self.workflows)

    def blueprints_for(self, works_on: str) -> list[str]:
        """Blueprints whose products a lane working on `works_on` (factory | repo) serves."""
        return [b for b, bp in self.blueprints.items() if ("repo" if bp.target == "repo" else "factory") == works_on]

    def eval_blueprint(self, works_on: str) -> str | None:
        """The blueprint an evaluation builds its products with (ADR-0025): the first one that
        builds new products; only lanes working on new builds are evaluated."""
        return next((b for b in self.blueprints_for(works_on) if self.blueprints[b].target == "new"), None)

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
