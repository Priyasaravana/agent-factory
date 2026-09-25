"""Typed loader for .agent-factory/config.yaml.

The config is the factory's public extension surface: most adopters change
YAML, templates, prompts and skills — never engine code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
    verify_command: str = "make verify"
    chart_path: str = "deploy/chart"
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


class Station(BaseModel):
    id: str
    kind: Literal["agent", "check"]
    role: str | None = None
    model: str | None = None
    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool = False

    @model_validator(mode="after")
    def _agent_needs_role(self) -> Station:
        if self.kind == "agent" and not self.role:
            raise ValueError(f"agent station '{self.id}' needs a role")
        return self


class Budgets(BaseModel):
    max_attempts_per_station: int = 3
    max_loops_per_run: int = 6
    agent_max_turns: dict[str, int] = Field(default_factory=dict)
    run_wall_clock_minutes: int = 120


class Limits(BaseModel):
    pause_at_utilization: float = 0.95


class FactoryConfig(BaseModel):
    version: int = 1
    timezone: str = "UTC"
    factory: FactorySection = FactorySection()
    models: Models = Models()
    product_lines: dict[str, ProductLine]
    policies: Policies = Policies()
    gates: list[Gate] = Field(default_factory=list)
    stations: list[Station]
    budgets: Budgets = Budgets()
    limits: Limits = Limits()
    role_skills: dict[str, list[str]] = Field(default_factory=dict)
    skill_prompts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _routes_exist(self) -> FactoryConfig:
        ids = {s.id for s in self.stations}
        for s in self.stations:
            for target in (s.on_fail, s.next):
                if target and target not in ids:
                    raise ValueError(f"station '{s.id}' routes to unknown station '{target}'")
        return self

    # -- helpers -----------------------------------------------------------
    def station(self, station_id: str) -> Station:
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(station_id)

    def forward_stations(self) -> list[Station]:
        """The main line, excluding repair stations reached only on failure."""
        return [s for s in self.stations if not s.only_on_fail]

    def next_forward(self, station_id: str) -> str | None:
        line = self.forward_stations()
        ids = [s.id for s in line]
        if station_id not in ids:
            return None
        i = ids.index(station_id)
        return ids[i + 1] if i + 1 < len(ids) else None

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
