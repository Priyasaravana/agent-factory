"""Readiness checks and order preflight (phase 4, ADR-0015).

Before a run can spend model tokens, everything it will need is checked:

    agents    model credential, agent sandbox
    build     Docker-in-Docker          (the registry integration's check)
    deploy    cluster API, free app port (the deploy integration's check)
    scan      scanner configured
    publish   credential reference resolvable (per integration; never blocks)
    workflow  the active workflow keeps apps at readiness Level 3

A `failed` check blocks what it guards: every check guards new orders, and all
but the app-port check also guard new iterations. `degraded` never blocks; it is
shown on the Integrations page and in the header. Results are cached and
refreshed at startup, every `preflight.interval_minutes`, on demand, and when a
gate finds them older than `preflight.max_age_seconds`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_factory.models import CheckView, PreflightView
from agent_factory.providers.base import CheckContext, Readiness

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import RunManager

log = logging.getLogger("agent_factory.preflight")

BLOCK_ALL = ["order", "iteration"]
_RANK = {"ready": 0, "degraded": 1, "failed": 2}


class PreflightFailed(Exception):
    def __init__(self, view: PreflightView, action: str) -> None:
        self.view, self.action = view, action
        failing = [c for c in view.checks if c.state == "failed" and action in c.blocks]
        self.problems = [f"{c.title}: {'; '.join(c.reasons)}" for c in failing]
        super().__init__(f"preflight failed for {view.product_line} ({len(failing)} check(s)); nothing was started")


def _check(
    id: str,
    title: str,
    area: str,
    state: str,
    reasons: list[str],
    integration: str | None = None,
    blocks: list[str] | None = None,
) -> CheckView:
    return CheckView(
        id=id,
        title=title,
        area=area,
        state=state,
        reasons=reasons,
        integration=integration,
        blocks=list(BLOCK_ALL if blocks is None else blocks),
    )


class Preflight:
    def __init__(self, manager: RunManager) -> None:
        self.m = manager
        self.cache: dict[str, PreflightView] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.Task[None] | None = None
        self.mapped_node_ports: set[int] | None = None  # learnt from the local cluster

    # --------------------------------------------------------------- API --
    async def product_line(self, pl: str, max_age: float | None = None) -> PreflightView:
        max_age = self.m.cfg.preflight.max_age_seconds if max_age is None else max_age
        cached = self.cache.get(pl)
        if cached and (datetime.now(UTC) - cached.checked_at).total_seconds() <= max_age:
            return cached
        async with self._lock:
            view = await self._run(pl)
            self.cache[pl] = view
            return view

    async def all(self, max_age: float = 0) -> list[PreflightView]:
        return [await self.product_line(pl, max_age) for pl in self.m.cfg.product_lines]

    async def gate(self, pl: str, action: str) -> PreflightView:
        """Raise PreflightFailed if a check guarding `action` ("order"/"iteration") failed.
        Integration checks may come from the cache; the in-memory checks (model,
        sandbox, ports, workflow) are always re-evaluated, so they are never stale."""
        view = self.refresh_local(await self.product_line(pl))
        if self._blocked(view, action):
            view = await self.product_line(pl, max_age=0)  # a cached failure may have been fixed
            if self._blocked(view, action):
                raise PreflightFailed(view, action)
        return view

    @staticmethod
    def _blocked(view: PreflightView, action: str) -> bool:
        return any(c.state == "failed" and action in c.blocks for c in view.checks)

    def refresh_local(self, view: PreflightView) -> PreflightView:
        live = self.m.settings.factory_mode == "live"
        fresh = {"model": self._model(live), "sandbox": self._sandbox(live), "ports": self._ports(view.product_line)}
        checks = [fresh.get(c.id, c) for c in view.checks]
        state = max((c.state for c in checks), key=lambda st: _RANK.get(st, 2))
        view = view.model_copy(update={"checks": checks, "state": state})
        self.cache[view.product_line] = view
        return view

    def start(self) -> None:
        if self._loop is None or self._loop.done():
            self._loop = asyncio.create_task(self._periodic())

    async def stop(self) -> None:
        if self._loop:
            self._loop.cancel()
            await asyncio.gather(self._loop, return_exceptions=True)

    async def _periodic(self) -> None:
        while True:
            try:
                for view in await self.all(max_age=0):
                    bad = [c.title for c in view.checks if c.state != "ready"]
                    log.info("preflight %s: %s%s", view.product_line, view.state, f" ({', '.join(bad)})" if bad else "")
            except Exception:  # noqa: BLE001 - keep checking
                log.exception("preflight check crashed")
            await asyncio.sleep(max(60, self.m.cfg.preflight.interval_minutes * 60))

    # ------------------------------------------------------------ checks --
    async def _run(self, pl: str) -> PreflightView:
        start = time.perf_counter()
        line = self.m.cfg.product_lines[pl]
        env = self.m.providers.for_product_line(pl)
        live = self.m.settings.factory_mode == "live"
        checks: list[CheckView] = [self._model(live), self._sandbox(live)]
        # one check per distinct integration, reported under the capabilities it serves
        by_integration: dict[str, list[str]] = {}
        for cap in ("registry", "deploy", "scan", "publish"):
            by_integration.setdefault(getattr(env, cap).name, []).append(cap)
        for name, caps in by_integration.items():
            checks.append(await self._integration(name, caps, live))
        checks.append(self._ports(pl))
        checks.append(self._scanner(line.scan_command))
        checks.append(self._workflow(pl))
        state = max((c.state for c in checks), key=lambda s: _RANK.get(s, 2))
        return PreflightView(
            product_line=pl,
            environment=env.environment,
            state=state,
            checked_at=datetime.now(UTC),
            duration_ms=int((time.perf_counter() - start) * 1000),
            checks=checks,
        )

    def _model(self, live: bool) -> CheckView:
        if not live:
            return _check("model", "Model access", "agents", "ready", ["dry-run: no model usage"])
        if self.m.settings.model_auth_configured():
            return _check("model", "Model access", "agents", "ready", ["model credential configured"])
        return _check(
            "model",
            "Model access",
            "agents",
            "failed",
            ["no model credential: set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env"],
        )

    def _sandbox(self, live: bool) -> CheckView:
        sb = self.m.sandbox
        if not live:
            return _check("sandbox", "Agent sandbox", "agents", "ready", ["dry-run: agents are simulated"])
        if sb is None:
            return _check(
                "sandbox",
                "Agent sandbox",
                "agents",
                "degraded",
                ["off: agents run in the factory container (development only)"],
            )
        state = sb.state.state
        if state == "ready":
            return _check("sandbox", "Agent sandbox", "agents", "ready", list(sb.state.reasons))
        if state == "failed":
            return _check("sandbox", "Agent sandbox", "agents", "failed", list(sb.state.reasons))
        return _check(
            "sandbox", "Agent sandbox", "agents", "degraded", ["preparing (first start builds the image); runs wait"]
        )

    async def _integration(self, name: str, caps: list[str], live: bool) -> CheckView:
        p = self.m.providers.integrations[name]
        title = f"{name} ({p.kind}): {', '.join(caps)}"
        if not live:
            return _check(f"integration:{name}", title, caps[0], "ready", ["dry-run: delivery is simulated"], name)
        try:
            r: Readiness = await asyncio.wait_for(
                p.check(CheckContext(self.m.cfg, self.m.settings, self.m.ex)), timeout=60
            )
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failed check
            r = Readiness("failed", [f"check crashed: {type(exc).__name__}: {exc}"])
        if "mapped_node_ports" in r.data:
            self.mapped_node_ports = set(r.data["mapped_node_ports"]) or None  # none reported: don't restrict
        ref = self.m.cfg.integrations[name].auth.secret_ref
        if ref:
            ok, why = self.m.secrets.available(ref)
            if not ok:
                r.reasons.append(f"credential {ref} not available ({why})")
                if r.state == "ready":
                    r.state = "degraded"
        # publish-only integrations never block delivery: the Deliver station reports "not pushed"
        blocks = [] if caps == ["publish"] else None
        state = r.state if r.state in _RANK else "failed"
        return _check(f"integration:{name}", title, caps[0], state, list(r.reasons), name, blocks)

    def _ports(self, pl: str) -> CheckView:
        line = self.m.cfg.product_lines[pl]
        free = self.m.free_node_ports(pl)
        usable = [p for p in free if self.mapped_node_ports is None or p in self.mapped_node_ports]
        mapped_note = []
        if self.mapped_node_ports is not None:
            mapped = [p for p in line.node_ports if p in self.mapped_node_ports]
            if len(mapped) < len(line.node_ports):
                mapped_note = [
                    f"the cluster maps {len(mapped)} of {len(line.node_ports)} configured app ports; "
                    "`make reset-cluster` maps them all (running apps must be re-delivered)"
                ]
        if not usable:
            return _check(
                "ports",
                "Free app port",
                "deploy",
                "failed",
                ["every app port is in use: archive an order you no longer need", *mapped_note],
                blocks=["order"],
            )
        state = "degraded" if mapped_note else "ready"
        return _check(
            "ports",
            "Free app port",
            "deploy",
            state,
            [f"{len(usable)} free app port(s)", *mapped_note],
            blocks=["order"],
        )

    def _scanner(self, scan_command: str | None) -> CheckView:
        if scan_command:
            return _check("scanner", "Image scanner", "scan", "ready", ["scan_command configured"], blocks=[])
        return _check(
            "scanner", "Image scanner", "scan", "degraded", ["no scan_command: images are not scanned"], blocks=[]
        )

    def _workflow(self, pl: str) -> CheckView:
        from agent_factory.workflow import workflow_warnings

        wf = self.m.workflows[pl]
        doc = wf.get(wf.active_version())
        notes = [w for w in workflow_warnings(doc) if w.startswith("no readiness station")]
        if notes:
            return _check("workflow", "Workflow", "workflow", "degraded", notes, blocks=[])
        return _check(
            "workflow",
            "Workflow",
            "workflow",
            "ready",
            [f"{pl} v{wf.active_version()} holds apps to Level 3"],
            blocks=[],
        )
