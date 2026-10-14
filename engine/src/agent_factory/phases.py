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
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from agent_factory.config import FactoryConfig
from agent_factory.contract import Phase, PhaseRegistry


class PackError(ValueError):
    pass


class PackPhase(BaseModel):
    id: str
    title: str = Field(min_length=2, max_length=60)
    allows: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=lambda: ["report"])


class Pack(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    version: str = Field(pattern=r"^[0-9][0-9A-Za-z.+-]{0,30}$")
    title: str = ""
    phases: list[PackPhase] = Field(default_factory=list)

    @property
    def source(self) -> str:
        return f"{self.id}@{self.version}"


def load_pack(folder: Path) -> Pack:
    file = folder / "pack.yaml"
    if not file.is_file():
        raise PackError(f"pack {folder}: no pack.yaml")
    raw = yaml.safe_load(file.read_text()) or {}
    if "stations" in raw:
        raise PackError(f"pack {folder}: station templates from packs come with the workflow builder (ADR-0039 1c)")
    try:
        return Pack.model_validate(raw)
    except ValueError as exc:
        raise PackError(f"pack {folder}: {exc}") from exc


def registry_for(cfg: FactoryConfig, home: Path) -> PhaseRegistry:
    """The registry this install's workflows are checked and run against."""
    columns = [
        Phase(c.id, c.title, frozenset(c.allows), frozenset(c.gives), source="org", after=c.after)
        for c in cfg.phases.columns
    ]
    packs: list[Phase] = []
    for rel in cfg.phases.packs:
        pack = load_pack(home / rel)
        packs += [
            Phase(p.id, p.title, frozenset(p.allows), frozenset(p.gives), source=pack.source) for p in pack.phases
        ]
    try:
        return PhaseRegistry(columns, packs)
    except ValueError as exc:
        raise PackError(f"phases: {exc}") from exc
