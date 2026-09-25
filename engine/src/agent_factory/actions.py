"""Shared actions (pattern borrowed from BuilderIO agent-native).

Each capability is defined ONCE. The same function, with the same validation,
is exposed as:
  * an HTTP route in the OpenAPI contract (the TypeScript UI calls it), and
  * optionally an in-process MCP tool agents can call (agent_tool=True).
Agents never click the UI; UI and agents share one action layer.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from agent_factory.engine.pipeline import FactoryError
from agent_factory.models import (
    AnswersInput,
    ConfigView,
    CreateOrderInput,
    DecisionInput,
    Event,
    EventKind,
    FeedbackInput,
    HealthView,
    Order,
    OrderDetail,
    Run,
    RunDetail,
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
def _station_views(f: Factory, run: Run | None) -> list[StationView]:
    views = []
    for s in f.cfg.stations:
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
            StationView(id=s.id, kind=s.kind, role=s.role, repair=s.only_on_fail, state=state, attempts=attempts)
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
