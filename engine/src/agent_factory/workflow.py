"""Agent specs and workflows as data.

A *workflow template* is a folder (shipped in `workflow-templates/`, or imported
from a GitHub repo):

    workflow.yaml          stations and routes
    agents/<id>.md         one agent spec: YAML frontmatter + system prompt
    docs/<id>.md           reference documents (team standards)

Each blueprint has one *workflow*: seeded from a template into the database
as immutable, numbered versions; every change is pinned to the version it started
with. Versions export back to the same folder format.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

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

# Station handlers implemented by the engine, named after the DevOps phase they
# serve (ADR-0028). `agent` is the generic, spec-driven handler used for any
# custom agent station.
AGENT_HANDLERS = {
    "requirements",
    "design",
    "implement",
    "code-review",
    "deploy-repair",
    "acceptance",
    "agent",
    "assess",
    "repo-implement",
    "repo-review",
}
CHECK_HANDLERS = {
    "test",
    "quality-gate",
    "change-risk",
    "build",
    "deploy",
    "handover",
    "onboard",
    "repo-scan",
    "report",
    "repo-test",
    "pull-request",
}
# Handlers for existing repositories: assessment reads the repo (ADR-0031); a change is made
# on a branch of the clone and leaves only as a pull request (ADR-0033).
REPO_HANDLERS = {
    "onboard",
    "repo-scan",
    "assess",
    "report",
    "repo-implement",
    "repo-test",
    "repo-review",
    "pull-request",
}

# Names used before ADR-0028. Versions stored with them keep working and keep their
# station ids (runs and evidence are pinned to them); they resolve to the new
# handlers. Per kind, because `build` was the coding agent and is now the image check.
LEGACY_HANDLERS: dict[str, dict[str, str]] = {
    "agent": {"intake": "requirements", "build": "implement", "review": "code-review", "deploy_fix": "deploy-repair"},
    "check": {"verify": "test", "readiness": "quality-gate", "package": "build", "deliver": "handover"},
}


def canonical_handler(kind: str, name: str) -> str:
    """A handler name as the engine knows it today (legacy names translated)."""
    return LEGACY_HANDLERS.get(kind, {}).get(name, name)


# The DevOps loop the stations are grouped by, in run order. Operate and monitor
# come with the operate loop (master plan, phase 7).
PHASES: list[tuple[str, str]] = [
    ("plan", "Plan"),
    ("code", "Code"),
    ("test", "Test"),
    ("release", "Release"),
    ("deploy", "Deploy"),
    ("validate", "Validate"),
    ("handover", "Handover"),
]
PHASE_IDS = [p for p, _ in PHASES]
Phase = Literal["plan", "code", "test", "release", "deploy", "validate", "handover"]


class HandlerInfo(BaseModel):
    kind: Literal["agent", "check"]
    label: str
    phase: Phase | None  # None: a custom agent step takes the phase it sits in
    hint: str


HANDLER_INFO: dict[str, HandlerInfo] = {
    "requirements": HandlerInfo(
        kind="agent", label="Requirements", phase="plan", hint="turns the request into a spec (observe-only)"
    ),
    "design": HandlerInfo(kind="agent", label="Design", phase="plan", hint="writes the design docs and API contract"),
    "implement": HandlerInfo(kind="agent", label="Implement", phase="code", hint="writes the code and its tests"),
    "test": HandlerInfo(kind="check", label="Test", phase="test", hint="runs the tests, linters and coverage gate"),
    "quality-gate": HandlerInfo(
        kind="check", label="Quality gate", phase="test", hint="agent-readiness Level 3 and a secret scan"
    ),
    "code-review": HandlerInfo(
        kind="agent", label="Code review", phase="test", hint="reviews the change against the spec; the engine judges"
    ),
    "change-risk": HandlerInfo(
        kind="check",
        label="Change risk",
        phase="test",
        hint="rules over the diff; risky changes wait for a second admin (ADR-0027)",
    ),
    "build": HandlerInfo(
        kind="check", label="Build", phase="release", hint="builds, scans and pushes the image (SBOM, provenance)"
    ),
    "deploy": HandlerInfo(
        kind="check", label="Deploy", phase="deploy", hint="deploys to the product line's environment"
    ),
    "deploy-repair": HandlerInfo(kind="agent", label="Deploy repair", phase="deploy", hint="repairs a failed deploy"),
    "acceptance": HandlerInfo(
        kind="agent", label="Acceptance", phase="validate", hint="runs the hidden scenarios (observe-only)"
    ),
    "handover": HandlerInfo(
        kind="check", label="Handover", phase="handover", hint="commits, tags, opens the PR and hands over the app"
    ),
    "onboard": HandlerInfo(
        kind="check", label="Onboard", phase="plan", hint="records the commit and detects the repo's stack (ADR-0031)"
    ),
    "repo-scan": HandlerInfo(
        kind="check",
        label="Repo scan",
        phase="test",
        hint="readiness signals for any stack, rules over Dockerfiles and manifests, secret scan",
    ),
    "assess": HandlerInfo(
        kind="agent",
        label="Assess",
        phase="test",
        hint="reads the code for test gaps, risks and next changes; the engine keeps what the repo supports",
    ),
    "report": HandlerInfo(
        kind="check", label="Report", phase="handover", hint="the assessment report and a proposed AGENTS.md"
    ),
    "repo-implement": HandlerInfo(
        kind="agent", label="Implement", phase="code", hint="changes the team's repo on a branch (ADR-0033)"
    ),
    "repo-test": HandlerInfo(
        kind="check", label="Test", phase="test", hint="runs the repo's own checks in the sandbox"
    ),
    "repo-review": HandlerInfo(
        kind="agent", label="Code review", phase="test", hint="reviews the change; the engine decides what blocks"
    ),
    "pull-request": HandlerInfo(
        kind="check", label="Pull request", phase="handover", hint="pushes the branch and opens a PR; a person merges"
    ),
    "agent": HandlerInfo(
        kind="agent",
        label="Custom agent step",
        phase=None,
        hint="any step (review, docs, compliance…) returning a verdict",
    ),
}

_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
STATION_ID = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")

# What each engine handler needs from the agent running it. Violations block
# publishing: a design agent that cannot write would always fail, an intake
# agent that can write could tamper with the repo before the spec exists, and
# the acceptance agent sees the hidden scenarios so it must never write.
HANDLER_REQUIREMENTS: dict[str, dict[str, bool]] = {
    "requirements": {"write": False},
    "design": {"write": True},
    "implement": {"write": True, "shell": True},
    "deploy-repair": {"write": True, "shell": True},
    "acceptance": {"write": False, "shell": True},
    "code-review": {"write": False, "shell": True},  # reads the diff with git; judges, never fixes
    "assess": {"write": False},  # reads someone else's repository: never changes it
    "repo-implement": {"write": True, "shell": True},
    "repo-review": {"write": False, "shell": True},
}
# Advice (warnings, not errors).
RECOMMENDED_SKILLS: dict[str, list[str]] = {
    "acceptance": ["agent-watchdog"],
    "deploy-repair": ["helm-kind-deploy"],
}
JUDGMENT_HANDLERS = {"requirements", "design", "code-review", "acceptance", "assess", "repo-review"}

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
    # skills whose instructions go straight into the system prompt, so the agent
    # always follows them (the rest are loaded on demand, when the agent decides)
    preload_skills: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _preload_subset(self) -> AgentSpec:
        extra = [s for s in self.preload_skills if s not in self.skills]
        if extra:
            raise ValueError(f"preload_skills {extra} must also be listed in skills")
        return self

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
    # DevOps phase the station is shown under (ADR-0028). Built-in handlers have
    # one; a custom step without it takes the phase of the station before it.
    phase: Phase | None = None

    def resolved_handler(self) -> str:
        if self.handler:
            return canonical_handler(self.kind, self.handler)
        known = AGENT_HANDLERS if self.kind == "agent" else CHECK_HANDLERS
        if self.id in known:
            return self.id
        legacy = LEGACY_HANDLERS[self.kind].get(self.id)
        if legacy:
            return legacy
        return "agent" if self.kind == "agent" else self.id

    def label(self) -> str:
        """What people read: the built-in handler's name, or the custom station's id."""
        info = HANDLER_INFO.get(self.resolved_handler())
        return info.label if info and self.resolved_handler() != "agent" else self.id


EVAL_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
MAX_EVAL_CASES = 8


class EvalCase(BaseModel):
    """One fixed order of a workflow's evaluation suite (ADR-0025)."""

    id: str
    title: str = Field(min_length=3, max_length=120)
    requirements: str = Field(min_length=10, max_length=20_000)
    # answers given automatically if intake asks blocking questions; the case then
    # counts as "needed help" (an unplanned touch) in the evaluation
    answers: list[str] = Field(default_factory=list)


class WorkflowDoc(BaseModel):
    name: str = "default"
    description: str = ""
    # which template this workflow came from (older versions stored it as "blueprint")
    template: str | None = Field(default=None, validation_alias=AliasChoices("template", "blueprint"))
    template_hash: str | None = Field(default=None, validation_alias=AliasChoices("template_hash", "blueprint_hash"))
    stations: list[WorkflowStation]
    agents: dict[str, AgentSpec] = Field(default_factory=dict)
    docs: dict[str, RefDoc] = Field(default_factory=dict)
    # imported skill -> commit sha, fixed when the version is published
    skill_pins: dict[str, str] = Field(default_factory=dict)
    # spec review gate (ADR-0017): pause after design until a person approves the spec
    #   off: never · first: the first iteration of a product · always: every iteration
    spec_review: Literal["off", "first", "always"] = "off"
    # after a change that needed help, suggest learnings for an admin to accept (ADR-0021)
    learn_from_runs: bool = True
    # evaluation harness (ADR-0025): fixed products run against a new version before it
    # becomes active. off: no gate · warn: activate, then evaluate and report ·
    # block: a published version stays a candidate until its evaluation shows no regression
    evals: list[EvalCase] = Field(default_factory=list)
    eval_gate: Literal["off", "warn", "block"] = "off"

    @field_validator("spec_review", mode="before")
    @classmethod
    def _yaml_off(cls, v: object) -> object:
        # YAML 1.1 reads a bare `off` as false (and `on` as true)
        return {False: "off", True: "always"}.get(v, v) if isinstance(v, bool) else v

    def eval_suite_hash(self) -> str:
        """Identity of the suite: results are only compared across the same cases."""
        body = json.dumps([c.model_dump() for c in self.evals], sort_keys=True).encode()
        return hashlib.sha256(body).hexdigest()[:12]

    def spec_gate_applies(self, iteration: int) -> bool:
        return self.spec_review == "always" or (self.spec_review == "first" and iteration == 1)

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

    def phases(self) -> dict[str, str]:
        """Each station's DevOps phase: its own, its built-in handler's, or (for a
        custom step) the phase of the station before it in the list."""
        out: dict[str, str] = {}
        current = PHASE_IDS[0]
        for s in self.stations:
            info = HANDLER_INFO.get(s.resolved_handler())
            current = s.phase or (info.phase if info and info.phase else None) or current
            out[s.id] = current
        return out

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
    evaluation: str | None = None  # gate state (ADR-0025): candidate, evaluating, passed, failed …


# ------------------------------------------------------------ validate ----
def validate_workflow(
    doc: WorkflowDoc, skills_dir: Path | None = None, imported_skills: Collection[str] = ()
) -> list[str]:
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
                if sk not in imported_skills and not (skills_dir / sk / "SKILL.md").exists():
                    errors.append(f"agent '{aid}' uses unknown skill '{sk}'")
    case_ids = [c.id for c in doc.evals]
    if len(case_ids) != len(set(case_ids)):
        errors.append("evaluation case ids must be unique")
    for cid in case_ids:
        if not EVAL_CASE_ID.match(cid):
            errors.append(f"evaluation case id '{cid}' must be lowercase letters, digits or '-' (max 31)")
    if len(case_ids) > MAX_EVAL_CASES:
        errors.append(f"at most {MAX_EVAL_CASES} evaluation cases (each one is a full build)")
    if doc.eval_gate != "off" and not doc.evals:
        errors.append(f"eval_gate '{doc.eval_gate}' needs at least one evaluation case")
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
    handlers = {s.resolved_handler() for s in doc.stations}
    if "implement" in handlers and "change-risk" not in handlers:
        warnings.append(
            "no change-risk station: risky changes (security, tests, data, outside hosts) are not held "
            "for a second admin (add the `change-risk` check after code-review, ADR-0027)"
        )
    if "implement" in handlers and "quality-gate" not in handlers:
        warnings.append(
            "no quality-gate station: delivered apps are not held to agent-readiness Level 3 "
            "(add the `quality-gate` check after test)"
        )
    phase_of, last = doc.phases(), -1
    for s in doc.forward_stations():
        i = PHASE_IDS.index(phase_of[s.id])
        if i < last:
            warnings.append(f"station '{s.id}' ({phase_of[s.id]}) comes after a later phase: the lane is out of order")
        last = i  # one warning where the lane steps back, not one per later station
    return sorted(set(warnings))


# Built-in handlers that depend on an earlier one's output (spec → design →
# code → review → image → deployment → acceptance → handover). Their forward order
# is fixed; test, generic agents and repair stations can go anywhere.
STAGE_ORDER = ["requirements", "design", "implement", "code-review", "build", "deploy", "acceptance", "handover"]


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
    evals_file = path / "evals.yaml"
    evals = (yaml.safe_load(evals_file.read_text()) or []) if evals_file.is_file() else raw.get("evals", [])
    digest = hashlib.sha256()
    extra = [evals_file] if evals_file.is_file() else []
    for f in [wf_file, *sorted((path / "agents").glob("*.md")), *sorted((path / "docs").glob("*.md")), *extra]:
        digest.update(f.read_bytes())
    return WorkflowDoc.model_validate(
        {
            **raw,
            "evals": evals,
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
    if doc.eval_gate != "off":
        data["eval_gate"] = doc.eval_gate
    if doc.skill_pins:
        data["skill_pins"] = dict(doc.skill_pins)  # imported skills this version was published with
    (path / "workflow.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    if doc.evals:
        (path / "evals.yaml").write_text(
            yaml.safe_dump([c.model_dump(exclude_defaults=True) for c in doc.evals], sort_keys=False)
        )
    for spec in doc.agents.values():
        (path / "agents" / f"{spec.id}.md").write_text(render_agent_md(spec))
    if doc.docs:
        (path / "docs").mkdir(exist_ok=True)
        for d in doc.docs.values():
            (path / "docs" / f"{d.id}.md").write_text(render_doc_md(d))
