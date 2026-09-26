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

from agent_factory.engine.lines import LineError
from agent_factory.engine.pipeline import FactoryError
from agent_factory.line import (
    MAX_DOC_CHARS,
    MAX_LEARNINGS_CHARS,
    OBSERVE_ONLY_PRESETS,
    SAFE_EXTRA_TOOLS,
    TOOL_PRESETS,
    AgentSpec,
    LineDoc,
    LineVersionInfo,
    RefDoc,
)
from agent_factory.models import (
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
    HealthView,
    LineView,
    Order,
    OrderDetail,
    PublishInput,
    Run,
    RunDetail,
    SkillInfo,
    StationAgentInput,
    StationView,
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
def _station_views(f: Factory, run: Run | None, version: int | None = None) -> list[StationView]:
    views = []
    flow = f.lines.get(run.line_version if run else version)
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


@action("get_config", "The factory line, policies and product lines", "GET", "/api/config")
def get_config(f: Factory) -> ConfigView:
    p = f.cfg.policies
    return ConfigView(
        name=f.cfg.factory.name,
        mode=f.settings.factory_mode,
        line_version=f.lines.active_version(),
        product_lines={k: v.description for k, v in f.cfg.product_lines.items()},
        stations=_station_views(f, None),
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
    return RunDetail(run=run, order=order, stations=_station_views(f, run))


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


# ------------------------------------------------------------------ lines --
@action("get_line", "The active line: stations and agent specs", "GET", "/api/line")
def get_line(f: Factory) -> LineView:
    return _line_view(f, f.lines.active_version())


@action("get_line_version", "A specific (immutable) line version", "GET", "/api/line/versions/{version}")
def get_line_version(f: Factory, version: int) -> LineView:
    return _line_view(f, version)


@action("list_line_versions", "All line versions, newest first", "GET", "/api/line/versions")
def list_line_versions(f: Factory) -> list[LineVersionInfo]:
    return f.lines.versions()


@action(
    "activate_line_version",
    "Make a version the active line for new runs (runs in flight keep their version)",
    "POST",
    "/api/line/versions/{version}/activate",
)
def activate_line_version(f: Factory, version: int) -> LineVersionInfo:
    return f.lines.activate(version)


def _line_view(f: Factory, version: int) -> LineView:
    doc = f.lines.get(version)
    info = next(i for i in f.lines.versions() if i.version == version)
    return LineView(
        line_id=f.lines.line_id,
        version=version,
        active=info.active,
        note=info.note,
        name=doc.name,
        description=doc.description,
        blueprint=doc.blueprint,
        blueprint_update_available=f.lines.blueprint_update_available(),
        stations=_station_views(f, None, version),
        agents=_agent_views(f, doc),
        docs=list(doc.docs.values()),
    )


def _agent_views(f: Factory, doc: LineDoc) -> list[AgentView]:
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


def _station_views_for(doc: LineDoc) -> list[StationView]:
    return [
        StationView(
            id=s.id,
            kind=s.kind,
            role=s.agent,
            handler=s.resolved_handler(),
            repair=s.only_on_fail,
            state="pending",
            attempts=0,
        )
        for s in doc.stations
    ]


# ------------------------------------------------------------ line editing --
@action("get_catalog", "Choices for the editor: tool presets, model tiers, installed skills", "GET", "/api/catalog")
def get_catalog(f: Factory) -> CatalogView:
    return CatalogView(
        presets=TOOL_PRESETS,
        observe_only_presets=sorted(OBSERVE_ONLY_PRESETS),
        extra_tools=sorted(SAFE_EXTRA_TOOLS),
        model_tiers={t: f.cfg.models.resolve(t) for t in ("judgment", "default", "fast")},
        skills=_installed_skills(f.lines.skills_dir),
        max_previous_iterations=5,
        max_doc_chars=MAX_DOC_CHARS,
        max_learnings_chars=MAX_LEARNINGS_CHARS,
    )


@action(
    "get_draft",
    "The editable draft of the line (created from the active version on first edit)",
    "GET",
    "/api/line/draft",
)
def get_draft(f: Factory) -> DraftView:
    base, doc, stamp = f.lines.draft.get()
    active = f.lines.active_version()
    return DraftView(
        base_version=base,
        active_version=active,
        stale=base != active,
        dirty=f.lines.draft.dirty(),
        updated_at=stamp,
        problems=f.lines.draft.problems(),
        stations=_station_views_for(doc),
        agents=_agent_views(f, doc),
        docs=list(doc.docs.values()),
    )


@action("put_draft_agent", "Create or update an agent in the draft", "PUT", "/api/line/draft/agents/{agent_id}")
def put_draft_agent(f: Factory, agent_id: str, body: AgentSpec) -> DraftView:
    if body.id != agent_id:
        raise LineError("agent id in the path and body must match (ids cannot be renamed; duplicate instead)")
    f.lines.draft.upsert_agent(body)
    return get_draft(f)


@action("duplicate_draft_agent", "Copy an agent under a new id", "POST", "/api/line/draft/agents/{agent_id}/duplicate")
def duplicate_draft_agent(f: Factory, agent_id: str, body: DuplicateAgentInput) -> DraftView:
    f.lines.draft.duplicate_agent(agent_id, body.new_id)
    return get_draft(f)


@action("delete_draft_agent", "Delete an agent that no station uses", "DELETE", "/api/line/draft/agents/{agent_id}")
def delete_draft_agent(f: Factory, agent_id: str) -> DraftView:
    f.lines.draft.delete_agent(agent_id)
    return get_draft(f)


@action(
    "set_station_agent",
    "Choose which agent runs an agent station",
    "PUT",
    "/api/line/draft/stations/{station_id}/agent",
)
def set_station_agent(f: Factory, station_id: str, body: StationAgentInput) -> DraftView:
    f.lines.draft.set_station_agent(station_id, body.agent)
    return get_draft(f)


@action("put_draft_doc", "Create or update a reference document", "PUT", "/api/line/draft/docs/{doc_id}")
def put_draft_doc(f: Factory, doc_id: str, body: RefDoc) -> DraftView:
    if body.id != doc_id:
        raise LineError("doc id in the path and body must match")
    f.lines.draft.upsert_doc(body)
    return get_draft(f)


@action("delete_draft_doc", "Delete a reference document no agent uses", "DELETE", "/api/line/draft/docs/{doc_id}")
def delete_draft_doc(f: Factory, doc_id: str) -> DraftView:
    f.lines.draft.delete_doc(doc_id)
    return get_draft(f)


@action(
    "publish_draft",
    "Validate the draft and publish it as the next active line version",
    "POST",
    "/api/line/draft/publish",
    status_code=201,
)
def publish_draft(f: Factory, body: PublishInput) -> LineVersionInfo:
    return f.lines.draft.publish(body.note)


@action("discard_draft", "Throw the draft away", "DELETE", "/api/line/draft")
def discard_draft(f: Factory) -> DraftView:
    f.lines.draft.discard()
    return get_draft(f)


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
