"""Shared actions (pattern borrowed from BuilderIO agent-native).

Each capability is defined ONCE. The same function, with the same validation,
is exposed as:
  * an HTTP route in the OpenAPI contract (the TypeScript UI calls it), and
  * optionally an in-process MCP tool agents can call (agent_tool=True).
Agents never click the UI; UI and agents share one action layer.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from agent_factory import evidence
from agent_factory.engine.pipeline import FactoryError
from agent_factory.engine.workflows import WorkflowError
from agent_factory.github import Fetched, GitHubError, fetch_dir
from agent_factory.identity import current_identity
from agent_factory.models import (
    ActivateInput,
    AddStationInput,
    AgentCallView,
    AgentView,
    AnswersInput,
    AssessmentView,
    CatalogView,
    Change,
    ChangeCallsView,
    ChangeDetail,
    ChangeRiskView,
    ChangeStatus,
    ConfigView,
    CreateProductInput,
    DecisionInput,
    DeliveryBinding,
    DeliveryView,
    DenialView,
    DraftView,
    DuplicateAgentInput,
    EnvironmentView,
    EvalRun,
    EvalSuiteInput,
    Event,
    EventKind,
    EvidenceFile,
    EvidenceView,
    FeedbackInput,
    FromGitHubInput,
    FromTemplateInput,
    HealthView,
    InstallSkillInput,
    IntegrationView,
    LearningProposal,
    OnboardRepoInput,
    OutcomesView,
    PhaseInfo,
    PreflightView,
    Product,
    ProductDetail,
    PublishInput,
    ReadinessView,
    ReorderStationsInput,
    RequirementView,
    ReviewFinding,
    ReviewRequirement,
    ReviewView,
    RiskDecisionInput,
    RiskFindingView,
    ScenarioView,
    SkillDetail,
    SkillInfo,
    SkillSourceInput,
    SkillVersionInfo,
    SpecEditInput,
    SpecReviewInput,
    SpecView,
    StartEvalInput,
    StationAgentInput,
    StationView,
    TemplateInfo,
    TraceRow,
    UpdateStationInput,
    WorkflowSettingsInput,
    WorkflowSummary,
    WorkflowView,
)
from agent_factory.providers import CAPABILITIES
from agent_factory.skills import SkillError, SkillLibrary, SkillPreview, SkillRecord
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CHECK_HANDLERS,
    HANDLER_INFO,
    HANDLER_REQUIREMENTS,
    MAX_DOC_CHARS,
    MAX_LEARNINGS_CHARS,
    OBSERVE_ONLY_PRESETS,
    PHASES,
    SAFE_EXTRA_TOOLS,
    TOOL_PRESETS,
    AgentSpec,
    RefDoc,
    WorkflowDoc,
    WorkflowStation,
    WorkflowVersionInfo,
    workflow_warnings,
)

if TYPE_CHECKING:
    from agent_factory.app_factory import Factory


@dataclass
class ActionSpec:
    name: str
    description: str
    method: str
    path: str
    fn: Callable[..., Any]
    agent_tool: bool = False
    status_code: int = 200


REGISTRY: dict[str, ActionSpec] = {}


def action(
    name: str, description: str, method: str, path: str, *, agent_tool: bool = False, status_code: int = 200
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY[name] = ActionSpec(name, description, method, path, fn, agent_tool, status_code)
        return fn

    return register


# ------------------------------------------------------------------ views --
def _station_views(f: Factory, change: Change, workflow_id: str) -> list[StationView]:
    views = []
    flow = f.workflows[change.workflow_id or workflow_id].get(change.workflow_version)
    phase_of = flow.phases()
    for s in flow.stations:
        attempts = change.attempts.get(s.id, 0) if change else 0
        state = "pending"
        if change:
            if change.current_station == s.id:
                state = {
                    "running": "running",
                    "held": "held",
                    "needs_input": "waiting",
                    "awaiting_risk_approval": "waiting",
                    "paused_limits": "waiting",
                    "interrupted": "held",
                }.get(change.status, "pending")
            elif attempts:
                last = [
                    e
                    for e in f.store.list_events(change.id)
                    if e.station == s.id and e.kind == EventKind.station_finished
                ]
                state = last[-1].data.get("outcome", "passed") if last else "passed"
        views.append(
            StationView(
                id=s.id,
                kind=s.kind,
                role=s.agent,
                handler=s.resolved_handler(),
                label=s.label(),
                phase=phase_of[s.id],
                repair=s.only_on_fail,
                state=state,
                attempts=attempts,
            )
        )
    return views


# ---------------------------------------------------------------- actions --
@action("get_health", "Factory health and readiness", "GET", "/api/health")
def get_health(f: Factory) -> HealthView:
    return HealthView(
        status="ok",
        mode=f.settings.factory_mode,
        model_auth=f.settings.model_auth_configured(),
        github=_publish_token_available(f),
        active_changes=f.manager.active_count(),
        sandbox=_sandbox_state(f)[0],
        sandbox_detail=_sandbox_state(f)[1],
        preflight=_preflight_state(f),
    )


def _preflight_state(f: Factory) -> str:
    pf = f.manager.preflight
    # in-memory checks (ports, model, sandbox) are re-evaluated so the header is never stale
    views = [pf.refresh_local(v) for v in list(pf.cache.values())]
    if not views:
        return "unknown"
    rank = {"ready": 0, "degraded": 1, "failed": 2}
    return max((v.state for v in views), key=lambda s: rank.get(s, 2))


def _sandbox_state(f: Factory) -> tuple[str, list[str]]:
    sb = getattr(f, "sandbox", None)
    if sb is None:
        return "off", []
    return {"unknown": "preparing"}.get(sb.state.state, sb.state.state), list(sb.state.reasons)


@action("get_config", "Factory settings: blueprints, their workflow versions, policies, gates", "GET", "/api/config")
def get_config(f: Factory) -> ConfigView:
    p = f.cfg.policies
    return ConfigView(
        name=f.cfg.factory.name,
        mode=f.settings.factory_mode,
        workflows={w.workflow_id: w.active_version() for w in f.workflows},
        blueprints={k: v.description for k, v in f.cfg.blueprints.items() if v.target == "new"},
        repo_blueprints={k: v.description for k, v in f.cfg.blueprints.items() if v.target == "repo"},
        policies={k: getattr(p, k).mode for k in ("implement", "deploy", "publish", "merge", "recover")},
        gates=[f"{g.kind} after {g.after}" for g in f.cfg.gates],
    )


@action(
    "get_outcomes",
    "Outcomes: deliveries, lead time, change failure rate, autonomy, cost per change, "
    "where the time goes and who the factory is waiting on (ADR-0019)",
    "GET",
    "/api/outcomes",
)
async def get_outcomes(f: Factory, days: Annotated[int, Query(ge=1, le=365)] = 30) -> OutcomesView:
    from agent_factory.outcomes import outcomes

    # reads every change and its timeline: off the event loop (the store is thread-safe)
    return await asyncio.to_thread(outcomes, f.store, days)


@action("list_products", "List products, newest first (evaluation products are not listed)", "GET", "/api/products")
def list_products(f: Factory, include_archived: bool = False) -> list[Product]:
    return [o for o in f.store.list_products() if (include_archived or not o.archived_at) and not o.eval_run_id]


@action(
    "create_product",
    "Submit requirements for a new product; starts its first change",
    "POST",
    "/api/products",
    status_code=201,
)
async def create_product(f: Factory, body: CreateProductInput) -> ProductDetail:
    await _gate(f, body.blueprint, "product")
    product = f.manager.create_product(body)
    product.created_by = current_identity().user
    f.store.save_product(product)
    f.manager.start_change(product)
    return get_product(f, product.id)


@action(
    "onboard_repo",
    "Onboard an existing repository (public https URL) and start its read-only assessment (ADR-0031)",
    "POST",
    "/api/repos",
    status_code=201,
)
async def onboard_repo(f: Factory, body: OnboardRepoInput) -> ProductDetail:
    await _gate(f, f.cfg.existing_repos.blueprint, "product")
    product = f.manager.onboard_repo(body)
    product.created_by = current_identity().user
    f.store.save_product(product)
    f.manager.start_change(product)
    return get_product(f, product.id)


@action(
    "get_change_assessment",
    "An existing repo's assessment: stack, readiness signals, findings, risks, test gaps, "
    "recommended changes and a proposed AGENTS.md (ADR-0031)",
    "GET",
    "/api/changes/{change_id}/assessment",
)
def get_change_assessment(f: Factory, change_id: str) -> AssessmentView:
    import json

    from agent_factory import assess

    if not f.store.get_change(change_id):
        raise FactoryError("change not found")
    folder = f.manager.ws.data_dir / "artifacts" / change_id
    file = folder / "assessment.json"
    if not file.is_file():
        raise FactoryError("assessment not found: the report station has not run for this change")
    d = json.loads(file.read_text())
    card = d.get("readiness", {})
    md = folder / "assessment.md"
    return AssessmentView(
        change_id=change_id,
        repo=d.get("repo", {}),
        stack=d.get("stack", {}),
        stack_summary=d.get("stack_summary", ""),
        level=card.get("level", 0),
        points=card.get("points", 0),
        max_points=card.get("max_points", 0),
        signals=card.get("signals", []),
        pillars=card.get("pillars", []),
        findings=assess.group_findings(d.get("findings", [])),
        summary=d.get("summary", ""),
        risks=d.get("risks", []),
        test_gaps=d.get("test_gaps", []),
        recommendations=d.get("recommendations", []),
        dropped=d.get("dropped", []),
        agents_md=d.get("agents_md", ""),
        markdown=md.read_text() if md.is_file() else "",
        assessed_at=d.get("assessed_at", ""),
    )


@action("get_product", "Product with its changes and feedback", "GET", "/api/products/{product_id}")
def get_product(f: Factory, product_id: str) -> ProductDetail:
    product = f.store.get_product(product_id)
    if not product:
        raise FactoryError("product not found")
    return ProductDetail(
        product=product, changes=f.store.list_changes(product_id), feedback=f.store.list_feedback(product_id)
    )


@action(
    "submit_feedback",
    "Ask for a change to a delivered product (feature, bug or upkeep); starts the next iteration",
    "POST",
    "/api/products/{product_id}/feedback",
    status_code=201,
)
async def submit_feedback(f: Factory, product_id: str, body: FeedbackInput) -> Change:
    _may_steer(f, product_id)
    product = f.store.get_product(product_id)
    if product:
        await _gate(f, product.blueprint, "change")
    return f.manager.feedback(product_id, body.text, body.kind)


@action("get_change", "Change with station states", "GET", "/api/changes/{change_id}")
def get_change(f: Factory, change_id: str) -> ChangeDetail:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    product = f.store.get_product(change.product_id)
    assert product is not None
    return ChangeDetail(
        change=change,
        product=product,
        stations=_station_views(f, change, product.blueprint),
        risk=_risk_view(f, change),
    )


def _risk_view(f: Factory, change: Change) -> ChangeRiskView | None:
    got = f.store.last_event_data(change.id, "change_risk")
    data = got[1] if got else None
    if not isinstance(data, dict):
        return None
    hold = change.risk_hold if change.status == ChangeStatus.awaiting_risk_approval else None
    policy = f.cfg.change_risk
    return ChangeRiskView(
        digest=str(data.get("digest", "")),
        files_changed=int(data.get("files_changed", 0)),
        findings=[RiskFindingView.model_validate(x) for x in data.get("findings", [])],
        holds=int(data.get("holds", 0)),
        approved_by=data.get("approved_by"),
        waiting=hold is not None,
        requested_by=hold.requested_by if hold else None,
        break_glass=policy.break_glass,
        self_approval_at=hold.since + timedelta(minutes=policy.cooling_off_minutes) if hold else None,
    )


@action("list_events", "Append-only event/decision log for a change", "GET", "/api/changes/{change_id}/events")
def list_events(f: Factory, change_id: str, after: int = 0) -> list[Event]:
    return f.store.list_events(change_id, after)


@action("answer_questions", "Answer intake's blocking questions", "POST", "/api/changes/{change_id}/answers")
def answer_questions(f: Factory, change_id: str, body: AnswersInput) -> Change:
    _may_steer_change(f, change_id)
    return f.manager.answer(change_id, body.answers)


@action("resume_change", "Resume a held, interrupted or paused change", "POST", "/api/changes/{change_id}/resume")
async def resume_change(f: Factory, change_id: str) -> Change:
    _may_steer_change(f, change_id)
    change = f.store.get_change(change_id)
    product = f.store.get_product(change.product_id) if change else None
    if product:
        await _gate(f, product.blueprint, "change")
    return f.manager.resume(change_id)


async def _gate(f: Factory, blueprint: str, what: str) -> None:
    if blueprint in f.cfg.blueprints:
        await f.manager.preflight.gate(blueprint, what)


@action(
    "archive_product",
    "Archive a product: remove its app from the cluster and free its port; history is kept",
    "POST",
    "/api/products/{product_id}/archive",
)
async def archive_product(f: Factory, product_id: str) -> Product:
    _may_steer(f, product_id)
    return await f.manager.archive(product_id, current_identity().user)


def _may_steer_change(f: Factory, change_id: str) -> None:
    change = f.store.get_change(change_id)
    if change:
        _may_steer(f, change.product_id)


def _may_steer(f: Factory, product_id: str) -> None:
    """Steering a product (feedback, answers, resume, cancel, spec decisions,
    archive, transcripts): the product's creator or an admin."""
    who = current_identity()
    product = f.store.get_product(product_id)
    if product and not who.is_admin and product.created_by and product.created_by != who.user:
        raise PermissionError("only the product's creator or an admin can do this")


@action(
    "get_change_spec",
    "The change's specification: product spec, technical design, API, numbered requirements, "
    "acceptance scenarios, changes and traceability",
    "GET",
    "/api/changes/{change_id}/spec",
)
def get_change_spec(f: Factory, change_id: str) -> SpecView:
    from agent_factory import traceability as tr

    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    product = f.store.get_product(change.product_id)
    wt = f.manager.ws.change_dir(change.id)
    read = lambda rel: (wt / rel).read_text(errors="replace") if (wt / rel).is_file() else ""  # noqa: E731
    flow = f.workflows[change.workflow_id or (product.blueprint if product else "")].get(change.workflow_version)
    events = f.store.list_events(change.id)
    changes = next((e.data["spec_changes"] for e in reversed(events) if "spec_changes" in e.data), {})
    trace = next((e.data["traceability"] for e in reversed(events) if "traceability" in e.data), None)
    rev = next((e.data["review"] for e in reversed(events) if isinstance(e.data.get("review"), dict)), None)
    holdout = f.manager.ws.holdout_dir(product.slug) / "scenarios.yaml" if product else None
    return SpecView(
        change_id=change.id,
        status=change.status,
        gate=flow.spec_review,
        approved_by=change.spec_approved_by,
        product=read("docs/spec.md"),
        technical=read("docs/design.md"),
        openapi=read("docs/openapi.yaml"),
        requirements=[RequirementView(**r) for r in tr.requirements(wt)],
        acceptance=[ScenarioView(**s) for s in tr.acceptance_scenarios(wt)],
        holdout_count=len(tr.scenarios(holdout)) if holdout else 0,
        changes=changes,
        review_notes=change.review_notes,
        traceability=[TraceRow(**r) for r in (trace if trace is not None else tr.matrix(wt))],
        review=_review_view(rev),
    )


def _review_view(ev: dict[str, Any] | None) -> ReviewView | None:
    if not ev or not ev.get("judgement", {}).get("complete"):
        return None
    report, j = ev.get("report", {}), ev["judgement"]
    return ReviewView(
        passed=bool(j.get("passed")),
        implemented=int(j.get("implemented", 0)),
        total=int(j.get("total", 0)),
        summary=str(report.get("summary", "")),
        requirements=[
            ReviewRequirement(id=str(r.get("id")), status=str(r.get("status")), where=str(r.get("where", "")))
            for r in report.get("requirements", [])
        ],
        findings=[
            ReviewFinding(
                severity=str(f.get("severity", "minor")),
                message=str(f.get("message", "")),
                file=str(f.get("file", "")),
                requirement=str(f.get("requirement", "")),
            )
            for f in report.get("findings", [])
        ],
    )


@action(
    "get_change_calls",
    "Every agent call of the change (station, role, model, turns, duration, cost, tools, denials) "
    "and every guardrail denial (ADR-0022)",
    "GET",
    "/api/changes/{change_id}/calls",
)
def get_change_calls(f: Factory, change_id: str) -> ChangeCallsView:
    if not f.store.get_change(change_id):
        raise FactoryError("change not found")
    calls = [AgentCallView(**v, at=ts) for _, ts, v in f.store.events_with_key([change_id], "agent_call")]
    denials = [DenialView(**v, at=ts) for _, ts, v in f.store.events_with_key([change_id], "denied")]
    return ChangeCallsView(calls=calls, denials=denials)


@action(
    "get_change_evidence",
    "A change's sealed evidence manifest: every file with its SHA-256, re-verified now (changed, missing or added "
    "files are reported)",
    "GET",
    "/api/changes/{change_id}/evidence",
)
def get_change_evidence(f: Factory, change_id: str) -> EvidenceView:
    if not f.store.get_change(change_id):
        raise FactoryError("change not found")
    folder = f.manager.ws.data_dir / "artifacts" / change_id
    seal = evidence.latest_seal(f.store, change_id)
    v = evidence.verify(folder, seal["sha256"] if seal else None)
    if not v.sealed or seal is None:
        return EvidenceView(sealed=False)
    try:
        m = json.loads((folder / evidence.MANIFEST).read_text())
    except ValueError:
        m = {}
    files = [EvidenceFile(**x) for x in m.get("files", [])]
    return EvidenceView(
        sealed=True,
        sealed_at=m.get("sealed_at"),
        sha256=seal["sha256"],
        status_at_seal=seal.get("status"),
        commit=(m.get("source") or {}).get("commit"),
        files=files,
        total_bytes=sum(x.bytes for x in files),
        intact=v.intact,
        manifest_ok=v.manifest_ok,
        changed=v.changed,
        missing=v.missing,
        added=v.added,
    )


@action(
    "download_change_evidence",
    "Download a change's sealed evidence as a zip (manifest, SHA256SUMS, events, spec, transcripts, review, "
    "traceability, SBOM, provenance); the product's creator or an admin",
    "GET",
    "/api/changes/{change_id}/evidence/bundle",
)
def download_change_evidence(f: Factory, change_id: str) -> Response:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    _may_steer(f, change.product_id)  # transcripts hold full tool output (ADR-0022)
    folder = f.manager.ws.data_dir / "artifacts" / change_id
    if not evidence.latest_seal(f.store, change_id) or not (folder / evidence.MANIFEST).is_file():
        raise FactoryError("no sealed evidence yet: it is sealed when the change stops")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    evidence.write_bundle(folder, Path(tmp))
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=f"evidence-{change_id}.zip",
        background=BackgroundTask(os.unlink, tmp),
    )


@action(
    "get_call_transcript",
    "One agent call's transcript: text, tool calls with inputs, tool results, denials (secrets redacted); "
    "the product's creator or an admin",
    "GET",
    "/api/changes/{change_id}/calls/{name}/transcript",
)
def get_call_transcript(f: Factory, change_id: str, name: str) -> list[dict[str, Any]]:
    from agent_factory.observe import read_transcript

    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    # full tool output (code, command results): the product's creator or an admin (ADR-0022)
    _may_steer(f, change.product_id)
    try:
        return read_transcript(f.manager.ws.data_dir / "artifacts" / change_id, name)
    except (ValueError, FileNotFoundError) as exc:
        raise FactoryError(f"transcript not found: {name}") from exc


@action(
    "approve_spec",
    "Spec review gate: approve the spec; the change continues to implement",
    "POST",
    "/api/changes/{change_id}/spec/approve",
)
async def approve_spec(f: Factory, change_id: str) -> Change:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    _may_steer(f, change.product_id)
    product = f.store.get_product(change.product_id)
    if product:
        await _gate(f, product.blueprint, "change")
    return f.manager.approve_spec(change_id, current_identity().user)


@action(
    "approve_risk",
    "Change risk: an admin other than the requester accepts the risky changes, with a reason "
    "(the requester only as break-glass, after the cooling-off delay)",
    "POST",
    "/api/changes/{change_id}/risk/approve",
)
async def approve_risk(f: Factory, change_id: str, body: RiskDecisionInput) -> Change:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    product = f.store.get_product(change.product_id)
    if product:
        await _gate(f, product.blueprint, "change")
    return f.manager.approve_risk(change_id, current_identity().user, body.reason)


@action(
    "send_back_risk",
    "Change risk: don't accept the risky changes; the change goes back to its repair station with the reason",
    "POST",
    "/api/changes/{change_id}/risk/send-back",
)
async def send_back_risk(f: Factory, change_id: str, body: RiskDecisionInput) -> Change:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    _may_steer(f, change.product_id)
    return f.manager.send_back_risk(change_id, current_identity().user, body.reason)


@action(
    "request_spec_changes",
    "Spec review gate: send the spec back to intake and design with your notes",
    "POST",
    "/api/changes/{change_id}/spec/changes",
)
async def request_spec_changes(f: Factory, change_id: str, body: SpecReviewInput) -> Change:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    _may_steer(f, change.product_id)
    product = f.store.get_product(change.product_id)
    if product:
        await _gate(f, product.blueprint, "change")
    return f.manager.request_spec_changes(change_id, current_identity().user, body.comment)


@action(
    "edit_spec",
    "Spec review gate: edit the product spec or technical design directly",
    "PUT",
    "/api/changes/{change_id}/spec",
)
async def edit_spec(f: Factory, change_id: str, body: SpecEditInput) -> SpecView:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    _may_steer(f, change.product_id)
    await f.manager.edit_spec(change_id, current_identity().user, body.product, body.technical)
    return get_change_spec(f, change_id)


@action(
    "set_workflow_settings",
    "Workflow settings in the draft: spec review gate (off | first | always), learn from runs, "
    "evaluation gate (off | warn | block)",
    "PATCH",
    "/api/workflows/{workflow_id}/draft/settings",
)
def set_workflow_settings(f: Factory, workflow_id: str, body: WorkflowSettingsInput) -> DraftView:
    draft = f.workflows[workflow_id].draft
    if body.spec_review is not None:
        draft.set_spec_review(body.spec_review)
    if body.learn_from_runs is not None:
        draft.set_learn_from_runs(body.learn_from_runs)
    if body.eval_gate is not None:
        draft.set_eval_gate(body.eval_gate)
    return get_draft(f, workflow_id)


# ------------------------------------------------------ evaluation (ADR-0025) --
@action(
    "put_draft_evals",
    "Replace the draft's evaluation suite: the fixed requests a new version is measured on",
    "PUT",
    "/api/workflows/{workflow_id}/draft/evals",
)
def put_draft_evals(f: Factory, workflow_id: str, body: EvalSuiteInput) -> DraftView:
    f.workflows[workflow_id].draft.set_evals(body.cases)
    return get_draft(f, workflow_id)


@action(
    "list_evals",
    "Evaluations of this workflow's versions, newest first",
    "GET",
    "/api/workflows/{workflow_id}/evals",
)
def list_evals(f: Factory, workflow_id: str) -> list[EvalRun]:
    f.workflows[workflow_id]  # 404 for an unknown workflow
    return f.store.list_evals(workflow_id)


@action(
    "get_eval",
    "One evaluation: per-case results, summaries of candidate and baseline, and the verdict",
    "GET",
    "/api/workflows/{workflow_id}/evals/{eval_id}",
)
def get_eval(f: Factory, workflow_id: str, eval_id: str) -> EvalRun:
    e = f.store.get_eval(eval_id)
    if e is None or e.workflow_id != workflow_id:
        raise FactoryError("evaluation not found")
    return e


@action(
    "start_eval",
    "Run the version's evaluation suite (default: the newest version) against the active version",
    "POST",
    "/api/workflows/{workflow_id}/evals",
    status_code=201,
)
async def start_eval(f: Factory, workflow_id: str, body: StartEvalInput) -> EvalRun:
    from agent_factory import evals

    w = f.workflows[workflow_id]
    version = body.version or max(i.version for i in w.versions())
    await _gate(f, workflow_id, "product")
    return evals.start(f.manager, workflow_id, version, current_identity().user)


@action(
    "cancel_eval",
    "Stop a running evaluation: its changes are cancelled, its products archived, nothing is activated",
    "POST",
    "/api/workflows/{workflow_id}/evals/{eval_id}/cancel",
)
async def cancel_eval(f: Factory, workflow_id: str, eval_id: str) -> EvalRun:
    from agent_factory import evals

    e = get_eval(f, workflow_id, eval_id)
    return await evals.cancel(f.manager, e)


# ------------------------------------------------------ learnings (ADR-0021) --
@action(
    "list_learning_proposals",
    "Lessons suggested after changes that needed help, for an admin to accept or reject",
    "GET",
    "/api/workflows/{workflow_id}/learnings",
)
def list_learning_proposals(f: Factory, workflow_id: str, status: str | None = "pending") -> list[LearningProposal]:
    f.workflows[workflow_id]  # 404 for an unknown workflow
    return f.store.list_proposals(workflow_id, status or None)


def _decide(f: Factory, workflow_id: str, proposal_id: str) -> LearningProposal:
    if not current_identity().is_admin:
        raise PermissionError("only an admin can change what agents are taught")
    p = f.store.get_proposal(proposal_id)
    if not p or p.workflow_id != workflow_id:
        raise FactoryError("learning proposal not found")
    if p.status != "pending":
        raise FactoryError(f"learning proposal already {p.status}")
    return p


@action(
    "accept_learning",
    "Accept a suggested lesson: it is added to the agent's learnings in the workflow draft (publish to use it)",
    "POST",
    "/api/workflows/{workflow_id}/learnings/{proposal_id}/accept",
)
def accept_learning(f: Factory, workflow_id: str, proposal_id: str) -> LearningProposal:
    p = _decide(f, workflow_id, proposal_id)
    f.workflows[workflow_id].draft.add_learning(p.agent, p.lesson)  # WorkflowError (409) when full
    p.status, p.decided_by, p.decided_at = "accepted", current_identity().user, datetime.now(UTC)
    f.store.save_proposal(p)
    return p


@action(
    "reject_learning",
    "Reject a suggested lesson",
    "POST",
    "/api/workflows/{workflow_id}/learnings/{proposal_id}/reject",
)
def reject_learning(f: Factory, workflow_id: str, proposal_id: str) -> LearningProposal:
    p = _decide(f, workflow_id, proposal_id)
    p.status, p.decided_by, p.decided_at = "rejected", current_identity().user, datetime.now(UTC)
    f.store.save_proposal(p)
    return p


@action("get_preflight", "Readiness checks per blueprint (cached; see ADR-0015)", "GET", "/api/preflight")
async def get_preflight(f: Factory) -> list[PreflightView]:
    pf = f.manager.preflight
    return [pf.refresh_local(await pf.blueprint(pl, max_age=float("inf"))) for pl in f.cfg.blueprints]


@action("run_preflight", "Run every readiness check now", "POST", "/api/preflight")
async def run_preflight(f: Factory) -> list[PreflightView]:
    return await f.manager.preflight.all(max_age=0)


@action("cancel_change", "Cancel a change", "POST", "/api/changes/{change_id}/cancel")
def cancel_change(f: Factory, change_id: str) -> Change:
    _may_steer_change(f, change_id)
    return f.manager.cancel(change_id)


@action(
    "log_decision",
    "Record a decision or assumption in the change's append-only log",
    "POST",
    "/api/decisions",
    agent_tool=True,
    status_code=201,
)
def log_decision(f: Factory, body: DecisionInput) -> Event:
    if not f.store.get_change(body.change_id):
        raise FactoryError("change not found")
    msg = body.decision + (f" — because {body.rationale}" if body.rationale else "")
    return f.store.add_event(body.change_id, EventKind.decision, msg, station=body.station)


# -------------------------------------------------------------- workflows --
@action("list_workflows", "One workflow per blueprint", "GET", "/api/workflows")
def list_workflows(f: Factory) -> list[WorkflowSummary]:
    out = []
    for w in f.workflows:
        doc = w.get()
        out.append(
            WorkflowSummary(
                workflow_id=w.workflow_id,
                blueprint=f.cfg.blueprints[f.cfg.blueprint_of(w.workflow_id)].description,
                environment=f.cfg.blueprints[f.cfg.blueprint_of(w.workflow_id)].environment,
                active_version=w.active_version(),
                description=doc.description,
                template=doc.template,
                stations=len(doc.stations),
                agents=len(doc.agents),
                draft_dirty=w.draft.dirty(),
            )
        )
    return out


@action("get_workflow", "The active version of a workflow", "GET", "/api/workflows/{workflow_id}")
def get_workflow(f: Factory, workflow_id: str) -> WorkflowView:
    return _workflow_view(f, workflow_id, f.workflows[workflow_id].active_version())


@action(
    "get_workflow_version",
    "A specific (immutable) workflow version",
    "GET",
    "/api/workflows/{workflow_id}/versions/{version}",
)
def get_workflow_version(f: Factory, workflow_id: str, version: int) -> WorkflowView:
    return _workflow_view(f, workflow_id, version)


@action(
    "list_workflow_versions", "All versions of a workflow, newest first", "GET", "/api/workflows/{workflow_id}/versions"
)
def list_workflow_versions(f: Factory, workflow_id: str) -> list[WorkflowVersionInfo]:
    from agent_factory import evals

    infos = f.workflows[workflow_id].versions()
    states = evals.version_states(f.manager, workflow_id)
    return [i.model_copy(update={"evaluation": states.get(i.version)}) for i in infos]


@action(
    "activate_workflow_version",
    "Make a version active for new changes (changes in flight keep theirs)",
    "POST",
    "/api/workflows/{workflow_id}/versions/{version}/activate",
)
def activate_workflow_version(
    f: Factory, workflow_id: str, version: int, body: ActivateInput | None = None
) -> WorkflowVersionInfo:
    from agent_factory import evals

    f.workflows[workflow_id].get(version)  # 404 first
    evals.gate_activation(
        f.manager, workflow_id, version, current_identity().user, body.override_reason if body else None
    )
    info = f.workflows[workflow_id].activate(version)
    f.workflows[workflow_id].draft.discard_if_published_as(version)
    return info


def _workflow_view(f: Factory, workflow_id: str, version: int) -> WorkflowView:
    w = f.workflows[workflow_id]
    doc = w.get(version)
    info = next(i for i in w.versions() if i.version == version)
    return WorkflowView(
        workflow_id=workflow_id,
        version=version,
        active=info.active,
        note=info.note,
        name=doc.name,
        description=doc.description,
        template=doc.template,
        template_update_available=w.template_update_available(),
        stations=_station_views_for(doc),
        agents=_agent_views(f, doc),
        docs=list(doc.docs.values()),
        warnings=workflow_warnings(doc),
        spec_review=doc.spec_review,
        learn_from_runs=doc.learn_from_runs,
        eval_gate=doc.eval_gate,
        evals=doc.evals,
        environment=f.cfg.blueprints[f.cfg.blueprint_of(workflow_id)].environment,
        delivery=_bindings(f, f.cfg.blueprint_of(workflow_id)),
    )


def _auth_label(auth: Any) -> str | None:
    if auth.identity:
        return f"identity: {auth.identity}"
    return auth.secret_ref


def _publish_token_available(f: Factory) -> bool:
    """Whether the default blueprint's publish integration has a usable token (never the value)."""
    try:
        pl = next(iter(f.cfg.blueprints))
        publish = f.manager.providers.for_blueprint(pl).publish
    except (StopIteration, Exception):  # noqa: BLE001
        return False
    ref = getattr(getattr(publish, "auth", None), "secret_ref", None)
    return f.manager.secrets.available(ref)[0]


def _bindings(f: Factory, blueprint: str) -> list[DeliveryBinding]:
    ps = f.manager.providers.for_blueprint(blueprint)
    return [
        DeliveryBinding(capability=c, integration=getattr(ps, c).name, provider=getattr(ps, c).kind)
        for c in CAPABILITIES
    ]


def _agent_views(f: Factory, doc: WorkflowDoc) -> list[AgentView]:
    return [
        AgentView(
            spec=a,
            model_resolved=f.cfg.models.resolve(a.model),
            effective_tools=a.effective_tools(),
            observe_only=a.observe_only,
            used_by=doc.stations_using(a.id),
        )
        for a in doc.agents.values()
    ]


def _station_views_for(doc: WorkflowDoc) -> list[StationView]:
    phase_of = doc.phases()
    return [
        StationView(
            id=s.id,
            kind=s.kind,
            role=s.agent,
            handler=s.resolved_handler(),
            label=s.label(),
            phase=phase_of[s.id],
            repair=s.only_on_fail,
            on_fail=s.on_fail,
            next=s.next,
            state="pending",
            attempts=0,
        )
        for s in doc.stations
    ]


# -------------------------------------------------------------- templates --
@action("list_workflow_templates", "Built-in workflow templates", "GET", "/api/workflow-templates")
def list_workflow_templates(f: Factory) -> list[TemplateInfo]:
    return [
        TemplateInfo(
            name=name,
            description=doc.description,
            stations=[s.id for s in doc.stations],
            agents=sorted(doc.agents),
            docs=sorted(doc.docs),
        )
        for name, doc in f.workflows.templates()
    ]


@action(
    "draft_from_template",
    "Start the draft from a built-in template (publish to apply)",
    "POST",
    "/api/workflows/{workflow_id}/draft/from-template",
)
def draft_from_template(f: Factory, workflow_id: str, body: FromTemplateInput) -> DraftView:
    f.workflows[workflow_id].draft.replace_with(f.workflows.template(body.template))
    return get_draft(f, workflow_id)


@action(
    "draft_from_github",
    "Start the draft from a workflow template in a GitHub repo, pinned to a commit",
    "POST",
    "/api/workflows/{workflow_id}/draft/from-github",
)
def draft_from_github(f: Factory, workflow_id: str, body: FromGitHubInput) -> DraftView:
    try:
        fetched = fetch_dir(
            body.repo, body.path, body.ref, f.manager.secrets.resolve_optional(f.cfg.skills_github_token_ref)
        )
    except GitHubError as exc:
        raise WorkflowError(str(exc)) from exc
    try:
        label = f"github:{fetched.repo}/{fetched.path}@{fetched.sha[:12]}"
        doc = f.workflows.load_fetched(fetched.root, label)
    finally:
        fetched.cleanup()
    f.workflows[workflow_id].draft.replace_with(doc)
    return get_draft(f, workflow_id)


# ---------------------------------------------------------- workflow editing --
@action("get_catalog", "Choices for the editor: tool presets, model tiers, installed skills", "GET", "/api/catalog")
def get_catalog(f: Factory) -> CatalogView:
    return CatalogView(
        presets=TOOL_PRESETS,
        observe_only_presets=sorted(OBSERVE_ONLY_PRESETS),
        extra_tools=sorted(SAFE_EXTRA_TOOLS),
        model_tiers={t: f.cfg.models.resolve(t) for t in ("judgment", "default", "fast")},
        skills=list_skills(f),
        handlers={"agent": sorted(AGENT_HANDLERS), "check": sorted(CHECK_HANDLERS)},
        handler_info=HANDLER_INFO,
        phases=[PhaseInfo(id=p, label=label) for p, label in PHASES],
        requirements={k: dict(v) for k, v in HANDLER_REQUIREMENTS.items()},
        max_previous_iterations=5,
        max_doc_chars=MAX_DOC_CHARS,
        max_learnings_chars=MAX_LEARNINGS_CHARS,
    )


@action(
    "get_draft",
    "The editable draft of a workflow (created from the active version on first edit)",
    "GET",
    "/api/workflows/{workflow_id}/draft",
)
def get_draft(f: Factory, workflow_id: str) -> DraftView:
    w = f.workflows[workflow_id]
    base, doc, stamp = w.draft.get()
    active = w.active_version()
    return DraftView(
        workflow_id=workflow_id,
        base_version=base,
        active_version=active,
        stale=base != active,
        next_version=max(i.version for i in w.versions()) + 1,
        dirty=w.draft.dirty(),
        updated_at=stamp,
        template=doc.template,
        problems=w.draft.problems(),
        warnings=w.draft.warnings(),
        skill_updates=w.draft.skill_updates(),
        stations=_station_views_for(doc),
        agents=_agent_views(f, doc),
        docs=list(doc.docs.values()),
        spec_review=doc.spec_review,
        learn_from_runs=doc.learn_from_runs,
        eval_gate=doc.eval_gate,
        evals=doc.evals,
    )


@action(
    "put_draft_agent",
    "Create or update an agent in the draft",
    "PUT",
    "/api/workflows/{workflow_id}/draft/agents/{agent_id}",
)
def put_draft_agent(f: Factory, workflow_id: str, agent_id: str, body: AgentSpec) -> DraftView:
    if body.id != agent_id:
        raise WorkflowError("agent id in the path and body must match (ids cannot be renamed; duplicate instead)")
    f.workflows[workflow_id].draft.upsert_agent(body)
    return get_draft(f, workflow_id)


@action(
    "duplicate_draft_agent",
    "Copy an agent under a new id",
    "POST",
    "/api/workflows/{workflow_id}/draft/agents/{agent_id}/duplicate",
)
def duplicate_draft_agent(f: Factory, workflow_id: str, agent_id: str, body: DuplicateAgentInput) -> DraftView:
    f.workflows[workflow_id].draft.duplicate_agent(agent_id, body.new_id)
    return get_draft(f, workflow_id)


@action(
    "delete_draft_agent",
    "Delete an agent that no station uses",
    "DELETE",
    "/api/workflows/{workflow_id}/draft/agents/{agent_id}",
)
def delete_draft_agent(f: Factory, workflow_id: str, agent_id: str) -> DraftView:
    f.workflows[workflow_id].draft.delete_agent(agent_id)
    return get_draft(f, workflow_id)


@action(
    "set_station_agent",
    "Choose which agent runs an agent station",
    "PUT",
    "/api/workflows/{workflow_id}/draft/stations/{station_id}/agent",
)
def set_station_agent(f: Factory, workflow_id: str, station_id: str, body: StationAgentInput) -> DraftView:
    f.workflows[workflow_id].draft.set_station_agent(station_id, body.agent)
    return get_draft(f, workflow_id)


@action(
    "add_draft_station",
    "Add a station to the draft's lane (agent station or deterministic check)",
    "POST",
    "/api/workflows/{workflow_id}/draft/stations",
)
def add_draft_station(f: Factory, workflow_id: str, body: AddStationInput) -> DraftView:
    data = body.model_dump(exclude={"position"})
    f.workflows[workflow_id].draft.add_station(WorkflowStation(**data), body.position)
    return get_draft(f, workflow_id)


@action(
    "update_draft_station",
    "Change a station's routes, repair flag, handler or agent (only the sent fields)",
    "PATCH",
    "/api/workflows/{workflow_id}/draft/stations/{station_id}",
)
def update_draft_station(f: Factory, workflow_id: str, station_id: str, body: UpdateStationInput) -> DraftView:
    patch = {k: getattr(body, k) for k in body.model_fields_set}
    f.workflows[workflow_id].draft.update_station(station_id, patch)
    return get_draft(f, workflow_id)


@action(
    "delete_draft_station",
    "Remove a station; routes pointing at it are cleared",
    "DELETE",
    "/api/workflows/{workflow_id}/draft/stations/{station_id}",
)
def delete_draft_station(f: Factory, workflow_id: str, station_id: str) -> DraftView:
    f.workflows[workflow_id].draft.remove_station(station_id)
    return get_draft(f, workflow_id)


@action(
    "reorder_draft_stations",
    "Set the lane order (every station id exactly once)",
    "PUT",
    "/api/workflows/{workflow_id}/draft/stations/order",
)
def reorder_draft_stations(f: Factory, workflow_id: str, body: ReorderStationsInput) -> DraftView:
    f.workflows[workflow_id].draft.reorder_stations(body.order)
    return get_draft(f, workflow_id)


@action(
    "put_draft_doc", "Create or update a reference document", "PUT", "/api/workflows/{workflow_id}/draft/docs/{doc_id}"
)
def put_draft_doc(f: Factory, workflow_id: str, doc_id: str, body: RefDoc) -> DraftView:
    if body.id != doc_id:
        raise WorkflowError("doc id in the path and body must match")
    f.workflows[workflow_id].draft.upsert_doc(body)
    return get_draft(f, workflow_id)


@action(
    "delete_draft_doc",
    "Delete a reference document no agent uses",
    "DELETE",
    "/api/workflows/{workflow_id}/draft/docs/{doc_id}",
)
def delete_draft_doc(f: Factory, workflow_id: str, doc_id: str) -> DraftView:
    f.workflows[workflow_id].draft.delete_doc(doc_id)
    return get_draft(f, workflow_id)


@action(
    "publish_draft",
    "Validate the draft and publish it as the next active version",
    "POST",
    "/api/workflows/{workflow_id}/draft/publish",
    status_code=201,
)
async def publish_draft(f: Factory, workflow_id: str, body: PublishInput) -> WorkflowVersionInfo:
    """With an evaluation gate (ADR-0025) the new version is evaluated against the active
    one: `block` keeps it a candidate until it passes; `warn` activates it and reports."""
    from agent_factory import evals
    from agent_factory.engine.preflight import PreflightFailed

    w = f.workflows[workflow_id]
    _, doc, _ = w.draft.get()
    gate = doc.eval_gate if doc.evals and workflow_id in f.cfg.blueprints else "off"
    before = w.active_version()
    who = current_identity().user
    info = w.draft.publish(f"{body.note} (by {who})", activate=gate != "block")
    if gate == "off":
        return info
    try:
        await _gate(f, workflow_id, "product")
        e = evals.start(f.manager, workflow_id, info.version, who, trigger="publish", baseline=before)
        note = f"evaluation {e.id} running against version {before}"
    except (FactoryError, PreflightFailed) as exc:
        note = f"evaluation not started: {exc}"
        evals.record_not_started(f.manager, workflow_id, info.version, who, str(exc))
    return info.model_copy(update={"evaluation": note})


@action("discard_draft", "Throw the draft away", "DELETE", "/api/workflows/{workflow_id}/draft")
def discard_draft(f: Factory, workflow_id: str) -> DraftView:
    f.workflows[workflow_id].draft.discard()
    return get_draft(f, workflow_id)


# ------------------------------------------------------------- integrations --
@action(
    "get_delivery",
    "Delivery integrations and environments, with a live readiness check of each integration",
    "GET",
    "/api/integrations",
)
async def get_delivery(f: Factory) -> DeliveryView:
    providers = f.manager.providers
    envs = f.cfg.environments
    # readiness comes from the preflight checks (cached; POST /api/preflight re-runs them)
    checks: dict[str, ReadinessView] = {}
    for view in await get_preflight(f):
        for c in view.checks:
            if c.integration:
                checks[c.integration] = ReadinessView(state=c.state, reasons=c.reasons)
    integrations = []
    for name, p in providers.integrations.items():
        ready = checks.get(name) or ReadinessView(state="unknown", reasons=["not used by any blueprint"])
        integrations.append(
            IntegrationView(
                id=name,
                provider=p.kind,
                capabilities=sorted(p.capabilities),
                settings=dict(f.cfg.integrations[name].settings),
                auth=_auth_label(f.cfg.integrations[name].auth),
                readiness=ready,
                used_by=[e for e, env in envs.items() if name in env.model_dump().values()],
            )
        )
    environments = [
        EnvironmentView(
            name=e,
            bindings=env.model_dump(),
            blueprints=[pl for pl, cfg in f.cfg.blueprints.items() if cfg.environment == e and cfg.target == "new"],
        )
        for e, env in envs.items()
    ]
    return DeliveryView(integrations=integrations, environments=environments)


# ------------------------------------------------------------------ skills --
@action("list_skills", "Skills agents can use: built-in and imported from GitHub", "GET", "/api/skills")
def list_skills(f: Factory) -> list[SkillInfo]:
    lib = f.workflows.library
    imported = [_skill_info(r) for r in lib.imported()] if lib else []
    return _installed_skills(f.workflows.skills_dir) + imported


def _skill_info(r: SkillRecord) -> SkillInfo:
    return SkillInfo(
        name=r.name,
        description=r.description[:300],
        source="github",
        repo=r.repo,
        path=r.path,
        ref=r.ref,
        sha=r.sha,
        scripts=r.scripts,
        installed_at=r.installed_at,
    )


def _library(f: Factory) -> SkillLibrary:
    if f.workflows.library is None:
        raise SkillError("skill imports are not enabled")
    return f.workflows.library


def _fetch_skill(f: Factory, repo: str, path: str, ref: str) -> Fetched:
    try:
        return fetch_dir(repo, path, ref, f.manager.secrets.resolve_optional(f.cfg.skills_github_token_ref))
    except GitHubError as exc:
        raise SkillError(str(exc)) from exc


@action(
    "preview_skill",
    "Fetch a skill folder from GitHub for review: files, scripts, diff to the installed commit",
    "POST",
    "/api/skills/preview",
)
def preview_skill(f: Factory, body: SkillSourceInput) -> SkillPreview:
    fetched = _fetch_skill(f, body.repo, body.path, body.ref)
    try:
        return _library(f).preview(fetched)
    finally:
        fetched.cleanup()


@action(
    "install_skill",
    "Install (or update to) the reviewed commit of a GitHub skill",
    "POST",
    "/api/skills/install",
)
def install_skill(f: Factory, body: InstallSkillInput) -> SkillInfo:
    fetched = _fetch_skill(f, body.repo, body.path, body.sha)  # exactly the reviewed commit
    try:
        fetched.ref = body.ref  # remember what to follow for update checks
        rec = _library(f).install(fetched, body.accept_scripts)
    finally:
        fetched.cleanup()
    return _skill_info(rec)


@action("get_skill", "An imported skill: files, installed commits and users", "GET", "/api/skills/{name}")
def get_skill(f: Factory, name: str) -> SkillDetail:
    lib = _library(f)
    rec = lib.get(name)
    return SkillDetail(
        skill=_skill_info(rec),
        files=rec.files,
        versions=[
            SkillVersionInfo(sha=v.sha, ref=v.ref, installed_at=v.installed_at, current=v.sha == rec.sha)
            for v in lib.versions(name)
        ],
        used_by=f.workflows.skill_users(name),
    )


@action(
    "check_skill_update",
    "Fetch the latest commit of the skill's ref and diff it against the installed one",
    "POST",
    "/api/skills/{name}/check-update",
)
def check_skill_update(f: Factory, name: str) -> SkillPreview:
    rec = _library(f).get(name)
    return preview_skill(f, SkillSourceInput(repo=rec.repo, path=rec.path, ref=rec.ref))


@action(
    "remove_skill",
    "Remove an imported skill from the picker (blocked while a workflow uses it)",
    "DELETE",
    "/api/skills/{name}",
)
def remove_skill(f: Factory, name: str) -> list[SkillInfo]:
    users = f.workflows.skill_users(name)
    if users:
        raise SkillError(f"skill '{name}' is used by workflows {users}; remove it from their agents and publish first")
    _library(f).remove(name)
    return list_skills(f)


def _installed_skills(skills_dir: Path) -> list[SkillInfo]:
    out = []
    for skill in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill.read_text()
        m = re.search(r"^description:\s*(?:>-?\s*\n)?(.+?)(?:\n[a-z_-]+:|\n---)", text, re.DOTALL | re.MULTILINE)
        desc = " ".join(m.group(1).split()) if m else ""
        name = skill.parent.name
        out.append(SkillInfo(name=name, description=desc[:300]))
    return out


# --------------------------------------------------------------- adapters --
def endpoint_for(spec: ActionSpec, f: Factory) -> Callable[..., Any]:
    """Wrap an action as a FastAPI endpoint: same function, factory injected."""
    sig = inspect.signature(spec.fn)
    params = list(sig.parameters.values())[1:]  # drop `f`

    async def endpoint(**kwargs: Any) -> Any:
        # async so actions run on the event loop (they may schedule engine tasks)
        result = spec.fn(f, **kwargs)
        return await result if inspect.isawaitable(result) else result

    endpoint.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]
    endpoint.__name__ = spec.name
    endpoint.__doc__ = spec.description
    return endpoint


def agent_tools_server(f: Factory) -> Any:
    """Expose agent_tool actions as an in-process MCP server for the Agent SDK."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    tools = []
    for spec in REGISTRY.values():
        if not spec.agent_tool:
            continue
        body_model: type[BaseModel] = inspect.signature(spec.fn).parameters["body"].annotation
        if isinstance(body_model, str):
            body_model = globals()[body_model]

        def make(spec: ActionSpec = spec, model: type[BaseModel] = body_model) -> Any:
            @tool(spec.name, spec.description, model.model_json_schema())
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    out = spec.fn(f, body=model.model_validate(args))
                    text = out.model_dump_json() if isinstance(out, BaseModel) else str(out)
                    return {"content": [{"type": "text", "text": text}]}
                except Exception as exc:  # noqa: BLE001
                    return {"content": [{"type": "text", "text": f"error: {exc}"}], "is_error": True}

            return handler

        tools.append(make())
    return create_sdk_mcp_server(name="factory", version="1.0.0", tools=tools)
