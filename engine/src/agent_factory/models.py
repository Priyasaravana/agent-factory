"""Domain + API schemas. These pydantic models ARE the OpenAPI contract the
TypeScript UI is generated from (web/src/api/schema.d.ts)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agent_factory.line import AgentSpec, RefDoc


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
    line_version: int = 1  # the line version this run is pinned to
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
    line_version: int
    product_lines: dict[str, str]
    stations: list[StationView]
    policies: dict[str, str]
    gates: list[str]


class AgentView(BaseModel):
    spec: AgentSpec
    model_resolved: str
    effective_tools: list[str]
    observe_only: bool
    used_by: list[str]  # station ids that run this agent


class LineView(BaseModel):
    line_id: str
    version: int
    active: bool
    note: str
    name: str
    description: str
    blueprint: str | None
    blueprint_update_available: bool
    stations: list[StationView]
    agents: list[AgentView]
    docs: list[RefDoc] = Field(default_factory=list)


class DraftView(BaseModel):
    base_version: int
    active_version: int
    stale: bool  # the active version moved on since this draft was started
    dirty: bool  # the draft differs from its base version
    updated_at: str | None
    problems: list[str]  # publishing is blocked while this is non-empty
    stations: list[StationView]
    agents: list[AgentView]
    docs: list[RefDoc]


class SkillInfo(BaseModel):
    name: str
    description: str
    vendored: bool


class CatalogView(BaseModel):
    presets: dict[str, list[str]]
    observe_only_presets: list[str]
    extra_tools: list[str]
    model_tiers: dict[str, str]
    skills: list[SkillInfo]
    max_previous_iterations: int
    max_doc_chars: int
    max_learnings_chars: int


class DuplicateAgentInput(BaseModel):
    new_id: str


class StationAgentInput(BaseModel):
    agent: str


class PublishInput(BaseModel):
    note: str = Field(min_length=3, max_length=200)
