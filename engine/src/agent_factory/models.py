"""Domain + API schemas. These pydantic models ARE the OpenAPI contract the
TypeScript UI is generated from (web/src/api/schema.d.ts)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator

from agent_factory.workflow import AgentSpec, EvalCase, HandlerInfo, Phase, RefDoc


class ChangeStatus(StrEnum):
    queued = "queued"
    running = "running"
    needs_input = "needs_input"  # intake has blocking questions
    paused_limits = "paused_limits"  # usage window near the cap
    held = "held"  # budget exhausted / missing evidence -> human
    interrupted = "interrupted"  # process restarted mid-run
    awaiting_feedback = "awaiting_feedback"  # delivered; the feedback gate is open
    awaiting_approval = "awaiting_approval"  # spec review gate: a person approves the spec before build
    awaiting_risk_approval = "awaiting_risk_approval"  # risky change: a second admin approves (ADR-0027)
    cancelled = "cancelled"
    failed = "failed"  # engine error


TERMINAL = {ChangeStatus.awaiting_feedback, ChangeStatus.cancelled, ChangeStatus.failed}
RESUMABLE = {ChangeStatus.held, ChangeStatus.interrupted, ChangeStatus.paused_limits}


class ChangeKind(StrEnum):
    """What a change is for (ADR-0030). Every request is a change to a product."""

    new = "new"  # build the product from its requirements (its first change)
    feature = "feature"  # add or change behaviour
    bug = "bug"  # behaviour differs from a requirement: reproduce with a failing test, then fix
    upkeep = "upkeep"  # keep behaviour: dependencies, tooling, docs, refactors
    assess = "assess"  # report on a repo without changing it (existing repos, next)
    remove = "remove"  # remove a feature; the product owner confirms the list first (later)


# kinds a person can ask for today; the others are part of the model and come with their workflows
AVAILABLE_KINDS = {ChangeKind.new, ChangeKind.feature, ChangeKind.bug, ChangeKind.upkeep, ChangeKind.assess}
# kinds that iterate on a delivered product
ITERATION_KINDS = (ChangeKind.feature, ChangeKind.bug, ChangeKind.upkeep)


class ChangeSource(StrEnum):
    """Where a change came from (ADR-0030). `ui`: a signed-in member, in the UI or the API."""

    ui = "ui"
    ticket = "ticket"  # a ticket system, accepted by a linked member (later)
    schedule = "schedule"  # scheduled upkeep (later)


class ProductTarget(StrEnum):
    """What the factory works on (ADR-0030)."""

    new = "new"  # built here from a blueprint's template
    repo = "repo"  # a team's existing repository (next)
    factory = "factory"  # the factory itself, with the safety core out of bounds (later)


AVAILABLE_TARGETS = {ProductTarget.new, ProductTarget.repo}
# what each target's changes may be (ADR-0031): (first change, later changes)
TARGET_KINDS: dict[ProductTarget, tuple[ChangeKind, tuple[ChangeKind, ...]]] = {
    ProductTarget.new: (ChangeKind.new, ITERATION_KINDS),
    ProductTarget.repo: (ChangeKind.assess, (ChangeKind.assess,)),  # changes → PRs come next
}


class StationOutcome(StrEnum):
    passed = "passed"
    failed = "failed"
    needs_input = "needs_input"
    held = "held"
    paused_limits = "paused_limits"
    needs_approval = "needs_approval"  # a risky change waits for a person (ADR-0027)


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
class Product(BaseModel):
    id: str
    title: str
    requirements: str
    # "spec": the requirements are an existing specification (keep its wording/numbering)
    requirements_format: Literal["prose", "spec"] = "prose"
    blueprint: str = Field(validation_alias=AliasChoices("blueprint", "product_line"))
    target: ProductTarget = ProductTarget.new
    slug: str = Field(validation_alias=AliasChoices("slug", "product_slug"))
    created_at: datetime
    latest_change_id: str | None = Field(
        default=None, validation_alias=AliasChoices("latest_change_id", "latest_run_id")
    )
    latest_status: ChangeStatus | None = None
    node_port: int | None = None
    host_port: int | None = None
    app_url: str | None = None
    repo_url: str | None = None  # published repo; for an existing repo (target repo), its web URL
    repo_ref: str | None = None  # an existing repo's branch (ADR-0031)
    created_by: str | None = None
    archived_at: datetime | None = None  # archived: app removed from the cluster, port freed, history kept
    archived_by: str | None = None  # signed-in user who submitted it
    eval_run_id: str | None = None  # an evaluation case (ADR-0025): hidden from products and Outcomes


class Change(BaseModel):
    id: str
    product_id: str = Field(validation_alias=AliasChoices("product_id", "order_id"))
    iteration: int
    status: ChangeStatus
    current_station: str | None = None
    workflow_id: str | None = None  # the blueprint's workflow (None on runs from before workflows)
    workflow_version: int = Field(  # the workflow version this change is pinned to
        default=1, validation_alias=AliasChoices("workflow_version", "line_version")
    )
    # the work item (ADR-0030): what this change is for, where it came from and who asked
    kind: ChangeKind = ChangeKind.new
    source: ChangeSource = ChangeSource.ui
    requested_by: str | None = None
    attempts: dict[str, int] = Field(default_factory=dict)
    loops: int = 0
    change_request: str | None = None  # what was asked for this iteration (feedback, bug report, upkeep)
    last_failure: str | None = None  # evidence handed to the repair station
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    resume_at: datetime | None = None
    summary: str | None = None
    cost_usd: float = 0.0
    spec_approved_by: str | None = None  # spec review gate
    # change risk (ADR-0027): the findings now waiting, and every approval given
    risk_hold: RiskHold | None = None
    risk_approvals: list[RiskApproval] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)  # requested spec changes, fed to intake/design
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _kind_of_older_changes(cls, data: Any) -> Any:
        """Changes stored before work items have no kind: the first iteration built the
        product, later ones came from feedback."""
        if isinstance(data, dict) and "kind" not in data and "iteration" in data:
            data = {**data, "kind": ChangeKind.new if data["iteration"] == 1 else ChangeKind.feature}
        return data


class RiskHold(BaseModel):
    digest: str  # identity of the hold findings (risk.digest)
    station: str
    since: datetime
    findings: int
    requested_by: str | None = None  # who asked for the change (the product's creator)


class RiskApproval(BaseModel):
    digest: str  # an approval covers exactly these findings
    by: str
    reason: str
    at: datetime
    break_glass: bool = False  # the requester approved their own change on a single-admin install


class Event(BaseModel):
    id: int
    change_id: str
    ts: datetime
    station: str | None = None
    kind: EventKind
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class Transition(BaseModel):
    """One change status change (ADR-0019)."""

    change_id: str
    ts: datetime
    from_status: ChangeStatus | None = None
    to_status: ChangeStatus


# ----------------------------------------------------------- outcomes (ADR-0019) --
class DurationStat(BaseModel):
    median_s: float | None = None
    p90_s: float | None = None
    n: int = 0


class TimeSplit(BaseModel):
    """Seconds of run time in the window, by who the change was waiting on."""

    agents_s: float = 0.0  # queued or running: the factory is working
    person_s: float = 0.0  # questions, held, spec review, interrupted: a person must act
    system_s: float = 0.0  # paused for the model usage window
    suspended_s: float = 0.0  # the factory host was asleep (computer slept, Docker paused): nobody worked


class HumanTouches(BaseModel):
    answered_questions: int = 0  # unplanned: intake could not assume safely
    rescued: int = 0  # unplanned: a held run was resumed
    restarts: int = 0  # unplanned: resumed after the factory restarted
    spec_reviews: int = 0  # planned: the spec review gate (approve or request changes)
    risk_approvals: int = 0  # planned: a risky change approved or sent back (ADR-0027)


class WaitingItem(BaseModel):
    product_id: str
    product_title: str
    change_id: str
    iteration: int
    status: ChangeStatus
    since: datetime
    waiting_s: float
    owner: str  # who should act: the product's creator, "an admin", or "system"
    action: str


class WorkflowOutcome(BaseModel):
    workflow_id: str
    deliveries: int
    autonomy_ratio: float | None = None
    cost_per_delivery_usd: float | None = None
    lead_time_median_s: float | None = None


class WeekPoint(BaseModel):
    week_start: str  # ISO date
    deliveries: int
    cost_usd: float
    lead_time_median_s: float | None = None


class StationEffort(BaseModel):
    """Agent effort per station over the Outcomes window (ADR-0022)."""

    station: str
    calls: int
    cost_usd: float
    turns_median: float | None = None
    duration_median_s: float | None = None
    tool_calls: int = 0
    denied: int = 0


class PillarScore(BaseModel):
    """One quality pillar (ADR-0024): readiness signals passed out of applicable.
    `signals` = distinct signals tagged with the pillar; 0 means uncovered (never 100%)."""

    pillar: str
    title: str
    signals: int = 0
    passed: int = 0
    applicable: int = 0
    coverage: float | None = None  # passed / applicable; None when uncovered or nothing assessed
    apps_full: int = 0  # apps passing every applicable signal of this pillar


class AppQuality(BaseModel):
    """A live app's pillar scores, from its latest delivered iteration."""

    product_id: str
    product_title: str
    change_id: str
    pillars: list[PillarScore] = Field(default_factory=list)


class OutcomesView(BaseModel):
    window_days: int
    since: datetime
    generated_at: datetime
    # delivery (DORA-style, see docs/outcomes.md for the exact definitions)
    deliveries: int
    deliveries_per_week: float
    lead_time: DurationStat
    change_failure_rate: float | None = None
    finished: int = 0
    failed_or_rescued: int = 0
    recovery_time: DurationStat
    # autonomy
    autonomy_ratio: float | None = None
    touches: HumanTouches
    unplanned_touches_per_delivery: float | None = None
    # cost and effort
    cost_total_usd: float
    cost_per_delivery_usd: float | None = None
    fix_loops_per_delivery: float | None = None
    # where the time goes
    time_split: TimeSplit
    changes_with_timeline: int = 0
    changes_in_window: int = 0
    # quality of what is live
    products: int = 0
    products_level3: int = 0
    requirements_total: int = 0
    requirements_verified_live: int = 0
    # who needs to act now
    waiting: list[WaitingItem] = Field(default_factory=list)
    # quality pillars of what is live (ADR-0024)
    quality_pillars: list[PillarScore] = Field(default_factory=list)
    quality_by_app: list[AppQuality] = Field(default_factory=list)
    # agent effort (ADR-0022)
    effort_by_station: list[StationEffort] = Field(default_factory=list)
    guardrail_denials: int = 0
    by_workflow: list[WorkflowOutcome] = Field(default_factory=list)
    weekly: list[WeekPoint] = Field(default_factory=list)


class Feedback(BaseModel):
    id: int
    product_id: str
    change_id: str | None
    text: str
    created_at: datetime


# ------------------------------------------------------------- API inputs --
class CreateProductInput(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    requirements: str = Field(min_length=10, max_length=60_000)
    blueprint: str = Field(default="fastapi-service", validation_alias=AliasChoices("blueprint", "product_line"))
    requirements_format: Literal["prose", "spec"] = "prose"
    target: ProductTarget = ProductTarget.new  # only `new` is available yet (ADR-0030)


class OnboardRepoInput(BaseModel):
    """An existing repository to assess (ADR-0031): public https URLs on allowed hosts."""

    repo_url: str = Field(min_length=10, max_length=300, description="https://github.com/owner/repo")
    branch: str | None = Field(
        default=None, max_length=200, pattern=r"^[A-Za-z0-9._/-]+$", description="default: the repo's default branch"
    )
    title: str | None = Field(default=None, min_length=3, max_length=120, description="default: owner/repo")
    notes: str | None = Field(default=None, max_length=5_000, description="what the team wants to know")


class SpecReviewInput(BaseModel):
    comment: str = Field(min_length=3, max_length=5_000)


class SpecEditInput(BaseModel):
    """Direct edits while the spec awaits review (requirements change via request-changes,
    so intake keeps scenarios and requirement ids consistent)."""

    product: str | None = Field(default=None, max_length=60_000)  # docs/spec.md
    technical: str | None = Field(default=None, max_length=60_000)  # docs/design.md


class RequirementView(BaseModel):
    id: str
    title: str
    detail: str = ""


class ScenarioView(BaseModel):
    id: str
    given: str
    when: str
    then: str
    covers: list[str] = Field(default_factory=list)


class TraceRow(BaseModel):
    """One requirement, traced: scenarios -> tagged tests -> live acceptance."""

    id: str
    title: str
    scenarios: list[str] = Field(default_factory=list)
    tests: int = 0
    holdout: list[dict[str, Any]] = Field(default_factory=list)  # {scenario, passed} from the latest acceptance
    no_live_check: str | None = None  # why no hidden scenario can check it on the running app


class ReviewRequirement(BaseModel):
    id: str
    status: str  # implemented | partial | missing
    where: str = ""


class ReviewFinding(BaseModel):
    severity: str  # blocker | major | minor
    message: str
    file: str = ""
    requirement: str = ""


class ReviewView(BaseModel):
    """The latest spec review of the change (ADR-0020); the engine's judgement, not the agent's."""

    passed: bool
    implemented: int
    total: int
    summary: str = ""
    requirements: list[ReviewRequirement] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)


class SpecView(BaseModel):
    change_id: str
    status: str
    gate: str  # off | first | always (the change's workflow version)
    approved_by: str | None = None
    product: str = ""  # docs/spec.md
    technical: str = ""  # docs/design.md
    openapi: str = ""
    requirements: list[RequirementView] = Field(default_factory=list)
    acceptance: list[ScenarioView] = Field(default_factory=list)
    holdout_count: int = 0  # hidden scenarios are counted, never shown
    changes: dict[str, list[str]] = Field(default_factory=dict)  # added / changed / removed requirement ids
    review_notes: list[str] = Field(default_factory=list)
    traceability: list[TraceRow] = Field(default_factory=list)
    review: ReviewView | None = None  # latest spec review; None when the workflow has no review station


class WorkflowSettingsInput(BaseModel):
    """Draft settings; a field left out is unchanged."""

    spec_review: Literal["off", "first", "always"] | None = None
    learn_from_runs: bool | None = None  # suggest learnings after runs that needed help (ADR-0021)
    eval_gate: Literal["off", "warn", "block"] | None = None  # evaluation gate (ADR-0025)


class AgentCallView(BaseModel):
    """One agent call in a change (ADR-0022)."""

    station: str
    role: str
    model: str
    ok: bool
    error: str = ""
    turns: int = 0
    duration_s: float = 0.0
    cost_usd: float = 0.0
    tool_calls: int = 0
    tools: dict[str, int] = Field(default_factory=dict)
    denied: int = 0
    transcript: str | None = None
    transcript_truncated: bool = False
    at: datetime


class DenialView(BaseModel):
    station: str
    role: str
    tool: str
    input: str
    reason: str
    at: datetime


class ChangeCallsView(BaseModel):
    calls: list[AgentCallView] = Field(default_factory=list)
    denials: list[DenialView] = Field(default_factory=list)


class EvidenceFile(BaseModel):
    path: str
    sha256: str
    bytes: int


class EvidenceView(BaseModel):
    """A change's sealed evidence (ADR-0023) and whether it still matches its seal."""

    sealed: bool
    sealed_at: datetime | None = None
    sha256: str | None = None  # of manifest.json, as recorded in the event log
    status_at_seal: str | None = None
    commit: str | None = None
    files: list[EvidenceFile] = Field(default_factory=list)
    total_bytes: int = 0
    intact: bool = False
    manifest_ok: bool = False
    changed: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)


class EvalCaseResult(BaseModel):
    """One evaluation case on one workflow version (ADR-0025)."""

    case_id: str
    title: str
    product_id: str | None = Field(default=None, validation_alias=AliasChoices("product_id", "order_id"))
    change_id: str | None = Field(default=None, validation_alias=AliasChoices("change_id", "run_id"))
    # queued/running until the change stops; then delivered | failed | held | needed_input | cancelled
    status: str = "queued"
    final: bool = False
    cost_usd: float = 0.0
    fix_loops: int = 0
    lead_time_s: float | None = None
    readiness_level: int | None = None
    requirements: int = 0
    requirements_verified: int = 0
    unplanned_touch: bool = False  # canned answers were needed
    note: str | None = None


class EvalSummary(BaseModel):
    """What a version scored on the suite. Ratios are None when undefined."""

    cases: int
    delivered: int
    pass_rate: float
    autonomy: float | None = None  # delivered with no unplanned touch / delivered
    cost_usd: float
    cost_per_delivery_usd: float | None = None
    fix_loops_per_case: float
    lead_time_median_s: float | None = None
    level3_share: float | None = None  # of delivered cases
    verified_live_share: float | None = None  # requirements verified live / requirements, delivered cases


class EvalSide(BaseModel):
    version: int
    results: list[EvalCaseResult] = Field(default_factory=list)
    summary: EvalSummary | None = None


class EvalVerdict(BaseModel):
    passed: bool
    compared_to: int | None = None  # baseline version
    regressions: list[str] = Field(default_factory=list)  # block a gated activation
    warnings: list[str] = Field(default_factory=list)  # reported, never blocking


class EvalOverride(BaseModel):
    by: str
    reason: str
    at: datetime


class EvalRun(BaseModel):
    """Evaluation of a workflow version against a baseline version on the fixed suite (ADR-0025)."""

    id: str
    workflow_id: str
    suite_hash: str
    trigger: Literal["publish", "manual"] = "manual"
    started_by: str
    created_at: datetime
    finished_at: datetime | None = None
    status: Literal["running", "done", "cancelled", "not_started"] = "running"
    error: str | None = None  # why it could not start (not_started)
    candidate: EvalSide
    baseline: EvalSide | None = None  # run alongside, when no earlier result of the same suite exists
    baseline_from: str | None = None  # id of the earlier evaluation whose result is the baseline
    verdict: EvalVerdict | None = None
    activated: bool = False  # the gate promoted the candidate
    override: EvalOverride | None = None


class EvalSuiteInput(BaseModel):
    cases: list[EvalCase] = Field(max_length=8)


class StartEvalInput(BaseModel):
    version: int | None = None  # default: the newest version


class ActivateInput(BaseModel):
    # activating a newer version whose gate is `block` without a passing evaluation
    override_reason: str | None = Field(default=None, min_length=10, max_length=500)


class LearningProposal(BaseModel):
    """A lesson the retro suggests for one agent after a change that needed help (ADR-0021).
    Nothing changes until an admin accepts it into the workflow draft and publishes."""

    id: str
    workflow_id: str
    workflow_version: int
    agent: str
    lesson: str
    why: str
    evidence: str
    change_id: str = Field(validation_alias=AliasChoices("change_id", "run_id"))
    product_id: str = Field(validation_alias=AliasChoices("product_id", "order_id"))
    status: Literal["pending", "accepted", "rejected"] = "pending"
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None


class AnswersInput(BaseModel):
    answers: list[str] = Field(min_length=1)


class FeedbackInput(BaseModel):
    """A change to a delivered product: what to do and what kind of change it is (ADR-0030)."""

    text: str = Field(min_length=3, max_length=20_000)
    kind: ChangeKind = ChangeKind.feature  # feature | bug | upkeep


class RiskDecisionInput(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class DecisionInput(BaseModel):
    change_id: str = Field(validation_alias=AliasChoices("change_id", "run_id"))
    station: str
    decision: str = Field(min_length=3)
    rationale: str = ""


# ------------------------------------------------------------ API outputs --
class StationView(BaseModel):
    id: str
    kind: str
    role: str | None  # agent spec id for agent stations
    handler: str
    label: str = ""  # what people read: the built-in handler's name, or the custom id (ADR-0028)
    phase: str = "plan"  # DevOps phase the station is shown under
    on_fail: str | None = None
    next: str | None = None
    repair: bool
    state: str  # pending | running | passed | failed | held
    attempts: int


class RiskFindingView(BaseModel):
    rule: str
    category: int
    category_title: str
    severity: str  # hold | note
    title: str
    file: str
    line: int | None = None
    snippet: str = ""


class ChangeRiskView(BaseModel):
    """The latest change-risk check of a change (ADR-0027) and who may approve it."""

    digest: str
    files_changed: int = 0
    findings: list[RiskFindingView] = Field(default_factory=list)
    holds: int = 0
    approved_by: str | None = None
    waiting: bool = False  # the change is waiting for an approval of these findings
    requested_by: str | None = None
    break_glass: bool = True  # the requester may approve their own after the cooling-off delay
    self_approval_at: datetime | None = None  # when the requester's break-glass approval opens


class AssessmentFinding(BaseModel):
    rule: str
    severity: str  # high | medium | low
    area: str
    title: str
    detail: str = ""
    at: list[str] = Field(default_factory=list)  # file[:line], one per place it was found


class AssessmentSignal(BaseModel):
    id: str
    level: int
    title: str
    ok: bool
    pillar: str
    hint: str = ""


class AssessmentView(BaseModel):
    """An existing repo's assessment (ADR-0031): engine facts, plus what the assessor reported
    and the engine kept."""

    change_id: str
    repo: dict[str, Any] = Field(default_factory=dict)  # slug, url, ref, commit, files
    stack: dict[str, Any] = Field(default_factory=dict)
    stack_summary: str = ""
    level: int = 0
    points: int = 0
    max_points: int = 0
    signals: list[AssessmentSignal] = Field(default_factory=list)
    pillars: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[AssessmentFinding] = Field(default_factory=list)
    summary: str = ""
    risks: list[dict[str, Any]] = Field(default_factory=list)
    test_gaps: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    agents_md: str = ""
    markdown: str = ""
    assessed_at: str = ""


class ChangeDetail(BaseModel):
    change: Change
    product: Product
    stations: list[StationView]
    risk: ChangeRiskView | None = None


class ProductDetail(BaseModel):
    product: Product
    changes: list[Change]
    feedback: list[Feedback]


class HealthView(BaseModel):
    status: str
    mode: str
    model_auth: bool
    github: bool
    active_changes: int
    # agent sandbox (ADR-0014): off | preparing | ready | failed
    sandbox: str = "off"
    sandbox_detail: list[str] = Field(default_factory=list)
    # worst state of the latest readiness checks (ADR-0015): ready | degraded | failed | unknown
    preflight: str = "unknown"


class ConfigView(BaseModel):
    name: str
    mode: str
    workflows: dict[str, int]  # blueprint -> active workflow version
    blueprints: dict[str, str]  # blueprints that build new products
    repo_blueprints: dict[str, str] = Field(default_factory=dict)  # blueprints for existing repos (ADR-0031)
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
    spec_review: str = "off"  # off | first | always
    learn_from_runs: bool = True
    eval_gate: str = "off"  # off | warn | block (ADR-0025)
    evals: list[EvalCase] = Field(default_factory=list)
    environment: str = "local"  # where this blueprint's delivery steps run
    delivery: list[DeliveryBinding] = Field(default_factory=list)


class DraftView(BaseModel):
    workflow_id: str
    base_version: int
    active_version: int
    stale: bool  # the active version moved on since this draft was started
    next_version: int = 0  # the number publishing will give it
    dirty: bool  # the draft differs from its base version
    updated_at: str | None
    template: str | None = None
    problems: list[str]  # publishing is blocked while this is non-empty
    warnings: list[str] = Field(default_factory=list)  # advice; does not block publishing
    skill_updates: dict[str, str] = Field(default_factory=dict)  # imported skill -> newer commit, applied on publish
    stations: list[StationView]
    agents: list[AgentView]
    docs: list[RefDoc]
    spec_review: str = "off"
    learn_from_runs: bool = True
    eval_gate: str = "off"  # off | warn | block (ADR-0025)
    evals: list[EvalCase] = Field(default_factory=list)


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


class PhaseInfo(BaseModel):
    id: str
    label: str


class CatalogView(BaseModel):
    presets: dict[str, list[str]]
    observe_only_presets: list[str]
    extra_tools: list[str]
    model_tiers: dict[str, str]
    skills: list[SkillInfo]
    handlers: dict[str, list[str]]
    handler_info: dict[str, HandlerInfo] = Field(default_factory=dict)  # label, phase, hint (ADR-0028)
    phases: list[PhaseInfo] = Field(default_factory=list)  # the DevOps loop, in run order
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
    phase: Phase | None = None  # custom steps only; built-in handlers have their own
    position: int | None = None  # index in the lane; end when omitted


class UpdateStationInput(BaseModel):
    """Only the fields that are sent are changed; send null to clear a route."""

    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool | None = None
    handler: str | None = None
    agent: str | None = None
    phase: Phase | None = None


class ReorderStationsInput(BaseModel):
    order: list[str]


class PublishInput(BaseModel):
    note: str = Field(min_length=3, max_length=200)


class CheckView(BaseModel):
    """One readiness check. `failed` blocks what it guards (new products and/or new
    iterations); `degraded` is shown but never blocks."""

    id: str
    title: str
    area: str  # agents | build | registry | scan | deploy | publish | workflow
    state: str  # ready | degraded | failed
    reasons: list[str] = Field(default_factory=list)
    integration: str | None = None
    blocks: list[str] = Field(default_factory=list)  # "product", "change"


class PreflightView(BaseModel):
    blueprint: str
    environment: str
    state: str  # ready | degraded | failed
    checked_at: datetime
    duration_ms: int
    checks: list[CheckView]


class ReadinessView(BaseModel):
    state: str  # ready | degraded | failed | unknown
    reasons: list[str] = Field(default_factory=list)


class IntegrationView(BaseModel):
    id: str
    provider: str
    capabilities: list[str]
    settings: dict[str, Any] = Field(default_factory=dict)  # non-secret options only
    auth: str | None = None  # the secret *reference* or identity; never a value
    readiness: ReadinessView
    used_by: list[str] = Field(default_factory=list)  # environments


class EnvironmentView(BaseModel):
    name: str
    bindings: dict[str, str]  # capability -> integration id
    blueprints: list[str] = Field(default_factory=list)


class DeliveryView(BaseModel):
    integrations: list[IntegrationView]
    environments: list[EnvironmentView]


class DeliveryBinding(BaseModel):
    capability: str
    integration: str
    provider: str


class WorkflowSummary(BaseModel):
    workflow_id: str  # = blueprint id
    blueprint: str  # blueprint description
    environment: str = "local"  # delivery environment of the blueprint
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
