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
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

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

# Names used before ADR-0028. Versions stored with them keep working and keep their
# station ids (runs and evidence are pinned to them); they resolve to the new
# handlers. Per kind, because `build` was the coding agent and is now the image check.
LEGACY_HANDLERS: dict[str, dict[str, str]] = {
    "agent": {"intake": "requirements", "build": "implement", "review": "code-review", "deploy_fix": "deploy-repair"},
    "check": {
        "verify": "test",
        "readiness": "quality-gate",
        "package": "build",
        "deliver": "handover",
        "tool": "scan",  # ADR-0034's tool stations, named after their check since ADR-0035
    },
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
# A custom station's `phase:` names its container column (ADR-0037); the DevOps phases above
# are still read, as the column they belong to (`contract.PHASE_TO_COLUMN`).
Phase = Literal[
    "plan",
    "code",
    "test",
    "release",
    "deploy",
    "validate",
    "handover",
    "discover",
    "design",
    "develop",
    "verify",
    "deliver",
    "operate",
]


# a custom station naming a container column is shown under this DevOps phase until the
# editor moves to columns (ADR-0037 step 1c)
_COLUMN_TO_PHASE = {
    "discover": "plan",
    "design": "plan",
    "develop": "code",
    "verify": "test",
    "deliver": "release",
    "operate": "handover",
}


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
    "scan": HandlerInfo(
        kind="check",
        label="Scan",
        phase="test",
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
FailOn = Literal["critical", "high", "medium", "low", "none"]


def target_of(blueprint_target: str) -> Target:
    """A blueprint's `target` (new | repo) as the station contract's target (factory | repo)."""
    return "repo" if blueprint_target == "repo" else "factory"


class AppliesWhen(BaseModel):
    """When a station applies (ADR-0037 §4), as data. Empty = always. A station that doesn't
    apply is skipped and the skip is recorded with its reason."""

    target: list[Target] | None = None
    windows: list[str] | None = None
    not_windows: list[str] | None = None

    def why_not(self, window: str | None, target: str) -> str | None:
        if self.target is not None and target not in self.target:
            return f"not applicable: {target} target"
        if window is not None and self.windows is not None and window not in self.windows:
            return f"not applicable: window {window}"
        if window is not None and self.not_windows and window in self.not_windows:
            return f"not applicable: window {window}"
        return None


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
    # one standard lane (ADR-0037 §4): when the station applies; the agent per target when it
    # differs from `agent`; a scan's `fail_on` per window id or target
    applies_when: AppliesWhen = Field(default_factory=AppliesWhen)
    target_agents: dict[Target, str] = Field(default_factory=dict)
    fail_on_by: dict[str, FailOn] = Field(default_factory=dict)

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

    def agent_for_target(self, target: str) -> str | None:
        return self.target_agents.get(target, self.agent)  # type: ignore[call-overload]

    def fail_on_for(self, window: str | None, target: str) -> str | None:
        return self.fail_on_by.get(window or "") or self.fail_on_by.get(target) or self.fail_on

    def resolved_handler(self) -> str:
        """The handler people see (a typed station's first implementation)."""
        if self.type:
            t = contract.STATION_TYPES.get(self.type)
            if t is None:
                return "agent" if self.type == contract.CUSTOM else self.type
            return next(h for h in t.implementations.values() if h)
        if self.handler:
            return canonical_handler(self.kind, self.handler)
        known = AGENT_HANDLERS if self.kind == "agent" else CHECK_HANDLERS
        if self.id in known:
            return self.id
        legacy = LEGACY_HANDLERS[self.kind].get(self.id)
        if legacy:
            return legacy
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
WINDOW_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class Window(BaseModel):
    """A window over the standard lane (ADR-0037 §4): where a run enters and leaves, and what it
    starts from. `kinds` are the change kinds that run on it; a window without kinds is a preset."""

    id: str
    title: str = ""
    target: Target
    kinds: list[str] = Field(default_factory=list)
    enter: str  # a column, or a station id
    leave: str  # a column (its last station), or a station id
    entry: list[str]

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not WINDOW_ID.match(v):
            raise ValueError(f"window id '{v}' must be lowercase letters, digits and dashes")
        return v


class Plan(BaseModel):
    """The stations a run of one window goes through, and every station it skips, with why."""

    window: str | None
    target: Target
    stations: list[str]  # forward, in order
    repairs: list[str] = Field(default_factory=list)  # repair stations reachable in this window
    skipped: list[tuple[str, str]] = Field(default_factory=list)

    def runs(self, station_id: str) -> bool:
        return station_id in self.stations or station_id in self.repairs


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
    # entry artifacts (ADR-0037 §4): what a change brings before the first station. The engine
    # provides `branch` itself (scaffolded worktree, or the team repo's clone) and `request` from
    # the product or change text. Windows per change kind replace this in step 1b.
    entry: list[str] = Field(default_factory=lambda: list(DEFAULT_ENTRY))
    # windows and presets over this lane (ADR-0037 §4), from `presets.yaml`. A workflow without
    # windows runs every forward station from `entry` (custom lanes until step 1b-2).
    windows: list[Window] = Field(default_factory=list)

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
            own = _COLUMN_TO_PHASE.get(s.phase, s.phase) if s.phase else None
            current = own or (info.phase if info and info.phase else None) or current
            out[s.id] = current
        return out

    def columns(self) -> dict[str, str]:
        """Each station's container column (ADR-0037)."""
        return contract.columns(self.stations)

    def entry_refs(self) -> list[contract.ArtifactRef]:
        return [contract.parse_ref(e) for e in self.entry]

    def window(self, window_id: str) -> Window:
        for w in self.windows:
            if w.id == window_id:
                return w
        raise KeyError(window_id)

    def window_for(self, target: str, kind: str) -> Window | None:
        """The window a change of this kind runs on; None when the lane has no windows."""
        if not self.windows:
            return None
        for w in self.windows:
            if w.target == target and kind in w.kinds:
                return w
        raise KeyError(f"no window for a {kind} change on a {target} target")

    def entry_for(self, window_id: str | None) -> list[contract.ArtifactRef]:
        refs = self.window(window_id).entry if window_id else self.entry
        return [contract.parse_ref(e) for e in refs]

    def bounds(self, w: Window) -> tuple[int, int]:
        """Index range (inclusive) of a window over the forward stations; raises ValueError."""
        forward = self.forward_stations()
        col = self.columns()
        order = [contract.COLUMN_IDS.index(col[s.id]) for s in forward]
        idx = {s.id: i for i, s in enumerate(forward)}

        def at(ref: str, first: bool) -> int:
            if ref in contract.COLUMN_IDS:
                c = contract.COLUMN_IDS.index(ref)
                if first:  # the first station in the column, or after it when it is empty
                    hits = [i for i, o in enumerate(order) if o >= c]
                    return hits[0] if hits else -1
                hits = [i for i, o in enumerate(order) if o <= c]
                return hits[-1] if hits else -1
            if ref not in idx:
                raise ValueError(f"window '{w.id}': '{ref}' is neither a column nor a forward station")
            return idx[ref]

        lo, hi = at(w.enter, True), at(w.leave, False)
        if lo < 0 or hi < 0 or lo > hi:
            raise ValueError(f"window '{w.id}' enters at '{w.enter}' after it leaves at '{w.leave}'")
        return lo, hi

    def plan(self, window_id: str | None, target: str) -> Plan:
        """Which stations run for a window and target, and why each other one is skipped."""
        forward = self.forward_stations()
        lo, hi = self.bounds(self.window(window_id)) if window_id else (0, len(forward) - 1)
        runs: list[str] = []
        skipped: list[tuple[str, str]] = []
        for i, s in enumerate(forward):
            why = "outside window" if not lo <= i <= hi else self._why_not(s, window_id, target)
            if why:
                skipped.append((s.id, why))
            else:
                runs.append(s.id)
        repairs = []
        for s in self.stations:
            if not s.only_on_fail:
                continue
            routed = any(self.station(r).on_fail == s.id for r in runs)
            why = self._why_not(s, window_id, target) or (None if routed else "no station in this window routes to it")
            if why:
                skipped.append((s.id, why))
            else:
                repairs.append(s.id)
        return Plan(window=window_id, target=target, stations=runs, repairs=repairs, skipped=skipped)  # type: ignore[arg-type]

    def _why_not(self, s: WorkflowStation, window_id: str | None, target: str) -> str | None:
        if s.handler_for(target) is None:
            return f"not applicable: no {target} implementation"
        return s.applies_when.why_not(window_id, target)

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

    def agent_for(self, station_id: str, target: str | None = None) -> AgentSpec:
        st = self.station(station_id)
        name = st.agent_for_target(target) if target else st.agent
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
                errors += _role_errors(s.id, s.handler_for("factory") or handler, doc.agents[s.agent])
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
    errors += _order_errors(doc)
    errors += contract_errors(doc, roles)
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


def contract_errors(doc: WorkflowDoc, roles: Collection[str] = tuple(contract.BUILTIN_ROLES)) -> list[str]:
    """Publish checks of the station contract (ADR-0037): artifact references, container fit
    (column + data + floor) and role ids. Run after the structural checks; skipped when a
    reference does not parse, since fit would only repeat it."""
    errors: list[str] = []
    for e in doc.entry:
        try:
            contract.parse_ref(e)
        except ValueError as exc:
            errors.append(f"entry artifact: {exc}")
    for s in doc.stations:
        custom = s.station_type() is None
        for label, refs in (("needs", s.needs), ("gives", s.gives)):
            if refs is None:
                continue
            if not custom:
                errors.append(f"station '{s.id}': {label} are fixed by its type '{s.type_id()}'; remove them")
                continue
            for r in refs:
                try:
                    contract.parse_need(r) if label == "needs" else contract.parse_ref(r)
                except ValueError as exc:
                    errors.append(f"station '{s.id}' {label}: {exc}")
        if not custom and s.phase and contract.column_of_phase(s.phase) != s.column():
            errors.append(
                f"station '{s.id}': its type '{s.type_id()}' sits in {s.column()}, not {s.phase}; remove `phase`"
            )
    errors += _type_errors(doc)
    errors += _window_errors(doc)
    if errors:
        return errors
    col = doc.columns()
    if doc.windows:  # one lane: columns over the lane, data and floor per window
        errors += contract.column_errors(doc.forward_stations(), col)
        for w in doc.windows:
            plan = doc.plan(w.id, w.target)
            runs = [s for s in doc.stations if plan.runs(s.id)]
            if not plan.stations:
                errors.append(f"window '{w.id}' runs no station")
                continue
            errors += [
                f"window '{w.id}': {e}"
                for e in contract.fit_errors(runs, doc.entry_for(w.id), col, window_end_is_exit=True)
            ]
    else:
        errors += contract.fit_errors(doc.stations, doc.entry_refs())
    errors += contract.role_errors(doc.stations, roles)
    return errors


def _type_errors(doc: WorkflowDoc) -> list[str]:
    errors = []
    for s in doc.stations:
        if s.type is None:
            continue
        if s.type not in contract.STATION_TYPES and s.type != contract.CUSTOM:
            errors.append(f"station '{s.id}': unknown type '{s.type}' (one of {', '.join(contract.STATION_TYPES)})")
        if s.handler:
            errors.append(f"station '{s.id}' names a type; drop `handler` (the type picks it per target)")
        for target, agent in s.target_agents.items():
            if agent not in doc.agents:
                errors.append(f"station '{s.id}' uses unknown agent '{agent}' for {target}")
            else:
                handler = s.handler_for(target)
                if handler is None:
                    errors.append(f"station '{s.id}': its type has no {target} implementation for agent '{agent}'")
                else:
                    errors += _role_errors(s.id, handler, doc.agents[agent])
    return errors


def _window_errors(doc: WorkflowDoc) -> list[str]:
    from agent_factory.models import ChangeKind

    errors: list[str] = []
    ids = [w.id for w in doc.windows]
    if len(ids) != len(set(ids)):
        errors.append("window ids must be unique")
    seen: dict[tuple[str, str], str] = {}
    for w in doc.windows:
        for k in w.kinds:
            if k not in {c.value for c in ChangeKind}:
                errors.append(f"window '{w.id}': unknown change kind '{k}'")
            elif (w.target, k) in seen:
                errors.append(f"window '{w.id}': {k} changes on {w.target} already run on '{seen[(w.target, k)]}'")
            else:
                seen[(w.target, k)] = w.id
        for e in w.entry:
            try:
                contract.parse_ref(e)
            except ValueError as exc:
                errors.append(f"window '{w.id}' entry: {exc}")
        try:
            doc.bounds(w)
        except ValueError as exc:
            errors.append(str(exc))
    known = set(ids)
    for s in doc.stations:
        aw = s.applies_when
        for name in [*(aw.windows or []), *(aw.not_windows or [])]:
            if name not in known:
                errors.append(f"station '{s.id}': applies_when names unknown window '{name}'")
        for key in s.fail_on_by:
            if key not in known and key not in ("factory", "repo"):
                errors.append(f"station '{s.id}': fail_on_by key '{key}' is neither a window nor a target")
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
    presets_file = path / "presets.yaml"  # windows and presets over the lane (ADR-0037 §4)
    windows = (
        (yaml.safe_load(presets_file.read_text()) or {}).get("windows", [])
        if presets_file.is_file()
        else raw.get("windows", [])
    )
    digest = hashlib.sha256()
    extra = [f for f in (evals_file, presets_file) if f.is_file()]
    for f in [wf_file, *sorted((path / "agents").glob("*.md")), *sorted((path / "docs").glob("*.md")), *extra]:
        digest.update(f.read_bytes())
    return WorkflowDoc.model_validate(
        {
            **raw,
            "evals": evals,
            "windows": windows,
            "agents": agents,
            "docs": docs,
            "template": raw.get("template") or raw.get("blueprint") or path.name,
            "template_hash": digest.hexdigest()[:16],
        }
    )


def export_workflow_dir(doc: WorkflowDoc, path: Path) -> None:
    (path / "agents").mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "name": doc.name,
        "description": doc.description,
        "template": doc.template,
        "stations": [
            s.model_dump(exclude_none=True, exclude_defaults=True) | {"id": s.id, "kind": s.kind} for s in doc.stations
        ],
    }
    if list(doc.entry) != list(DEFAULT_ENTRY):
        data["entry"] = list(doc.entry)
    if doc.eval_gate != "off":
        data["eval_gate"] = doc.eval_gate
    if doc.skill_pins:
        data["skill_pins"] = dict(doc.skill_pins)  # imported skills this version was published with
    (path / "workflow.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    if doc.windows:
        (path / "presets.yaml").write_text(
            yaml.safe_dump({"windows": [w.model_dump(exclude_defaults=True) for w in doc.windows]}, sort_keys=False)
        )
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
