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
    # assessed first; later changes are assessments or pull requests (ADR-0033)
    ProductTarget.repo: (ChangeKind.assess, (ChangeKind.assess, *ITERATION_KINDS)),
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
class StackMatch(BaseModel):
    """A stack pack recognised in a team repo (ADR-0041 §5): guidance for one folder, never rules."""

    id: str
    version: str
    title: str = ""
    folder: str = ""  # "" = the repository root
    matched: list[str] = Field(default_factory=list)  # the files and dependencies that matched
    egress: list[str] = Field(default_factory=list)  # what the repo's sandboxes may reach for it


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
    # org workflows assigned to this product, per change kind (ADR-0039); unassigned kinds run the
    # factory default lane for (works_on, kind)
    workflows: dict[str, str] = Field(default_factory=dict)
    # the stack pack it builds with, as `id@version` (ADR-0041 §2); None: a blueprint without a pack
    pack: str | None = None
    # a team repo (target repo): the stack packs detected at its last assessment, per folder
    # (ADR-0041 §5). Guidance for its changes' agents and advisory signals; never its build.
    stack_matches: list[StackMatch] = Field(default_factory=list)


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
    work_items: list[str] = Field(default_factory=list)  # ledger items it addresses (ADR-0040)
    # entry artifacts from the org's own tools (ADR-0039 §5), by key (`report.test`): a lane that
    # starts after the phases that would make them; schema-checked and sealed like any artifact
    provided: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pr_url: str | None = None  # the pull request a change to an existing repo opened (ADR-0033)
    last_failure: str | None = None  # evidence handed to the repair station
    # a held change (ADR-0043): whose failure it was (work | infra | decision), and for a decision
    # the action a person must take before resuming
    hold_class: str | None = None
    required_action: RequiredAction | None = None
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    # a stack conflict before the first build: blueprints whose pack builds what was asked for, so
    # the product may switch to one instead of answering (ADR-0041 §2)
    switch_to: list[str] = Field(default_factory=list)
    resume_at: datetime | None = None
    summary: str | None = None
    cost_usd: float = 0.0
    spec_approved_by: str | None = None  # spec review gate
    # change risk (ADR-0027): the findings now waiting, and every approval given
    risk_hold: RiskHold | None = None
    risk_approvals: list[RiskApproval] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)  # requested spec changes, fed to intake/design
    # design decisions (ADR-0044 §1): the design station asks a person to answer the decision sheet
    # (decisions: ask), and the discussion with the architect on the review page
    decisions_pending: bool = False
    discussion: list[DiscussTurn] = Field(default_factory=list)
    discuss_pending: str | None = None  # a person's input the architect hasn't answered yet
    # an alignment turn found the requirements must change: back to intake, then design (ADR-0044 §1)
    rework_requirements: str | None = None
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


class DiscussTurn(BaseModel):
    """One message in the discussion with the architect on the review page (ADR-0044 §1). A
    person's text is their input to the product's design, never instructions to the factory."""

    role: Literal["person", "architect"]
    by: str
    text: str
    at: datetime
    files: list[str] = Field(default_factory=list)  # docs the architect changed in its reply


class DecisionOptionView(BaseModel):
    id: str
    title: str
    consequences: str
    allowed: bool  # within the lane's constraints


class DecisionQuestionView(BaseModel):
    id: str
    title: str
    pillar: str
    hint: str = ""
    options: list[DecisionOptionView]


class DecisionAnswerView(BaseModel):
    question: str
    choice: str
    other: str | None = None
    recommended: str
    rationale: str = ""
    requirements: list[str] = Field(default_factory=list)
    decided_by: str = "architect"
    reason: str | None = None


class RoadmapRowView(BaseModel):
    pillar: str
    now: str
    deferred: str
    why: str
    trigger: str


class DecisionsView(BaseModel):
    """A change's design decisions (ADR-0044): the questions, the answers, who decided, what changed
    this iteration, the pillar roadmap, and the discussion with the architect."""

    mode: str  # autonomous | ask
    pending: bool  # waiting for a person on the review page (decisions or spec review)
    asked: bool  # the product's stack has design questions
    questions: list[DecisionQuestionView] = Field(default_factory=list)
    answers: list[DecisionAnswerView] = Field(default_factory=list)
    open_questions: list[OpenQuestionView] = Field(default_factory=list)  # intake's, answered by the architect
    changed: list[str] = Field(default_factory=list)  # questions added or changed this iteration
    problems: list[str] = Field(default_factory=list)  # what keeps the sheet from passing
    roadmap: list[RoadmapRowView] = Field(default_factory=list)
    discussion: list[DiscussTurn] = Field(default_factory=list)
    discuss_pending: bool = False


class DecisionAnswer(BaseModel):
    """One answer a person gives on the review page."""

    question: str = Field(min_length=2, max_length=60)
    choice: str = Field(min_length=2, max_length=60)
    other: str | None = Field(default=None, max_length=600)
    reason: str | None = Field(default=None, max_length=1000)


class OpenQuestionAnswer(BaseModel):
    id: str = Field(min_length=2, max_length=10)
    answer: str = Field(min_length=2, max_length=2000)


class AlignInput(BaseModel):
    """Everything a person sends the architect at once from the review page (ADR-0044 §1)."""

    answers: list[DecisionAnswer] = Field(default_factory=list, max_length=30)
    open_answers: list[OpenQuestionAnswer] = Field(default_factory=list, max_length=30)
    message: str = Field(default="", max_length=4000)


class OpenQuestionView(BaseModel):
    id: str
    question: str
    answer: str = ""
    rationale: str = ""
    decided_by: str = "architect"


class RequiredAction(BaseModel):
    """What a person must do before a held change can resume (ADR-0043 §4)."""

    kind: Literal["accept_risk"]
    text: str  # the instruction, shown on the change page and when a resume is refused
    items: list[str] = Field(default_factory=list)  # work ledger items (accept_risk)


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
    # infra failures retried in place (ADR-0043): effort, like fix loops, never a person's touch
    retries: int = 0
    retry_wait_s: float = 0.0


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


# ------------------------------------------------------ product work ledger --
class WorkItemEvent(BaseModel):
    """One line of a work item's history (ADR-0040)."""

    at: datetime
    iteration: int | None = None
    change_id: str | None = None
    # found | seen | claimed | proposed | still-present | fixed | reappeared | wont-do | accepted
    # | reopened | released
    event: str
    note: str = ""
    by: str | None = None


class WorkItem(BaseModel):
    """Something found about a product that may need a change (ADR-0040): an issue, a risk, a
    test gap, a recommendation or a coverage gap. It lives across iterations: found once, seen
    again, addressed by a change, verified as fixed by a later assessment, or decided against."""

    id: str
    product_id: str
    kind: Literal["issue", "risk", "test_gap", "recommendation", "coverage_gap"]
    fingerprint: str  # what makes it the same item across assessments
    title: str
    severity: str = "medium"  # critical | high | medium | low | info
    category: str = ""  # issue: check or rules:<area>; risk: pillar; recommendation: kind
    detail: str = ""
    at: list[str] = Field(default_factory=list)  # file[:line] where it was last seen
    sources: list[dict[str, str]] = Field(default_factory=list)  # station, via, rule
    related: list[str] = Field(default_factory=list)  # a recommendation's issue items
    change_kind: str = "upkeep"  # the change that would address it: feature | bug | upkeep
    effort: str = ""  # S | M | L (recommendations)
    status: Literal["open", "in_progress", "fixed", "wont_do", "accepted_risk"] = "open"
    first_iteration: int = 0
    last_iteration: int = 0  # the latest assessment that reported it
    seen_in: list[int] = Field(default_factory=list)
    claimed_by: list[str] = Field(default_factory=list)  # changes that addressed it
    resolved_by: str | None = None  # the assessment change that verified the fix
    decision_reason: str = ""
    decision_by: str | None = None
    accepted_until: datetime | None = None
    history: list[WorkItemEvent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkItemDecisionInput(BaseModel):
    """Won't do, accept the risk (until a date), or reopen (ADR-0040)."""

    items: list[str] = Field(min_length=1, max_length=200)
    decision: Literal["wont_do", "accepted_risk", "reopen"]
    reason: str = Field(default="", max_length=2000)
    until: datetime | None = None

    @model_validator(mode="after")
    def _reason(self) -> WorkItemDecisionInput:
        if self.decision != "reopen" and len(self.reason.strip()) < 10:
            raise ValueError("say why (at least 10 characters): the reason is kept for audit")
        return self


class ChangeOutcome(BaseModel):
    """What a change was asked to do, what it did and changed, what it gathered, and what a
    later assessment verified (ADR-0040)."""

    change_id: str
    iteration: int
    kind: str
    status: str
    asked: str = ""
    items: list[WorkItem] = Field(default_factory=list)  # the work items it addressed
    done: str = ""  # its summary
    pr_url: str | None = None
    files: list[str] = Field(default_factory=list)  # what it changed
    gathered: list[dict[str, Any]] = Field(default_factory=list)  # artifact, station, path
    found: dict[str, int] = Field(default_factory=dict)  # an assessment: new | seen | fixed | reappeared
    verified: list[dict[str, Any]] = Field(default_factory=list)  # item, result, by (a later assessment)


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
    # what intake assumed where the request was silent: a person confirms or changes them next
    assumptions: list[str] = Field(default_factory=list)
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
    # design decisions (ADR-0044 §1): who answers the sheet, and the options allowed per question
    decisions: Literal["autonomous", "ask"] | None = None
    decision_constraints: dict[str, list[str]] | None = None
    learn_from_runs: bool | None = None  # suggest learnings after runs that needed help (ADR-0021)
    eval_gate: Literal["off", "warn", "block"] | None = None  # evaluation gate (ADR-0025)
    # the lane's scope (ADR-0039 §5): first and last phase; [] = what its stations cover
    scope: list[str] | None = Field(default=None, max_length=2)
    # what the lane starts from beyond the request and the code, e.g. report.test@1
    entry: list[str] | None = Field(default=None, max_length=12)


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
    # skills this agent was given (granted), had in its prompt from the start (preloaded)
    # and actually opened (invoked); granted minus the other two were never used
    skills: dict[str, list[str]] = Field(default_factory=dict)
    denied: int = 0
    # model gateway for this call (ADR-0036): provider, key_alias, spend_usd, revoked; null = direct
    gateway: dict[str, Any] | None = None
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


class CopyWorkflowInput(BaseModel):
    new_id: str = Field(min_length=2, max_length=41)
    note: str = Field(default="", max_length=500)


class AssignWorkflowInput(BaseModel):
    """Assign an org workflow to a product (ADR-0039) for some or all of the kinds it serves."""

    product_id: str
    kinds: list[str] | None = None  # default: every kind the workflow serves


class UnassignWorkflowInput(BaseModel):
    product_id: str


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
    # work items this change addresses (ADR-0040): they move to "in progress" and are verified
    # by the next assessment
    items: list[str] = Field(default_factory=list, max_length=50)
    # entry artifacts from your own tools, by key (`report.test`, `image`), for a lane whose scope
    # starts after the phases that make them (ADR-0039 §5)
    provided: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=10)


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
    state: str  # pending | running | passed | failed | held | skipped
    skip_reason: str | None = None  # a sentence: why this run skips it and what would make it run
    attempts: int
    tool: str | None = None  # scan stations: a specific catalogue tool (ADR-0034)
    fail_on: str | None = None
    check: str | None = None  # scan stations: what is checked (ADR-0035)
    provider: str | None = None  # who answers it, when the station overrides the config
    # the builder (ADR-0039 1c): the station's type, whether it is a mandatory control, and for a
    # custom step what it declares (built-in types fix theirs)
    type: str = ""
    control: bool = False
    custom: bool = False
    uses: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    gives: list[str] = Field(default_factory=list)


class ToolFindingView(BaseModel):
    tool: str
    rule: str
    severity: str  # critical | high | medium | low | info
    title: str
    file: str = ""
    line: int | None = None
    detail: str = ""


class ScanFallbackView(BaseModel):
    provider: str
    tool: str = ""
    reason: str


class ToolReportView(BaseModel):
    """One scan station's report on a change (ADR-0034/0035): who answered, the normalised
    findings and the verdict."""

    station: str
    tool: str
    title: str  # the check, e.g. "Code scan"
    check: str = ""
    provider: str = "local"  # local | github | snyk | agent
    via: str = ""  # the tool or service that answered, e.g. "Semgrep (code)"
    confidence: str = "tool"  # tool | agent
    fallback_from: list[ScanFallbackView] = Field(default_factory=list)
    advisory: str = ""  # why its findings can't block this change
    dropped: list[str] = Field(default_factory=list)  # agent findings the repo didn't support
    fail_on: str
    scope: str  # all | changed (a change to an existing repo answers only for its files)
    passed: bool
    simulated: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    blocking: int = 0
    outside: int = 0
    findings: list[ToolFindingView] = Field(default_factory=list)
    truncated: bool = False


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


class PackAdvisorySignal(BaseModel):
    id: str
    title: str
    level: int
    pillar: str
    ok: bool
    hint: str = ""


class PackAdvisory(BaseModel):
    """How one folder of a team repo measures against a detected pack's golden path (ADR-0041 §5).
    Advisory: never part of the repo's Level."""

    pack: str
    version: str
    title: str
    folder: str = ""
    met: int = 0
    total: int = 0
    signals: list[PackAdvisorySignal] = Field(default_factory=list)


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
    # consolidated after the scans (rollup.py): ranked by severity then impact, each with its
    # source, basis and the issue ids behind it; the assessor's own list is kept beside it
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    assessor_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    # every station's findings, deduplicated, with every place and source as evidence
    issues: list[dict[str, Any]] = Field(default_factory=list)
    issue_counts: dict[str, int] = Field(default_factory=dict)
    # which scans ran and how; `complete` is false when one didn't run, was simulated, was
    # answered by the agent or was cut short (`gaps` says what that leaves out)
    coverage: dict[str, Any] = Field(default_factory=dict)
    dropped: list[str] = Field(default_factory=list)
    agents_md: str = ""
    markdown: str = ""
    assessed_at: str = ""
    # stack packs detected in the repo, per folder, and how each folder measures against them
    stack_packs: list[StackMatch] = Field(default_factory=list)
    pack_signals: list[PackAdvisory] = Field(default_factory=list)


class ChangeDetail(BaseModel):
    change: Change
    product: Product
    stations: list[StationView]
    risk: ChangeRiskView | None = None


class StationSpend(BaseModel):
    station: str
    cost_usd: float


class PhaseSpend(BaseModel):
    phase: str  # a column id (design, develop, verify, …), learn (the retro) or other
    title: str
    cost_usd: float
    stations: list[StationSpend] = Field(default_factory=list)


class ProductSpend(BaseModel):
    """What the product has cost so far across all its changes (`spend.product_spend`): model
    spend; tests, scans, builds and deploys run locally at no extra cost."""

    total_usd: float = 0.0
    changes: int = 0
    phases: list[PhaseSpend] = Field(default_factory=list)


class ProductDetail(BaseModel):
    product: Product
    changes: list[Change]
    feedback: list[Feedback]
    spend: ProductSpend = Field(default_factory=ProductSpend)


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


class StackCard(BaseModel):
    """One choice on the create form's "What are you building?" (ADR-0041 §2): a blueprint that
    builds new products, described by the stack pack it builds with."""

    blueprint: str
    title: str
    summary: str
    builds: str | None = None  # service | web-app | spa | ...; None: a blueprint without a pack
    technologies: list[str] = Field(default_factory=list)
    pack: str | None = None  # id@version


class SwitchStackInput(BaseModel):
    blueprint: str = Field(min_length=1, max_length=80)


class ConfigView(BaseModel):
    name: str
    mode: str
    workflows: dict[str, int]  # blueprint -> active workflow version
    blueprints: dict[str, str]  # blueprints that build new products
    stacks: list[StackCard] = Field(default_factory=list)  # the same blueprints as stack cards
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
    works_on: str = "factory"
    kinds: list[str] = Field(default_factory=list)
    factory_default: bool = False
    products: list[str] = Field(default_factory=list)
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
    decisions: str = "autonomous"  # autonomous | ask (ADR-0044 §1)
    decision_constraints: dict[str, list[str]] = Field(default_factory=dict)
    learn_from_runs: bool = True
    eval_gate: str = "off"  # off | warn | block (ADR-0025)
    evals: list[EvalCase] = Field(default_factory=list)
    # the blueprints whose products run this workflow, each with where its delivery steps run
    blueprints: list[BlueprintUse] = Field(default_factory=list)


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
    decisions: str = "autonomous"  # autonomous | ask (ADR-0044 §1)
    decision_constraints: dict[str, list[str]] = Field(default_factory=dict)
    learn_from_runs: bool = True
    eval_gate: str = "off"  # off | warn | block (ADR-0025)
    evals: list[EvalCase] = Field(default_factory=list)
    # the lane (ADR-0039): what it works on, the change kinds it serves, its scope and entry
    works_on: str = "factory"
    kinds: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)  # as set: [first, last] or [] (what the stations cover)
    span: list[str] = Field(default_factory=list)  # the phases it covers, in order
    preset: str | None = None  # the scope preset it matches, if any
    presets: list[str] = Field(default_factory=list)  # the presets that fit this lane
    evaluable: bool = False  # the evaluation gate applies: the lane builds new products (ADR-0025)
    entry: list[EntryArtifactView] = Field(default_factory=list)


class EntryArtifactView(BaseModel):
    """An artifact a lane starts from: the factory makes the request and the code; anything else
    comes from the org's own tools with each change (ADR-0039 §5)."""

    ref: str  # e.g. report.test@1
    key: str  # e.g. report.test
    title: str
    by: Literal["factory", "your tools"]


class ScopePresetInfo(BaseModel):
    id: str
    title: str
    hint: str
    scope: list[str]  # [] = what the lane's stations cover
    entry: list[str]  # beyond the request and the code
    adds: list[str]  # template ids it adds when the lane has none of their kind


class StationTemplateView(BaseModel):
    """What a phase box's "Add station" offers, with the drop-time check."""

    id: str
    title: str
    hint: str
    kind: str  # agent | check
    source: str  # built-in | <pack>@<version>
    control: bool = False  # a mandatory control (test, secret scan, change-risk)
    present: bool = False  # the lane already has a station of this type
    # what adding it at the end of this phase now would break, in the publish checks' words
    adds_problems: list[str] = Field(default_factory=list)


class DraftTemplatesView(BaseModel):
    phases: dict[str, list[StationTemplateView]]  # phase id -> templates its box offers


class AddFromTemplateInput(BaseModel):
    template: str
    phase: str
    id: str | None = None  # the station id; suggested from the template when omitted


class PresetInput(BaseModel):
    preset: str


class PresetAppliedView(BaseModel):
    draft: DraftView
    removed: list[str]  # stations outside the new scope
    added: list[str]  # stations the preset added


class ProvidedArtifactInfo(BaseModel):
    """An entry artifact a change to this product must bring from the org's own tools."""

    key: str
    ref: str
    title: str
    example: dict[str, Any]  # a valid manifest with placeholder values, to edit


class ChangeEntryView(BaseModel):
    workflow_id: str
    kind: str
    takes: list[ProvidedArtifactInfo]


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
    """One column of the phase registry (ADR-0039 §2)."""

    id: str
    label: str
    allows: list[str] = Field(default_factory=list)  # capabilities a station here may use
    gives: list[str] = Field(default_factory=list)  # artifact types a station here may give
    source: str = "built-in"  # built-in | org | <pack>@<version>


class CatalogView(BaseModel):
    presets: dict[str, list[str]]
    observe_only_presets: list[str]
    extra_tools: list[str]
    model_tiers: dict[str, str]
    skills: list[SkillInfo]
    handlers: dict[str, list[str]]
    handler_info: dict[str, HandlerInfo] = Field(default_factory=dict)  # label, phase, hint (ADR-0028)
    phases: list[PhaseInfo] = Field(default_factory=list)  # the phase registry, in run order (ADR-0039)
    capabilities: dict[str, str] = Field(default_factory=dict)  # what a station may do, id -> meaning
    scope_presets: list[ScopePresetInfo] = Field(default_factory=list)  # the builder (ADR-0039 §5)
    entry_choices: list[EntryArtifactView] = Field(default_factory=list)  # what a lane may start from
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
    check: Literal["secrets", "code", "dependencies", "config"] | None = None  # scan stations (ADR-0035)
    provider: Literal["local", "github", "snyk", "agent"] | None = None
    tool: str | None = None  # a specific catalogue tool (ADR-0034)
    fail_on: Literal["critical", "high", "medium", "low", "none"] | None = None
    # custom steps only: capabilities beyond observe (read-repo), inside the phase (ADR-0039 §3)
    uses: list[str] | None = None
    position: int | None = None  # index in the lane; end when omitted


class UpdateStationInput(BaseModel):
    """Only the fields that are sent are changed; send null to clear a route."""

    on_fail: str | None = None
    next: str | None = None
    only_on_fail: bool | None = None
    handler: str | None = None
    agent: str | None = None
    phase: Phase | None = None
    check: Literal["secrets", "code", "dependencies", "config"] | None = None  # scan stations (ADR-0035)
    provider: Literal["local", "github", "snyk", "agent"] | None = None
    tool: str | None = None  # a specific catalogue tool (ADR-0034)
    fail_on: Literal["critical", "high", "medium", "low", "none"] | None = None
    uses: list[str] | None = None  # custom steps only (ADR-0039 §3)


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


class BlueprintUse(BaseModel):
    """A blueprint whose products run a workflow (ADR-0037: workflows belong to the org)."""

    id: str
    description: str
    target: str  # new | repo
    environment: str = "local"  # where its delivery steps run
    delivery: list[DeliveryBinding] = Field(default_factory=list)


class WorkflowSummary(BaseModel):
    workflow_id: str
    name: str = ""
    # the lane (ADR-0039): what it works on and the change kinds it serves; a factory default
    # serves every product of that kind of work that has no org workflow assigned
    works_on: str = "factory"
    kinds: list[str] = Field(default_factory=list)
    factory_default: bool = False
    blueprints: list[str] = Field(default_factory=list)  # blueprints whose products it can serve
    products: list[str] = Field(default_factory=list)  # product slugs assigned to it (org workflows)
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
