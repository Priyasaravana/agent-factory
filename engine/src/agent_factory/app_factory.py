"""Composition root: wires config, state, executor, agents and the change manager,
and exposes the action registry as an OpenAPI-first FastAPI app."""

from __future__ import annotations

import asyncio
import json
import logging
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
from agent_factory.engine.pipeline import ChangeManager, FactoryError, InvalidRequestError, RefusedError
from agent_factory.engine.preflight import PreflightFailed
from agent_factory.engine.workflows import WorkflowError, WorkflowRegistry
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import Executor, FakeExecutor, LocalExecutor
from agent_factory.github import fetch_dir
from agent_factory.identity import IdentityMiddleware
from agent_factory.models import TERMINAL, ChangeStatus
from agent_factory.providers import Providers
from agent_factory.sandbox import SandboxManager
from agent_factory.secret_refs import SecretResolver
from agent_factory.settings import Settings
from agent_factory.skills import SkillError, SkillLibrary
from agent_factory.state import SqliteStateStore, StateStore

log = logging.getLogger("agent_factory")


@dataclass
class Factory:
    cfg: FactoryConfig
    settings: Settings
    store: StateStore
    manager: ChangeManager
    workflows: WorkflowRegistry
    sandbox: SandboxManager | None = None


def sandbox_for(cfg: FactoryConfig, settings: Settings, home: Path, data_dir: Path) -> SandboxManager | None:
    mode = settings.agent_sandbox or cfg.sandbox.mode
    if mode == "off":
        log.warning(
            "agent sandbox is OFF: agents and generated code run inside the factory container, "
            "next to its credentials and the Docker daemon. Use only for development."
        )
        return None
    return SandboxManager(cfg.sandbox, home, data_dir)


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

    secrets = SecretResolver(settings)
    library = SkillLibrary(store, home / "plugin" / "skills", data_dir / "skill-plugins")
    workflows = WorkflowRegistry(
        store,
        {wid: home / template for wid, template in cfg.workflow_templates().items()},
        home / "workflow-templates",
        home / "plugin" / "skills",
        library,
    )
    # default skills first: the default workflow template uses them
    skill_problems = library.ensure_defaults(
        cfg.default_skills,
        Path(settings.skill_seeds_dir),
        lambda repo, path, ref: fetch_dir(repo, path, ref, secrets.resolve_optional(cfg.skills_github_token_ref)),
    )
    for problem in skill_problems:
        log.warning("%s", problem)
    try:
        workflows.ensure_seeded()  # first start: template -> workflow v1 per blueprint (never overwrites)
    except WorkflowError as exc:
        if skill_problems:
            raise WorkflowError(
                "could not seed the workflows because default skills are missing "
                "(first start needs GitHub access, or an image built with them)",
                exc.problems + skill_problems,
            ) from exc
        raise  # first start: template -> workflow v1 per blueprint (never overwrites)
    factory = Factory(cfg, settings, store, manager=None, workflows=workflows)  # type: ignore[arg-type]
    sandbox = sandbox_for(cfg, settings, home, data_dir) if live else None
    factory.sandbox = sandbox
    if agents is None:
        if live:
            agents = ClaudeAgentRunner(home, tools_server=actions.agent_tools_server(factory), sandbox=sandbox)
        else:
            agents = FakeAgentRunner()
    factory.manager = ChangeManager(
        cfg, settings, store, ws, executor, agents, workflows, Providers(cfg), secrets, sandbox=sandbox
    )
    return factory


def create_app(factory: Factory | None = None) -> FastAPI:
    holder: dict[str, Factory] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        f = factory or build_factory()
        holder["f"] = f
        if f.sandbox is not None:
            f.sandbox.start()  # image, network and egress proxy; the first build takes a few minutes
        f.manager.host_watcher.start()  # host sleep is not agent time (hostclock.py)
        f.manager.preflight.start()  # readiness checks now and every preflight.interval_minutes
        interrupted = f.manager.recover_on_startup()
        from agent_factory.evals import recover_on_startup as recover_evals

        await recover_evals(f.manager)  # evaluations cut short by a restart are cancelled (ADR-0025)
        if interrupted:
            app.state.interrupted = interrupted
        yield
        await f.manager.preflight.stop()
        await f.manager.host_watcher.stop()
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
    app.add_middleware(IdentityMiddleware, mode=settings.auth_mode)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(WorkflowError)
    async def workflow_error(_: Request, exc: WorkflowError) -> JSONResponse:
        code = 404 if "not found" in str(exc) else 409
        return JSONResponse(status_code=code, content={"detail": str(exc), "problems": exc.problems})

    @app.exception_handler(SkillError)
    async def skill_error(_: Request, exc: SkillError) -> JSONResponse:
        code = 404 if "not found" in str(exc) else 409
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.exception_handler(PreflightFailed)
    async def preflight_failed(_: Request, exc: PreflightFailed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "problems": exc.problems, "preflight": exc.view.model_dump(mode="json")},
        )

    @app.exception_handler(PermissionError)
    async def forbidden(_: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(FactoryError)
    async def factory_error(_: Request, exc: FactoryError) -> JSONResponse:
        code = 422 if isinstance(exc, RefusedError | InvalidRequestError) else 404 if "not found" in str(exc) else 409
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
        "/api/changes/{change_id}/stream",
        operation_id="stream_change",
        tags=["factory"],
        summary="Server-sent events: live run events and status",
    )
    async def stream_change(change_id: str, request: Request, after: int = 0) -> EventSourceResponse:
        f = holder["f"]

        async def gen() -> AsyncIterator[dict[str, str]]:
            last = after
            while not await request.is_disconnected():
                for ev in f.store.list_events(change_id, last):
                    last = ev.id
                    yield {"event": "event", "id": str(ev.id), "data": ev.model_dump_json()}
                change = f.store.get_change(change_id)
                if change:
                    yield {
                        "event": "status",
                        "data": json.dumps({"status": change.status, "station": change.current_station}),
                    }
                    if change.status in TERMINAL | {
                        ChangeStatus.held,
                        ChangeStatus.needs_input,
                        ChangeStatus.awaiting_approval,
                        ChangeStatus.awaiting_risk_approval,
                    } and not f.manager.is_active(change_id):
                        break
                await asyncio.sleep(1.0)

        return EventSourceResponse(gen())

    return app


app = create_app()  # uvicorn agent_factory.app_factory:app
