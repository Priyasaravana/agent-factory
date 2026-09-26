"""Shared actions (pattern borrowed from BuilderIO agent-native).

Each capability is defined ONCE. The same function, with the same validation,
is exposed as:
  * an HTTP route in the OpenAPI contract (the TypeScript UI calls it), and
  * optionally an in-process MCP tool agents can call (agent_tool=True).
Agents never click the UI; UI and agents share one action layer.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from agent_factory.engine.pipeline import FactoryError
from agent_factory.engine.workflows import WorkflowError
from agent_factory.github import Fetched, GitHubError, fetch_dir
from agent_factory.models import (
    AddStationInput,
    AgentView,
    AnswersInput,
    CatalogView,
    ConfigView,
    CreateOrderInput,
    DecisionInput,
    DraftView,
    DuplicateAgentInput,
    Event,
    EventKind,
    FeedbackInput,
    FromGitHubInput,
    FromTemplateInput,
    HealthView,
    InstallSkillInput,
    Order,
    OrderDetail,
    PublishInput,
    ReorderStationsInput,
    Run,
    RunDetail,
    SkillDetail,
    SkillInfo,
    SkillSourceInput,
    SkillVersionInfo,
    StationAgentInput,
    StationView,
    TemplateInfo,
    UpdateStationInput,
    WorkflowSummary,
    WorkflowView,
)
from agent_factory.skills import SkillError, SkillLibrary, SkillPreview, SkillRecord
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CHECK_HANDLERS,
    HANDLER_REQUIREMENTS,
    MAX_DOC_CHARS,
    MAX_LEARNINGS_CHARS,
    OBSERVE_ONLY_PRESETS,
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
def _station_views(f: Factory, run: Run, workflow_id: str) -> list[StationView]:
    views = []
    flow = f.workflows[run.workflow_id or workflow_id].get(run.workflow_version)
    for s in flow.stations:
        attempts = run.attempts.get(s.id, 0) if run else 0
        state = "pending"
        if run:
            if run.current_station == s.id:
                state = {
                    "running": "running",
                    "held": "held",
                    "needs_input": "waiting",
                    "paused_limits": "waiting",
                    "interrupted": "held",
                }.get(run.status, "pending")
            elif attempts:
                last = [
                    e for e in f.store.list_events(run.id) if e.station == s.id and e.kind == EventKind.station_finished
                ]
                state = last[-1].data.get("outcome", "passed") if last else "passed"
        views.append(
            StationView(
                id=s.id,
                kind=s.kind,
                role=s.agent,
                handler=s.resolved_handler(),
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
        github=bool(f.settings.github_token),
        active_runs=f.manager.active_count(),
    )


@action("get_config", "Factory settings: product lines, their workflow versions, policies, gates", "GET", "/api/config")
def get_config(f: Factory) -> ConfigView:
    p = f.cfg.policies
    return ConfigView(
        name=f.cfg.factory.name,
        mode=f.settings.factory_mode,
        workflows={w.workflow_id: w.active_version() for w in f.workflows},
        product_lines={k: v.description for k, v in f.cfg.product_lines.items()},
        policies={k: getattr(p, k).mode for k in ("implement", "deploy", "publish", "merge", "recover")},
        gates=[f"{g.kind} after {g.after}" for g in f.cfg.gates],
    )


@action("list_orders", "List orders, newest first", "GET", "/api/orders")
def list_orders(f: Factory) -> list[Order]:
    return f.store.list_orders()


@action("create_order", "Submit requirements; starts the first run", "POST", "/api/orders", status_code=201)
def create_order(f: Factory, body: CreateOrderInput) -> OrderDetail:
    order = f.manager.create_order(body)
    f.manager.start_run(order)
    return get_order(f, order.id)


@action("get_order", "Order with its runs and feedback", "GET", "/api/orders/{order_id}")
def get_order(f: Factory, order_id: str) -> OrderDetail:
    order = f.store.get_order(order_id)
    if not order:
        raise FactoryError("order not found")
    return OrderDetail(order=order, runs=f.store.list_runs(order_id), feedback=f.store.list_feedback(order_id))


@action(
    "submit_feedback",
    "Post-deploy feedback; starts the next iteration",
    "POST",
    "/api/orders/{order_id}/feedback",
    status_code=201,
)
def submit_feedback(f: Factory, order_id: str, body: FeedbackInput) -> Run:
    return f.manager.feedback(order_id, body.text)


@action("get_run", "Run with station states", "GET", "/api/runs/{run_id}")
def get_run(f: Factory, run_id: str) -> RunDetail:
    run = f.store.get_run(run_id)
    if not run:
        raise FactoryError("run not found")
    order = f.store.get_order(run.order_id)
    assert order is not None
    return RunDetail(run=run, order=order, stations=_station_views(f, run, order.product_line))


@action("list_events", "Append-only event/decision log for a run", "GET", "/api/runs/{run_id}/events")
def list_events(f: Factory, run_id: str, after: int = 0) -> list[Event]:
    return f.store.list_events(run_id, after)


@action("answer_questions", "Answer intake's blocking questions", "POST", "/api/runs/{run_id}/answers")
def answer_questions(f: Factory, run_id: str, body: AnswersInput) -> Run:
    return f.manager.answer(run_id, body.answers)


@action("resume_run", "Resume a held, interrupted or paused run", "POST", "/api/runs/{run_id}/resume")
def resume_run(f: Factory, run_id: str) -> Run:
    return f.manager.resume(run_id)


@action("cancel_run", "Cancel a run", "POST", "/api/runs/{run_id}/cancel")
def cancel_run(f: Factory, run_id: str) -> Run:
    return f.manager.cancel(run_id)


@action(
    "log_decision",
    "Record a decision or assumption in the run's append-only log",
    "POST",
    "/api/decisions",
    agent_tool=True,
    status_code=201,
)
def log_decision(f: Factory, body: DecisionInput) -> Event:
    if not f.store.get_run(body.run_id):
        raise FactoryError("run not found")
    msg = body.decision + (f" — because {body.rationale}" if body.rationale else "")
    return f.store.add_event(body.run_id, EventKind.decision, msg, station=body.station)


# -------------------------------------------------------------- workflows --
@action("list_workflows", "One workflow per product line", "GET", "/api/workflows")
def list_workflows(f: Factory) -> list[WorkflowSummary]:
    out = []
    for w in f.workflows:
        doc = w.get()
        out.append(
            WorkflowSummary(
                workflow_id=w.workflow_id,
                product_line=f.cfg.product_lines[w.workflow_id].description,
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
    return f.workflows[workflow_id].versions()


@action(
    "activate_workflow_version",
    "Make a version active for new runs (runs in flight keep theirs)",
    "POST",
    "/api/workflows/{workflow_id}/versions/{version}/activate",
)
def activate_workflow_version(f: Factory, workflow_id: str, version: int) -> WorkflowVersionInfo:
    return f.workflows[workflow_id].activate(version)


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
    )


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
    return [
        StationView(
            id=s.id,
            kind=s.kind,
            role=s.agent,
            handler=s.resolved_handler(),
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
        fetched = fetch_dir(body.repo, body.path, body.ref, f.settings.skills_github_token)
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
        dirty=w.draft.dirty(),
        updated_at=stamp,
        template=doc.template,
        problems=w.draft.problems(),
        warnings=w.draft.warnings(),
        skill_updates=w.draft.skill_updates(),
        stations=_station_views_for(doc),
        agents=_agent_views(f, doc),
        docs=list(doc.docs.values()),
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
def publish_draft(f: Factory, workflow_id: str, body: PublishInput) -> WorkflowVersionInfo:
    return f.workflows[workflow_id].draft.publish(body.note)


@action("discard_draft", "Throw the draft away", "DELETE", "/api/workflows/{workflow_id}/draft")
def discard_draft(f: Factory, workflow_id: str) -> DraftView:
    f.workflows[workflow_id].draft.discard()
    return get_draft(f, workflow_id)


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
        vendored=False,
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
        return fetch_dir(repo, path, ref, f.settings.skills_github_token)
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
    vendored = set()
    note = skills_dir / "VENDORED.md"
    if note.exists():
        vendored = set(re.findall(r"^\| ([a-z0-9-]+) \|", note.read_text(), re.MULTILINE))
    out = []
    for skill in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill.read_text()
        m = re.search(r"^description:\s*(?:>-?\s*\n)?(.+?)(?:\n[a-z_-]+:|\n---)", text, re.DOTALL | re.MULTILINE)
        desc = " ".join(m.group(1).split()) if m else ""
        name = skill.parent.name
        out.append(SkillInfo(name=name, description=desc[:300], vendored=name in vendored))
    return out


# --------------------------------------------------------------- adapters --
def endpoint_for(spec: ActionSpec, f: Factory) -> Callable[..., Any]:
    """Wrap an action as a FastAPI endpoint: same function, factory injected."""
    sig = inspect.signature(spec.fn)
    params = list(sig.parameters.values())[1:]  # drop `f`

    async def endpoint(**kwargs: Any) -> Any:
        # async so actions run on the event loop (they may schedule engine tasks)
        return spec.fn(f, **kwargs)

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
