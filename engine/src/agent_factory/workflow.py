"""Agent specs and workflows as data.

A *workflow template* is a folder (shipped in `workflow-templates/`, or imported
from a GitHub repo):

    workflow.yaml          stations and routes
    agents/<id>.md         one agent spec: YAML frontmatter + system prompt
    docs/<id>.md           reference documents (team standards)

Each product line has one *workflow*: seeded from a template into the database
as immutable, numbered versions; every run is pinned to the version it started
with. Versions export back to the same folder format.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator

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
STATION_ID = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")

# What each engine handler needs from the agent running it. Violations block
# publishing: a design agent that cannot write would always fail, an intake
# agent that can write could tamper with the repo before the spec exists, and
# the acceptance agent sees the hidden scenarios so it must never write.
HANDLER_REQUIREMENTS: dict[str, dict[str, bool]] = {
    "intake": {"write": False},
    "design": {"write": True},
    "build": {"write": True, "shell": True},
    "deploy_fix": {"write": True, "shell": True},
    "acceptance": {"write": False, "shell": True},
}
# Advice (warnings, not errors).
RECOMMENDED_SKILLS: dict[str, list[str]] = {
    "acceptance": ["agent-watchdog"],
    "deploy_fix": ["helm-kind-deploy"],
}
JUDGMENT_HANDLERS = {"intake", "design", "acceptance"}

# Context budgets: reference material is useful, but unbounded context is
# expensive and dilutes the agent's attention.
MAX_DOC_CHARS = 20_000
MAX_DOCS_PER_AGENT_CHARS = 40_000
MAX_LEARNINGS_CHARS = 4_000


class RefDoc(BaseModel):
    """Team knowledge (standards, conventions, architecture rules) attached to agents."""

    id: str
    title: str
    content: str = Field(max_length=MAX_DOC_CHARS)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError(f"doc id '{v}' must be lowercase letters, digits and dashes")
        return v


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
    context_docs: list[str] = Field(default_factory=list)  # RefDoc ids from the workflow's doc library
    previous_iterations: int = Field(default=0, ge=0, le=5)  # earlier iterations of this product to recall
    learnings: str = Field(default="", max_length=MAX_LEARNINGS_CHARS)  # human-approved lessons
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


class WorkflowStation(BaseModel):
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


class WorkflowDoc(BaseModel):
    name: str = "default"
    description: str = ""
    # which template this workflow came from (older versions stored it as "blueprint")
    template: str | None = Field(default=None, validation_alias=AliasChoices("template", "blueprint"))
    template_hash: str | None = Field(default=None, validation_alias=AliasChoices("template_hash", "blueprint_hash"))
    stations: list[WorkflowStation]
    agents: dict[str, AgentSpec] = Field(default_factory=dict)
    docs: dict[str, RefDoc] = Field(default_factory=dict)

    def stations_using(self, agent_id: str) -> list[str]:
        return [s.id for s in self.stations if s.agent == agent_id]

    def station(self, station_id: str) -> WorkflowStation:
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(station_id)

    def forward_stations(self) -> list[WorkflowStation]:
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


class WorkflowVersionInfo(BaseModel):
    workflow_id: str
    version: int
    note: str
    created_at: str
    active: bool = False
    stations: int = 0
    agents: int = 0


# ------------------------------------------------------------ validate ----
def validate_workflow(doc: WorkflowDoc, skills_dir: Path | None = None) -> list[str]:
    """Return blocking problems; an empty list means the workflow is runnable.
    Non-blocking advice comes from `workflow_warnings`."""
    errors: list[str] = []
    ids = [s.id for s in doc.stations]
    if len(ids) != len(set(ids)):
        errors.append("station ids must be unique")
    if not doc.forward_stations():
        errors.append("the workflow needs at least one forward station")
    for s in doc.stations:
        if not STATION_ID.match(s.id):
            errors.append(f"station id '{s.id}' must be lowercase letters, digits, '-' or '_'")
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
            if s.agent in doc.agents:
                errors += _role_errors(s.id, handler, doc.agents[s.agent])
        else:
            if handler not in CHECK_HANDLERS:
                errors.append(f"check station '{s.id}': unknown handler '{handler}'")
        if s.only_on_fail and not s.next:
            errors.append(f"repair station '{s.id}' needs `next` (where to go after the fix)")
    errors += _reachability(doc)
    errors += _order_errors(doc)
    for did, d in doc.docs.items():
        if did != d.id:
            errors.append(f"doc key '{did}' does not match its id '{d.id}'")
    for aid, spec in doc.agents.items():
        missing_docs = [d for d in spec.context_docs if d not in doc.docs]
        if missing_docs:
            errors.append(f"agent '{aid}' references unknown docs {missing_docs}")
        size = sum(len(doc.docs[d].content) for d in spec.context_docs if d in doc.docs)
        if size > MAX_DOCS_PER_AGENT_CHARS:
            errors.append(f"agent '{aid}' has {size} chars of reference docs (max {MAX_DOCS_PER_AGENT_CHARS})")
        if aid != spec.id:
            errors.append(f"agent key '{aid}' does not match its id '{spec.id}'")
        if not spec.prompt.strip():
            errors.append(f"agent '{aid}' has an empty prompt")
        if skills_dir is not None:
            for sk in spec.skills:
                if not (skills_dir / sk / "SKILL.md").exists():
                    errors.append(f"agent '{aid}' uses unknown skill '{sk}'")
    return errors


def _role_errors(station_id: str, handler: str, spec: AgentSpec) -> list[str]:
    need = HANDLER_REQUIREMENTS.get(handler, {})
    tools = set(spec.effective_tools())
    can_write = not spec.observe_only and bool(tools & {"Write", "Edit"})
    can_shell = "Bash" in tools
    errors = []
    if need.get("write") is True and not can_write:
        errors.append(
            f"station '{station_id}' ({handler}) needs an agent that can write files; '{spec.id}' uses '{spec.tools}'"
        )
    if need.get("write") is False and can_write:
        errors.append(
            f"station '{station_id}' ({handler}) must use an observe-only agent; '{spec.id}' can modify files"
        )
    if need.get("shell") and not can_shell:
        errors.append(f"station '{station_id}' ({handler}) needs shell access (Bash); '{spec.id}' has none")
    return errors


def workflow_warnings(doc: WorkflowDoc) -> list[str]:
    """Non-blocking advice about roles, skills and models."""
    warnings: list[str] = []
    used = {s.agent for s in doc.stations if s.agent}
    for s in doc.stations:
        if s.kind != "agent" or s.agent not in doc.agents:
            continue
        spec, handler = doc.agents[s.agent], s.resolved_handler()
        for skill in RECOMMENDED_SKILLS.get(handler, []):
            if skill not in spec.skills:
                warnings.append(f"station '{s.id}': agent '{spec.id}' would benefit from skill '{skill}'")
        if "factory-station-contract" not in spec.skills:
            warnings.append(f"agent '{spec.id}' lacks 'factory-station-contract' (how to report evidence)")
        if handler in JUDGMENT_HANDLERS and spec.model == "fast":
            warnings.append(f"station '{s.id}' does judgment work but agent '{spec.id}' uses the fast tier")
        if handler == "agent" and "review" in s.id and not spec.observe_only:
            warnings.append(f"station '{s.id}' looks like a review but agent '{spec.id}' can modify files")
        if handler == "agent" and not s.on_fail:
            warnings.append(f"station '{s.id}' has no on_fail route: a failed verdict will hold the run")
    for aid in doc.agents:
        if aid not in used:
            warnings.append(f"agent '{aid}' is not used by any station")
    return sorted(set(warnings))


# Built-in handlers that depend on an earlier one's output (spec → design →
# code → image → deployment → acceptance → delivery). Their forward order is fixed;
# verify, generic agents and repair stations can go anywhere.
STAGE_ORDER = ["intake", "design", "build", "package", "deploy", "acceptance", "deliver"]


def _order_errors(doc: WorkflowDoc) -> list[str]:
    seen: list[tuple[int, str]] = []
    for s in doc.forward_stations():
        h = s.resolved_handler()
        if h in STAGE_ORDER:
            seen.append((STAGE_ORDER.index(h), s.id))
    errors = []
    for (a, a_id), (b, b_id) in zip(seen, seen[1:], strict=False):
        if b < a:
            errors.append(
                f"station '{b_id}' ({STAGE_ORDER[b]}) must come before '{a_id}' ({STAGE_ORDER[a]}): "
                f"order is {' → '.join(STAGE_ORDER)}"
            )
    return errors


def _reachability(doc: WorkflowDoc) -> list[str]:
    """Every station must be reachable from the start, or it would never run."""
    forward = doc.forward_stations()
    if not forward:
        return []
    ids = {s.id for s in doc.stations}
    seen: set[str] = set()
    todo = [forward[0].id]
    while todo:
        sid = todo.pop()
        if sid in seen or sid not in ids:
            continue
        seen.add(sid)
        st = doc.station(sid)
        todo += [t for t in (st.next or doc.next_forward(sid), st.on_fail) if t]
    return [f"station '{s}' can never be reached" for s in [s.id for s in doc.stations] if s not in seen]


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


def parse_doc_md(text: str) -> RefDoc:
    m = _FRONT.match(text)
    if not m:
        raise ValueError("reference doc must start with a --- YAML frontmatter block ---")
    meta = yaml.safe_load(m.group(1)) or {}
    return RefDoc.model_validate({**meta, "content": m.group(2).strip() + "\n"})


def render_doc_md(d: RefDoc) -> str:
    return "---\n" + yaml.safe_dump({"id": d.id, "title": d.title}, sort_keys=False) + "---\n" + d.content


def workflow_file(path: Path) -> Path:
    """`workflow.yaml` (current) or `line.yaml` (before the rename)."""
    return path / "workflow.yaml" if (path / "workflow.yaml").exists() else path / "line.yaml"


def load_workflow_dir(path: Path) -> WorkflowDoc:
    wf_file = workflow_file(path)
    raw = yaml.safe_load(wf_file.read_text()) or {}
    agents: dict[str, AgentSpec] = {}
    for f in sorted((path / "agents").glob("*.md")):
        spec = parse_agent_md(f.read_text())
        agents[spec.id] = spec
    docs: dict[str, RefDoc] = {}
    for f in sorted((path / "docs").glob("*.md")):
        d = parse_doc_md(f.read_text())
        docs[d.id] = d
    digest = hashlib.sha256()
    for f in [wf_file, *sorted((path / "agents").glob("*.md")), *sorted((path / "docs").glob("*.md"))]:
        digest.update(f.read_bytes())
    return WorkflowDoc.model_validate(
        {
            **raw,
            "agents": agents,
            "docs": docs,
            "template": raw.get("template") or raw.get("blueprint") or path.name,
            "template_hash": digest.hexdigest()[:16],
        }
    )


def export_workflow_dir(doc: WorkflowDoc, path: Path) -> None:
    (path / "agents").mkdir(parents=True, exist_ok=True)
    data = {
        "name": doc.name,
        "description": doc.description,
        "template": doc.template,
        "stations": [
            s.model_dump(exclude_none=True, exclude_defaults=True) | {"id": s.id, "kind": s.kind} for s in doc.stations
        ],
    }
    (path / "workflow.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    for spec in doc.agents.values():
        (path / "agents" / f"{spec.id}.md").write_text(render_agent_md(spec))
    if doc.docs:
        (path / "docs").mkdir(exist_ok=True)
        for d in doc.docs.values():
            (path / "docs" / f"{d.id}.md").write_text(render_doc_md(d))
