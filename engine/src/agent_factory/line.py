"""Agent specs and lines as data.

A *blueprint* is a folder shipped in the repo (read-only):

    blueprints/<name>/line.yaml          stations, routes
    blueprints/<name>/agents/<id>.md     one agent spec: YAML frontmatter + system prompt

An *instance line* is what a factory actually runs. It is seeded from a
blueprint into the database as immutable, numbered versions; every run is
pinned to the version it started with. Versions can be exported back to the
same folder format and imported into another instance.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------- tools ----
# A preset is the ceiling of what an agent may do. Guardrail hooks still apply
# on top; no spec can grant git push, credentials or holdout access.
TOOL_PRESETS: dict[str, list[str]] = {
    "observer": ["Read", "Glob", "Grep"],
    "reviewer": ["Read", "Glob", "Grep", "Bash"],  # Bash restricted to observe-only commands
    "author": ["Read", "Write", "Edit", "Glob", "Grep"],
    "operator": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    "builder": ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Task"],
}
OBSERVE_ONLY_PRESETS = {"observer", "reviewer"}
SAFE_EXTRA_TOOLS = {"WebFetch", "WebSearch"}  # read-only additions any preset may opt into

# Station handlers implemented by the engine. `agent` is the generic,
# spec-driven handler used for any custom agent station.
AGENT_HANDLERS = {"intake", "design", "build", "deploy_fix", "acceptance", "agent"}
CHECK_HANDLERS = {"verify", "package", "deploy", "deliver"}

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class AgentSpec(BaseModel):
    id: str
    description: str = ""
    model: str = "default"  # tier (judgment | default | fast) or a model alias
    tools: Literal["observer", "reviewer", "author", "operator", "builder"] = "observer"
    extra_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=50, ge=1, le=500)
    produces: list[str] = Field(default_factory=list)  # files that must exist afterwards
    prompt: str = ""

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError(f"agent id '{v}' must be lowercase letters, digits and dashes")
        return v

    @field_validator("extra_tools")
    @classmethod
    def _safe_extras(cls, v: list[str]) -> list[str]:
        bad = set(v) - SAFE_EXTRA_TOOLS
        if bad:
            raise ValueError(f"extra_tools may only add {sorted(SAFE_EXTRA_TOOLS)}, not {sorted(bad)}")
        return v

    @property
    def observe_only(self) -> bool:
        return self.tools in OBSERVE_ONLY_PRESETS

    def effective_tools(self) -> list[str]:
        base = [t for t in TOOL_PRESETS[self.tools] if t not in self.disallowed_tools]
        return base + [t for t in self.extra_tools if t not in base]


class LineStation(BaseModel):
    id: str
    kind: Literal["agent", "check"]
    agent: str | None = None  # AgentSpec id (agent stations)
    handler: str | None = None  # defaults to the station id if the engine knows it, else "agent"
    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool = False

    def resolved_handler(self) -> str:
        if self.handler:
            return self.handler
        known = AGENT_HANDLERS if self.kind == "agent" else CHECK_HANDLERS
        return self.id if self.id in known else ("agent" if self.kind == "agent" else self.id)


class LineDoc(BaseModel):
    name: str = "default"
    description: str = ""
    blueprint: str | None = None
    blueprint_hash: str | None = None
    stations: list[LineStation]
    agents: dict[str, AgentSpec] = Field(default_factory=dict)

    def station(self, station_id: str) -> LineStation:
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(station_id)

    def forward_stations(self) -> list[LineStation]:
        return [s for s in self.stations if not s.only_on_fail]

    def next_forward(self, station_id: str) -> str | None:
        ids = [s.id for s in self.forward_stations()]
        if station_id not in ids:
            return None
        i = ids.index(station_id)
        return ids[i + 1] if i + 1 < len(ids) else None

    def agent_for(self, station_id: str) -> AgentSpec:
        st = self.station(station_id)
        if not st.agent:
            raise KeyError(f"station '{station_id}' has no agent")
        return self.agents[st.agent]

    def repair_targets(self) -> set[str]:
        """Stations that receive failure evidence (their pass clears it)."""
        return {s.on_fail for s in self.stations if s.on_fail}


class LineVersionInfo(BaseModel):
    line_id: str
    version: int
    note: str
    created_at: str
    active: bool = False
    stations: int = 0
    agents: int = 0


# ------------------------------------------------------------ validate ----
def validate_line(doc: LineDoc, skills_dir: Path | None = None) -> list[str]:
    """Return human-readable problems; empty list means the line is runnable."""
    errors: list[str] = []
    ids = [s.id for s in doc.stations]
    if len(ids) != len(set(ids)):
        errors.append("station ids must be unique")
    if not doc.forward_stations():
        errors.append("the line needs at least one forward station")
    for s in doc.stations:
        for label, target in (("on_fail", s.on_fail), ("next", s.next)):
            if target and target not in ids:
                errors.append(f"station '{s.id}': {label} routes to unknown station '{target}'")
        handler = s.resolved_handler()
        if s.kind == "agent":
            if not s.agent:
                errors.append(f"agent station '{s.id}' must name an agent")
            elif s.agent not in doc.agents:
                errors.append(f"station '{s.id}' uses unknown agent '{s.agent}'")
            if handler not in AGENT_HANDLERS:
                errors.append(f"station '{s.id}': unknown agent handler '{handler}'")
            if handler == "acceptance" and s.agent in doc.agents and not doc.agents[s.agent].observe_only:
                errors.append(f"acceptance agent '{s.agent}' must use an observe-only tool preset")
        else:
            if handler not in CHECK_HANDLERS:
                errors.append(f"check station '{s.id}': unknown handler '{handler}'")
        if s.only_on_fail and not s.next:
            errors.append(f"repair station '{s.id}' needs `next` (where to go after the fix)")
    for aid, spec in doc.agents.items():
        if aid != spec.id:
            errors.append(f"agent key '{aid}' does not match its id '{spec.id}'")
        if not spec.prompt.strip():
            errors.append(f"agent '{aid}' has an empty prompt")
        if skills_dir is not None:
            for sk in spec.skills:
                if not (skills_dir / sk / "SKILL.md").exists():
                    errors.append(f"agent '{aid}' uses unknown skill '{sk}'")
    return errors


# ---------------------------------------------------------- file format ---
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_agent_md(text: str) -> AgentSpec:
    m = _FRONT.match(text)
    if not m:
        raise ValueError("agent spec must start with a --- YAML frontmatter block ---")
    meta = yaml.safe_load(m.group(1)) or {}
    return AgentSpec.model_validate({**meta, "prompt": m.group(2).strip() + "\n"})


def render_agent_md(spec: AgentSpec) -> str:
    meta = spec.model_dump(exclude={"prompt"}, exclude_defaults=False)
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + spec.prompt


def load_line_dir(path: Path) -> LineDoc:
    raw = yaml.safe_load((path / "line.yaml").read_text()) or {}
    agents: dict[str, AgentSpec] = {}
    for f in sorted((path / "agents").glob("*.md")):
        spec = parse_agent_md(f.read_text())
        agents[spec.id] = spec
    digest = hashlib.sha256()
    for f in [path / "line.yaml", *sorted((path / "agents").glob("*.md"))]:
        digest.update(f.read_bytes())
    return LineDoc.model_validate(
        {
            **raw,
            "agents": agents,
            "blueprint": raw.get("blueprint") or path.name,
            "blueprint_hash": digest.hexdigest()[:16],
        }
    )


def export_line_dir(doc: LineDoc, path: Path) -> None:
    (path / "agents").mkdir(parents=True, exist_ok=True)
    line = {
        "name": doc.name,
        "description": doc.description,
        "blueprint": doc.blueprint,
        "stations": [
            s.model_dump(exclude_none=True, exclude_defaults=True) | {"id": s.id, "kind": s.kind} for s in doc.stations
        ],
    }
    (path / "line.yaml").write_text(yaml.safe_dump(line, sort_keys=False))
    for spec in doc.agents.values():
        (path / "agents" / f"{spec.id}.md").write_text(render_agent_md(spec))
