"""Agent specs and workflows as data.

A *workflow template* is a folder (shipped in `workflow-templates/`, or imported
from a GitHub repo):

    workflow.yaml          stations and routes
    agents/<id>.md         one agent spec: YAML frontmatter + system prompt
    docs/<id>.md           reference documents (team standards)

Workflows belong to the org (ADR-0037): `standard` by default, used by every
product whatever its blueprint. Each is seeded from a template into the database
as immutable, numbered versions; every change is pinned to the version it started
with. Versions export back to the same folder format.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agent_factory import checks, contract

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
# The capability each tool needs (any one of them): an agent gets only the tools its station is
# granted (ADR-0039 §4 layer 3). WebFetch/WebSearch stay read-only extras behind the egress proxy.
TOOL_NEEDS: dict[str, frozenset[str]] = {
    "Read": frozenset({"read-repo"}),
    "Glob": frozenset({"read-repo"}),
    "Grep": frozenset({"read-repo"}),
    "Write": contract.WRITES,
    "Edit": contract.WRITES,
    "NotebookEdit": contract.WRITES,
    "Bash": frozenset({"run-sandbox"}),
    "Task": frozenset({"write-code"}),  # subagents inherit the builder's tools
}


def tools_within(tools: list[str], grant: Collection[str]) -> tuple[list[str], list[str]]:
    """(kept, denied): the tools a grant covers, and the rest."""
    kept = [t for t in tools if t not in TOOL_NEEDS or TOOL_NEEDS[t] & set(grant)]
    return kept, [t for t in tools if t not in kept]


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
    "scan",
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


# The phase columns stations are grouped by, in run order (ADR-0037/0039).
PHASES: list[tuple[str, str]] = list(contract.COLUMNS)
PHASE_IDS = [p for p, _ in PHASES]
BuiltinPhase = Literal["discover", "design", "plan", "develop", "verify", "deliver", "operate"]
# a station's column: a built-in phase, an org column or a pack phase (the registry decides)
Phase = str


class HandlerInfo(BaseModel):
    kind: Literal["agent", "check"]
    label: str
    phase: BuiltinPhase | None  # None: a custom agent step takes the phase it sits in
    hint: str


HANDLER_INFO: dict[str, HandlerInfo] = {
    "requirements": HandlerInfo(
        kind="agent", label="Requirements", phase="design", hint="turns the request into a spec (observe-only)"
    ),
    "design": HandlerInfo(kind="agent", label="Design", phase="design", hint="writes the design docs and API contract"),
    "implement": HandlerInfo(kind="agent", label="Implement", phase="develop", hint="writes the code and its tests"),
    "test": HandlerInfo(kind="check", label="Test", phase="verify", hint="runs the tests, linters and coverage gate"),
    "quality-gate": HandlerInfo(
        kind="check", label="Quality gate", phase="verify", hint="agent-readiness Level 3 and a secret scan"
    ),
    "code-review": HandlerInfo(
        kind="agent", label="Code review", phase="verify", hint="reviews the change against the spec; the engine judges"
    ),
    "change-risk": HandlerInfo(
        kind="check",
        label="Change risk",
        phase="verify",
        hint="rules over the diff; risky changes wait for a second admin (ADR-0027)",
    ),
    "build": HandlerInfo(
        kind="check", label="Build", phase="deliver", hint="builds, scans and pushes the image (SBOM, provenance)"
    ),
    "deploy": HandlerInfo(
        kind="check", label="Deploy", phase="deliver", hint="deploys to the product line's environment"
    ),
    "deploy-repair": HandlerInfo(kind="agent", label="Deploy repair", phase="deliver", hint="repairs a failed deploy"),
    "acceptance": HandlerInfo(
        kind="agent", label="Acceptance", phase="deliver", hint="runs the hidden scenarios (observe-only)"
    ),
    "handover": HandlerInfo(
        kind="check", label="Handover", phase="deliver", hint="commits, tags, opens the PR and hands over the app"
    ),
    "onboard": HandlerInfo(
        kind="check",
        label="Onboard",
        phase="discover",
        hint="records the commit and detects the repo's stack (ADR-0031)",
    ),
    "repo-scan": HandlerInfo(
        kind="check",
        label="Repo scan",
        phase="discover",
        hint="readiness signals for any stack, rules over Dockerfiles and manifests, secret scan",
    ),
    "assess": HandlerInfo(
        kind="agent",
        label="Assess",
        phase="discover",
        hint="reads the code for test gaps, risks and next changes; the engine keeps what the repo supports",
    ),
    "report": HandlerInfo(
        kind="check", label="Report", phase="deliver", hint="the assessment report and a proposed AGENTS.md"
    ),
    "repo-implement": HandlerInfo(
        kind="agent", label="Implement", phase="develop", hint="changes the team's repo on a branch (ADR-0033)"
    ),
    "repo-test": HandlerInfo(
        kind="check", label="Test", phase="verify", hint="runs the repo's own checks in the sandbox"
    ),
    "repo-review": HandlerInfo(
        kind="agent", label="Code review", phase="verify", hint="reviews the change; the engine decides what blocks"
    ),
    "pull-request": HandlerInfo(
        kind="check", label="Pull request", phase="deliver", hint="pushes the branch and opens a PR; a person merges"
    ),
    "scan": HandlerInfo(
        kind="check",
        label="Scan",
        phase="verify",
        hint="a security check (secrets, code, dependencies, config) answered by the configured "
        "provider: open-source tool, GitHub, Snyk or an agent, with a pass/fail policy (ADR-0035)",
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


Target = Literal["factory", "repo"]


def target_of(blueprint_target: str) -> Target:
    """A blueprint's `target` (new | repo) as the station contract's target (factory | repo)."""
    return "repo" if blueprint_target == "repo" else "factory"


class WorkflowStation(BaseModel):
    id: str
    # station type (ADR-0037): a built-in type picks its handler by the change's target; `agent`
    # is a custom agent step. `kind` and `handler` are the pre-ADR-0037 way to say the same.
    type: str | None = None
    kind: Literal["agent", "check"]
    agent: str | None = None  # AgentSpec id (agent stations)
    handler: str | None = None  # defaults to the station id if the engine knows it, else "agent"
    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool = False
    # DevOps phase the station is shown under (ADR-0028). Built-in handlers have
    # one; a custom step without it takes the phase of the station before it.
    phase: Phase | None = None
    # scan stations (ADR-0034/0035): what is checked; who answers it (the config's choice
    # unless set here); a specific catalogue tool (stations saved before ADR-0035); and the
    # lowest severity that fails the station (lower ones are warnings; `none` never fails).
    # `agent` names the observe-only agent used when the provider is (or falls back to) agent.
    check: Literal["secrets", "code", "dependencies", "config"] | None = None
    provider: Literal["local", "github", "snyk", "agent"] | None = None
    tool: str | None = None
    fail_on: Literal["critical", "high", "medium", "low", "none"] | None = None
    # station contract (ADR-0037): the role that owns the station; and, for custom stations only,
    # the artifacts it needs and gives (built-in types fix theirs in `contract.STATION_TYPES`)
    owner: str | None = None
    needs: list[str] | None = None
    gives: list[str] | None = None
    # capabilities a custom station uses (ADR-0039 §3); built-in types fix theirs. Without it a
    # custom step is observe-only (read-repo); an admin grants more, inside its phase's allows.
    uses: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _kind_from_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("type") and not data.get("kind"):
            t = contract.STATION_TYPES.get(str(data["type"]))
            handlers = [h for h in t.implementations.values() if h] if t else []
            agent = data["type"] == contract.CUSTOM or any(h in AGENT_HANDLERS for h in handlers)
            data = {**data, "kind": "agent" if agent else "check"}
        return data

    def handler_for(self, target: str) -> str | None:
        """The engine handler that runs this station for a target; None when its type has no
        implementation there (the station is then skipped)."""
        t = contract.STATION_TYPES.get(self.type) if self.type else None
        if t is not None:
            return t.implementations.get(target)
        return self.resolved_handler()

    def resolved_handler(self) -> str:
        """The handler people see (a typed station's first implementation)."""
        if self.type:
            t = contract.STATION_TYPES.get(self.type)
            if t is None:
                return "agent" if self.type == contract.CUSTOM else self.type
            return next(h for h in t.implementations.values() if h)
        if self.handler:
            return self.handler
        known = AGENT_HANDLERS if self.kind == "agent" else CHECK_HANDLERS
        if self.id in known:
            return self.id
        return "agent" if self.kind == "agent" else self.id

    def check_name(self) -> str:
        """What a scan station checks; stations saved before ADR-0035 name only a tool."""
        if self.check:
            return self.check
        from agent_factory.tools import TOOLS

        return checks.check_of(TOOLS.get(self.tool or ""), self.tool or "")

    # ------------------------------------------------ station contract --
    def type_id(self) -> str:
        """The station type whose contract this station follows (`agent` = custom)."""
        if self.type:
            return self.type
        return contract.TYPE_OF_HANDLER.get(self.resolved_handler(), contract.CUSTOM)

    def station_type(self) -> contract.StationType | None:
        return contract.STATION_TYPES.get(self.type_id())

    def column(self, before: str = contract.COLUMN_IDS[0]) -> str:
        """Its container column: fixed by a built-in type; a custom station names one or
        takes the column of the station before it."""
        t = self.station_type()
        if t:
            return t.column
        return contract.column_of_phase(self.phase) if self.phase else before

    def needs_refs(self) -> tuple[contract.Need, ...]:
        t = self.station_type()
        if t:
            return t.needs
        return tuple(contract.parse_need(n) for n in self.needs or [])

    def gives_refs(self) -> tuple[contract.ArtifactRef, ...]:
        t = self.station_type()
        if t:
            return t.gives
        if self.gives is not None:
            return tuple(contract.parse_ref(g) for g in self.gives)
        return (contract.ArtifactRef("report", self.id.replace("_", "-"), 1),)  # observe-only default

    def uses_caps(self) -> frozenset[str]:
        t = self.station_type()
        if t:
            return t.uses
        return frozenset(self.uses) if self.uses is not None else contract.OBSERVE

    def floor_controls(self) -> frozenset[str]:
        t = self.station_type()
        controls = set(t.floor) if t else set()
        if self.resolved_handler() == "scan" and self.check_name() == "secrets":
            controls.add("secrets")
        return frozenset(controls)

    def label(self) -> str:
        """What people read: the built-in handler's name, or the custom station's id."""
        if self.resolved_handler() == "scan":
            return checks.title_of(self.check_name(), self.tool or self.id)
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


DEFAULT_ENTRY = ("request@1", "branch@1")
_WORKS_ON = {"factory": "a new build", "repo": "an existing repo"}


class Plan(BaseModel):
    """The stations a run of a lane goes through, its reachable repairs, and every station it
    skips with a sentence saying why (ADR-0039: skips are only conditional)."""

    target: Target
    stations: list[str]  # forward, in order
    repairs: list[str] = Field(default_factory=list)  # repair stations reachable in this lane
    skipped: list[tuple[str, str]] = Field(default_factory=list)

    def runs(self, station_id: str) -> bool:
        return station_id in self.stations or station_id in self.repairs


class WorkflowDoc(BaseModel):
    name: str = "default"
    description: str = ""
    # which template this workflow came from
    template: str | None = None
    template_hash: str | None = None
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
    # the lane (ADR-0039 §1): what it works on and which change kinds it serves; a change runs the
    # lane its product is assigned for its kind, else the factory default lane for (works_on, kind)
    works_on: Target = "factory"
    kinds: list[str] = Field(default_factory=list)
    # entry artifacts: what a change brings before the first station. The engine provides
    # `branch` itself (scaffolded worktree, or the team repo's clone) and `request` from the
    # product or change text.
    entry: list[str] = Field(default_factory=lambda: list(DEFAULT_ENTRY))
    # the lane's scope (ADR-0039 §5): the first and last phase it covers, a run of adjacent
    # phases. Empty: from its first station's phase to its last's. What it doesn't cover arrives
    # as entry artifacts from the org's own tools, or leaves as outputs.
    scope: list[str] = Field(default_factory=list)

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
        """Each station's phase column (the same as `columns`)."""
        return self.columns()

    def columns(self) -> dict[str, str]:
        """Each station's container column (ADR-0037)."""
        return contract.columns(self.stations, self.scope[0] if self.scope else contract.COLUMN_IDS[0])

    def scope_phases(self, registry: contract.PhaseRegistry = contract.BUILTIN_REGISTRY) -> list[str]:
        """The phases the lane covers, in order."""
        ids = registry.ids
        if self.scope:
            first, last = self.scope[0], self.scope[-1]
        else:
            cols = [c for c in self.columns().values() if c in registry] or [ids[0]]
            first, last = min(cols, key=ids.index), max(cols, key=ids.index)
        return ids[ids.index(first) : ids.index(last) + 1]

    def grant(self, station_id: str, registry: contract.PhaseRegistry = contract.BUILTIN_REGISTRY) -> frozenset[str]:
        """What the engine lets a station do (ADR-0039 §4 layer 3): what it uses, inside what its
        phase allows. A repair re-entering verify keeps write-code (see `capability_errors`)."""
        col = self.columns()
        st = self.station(station_id)
        phase_id = col[station_id]
        if phase_id not in registry:
            return frozenset()
        granted = contract.narrow(st.uses_caps(), registry.get(phase_id).allows)
        if "write-code" in st.uses_caps() and contract.reenters_verify(st, col, registry.ids):
            granted |= {"write-code"}
        return granted

    def entry_refs(self) -> list[contract.ArtifactRef]:
        return [contract.parse_ref(e) for e in self.entry]

    def serves(self, works_on: str, kind: str) -> bool:
        return self.works_on == works_on and kind in self.kinds

    def plan(self) -> Plan:
        """The lane's stations in order and its reachable repairs. A station without an
        implementation for what the lane works on is skipped with the reason (publish refuses
        such lanes, so this only guards versions stored before the check)."""
        runs: list[str] = []
        skipped: list[tuple[str, str]] = []
        for s in self.forward_stations():
            if s.handler_for(self.works_on) is None:
                skipped.append((s.id, f"Skipped: it does not work on {_WORKS_ON[self.works_on]}."))
            else:
                runs.append(s.id)
        repairs = [
            s.id
            for s in self.stations
            if s.only_on_fail
            and s.handler_for(self.works_on) is not None
            and any(self.station(r).on_fail == s.id for r in runs)
        ]
        return Plan(target=self.works_on, stations=runs, repairs=repairs, skipped=skipped)

    def next_in(self, plan: Plan, station_id: str) -> str | None:
        """Where a run goes after a station passes: its `next` (repairs), else the next station
        of the plan."""
        st = self.station(station_id)
        if st.next:
            return st.next if plan.runs(st.next) else None
        if station_id not in plan.stations:
            return None
        i = plan.stations.index(station_id)
        return plan.stations[i + 1] if i + 1 < len(plan.stations) else None

    def agent_for(self, station_id: str) -> AgentSpec:
        st = self.station(station_id)
        name = st.agent
        if not name:
            raise KeyError(f"station '{station_id}' has no agent")
        return self.agents[name]

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
    doc: WorkflowDoc,
    skills_dir: Path | None = None,
    imported_skills: Collection[str] = (),
    roles: Collection[str] = tuple(contract.BUILTIN_ROLES),
    phases: contract.PhaseRegistry = contract.BUILTIN_REGISTRY,
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
                errors += _role_errors(s.id, s.handler_for(doc.works_on) or handler, doc.agents[s.agent])
        else:
            if handler not in CHECK_HANDLERS:
                errors.append(f"check station '{s.id}': unknown handler '{handler}'")
            if handler == "scan" and not (s.check or s.tool):
                errors.append(f"scan station '{s.id}' must say what it checks: secrets, code, dependencies or config")
            if handler == "scan" and s.agent:
                if s.agent not in doc.agents:
                    errors.append(f"station '{s.id}' uses unknown agent '{s.agent}'")
                elif not doc.agents[s.agent].observe_only:
                    errors.append(f"scan station '{s.id}': its agent '{s.agent}' must be observe-only")
        if s.only_on_fail and not s.next:
            errors.append(f"repair station '{s.id}' needs `next` (where to go after the fix)")
    errors += _reachability(doc)
    errors += contract_errors(doc, roles, phases)
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


def contract_errors(
    doc: WorkflowDoc,
    roles: Collection[str] = tuple(contract.BUILTIN_ROLES),
    phases: contract.PhaseRegistry = contract.BUILTIN_REGISTRY,
) -> list[str]:
    """Publish checks of the station contract (ADR-0037, ADR-0039): artifact references, phases
    from the registry, container fit (column + data + floor), capabilities, scope and role ids.
    Run after the structural checks; skipped when a reference does not parse, since fit would
    only repeat it."""
    errors: list[str] = []
    for e in doc.entry:
        try:
            contract.parse_ref(e)
        except ValueError as exc:
            errors.append(f"entry artifact: {exc}")
    for s in doc.stations:
        custom = s.station_type() is None
        for label, refs in (("needs", s.needs), ("gives", s.gives), ("uses", s.uses)):
            if refs is None:
                continue
            if not custom:
                errors.append(f"station '{s.id}': {label} are fixed by its type '{s.type_id()}'; remove them")
                continue
            for r in refs:
                if label == "uses":
                    if problem := contract.capability_problem(r):
                        errors.append(f"station '{s.id}' uses: {problem}")
                    continue
                try:
                    contract.parse_need(r) if label == "needs" else contract.parse_ref(r)
                except ValueError as exc:
                    errors.append(f"station '{s.id}' {label}: {exc}")
        if custom and s.phase and contract.column_of_phase(s.phase) not in phases:
            errors.append(f"station '{s.id}': unknown phase '{s.phase}' (one of {', '.join(phases.ids)})")
        if not custom and s.phase and contract.column_of_phase(s.phase) != s.column():
            errors.append(
                f"station '{s.id}': its type '{s.type_id()}' sits in {s.column()}, not {s.phase}; remove `phase`"
            )
    errors += _type_errors(doc)
    errors += _lane_errors(doc)
    errors += [f"scope: {e}" for e in _scope_ref_errors(doc, phases)]
    if errors:
        return errors
    col = doc.columns()
    errors += contract.fit_errors(doc.stations, doc.entry_refs(), col, end_is_exit=True, order=phases.ids)
    errors += contract.capability_errors(doc.stations, col, phases)
    errors += _tool_errors(doc, phases)
    errors += scope_errors(doc, phases)
    errors += contract.role_errors(doc.stations, roles)
    return errors


def _scope_ref_errors(doc: WorkflowDoc, phases: contract.PhaseRegistry) -> list[str]:
    if not doc.scope:
        return []
    if len(doc.scope) != 2:
        return ["give the first and the last phase the lane covers, e.g. [develop, verify]"]
    unknown = [p for p in doc.scope if p not in phases]
    if unknown:
        return [f"unknown phase {', '.join(unknown)} (one of {', '.join(phases.ids)})"]
    if phases.index(doc.scope[0]) > phases.index(doc.scope[1]):
        return [f"{doc.scope[0]} comes after {doc.scope[1]}: give the first phase first"]
    return []


# what a lane that delivers without developing must start from (ADR-0039 §5)
DELIVERY_ENTRY = ("report.test", "report.risk")


def scope_errors(doc: WorkflowDoc, phases: contract.PhaseRegistry = contract.BUILTIN_REGISTRY) -> list[str]:
    """A lane covers a run of adjacent phases (ADR-0039 §5): its stations sit inside it; a scope
    that changes code includes Verify; one that lets a change out without developing it starts
    from the code (`image` or `branch`) and the evidence the floor would have made (test, risk)."""
    errors: list[str] = []
    span = doc.scope_phases(phases)
    col = doc.columns()
    for s in doc.stations:
        if col[s.id] not in span:
            errors.append(
                f"station '{s.id}' sits in {phases.title(col[s.id])}, outside the lane's scope "
                f"({phases.title(span[0])} to {phases.title(span[-1])})"
            )
    changes_code = any(g.type == "branch" for s in doc.stations for g in s.gives_refs())
    if changes_code and "verify" not in span:
        errors.append("a scope that changes code must include Verify (test, a secret scan and change-risk)")
    exits = any(g.type in contract.EXITS for s in doc.stations for g in s.gives_refs())
    if exits and not changes_code:
        have = {r.key for r in doc.entry_refs()}
        missing = [k for k in DELIVERY_ENTRY if k not in have]
        if not have & {"image", "branch"}:
            missing.insert(0, "image or branch")
        if missing:
            errors.append(
                f"a lane that delivers without developing must start from {', '.join(missing)} "
                f"(entry artifacts from your own tools), so nothing leaves without the controls' evidence"
            )
    return errors


def _tool_errors(doc: WorkflowDoc, phases: contract.PhaseRegistry) -> list[str]:
    """An agent station's agent may use only the tools its station is granted: publish refuses
    the rest instead of letting the engine drop them silently at runtime."""
    errors = []
    for s in doc.stations:
        if not s.agent or s.agent not in doc.agents:
            continue
        grant = doc.grant(s.id, phases)
        _, denied = tools_within(doc.agents[s.agent].effective_tools(), grant)
        if denied:
            needs = sorted({c for t in denied for c in TOOL_NEEDS[t]})
            errors.append(
                f"station '{s.id}': agent '{s.agent}' can use {', '.join(denied)}, but the station is granted "
                f"only {', '.join(sorted(grant)) or 'nothing'} (it would need {' or '.join(needs)}"
                + (" in `uses`" if s.station_type() is None else "")
                + ")"
            )
    return errors


def _type_errors(doc: WorkflowDoc) -> list[str]:
    errors = []
    for s in doc.stations:
        if s.type is None:
            continue
        if s.type not in contract.STATION_TYPES and s.type != contract.CUSTOM:
            errors.append(f"station '{s.id}': unknown type '{s.type}' (one of {', '.join(contract.STATION_TYPES)})")
        if s.handler:
            errors.append(f"station '{s.id}' names a type; drop `handler` (the lane's works_on picks it)")
    return errors


def _lane_errors(doc: WorkflowDoc) -> list[str]:
    """A lane works on one kind of product and serves change kinds that exist for it (ADR-0039);
    each of its stations must work on that kind of product."""
    from agent_factory.models import ChangeKind

    errors: list[str] = []
    known = {c.value for c in ChangeKind}
    for k in doc.kinds:
        if k not in known:
            errors.append(f"unknown change kind '{k}' (one of {', '.join(sorted(known))})")
        elif k == "new" and doc.works_on == "repo":
            errors.append("an existing repo has no 'new' change: its first change is 'assess'")
        elif k == "assess" and doc.works_on == "factory":
            errors.append("'assess' changes are for existing repos, not new builds")
    if len(doc.kinds) != len(set(doc.kinds)):
        errors.append("change kinds must be unique")
    for s in doc.stations:
        if s.handler_for(doc.works_on) is None:
            t = s.station_type()
            other = [w for w, h in (t.implementations.items() if t else []) if h]
            errors.append(
                f"station '{s.id}' ({s.label()}) does not work on {_WORKS_ON[doc.works_on]}"
                + (f"; it is for {' and '.join(_WORKS_ON[w] for w in other)}" if other else "")
            )
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
    if "implement" in handlers and "quality-gate" not in handlers:
        warnings.append(
            "no quality-gate station: delivered apps are not held to agent-readiness Level 3 "
            "(add the `quality-gate` check after test)"
        )
    # lane order is the container's column rule now (ADR-0037), an error, not advice
    return sorted(set(warnings))


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
    """A template is a folder with `workflow.yaml`, or one lane file under a folder's `lanes/`
    sharing that folder's agents, docs and evaluation cases (the factory default lanes)."""
    return path if path.suffix == ".yaml" else path / "workflow.yaml"


def template_root(path: Path) -> Path:
    """The folder holding a template's agents/, docs/ and evals.yaml."""
    return path.parent.parent if path.suffix == ".yaml" else path


def template_name(path: Path) -> str:
    return f"{path.parent.parent.name}/{path.stem}" if path.suffix == ".yaml" else path.name


def load_workflow_dir(path: Path) -> WorkflowDoc:
    wf_file = workflow_file(path)
    root = template_root(path)
    raw = yaml.safe_load(wf_file.read_text()) or {}
    agents: dict[str, AgentSpec] = {}
    for f in sorted((root / "agents").glob("*.md")):
        spec = parse_agent_md(f.read_text())
        agents[spec.id] = spec
    used = {s.get("agent") for s in raw.get("stations", []) if isinstance(s, dict)}
    if path.suffix == ".yaml":  # a lane of a shared folder keeps only the agents it uses
        agents = {k: v for k, v in agents.items() if k in used}
    docs: dict[str, RefDoc] = {}
    for f in sorted((root / "docs").glob("*.md")):
        d = parse_doc_md(f.read_text())
        docs[d.id] = d
    wanted = {d for a in agents.values() for d in a.context_docs}
    if path.suffix == ".yaml":
        docs = {k: v for k, v in docs.items() if k in wanted}
    evals_file = root / "evals.yaml"
    evaluated = raw.get("eval_gate", "off") != "off" or path.suffix != ".yaml"
    evals = (
        (yaml.safe_load(evals_file.read_text()) or []) if evals_file.is_file() and evaluated else raw.get("evals", [])
    )
    digest = hashlib.sha256()
    files = [wf_file, *sorted(f for f in (root / "agents").glob("*.md") if f.stem in agents)]
    files += sorted(f for f in (root / "docs").glob("*.md") if f.stem in docs)
    if evals_file.is_file() and evaluated:
        files.append(evals_file)
    for f in files:
        digest.update(f.read_bytes())
    return WorkflowDoc.model_validate(
        {
            **raw,
            "evals": evals,
            "agents": agents,
            "docs": docs,
            "template": raw.get("template") or template_name(path),
            "template_hash": digest.hexdigest()[:16],
        }
    )


def export_workflow_dir(doc: WorkflowDoc, path: Path) -> None:
    (path / "agents").mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "name": doc.name,
        "description": doc.description,
        "template": doc.template,
        "works_on": doc.works_on,
        "kinds": list(doc.kinds),
        "stations": [
            s.model_dump(exclude_none=True, exclude_defaults=True) | {"id": s.id, "kind": s.kind} for s in doc.stations
        ],
    }
    if list(doc.entry) != list(DEFAULT_ENTRY):
        data["entry"] = list(doc.entry)
    if doc.scope:
        data["scope"] = list(doc.scope)
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
