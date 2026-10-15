"""The org's phase registry (ADR-0039 §2) from the config and the packs an admin installed.

The pure registry and its rules live in `contract` (`PhaseRegistry`, `registry_errors`); this
module only reads the files. A pack is a folder with `pack.yaml`:

    id: ops-pack
    version: 1.0.0
    title: Operations pack
    phases:
      - {id: observe-act, title: Observe & act, allows: [read-metrics, read-logs, rollback:*], gives: [report]}

Pack phases are appended after operate, in the order the packs are listed; unlike org columns they
may allow any capability, which is why only an admin installs a pack, after reviewing it.

A pack may also ship station templates (ADR-0039 §3), each with its agent:

    stations:
      - id: rollout-review
        title: Rollout review
        hint: reads metrics and logs after a deploy and reports what changed
        phase: observe-act          # any phase of the registry
        works_on: [factory, repo]
        gives: [report.rollout-review@1]
        uses: [read-repo, read-metrics, read-logs]
        agent: agents/rollout-reviewer.md

Loading refuses a template whose `uses` (or its agent's tools) fall outside its phase's `allows`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from agent_factory import contract
from agent_factory.config import FactoryConfig
from agent_factory.contract import Phase, PhaseRegistry
from agent_factory.station_templates import StationTemplate
from agent_factory.workflow import TOOL_NEEDS, AgentSpec, parse_agent_md, tools_within


class PackError(ValueError):
    pass


class PackPhase(BaseModel):
    id: str
    title: str = Field(min_length=2, max_length=60)
    allows: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=lambda: ["report"])


_BOTH: tuple[Literal["factory", "repo"], ...] = ("factory", "repo")


class PackStation(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}$")
    title: str = Field(min_length=2, max_length=60)
    hint: str = Field(default="", max_length=200)
    phase: str
    works_on: list[Literal["factory", "repo"]] = Field(default_factory=lambda: list(_BOTH), min_length=1)
    needs: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=list)
    uses: list[str] = Field(default_factory=lambda: ["read-repo"])
    agent: str  # path of its agent spec inside the pack, e.g. agents/reviewer.md


class Pack(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    version: str = Field(pattern=r"^[0-9][0-9A-Za-z.+-]{0,30}$")
    title: str = ""
    phases: list[PackPhase] = Field(default_factory=list)
    stations: list[PackStation] = Field(default_factory=list)
    folder: Path | None = None

    @property
    def source(self) -> str:
        return f"{self.id}@{self.version}"


def load_pack(folder: Path) -> Pack:
    file = folder / "pack.yaml"
    if not file.is_file():
        raise PackError(f"pack {folder}: no pack.yaml")
    raw = yaml.safe_load(file.read_text()) or {}
    try:
        return Pack.model_validate({**raw, "folder": folder})
    except ValueError as exc:
        raise PackError(f"pack {folder}: {exc}") from exc


def _template(pack: Pack, st: PackStation, registry: PhaseRegistry) -> StationTemplate:
    """A pack's station as a builder template, refused unless it fits its phase (ADR-0039 §3)."""
    where = f"pack {pack.source} station '{st.id}'"
    if st.phase not in registry:
        raise PackError(f"{where}: unknown phase '{st.phase}' (one of {', '.join(registry.ids)})")
    phase = registry.get(st.phase)
    errors: list[str] = []
    for c in st.uses:
        if problem := contract.capability_problem(c):
            errors.append(problem)
        elif not contract.grants(phase.allows, c):
            errors.append(f"uses {c}, which {phase.title} does not allow")
    try:
        needs = [contract.need_text(contract.parse_need(n)) for n in st.needs]
        gives = [contract.parse_ref(g) for g in st.gives]
    except ValueError as exc:
        raise PackError(f"{where}: {exc}") from exc
    errors += [
        f"gives {g.type}, which a {phase.title} station may not give" for g in gives if g.type not in phase.gives
    ]
    assert pack.folder is not None  # noqa: S101 - set by load_pack
    agent_file = (pack.folder / st.agent).resolve()
    if pack.folder.resolve() not in agent_file.parents or not agent_file.is_file():
        raise PackError(f"{where}: agent file '{st.agent}' is not in the pack")
    try:
        agent: AgentSpec = parse_agent_md(agent_file.read_text())
    except ValueError as exc:
        raise PackError(f"{where}: agent {st.agent}: {exc}") from exc
    _, denied = tools_within(agent.effective_tools(), st.uses)
    if denied:
        needed = sorted({c for t in denied for c in TOOL_NEEDS[t]})
        errors.append(f"its agent uses {', '.join(denied)}, which need {' or '.join(needed)} in `uses`")
    if errors:
        raise PackError(f"{where}: " + "; ".join(errors))
    return StationTemplate(
        id=f"{pack.id}/{st.id}",
        title=st.title,
        hint=st.hint,
        kind="agent",
        phase=st.phase,
        base_id=st.id,
        station={"type": contract.CUSTOM, "needs": needs, "gives": [str(g) for g in gives] or None, "uses": st.uses},
        source=pack.source,
        works_on=frozenset(st.works_on),
        agent=agent,
    )


def load_phases(cfg: FactoryConfig, home: Path) -> tuple[PhaseRegistry, list[StationTemplate]]:
    """The registry this install's workflows are checked and run against, and the station
    templates its packs add to the builder."""
    packs = [load_pack(home / rel) for rel in cfg.phases.packs]
    registry = _registry(cfg, packs)
    templates = [_template(p, st, registry) for p in packs for st in p.stations]
    ids = [t.id for t in templates]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise PackError(f"station templates listed twice: {', '.join(dupes)}")
    return registry, templates


def registry_for(cfg: FactoryConfig, home: Path) -> PhaseRegistry:
    """The registry this install's workflows are checked and run against."""
    return load_phases(cfg, home)[0]


def _registry(cfg: FactoryConfig, loaded: list[Pack]) -> PhaseRegistry:
    columns = [
        Phase(c.id, c.title, frozenset(c.allows), frozenset(c.gives), source="org", after=c.after)
        for c in cfg.phases.columns
    ]
    packs: list[Phase] = []
    for pack in loaded:
        packs += [
            Phase(p.id, p.title, frozenset(p.allows), frozenset(p.gives), source=pack.source) for p in pack.phases
        ]
    try:
        return PhaseRegistry(columns, packs)
    except ValueError as exc:
        raise PackError(f"phases: {exc}") from exc
