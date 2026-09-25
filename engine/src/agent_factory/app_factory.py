"""Composition root: wires config, state, executor, agents and the run manager,
and exposes the action registry as an OpenAPI-first FastAPI app."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from agent_factory import actions
from agent_factory.agents import AgentRunner, ClaudeAgentRunner, FakeAgentRunner
from agent_factory.config import FactoryConfig, load_config
from agent_factory.engine.pipeline import FactoryError, RunManager
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import Executor, FakeExecutor, LocalExecutor
from agent_factory.models import TERMINAL, RunStatus
from agent_factory.settings import Settings
from agent_factory.state import SqliteStateStore, StateStore


@dataclass
class Factory:
    cfg: FactoryConfig
    settings: Settings
    store: StateStore
    manager: RunManager


def build_factory(
    settings: Settings | None = None,
    cfg: FactoryConfig | None = None,
    store: StateStore | None = None,
    executor: Executor | None = None,
    agents: AgentRunner | None = None,
) -> Factory:
    settings = settings or Settings()
    cfg = cfg or load_config(settings.factory_config)
    data_dir = Path(settings.data_dir or cfg.factory.data_dir)
    home = Path(settings.factory_home)
    store = store or SqliteStateStore(data_dir / "factory.db")
    live = settings.factory_mode == "live"
    executor = executor or (LocalExecutor() if live else FakeExecutor())
    ws = Workspace(data_dir, home, settings.git_author_name, settings.git_author_email)

    factory = Factory(cfg, settings, store, manager=None)  # type: ignore[arg-type]
    if agents is None:
        if live:
            agents = ClaudeAgentRunner(home, tools_server=actions.agent_tools_server(factory))
        else:
            agents = FakeAgentRunner()
    factory.manager = RunManager(cfg, settings, store, ws, executor, agents)
    return factory


def create_app(factory: Factory | None = None) -> FastAPI:
    holder: dict[str, Factory] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        f = factory or build_factory()
        holder["f"] = f
        interrupted = f.manager.recover_on_startup()
        if interrupted:
            app.state.interrupted = interrupted
        yield
        await f.manager.shutdown()

    app = FastAPI(
        title="Agent Factory API",
        version="0.1.0",
        description="API-first control plane for the agent software factory. "
        "Every route is a shared action also available to agents.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    settings = factory.settings if factory else Settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(FactoryError)
    async def factory_error(_: Request, exc: FactoryError) -> JSONResponse:
        code = 404 if "not found" in str(exc) else 409
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    class _Lazy:
        """Resolve the factory at request time (it is built in lifespan)."""

        def __getattr__(self, item: str):  # type: ignore[no-untyped-def]
            return getattr(holder["f"], item)

    lazy = _Lazy()
    for spec in actions.REGISTRY.values():
        app.add_api_route(
            spec.path,
            actions.endpoint_for(spec, lazy),  # type: ignore[arg-type]
            methods=[spec.method],
            name=spec.name,
            summary=spec.description,
            operation_id=spec.name,
            status_code=spec.status_code,
            tags=["agent-tool"] if spec.agent_tool else ["factory"],
        )

    @app.get(
        "/api/runs/{run_id}/stream",
        operation_id="stream_run",
        tags=["factory"],
        summary="Server-sent events: live run events and status",
    )
    async def stream_run(run_id: str, request: Request, after: int = 0) -> EventSourceResponse:
        f = holder["f"]

        async def gen() -> AsyncIterator[dict[str, str]]:
            last = after
            while not await request.is_disconnected():
                for ev in f.store.list_events(run_id, last):
                    last = ev.id
                    yield {"event": "event", "id": str(ev.id), "data": ev.model_dump_json()}
                run = f.store.get_run(run_id)
                if run:
                    yield {
                        "event": "status",
                        "data": json.dumps({"status": run.status, "station": run.current_station}),
                    }
                    if run.status in TERMINAL | {RunStatus.held, RunStatus.needs_input} and not f.manager.is_active(
                        run_id
                    ):
                        break
                await asyncio.sleep(1.0)

        return EventSourceResponse(gen())

    return app


app = create_app()  # uvicorn agent_factory.app_factory:app
