"""Domain + API schemas. These pydantic models ARE the OpenAPI contract the
TypeScript UI is generated from (web/src/api/schema.d.ts)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field

from agent_factory.workflow import AgentSpec, RefDoc


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    needs_input = "needs_input"  # intake has blocking questions
    paused_limits = "paused_limits"  # usage window near the cap
    held = "held"  # budget exhausted / missing evidence -> human
    interrupted = "interrupted"  # process restarted mid-run
    awaiting_feedback = "awaiting_feedback"  # delivered; the feedback gate is open
    cancelled = "cancelled"
    failed = "failed"  # engine error


TERMINAL = {RunStatus.awaiting_feedback, RunStatus.cancelled, RunStatus.failed}
RESUMABLE = {RunStatus.held, RunStatus.interrupted, RunStatus.paused_limits}


class StationOutcome(StrEnum):
    passed = "passed"
    failed = "failed"
    needs_input = "needs_input"
    held = "held"
    paused_limits = "paused_limits"


class EventKind(StrEnum):
    station_started = "station_started"
    station_finished = "station_finished"
    decision = "decision"  # append-only decision log
    log = "log"
    agent = "agent"  # agent progress / text
    command = "command"  # deterministic command + tail of output
    status = "status"
    feedback = "feedback"


# ---------------------------------------------------------------- records --
class Order(BaseModel):
    id: str
    title: str
    requirements: str
    product_line: str
    product_slug: str
    created_at: datetime
    latest_run_id: str | None = None
    latest_status: RunStatus | None = None
    node_port: int | None = None
    host_port: int | None = None
    app_url: str | None = None
    repo_url: str | None = None


class Run(BaseModel):
    id: str
    order_id: str
    iteration: int
    status: RunStatus
    current_station: str | None = None
    workflow_id: str | None = None  # the product line's workflow (None on runs from before workflows)
    workflow_version: int = Field(  # the workflow version this run is pinned to
        default=1, validation_alias=AliasChoices("workflow_version", "line_version")
    )
    attempts: dict[str, int] = Field(default_factory=dict)
    loops: int = 0
    change_request: str | None = None  # feedback that started this iteration
    last_failure: str | None = None  # evidence handed to the repair station
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    resume_at: datetime | None = None
    summary: str | None = None
    cost_usd: float = 0.0
    created_at: datetime
    updated_at: datetime


class Event(BaseModel):
    id: int
    run_id: str
    ts: datetime
    station: str | None = None
    kind: EventKind
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class Feedback(BaseModel):
    id: int
    order_id: str
    run_id: str | None
    text: str
    created_at: datetime


# ------------------------------------------------------------- API inputs --
class CreateOrderInput(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    requirements: str = Field(min_length=10, max_length=20_000)
    product_line: str = "fastapi-service"


class AnswersInput(BaseModel):
    answers: list[str] = Field(min_length=1)


class FeedbackInput(BaseModel):
    text: str = Field(min_length=3, max_length=20_000)


class DecisionInput(BaseModel):
    run_id: str
    station: str
    decision: str = Field(min_length=3)
    rationale: str = ""


# ------------------------------------------------------------ API outputs --
class StationView(BaseModel):
    id: str
    kind: str
    role: str | None  # agent spec id for agent stations
    handler: str
    on_fail: str | None = None
    next: str | None = None
    repair: bool
    state: str  # pending | running | passed | failed | held
    attempts: int


class RunDetail(BaseModel):
    run: Run
    order: Order
    stations: list[StationView]


class OrderDetail(BaseModel):
    order: Order
    runs: list[Run]
    feedback: list[Feedback]


class HealthView(BaseModel):
    status: str
    mode: str
    model_auth: bool
    github: bool
    active_runs: int


class ConfigView(BaseModel):
    name: str
    mode: str
    workflows: dict[str, int]  # product line -> active workflow version
    product_lines: dict[str, str]
    policies: dict[str, str]
    gates: list[str]


class AgentView(BaseModel):
    spec: AgentSpec
    model_resolved: str
    effective_tools: list[str]
    observe_only: bool
    used_by: list[str]  # station ids that run this agent


class WorkflowView(BaseModel):
    workflow_id: str
    version: int
    active: bool
    note: str
    name: str
    description: str
    template: str | None
    template_update_available: bool
    stations: list[StationView]
    agents: list[AgentView]
    docs: list[RefDoc] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    environment: str = "local"  # where this product line's delivery steps run
    delivery: list[DeliveryBinding] = Field(default_factory=list)


class DraftView(BaseModel):
    workflow_id: str
    base_version: int
    active_version: int
    stale: bool  # the active version moved on since this draft was started
    dirty: bool  # the draft differs from its base version
    updated_at: str | None
    template: str | None = None
    problems: list[str]  # publishing is blocked while this is non-empty
    warnings: list[str] = Field(default_factory=list)  # advice; does not block publishing
    skill_updates: dict[str, str] = Field(default_factory=dict)  # imported skill -> newer commit, applied on publish
    stations: list[StationView]
    agents: list[AgentView]
    docs: list[RefDoc]


class SkillInfo(BaseModel):
    name: str
    description: str
    source: Literal["builtin", "github"] = "builtin"
    repo: str | None = None  # github imports: owner/repo, folder, ref and the pinned commit
    path: str | None = None
    ref: str | None = None
    sha: str | None = None
    scripts: list[str] = Field(default_factory=list)
    installed_at: str | None = None


class SkillSourceInput(BaseModel):
    repo: str = Field(min_length=3, max_length=200)  # owner/repo or https://github.com/owner/repo
    path: str = Field(default="", max_length=300)  # folder that contains SKILL.md
    ref: str = Field(default="main", min_length=1, max_length=200)


class InstallSkillInput(SkillSourceInput):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")  # the commit that was reviewed in the preview
    accept_scripts: bool = False


class SkillVersionInfo(BaseModel):
    sha: str
    ref: str
    installed_at: str
    current: bool


class SkillDetail(BaseModel):
    skill: SkillInfo
    files: dict[str, str]
    versions: list[SkillVersionInfo]
    used_by: list[str]  # workflows whose active version or draft uses it


class CatalogView(BaseModel):
    presets: dict[str, list[str]]
    observe_only_presets: list[str]
    extra_tools: list[str]
    model_tiers: dict[str, str]
    skills: list[SkillInfo]
    handlers: dict[str, list[str]]
    requirements: dict[str, dict[str, bool]]  # handler -> {write, shell} needs
    max_previous_iterations: int
    max_doc_chars: int
    max_learnings_chars: int


class DuplicateAgentInput(BaseModel):
    new_id: str


class StationAgentInput(BaseModel):
    agent: str


class AddStationInput(BaseModel):
    id: str
    kind: Literal["agent", "check"]
    agent: str | None = None
    handler: str | None = None
    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool = False
    position: int | None = None  # index in the lane; end when omitted


class UpdateStationInput(BaseModel):
    """Only the fields that are sent are changed; send null to clear a route."""

    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool | None = None
    handler: str | None = None
    agent: str | None = None


class ReorderStationsInput(BaseModel):
    order: list[str]


class PublishInput(BaseModel):
    note: str = Field(min_length=3, max_length=200)


class ReadinessView(BaseModel):
    state: str  # ready | degraded | failed | unknown
    reasons: list[str] = Field(default_factory=list)


class IntegrationView(BaseModel):
    id: str
    provider: str
    capabilities: list[str]
    settings: dict[str, Any] = Field(default_factory=dict)  # non-secret options only
    readiness: ReadinessView
    used_by: list[str] = Field(default_factory=list)  # environments


class EnvironmentView(BaseModel):
    name: str
    bindings: dict[str, str]  # capability -> integration id
    product_lines: list[str] = Field(default_factory=list)


class DeliveryView(BaseModel):
    integrations: list[IntegrationView]
    environments: list[EnvironmentView]


class DeliveryBinding(BaseModel):
    capability: str
    integration: str
    provider: str


class WorkflowSummary(BaseModel):
    workflow_id: str  # = product line id
    product_line: str  # product line description
    environment: str = "local"  # delivery environment of the product line
    active_version: int
    description: str
    template: str | None
    stations: int
    agents: int
    draft_dirty: bool


class TemplateInfo(BaseModel):
    name: str
    description: str
    stations: list[str]
    agents: list[str]
    docs: list[str]


class FromTemplateInput(BaseModel):
    template: str


class FromGitHubInput(BaseModel):
    repo: str = Field(description="owner/name or https://github.com/owner/name")
    path: str = Field(default="", description="folder containing workflow.yaml")
    ref: str = Field(default="main", description="branch, tag or commit sha (pinned to a commit on import)")
