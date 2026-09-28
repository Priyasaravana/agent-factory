"""Typed loader for .agent-factory/config.yaml.

The config is the factory's public extension surface: most adopters change
YAML, templates, prompts and skills — never engine code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

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
    service_port: int = 8000
    node_ports: list[int] = Field(default_factory=lambda: [30080])


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


class Integration(BaseModel):
    """An external system the factory delivers through. `provider` is the type
    (e.g. "local"); `settings` are non-secret options. Credentials are never
    stored here (secret references arrive in phase 2 of docs/design/integrations.md)."""

    provider: str
    settings: dict[str, Any] = Field(default_factory=dict)


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
    environments: dict[str, Environment] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _local_defaults(self) -> FactoryConfig:
        # today's behaviour needs no config: a `local` integration and environment always exist
        self.integrations.setdefault("local", Integration(provider="local"))
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
