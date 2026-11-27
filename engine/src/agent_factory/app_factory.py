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

from agent_factory import actions, extensions
from agent_factory.agents import AgentRunner, ClaudeAgentRunner, FakeAgentRunner
from agent_factory.config import FactoryConfig, SandboxConfig, load_factory_config
from agent_factory.engine.pipeline import ChangeManager, FactoryError, InvalidRequestError, RefusedError
from agent_factory.engine.preflight import PreflightFailed
from agent_factory.engine.workflows import WorkflowError, WorkflowRegistry
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import Executor, FakeExecutor, LocalExecutor
from agent_factory.gateway import DirectGateway, build_gateway, gateway_egress, resolve_address, shared_refusal
from agent_factory.github import fetch_dir
from agent_factory.identity import IdentityMiddleware, read_proof
from agent_factory.models import TERMINAL, ChangeStatus
from agent_factory.packs import Pack
from agent_factory.packs import install as install_packs
from agent_factory.phases import load_phases
from agent_factory.providers import Providers
from agent_factory.providers.local import off_the_factory_site
from agent_factory.sandbox import SandboxManager, toolchain
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


def effective_sandbox(cfg: FactoryConfig) -> SandboxConfig:
    """The sandbox config every SandboxManager must use: the configured one, with the
    model gateway's egress applied (ADR-0036). Anything that builds a manager (the engine,
    `make sandbox-check`) goes through here; a manager built from the raw config would
    recreate the egress proxy without the gateway and cut agents off from the model."""
    gw = cfg.model_gateway
    if gw.provider == "direct":
        return cfg.sandbox
    # agents reach the model only through the gateway: it replaces direct model egress,
    # through a route because the egress proxy can't resolve compose names
    ip = resolve_address(gw.url)
    if ip is None:
        log.warning("model gateway %s does not resolve yet; sandboxes will not reach it", gw.url)
    egress, routes = gateway_egress(cfg.sandbox.egress, cfg.sandbox.routes, gw.url, ip)
    return cfg.sandbox.model_copy(update={"egress": egress, "routes": routes, "model_endpoint": egress[0]})


def sandbox_layers(packs: list[Pack]) -> list[str]:
    """The optional sandbox image layers the installed stack packs ask for (ADR-0041 §4)."""
    return toolchain.layers_for(p.stack.toolchain for p in packs if p.stack)


def sandbox_for(
    cfg: FactoryConfig, settings: Settings, home: Path, data_dir: Path, packs: list[Pack] | None = None
) -> SandboxManager | None:
    mode = settings.agent_sandbox or cfg.sandbox.mode
    if mode == "off":
        log.warning(
            "agent sandbox is OFF: agents and generated code run inside the factory container, "
            "next to its credentials and the Docker daemon. Use only for development."
        )
        return None
    cfg.sandbox = effective_sandbox(cfg)
    return SandboxManager(cfg.sandbox, home, data_dir, layers=sandbox_layers(packs or []))


def build_factory(
    settings: Settings | None = None,
    cfg: FactoryConfig | None = None,
    store: StateStore | None = None,
    executor: Executor | None = None,
    agents: AgentRunner | None = None,
) -> Factory:
    settings = settings or Settings()
    cfg = cfg or load_factory_config(settings)
    if cfg.local_overrides:
        log.info(
            "config: %d setting(s) from the local override: %s",
            len(cfg.local_overrides),
            ", ".join(cfg.local_overrides),
        )
    data_dir = Path(settings.data_dir or cfg.factory.data_dir)
    home = Path(settings.factory_home)
    # installed extensions (ADR-0051): none in the core image; the licensed edition's, by its key
    reg = extensions.activate(extensions.load())
    if reg.loaded:
        log.info("edition %s: %s", reg.edition, ", ".join(f"{e.name} {e.status}" for e in reg.loaded))
    store = store or SqliteStateStore(data_dir / "factory.db")
    live = settings.factory_mode == "live"
    executor = executor or (LocalExecutor() if live else FakeExecutor())
    ws = Workspace(data_dir, home, settings.git_author_name, settings.git_author_email)

    secrets = SecretResolver(settings)
    library = SkillLibrary(store, home / "plugin" / "skills", data_dir / "skill-plugins")
    # installed packs (ADR-0039 §2, ADR-0041): each blueprint bound to the stack pack it names, then
    # built-in phases + org columns + pack phases, and the packs' station templates
    installed = install_packs(cfg, home)
    phases, pack_templates = load_phases(cfg, home, installed)
    workflows = WorkflowRegistry(
        store,
        {wid: home / template for wid, template in cfg.workflow_templates().items()},
        home / "workflow-templates",
        home / "plugin" / "skills",
        library,
        cfg.role_ids(),
        phases,
        pack_templates,
        [p.catalogue for p in installed if p.catalogue is not None],
    )
    # default skills first: the default workflow template uses them
    skill_problems = library.ensure_defaults(
        cfg.default_skills,
        Path(settings.skill_seeds_dir),
        lambda repo, path, ref: fetch_dir(repo, path, ref, secrets.resolve_optional(cfg.skills_github_token_ref)),
    )
    for problem in skill_problems:
        log.warning("%s", problem)
    from agent_factory import reset

    reset.check(store)  # data from before the one-lane workflows needs `agent-factory reset` (ADR-0037 §7)
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
    if live and (why := shared_refusal(cfg.factory.shared, cfg.model_gateway.provider, cfg.model_gateway.credential)):
        raise RuntimeError(why)  # S-03: refused at start, before any sandbox gets the credential
    factory = Factory(cfg, settings, store, manager=None, workflows=workflows)  # type: ignore[arg-type]
    sandbox = sandbox_for(cfg, settings, home, data_dir, installed) if live else None
    factory.sandbox = sandbox
    gateway = build_gateway(cfg.model_gateway, secrets) if live else DirectGateway()
    if agents is None:
        if live:
            agents = ClaudeAgentRunner(
                home, tools_server=actions.agent_tools_server(factory), sandbox=sandbox, gateway=gateway
            )
        else:
            agents = FakeAgentRunner()
    factory.manager = ChangeManager(
        cfg, settings, store, ws, executor, agents, workflows, Providers(cfg), secrets, sandbox=sandbox, gateway=gateway
    )
    # S-04: a product delivered before apps moved off the UI's host is shown at its new address at once,
    # not at its next deploy (the same app, the same port; evidence already sealed is left as recorded)
    for product in store.list_products():
        if product.app_url and (moved := off_the_factory_site(product.app_url)) != product.app_url:
            product.app_url = moved
            store.save_product(product)
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
        f.manager.pr_watcher.start()  # follows changes' pull requests on team repos (J3)
        await f.manager.pr_watcher.resume()  # a merged or closed PR's follow-up cut short by a restart
        interrupted = f.manager.recover_on_startup()
        # a change cut off while its unaccepted release served: production goes back first (ADR-0046)
        await f.manager.settle_interrupted(interrupted)
        # temporary validation environments left behind by a crash or a failed removal (ADR-0046)
        await f.manager.sweep_validation()
        # apps delivered before the egress policy existed get it now, not at their next deploy (ADR-0048)
        secured, unsecured = await f.manager.secure_app_namespaces()
        if secured:
            log.info("egress policy applied to %d app namespace(s): %s", len(secured), ", ".join(secured))
        if unsecured:
            log.error(
                "the egress policy could NOT be applied to: %s. Their pods may still reach the internet; "
                "it is applied again at their next deploy and at the next start",
                ", ".join(unsecured),
            )
        from agent_factory.evals import recover_on_startup as recover_evals

        await recover_evals(f.manager)  # evaluations cut short by a restart are cancelled (ADR-0025)
        if interrupted:
            app.state.interrupted = interrupted
        f.manager.operations.start()  # watches, backs up and keeps up delivered products (J4)
        yield
        await f.manager.operations.stop()
        await f.manager.pr_watcher.stop()
        await f.manager.preflight.stop()
        await f.manager.host_watcher.stop()
        await f.manager.shutdown()

    app = FastAPI(
        title="Agent Software Factory API",
        version="0.1.0",
        description="API-first control plane for the agent software factory. "
        "Every route is a shared action also available to agents.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    settings = factory.settings if factory else Settings()
    # gateway mode refuses to start without the gateway proof (S-01): never headers on trust alone
    proof = read_proof(settings.gateway_proof_file) if settings.auth_mode == "gateway" else None
    app.add_middleware(IdentityMiddleware, mode=settings.auth_mode, proof=proof)
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

    @app.post(
        "/api/hooks/github",
        operation_id="github_webhook",
        tags=["factory"],
        summary="GitHub webhook (J3): a signed delivery makes the factory read the named pull requests again",
        status_code=202,
    )
    async def github_webhook(request: Request) -> JSONResponse:
        """Public (no sign-in): the delivery proves itself by `X-Hub-Signature-256`. Off unless
        `existing_repos.pr_updates` is `webhook` and its secret resolves. The payload only names which
        PRs to look at again; their state is always read from the host (`pr_loop.py`)."""
        from agent_factory import pr_loop

        f = holder["f"]
        if not f.manager.pr_watcher.webhook_secrets():  # each source has its own secret (ADR-0047)
            return JSONResponse(status_code=404, content={"detail": "not found"})
        declared = request.headers.get("content-length")
        if declared and (not declared.isdigit() or int(declared) > pr_loop.MAX_BODY):
            return JSONResponse(status_code=413, content={"detail": "too large"})
        body = await request.body()
        if len(body) > pr_loop.MAX_BODY:
            return JSONResponse(status_code=413, content={"detail": "too large"})
        verified = f.manager.pr_watcher.verified_by(body, request.headers.get(pr_loop.SIGNATURE_HEADER))
        if not verified:
            return JSONResponse(status_code=401, content={"detail": "bad signature"})
        event = request.headers.get(pr_loop.EVENT_HEADER, "")
        if event == "ping":
            return JSONResponse(status_code=200, content={"ok": True})
        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "not JSON"})
        if not pr_loop.targets(event, payload):
            return JSONResponse(status_code=202, content={"accepted": False})
        # GitHub waits 10 s at most: read afterwards, only PRs on repos a verifying source covers
        f.manager.pr_watcher.schedule_webhook(event, payload, verified)
        return JSONResponse(status_code=202, content={"accepted": True})

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
