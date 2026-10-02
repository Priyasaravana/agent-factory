"""The workflow controller: a deterministic state machine that moves a run through
the configured stations. LLMs work *inside* stations; they never choose the
route. Every transition is persisted, so any run can be resumed or audited.
"""

from __future__ import annotations

import asyncio
import re
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agent_factory.agents.runner import AgentRunner
from agent_factory.config import FactoryConfig
from agent_factory.engine.stations import STATIONS, StationContext, StationResult
from agent_factory.engine.workflows import WorkflowRegistry
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import Executor
from agent_factory.models import (
    RESUMABLE,
    CreateOrderInput,
    EventKind,
    Order,
    Run,
    RunStatus,
    StationOutcome,
)
from agent_factory.providers import Providers
from agent_factory.secret_refs import SecretResolver
from agent_factory.settings import Settings
from agent_factory.state.base import StateStore

if TYPE_CHECKING:
    from agent_factory.sandbox import SandboxManager


class FactoryError(Exception):
    """A request the factory refuses (surfaced as HTTP 409/404)."""


# a run in one of these states has stopped: feedback may start the next iteration
FEEDBACK_OPEN = {RunStatus.awaiting_feedback, RunStatus.held, RunStatus.failed, RunStatus.cancelled}


def _now() -> datetime:
    return datetime.now(UTC)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "app")[:40].strip("-")


class RunManager:
    def __init__(
        self,
        cfg: FactoryConfig,
        settings: Settings,
        store: StateStore,
        ws: Workspace,
        ex: Executor,
        agents: AgentRunner,
        workflows: WorkflowRegistry,
        providers: Providers | None = None,
        secrets: SecretResolver | None = None,
        sandbox: SandboxManager | None = None,
    ) -> None:
        self.cfg, self.settings, self.store = cfg, settings, store
        self.workflows = workflows
        self.providers = providers or Providers(cfg)
        self.secrets = secrets or SecretResolver(settings)
        self.ws, self.ex, self.agents = ws, ex, agents
        self.sandbox = sandbox  # runs agent-written code (verify) and agent sessions
        from agent_factory.engine.preflight import Preflight

        self.preflight = Preflight(self)  # readiness checks + order gate (ADR-0015)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        from agent_factory.hostclock import SuspendWatcher

        self.host_watcher = SuspendWatcher(self.host_suspended)
        self._sem = asyncio.Semaphore(cfg.factory.max_concurrent_runs)

    # ------------------------------------------------------------ queries --
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def is_active(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return bool(t and not t.done())

    def host_suspended(self, start: datetime, end: datetime) -> None:
        """The factory host slept (hostclock.py): record it, and tell every active run."""
        from agent_factory.hostclock import human

        self.store.add_host_pause(start, end)
        secs = (end - start).total_seconds()
        for run_id, task in list(self._tasks.items()):
            if task.done() or not self.store.get_run(run_id):
                continue  # finished, or not a run (evaluation and seal tasks)
            self.store.add_event(
                run_id,
                EventKind.decision,
                f"factory host was asleep for {human(secs)} (the computer slept or Docker was paused): "
                "nothing ran, and this time is not counted as agent time",
                data={"host_suspended": {"start": start.isoformat(), "end": end.isoformat(), "seconds": secs}},
            )

    async def shutdown(self) -> None:
        """Stop in-flight runs; they are marked interrupted on next startup."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------ orders ---
    def create_order(self, data: CreateOrderInput) -> Order:
        line = self.cfg.product_lines.get(data.product_line)
        if not line:
            raise FactoryError(f"unknown product line '{data.product_line}'")
        orders = self.store.list_orders()
        taken_slugs = {o.product_slug for o in orders}
        slug, n = slugify(data.title), 2
        base = slug
        while slug in taken_slugs:
            slug, n = f"{base}-{n}", n + 1
        node_port = host_port = None
        if self.uses_node_ports(data.product_line):
            free = self.usable_node_ports(data.product_line)
            if not free:
                raise FactoryError("no free app port: archive an order you no longer need (see config node_ports)")
            node_port = free[0]
            host_port = self.settings.app_host_port_base + line.node_ports.index(node_port)
        order = Order(
            id=uuid.uuid4().hex[:12],
            title=data.title,
            requirements=data.requirements,
            product_line=data.product_line,
            product_slug=slug,
            requirements_format=data.requirements_format,
            created_at=_now(),
            node_port=node_port,
            host_port=host_port,
        )
        return self.store.create_order(order)

    def free_node_ports(self, product_line: str) -> list[int]:
        """App ports not held by a live (non-archived) order of this product line."""
        line = self.cfg.product_lines[product_line]
        used = {o.node_port for o in self.store.list_orders() if not o.archived_at}
        return [p for p in line.node_ports if p not in used]

    def uses_node_ports(self, product_line: str) -> bool:
        """Local kind needs one node port per app; an ingress-based target does not (ADR-0026)."""
        deploy = self.providers.for_product_line(product_line).deploy
        return bool(getattr(deploy, "uses_node_ports", True))

    def usable_node_ports(self, product_line: str) -> list[int]:
        """Free app ports that the local cluster actually maps (older clusters map fewer)."""
        mapped = self.preflight.mapped_node_ports
        return [p for p in self.free_node_ports(product_line) if mapped is None or p in mapped]

    async def archive(self, order_id: str, by: str) -> Order:
        """Remove the order's app from its deploy target and free its port. The
        product repo, runs, events and feedback are kept; no new iterations."""
        order = self.store.get_order(order_id)
        if not order:
            raise FactoryError("order not found")
        if order.archived_at:
            return order
        runs = self.store.list_runs(order.id)
        # a stopped run may still be finishing post-run work (retro, evidence seal);
        # that never blocks the next iteration
        if any(self.is_active(r.id) and r.status not in FEEDBACK_OPEN for r in runs):
            raise FactoryError("a run for this order is in progress: cancel it first")
        latest = self.store.get_run(order.latest_run_id) if order.latest_run_id else (runs[0] if runs else None)
        if latest is not None:
            ctx = StationContext(
                self.cfg,
                self.settings,
                self.store,
                self.ws,
                self.ex,
                self.agents,
                order,
                latest,
                self.ws.run_dir(latest.id),
                "archive",
                self.workflows[latest.workflow_id or order.product_line].get(latest.workflow_version),
            )
            ctx.providers = self.providers.for_product_line(order.product_line)
            res = await ctx.providers.deploy.undeploy(ctx)
            if not res.ok:
                raise FactoryError(f"archive stopped: {res.summary}\n{res.detail}")
            if self.sandbox is not None:
                for r in runs:
                    await self.sandbox.remove_run(r.id)
            self.store.add_event(
                latest.id,
                EventKind.decision,
                f"order archived by {by}: {res.summary}"
                + (f"; app port {order.host_port} freed" if order.host_port else ""),
                station="archive",
                data={"archived_by": by, "app_url": order.app_url},
            )
        order.archived_at, order.archived_by, order.app_url = _now(), by, None
        self.store.save_order(order)
        return order

    def start_run(self, order: Order, change_request: str | None = None, version: int | None = None) -> Run:
        if order.archived_at:
            raise FactoryError("this order is archived: place a new order instead")
        runs = self.store.list_runs(order.id)
        # a stopped run may still be finishing post-run work (retro, evidence seal);
        # that never blocks the next iteration
        if any(self.is_active(r.id) and r.status not in FEEDBACK_OPEN for r in runs):
            raise FactoryError("a run for this order is already in progress")
        now = _now()
        run = Run(
            id=uuid.uuid4().hex[:12],
            order_id=order.id,
            iteration=len(runs) + 1,
            status=RunStatus.queued,
            workflow_id=order.product_line,
            workflow_version=(version := version or self.workflows[order.product_line].active_version()),
            current_station=self.workflows[order.product_line].get(version).forward_stations()[0].id,
            change_request=change_request,
            created_at=now,
            updated_at=now,
        )
        self.store.create_run(run)
        order.latest_run_id, order.latest_status = run.id, run.status
        self.store.save_order(order)
        self.store.add_event(run.id, EventKind.status, f"run queued (iteration {run.iteration})")
        env = self.providers.for_product_line(order.product_line)
        self.store.add_event(
            run.id,
            EventKind.decision,
            f"delivery environment: {env.environment} — "
            + ", ".join(f"{cap}: {who}" for cap, who in env.describe().items()),
            data={"environment": env.environment, **env.describe()},
        )
        self._schedule(run.id)
        return run

    # ----------------------------------------------------- human actions ---
    def answer(self, run_id: str, answers: list[str], by: str = "human") -> Run:
        run = self._get(run_id)
        if run.status != RunStatus.needs_input:
            raise FactoryError("run is not waiting for answers")
        run.answers = answers
        run.status = RunStatus.queued
        self.store.save_run(run)
        self.store.add_event(run.id, EventKind.decision, f"{by} answered intake questions", data={"answers": answers})
        self._schedule(run.id)
        return run

    def resume(self, run_id: str) -> Run:
        run = self._get(run_id)
        if run.status not in RESUMABLE or self.is_active(run_id):
            raise FactoryError(f"run in status '{run.status}' cannot be resumed")
        run.attempts = {}  # a human resume authorises a fresh budget
        run.loops = 0
        run.status = RunStatus.queued
        self.store.save_run(run)
        self.store.add_event(run.id, EventKind.decision, "run resumed by human", station=run.current_station)
        self._schedule(run.id)
        return run

    # ------------------------------------------------------- spec review ---
    def _awaiting_spec(self, run_id: str) -> Run:
        run = self._get(run_id)
        if run.status != RunStatus.awaiting_approval:
            raise FactoryError(f"run is '{run.status}', not waiting for spec review")
        return run

    def approve_spec(self, run_id: str, by: str) -> Run:
        run = self._awaiting_spec(run_id)
        run.spec_approved_by = by
        run.status = RunStatus.queued
        run.summary = None
        self.store.save_run(run)
        self._sync_order(run)
        self.store.add_event(run.id, EventKind.decision, f"spec approved by {by}", data={"approved_by": by})
        self._schedule(run.id)
        return run

    def request_spec_changes(self, run_id: str, by: str, comment: str) -> Run:
        """Back to intake with the reviewer's notes; design follows, then the gate again."""
        run = self._awaiting_spec(run_id)
        order = self.store.get_order(run.order_id)
        flow = self.workflows[run.workflow_id or (order.product_line if order else "")].get(run.workflow_version)
        first = flow.forward_stations()[0].id
        run.review_notes.append(f"{comment} (by {by})")
        for sid in [s.id for s in flow.forward_stations()]:
            run.attempts.pop(sid, None)
            if flow.station(sid).resolved_handler() == "design":
                break
        run.current_station = first
        run.status = RunStatus.queued
        run.summary = None
        self.store.save_run(run)
        self._sync_order(run)
        self.store.add_event(
            run.id, EventKind.decision, f"spec changes requested by {by}: {comment}", data={"requested_by": by}
        )
        self._schedule(run.id)
        return run

    async def edit_spec(self, run_id: str, by: str, product: str | None, technical: str | None) -> Run:
        run = self._awaiting_spec(run_id)
        wt = self.ws.run_dir(run.id)
        changed = []
        for rel, text in (("docs/spec.md", product), ("docs/design.md", technical)):
            if text is not None and text != ((wt / rel).read_text() if (wt / rel).exists() else None):
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                (wt / rel).write_text(text)
                changed.append(rel)
        if changed:
            await self.ws.commit_all(wt, f"docs: spec edited in review by {by}")
            self.store.add_event(
                run.id, EventKind.decision, f"spec edited by {by}: {', '.join(changed)}", data={"files": changed}
            )
        return run

    def cancel(self, run_id: str) -> Run:
        run = self._get(run_id)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        if self.sandbox is not None:
            asyncio.get_running_loop().create_task(self.sandbox.remove_run(run_id))
        run.status = RunStatus.cancelled
        self.store.save_run(run)
        self._sync_order(run)
        self.store.add_event(run.id, EventKind.status, "run cancelled by human")
        self._tasks[run_id] = asyncio.get_running_loop().create_task(
            self._seal_after_cancel(run_id, task), name=f"seal-{run_id}"
        )
        return run

    def feedback(self, order_id: str, text: str) -> Run:
        order = self.store.get_order(order_id)
        if not order:
            raise FactoryError("order not found")
        latest = self.store.get_run(order.latest_run_id) if order.latest_run_id else None
        if latest and latest.status not in FEEDBACK_OPEN:
            raise FactoryError(f"latest run is '{latest.status}'; feedback opens after delivery")
        self.store.add_feedback(order_id, latest.id if latest else None, text)
        run = self.start_run(order, change_request=text)
        self.store.add_event(run.id, EventKind.feedback, text)
        return run

    def recover_on_startup(self) -> list[str]:
        """Builder `factory-recover` rule: never silently restart; mark and let a
        human resume, unless the recover policy is explicitly auto."""
        touched = []
        for run in self.store.runs_with_status({RunStatus.running, RunStatus.queued}):
            run.status = RunStatus.interrupted
            self.store.save_run(run)
            self._sync_order(run)
            self.store.add_event(
                run.id, EventKind.status, "engine restarted: run interrupted", station=run.current_station
            )
            touched.append(run.id)
            if self.cfg.policies.recover.mode == "auto":
                self.resume(run.id)
        for run in self.store.runs_with_status({RunStatus.paused_limits}):
            self._wake_later(run)
        return touched

    # -------------------------------------------------------- internals ----
    def _get(self, run_id: str) -> Run:
        run = self.store.get_run(run_id)
        if not run:
            raise FactoryError("run not found")
        return run

    def _schedule(self, run_id: str) -> None:
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id), name=f"run-{run_id}")

    def _sync_order(self, run: Run, order: Order | None = None) -> None:
        order = order or self.store.get_order(run.order_id)
        if order and order.latest_run_id == run.id:
            order.latest_status = run.status
            self.store.save_order(order)

    def _wake_later(self, run: Run) -> None:
        async def wake() -> None:
            delay = max(5.0, ((run.resume_at or _now()) - _now()).total_seconds())
            await asyncio.sleep(delay)
            current = self.store.get_run(run.id)
            if current and current.status == RunStatus.paused_limits:
                current.status = RunStatus.queued
                self.store.save_run(current)
                self.store.add_event(run.id, EventKind.status, "usage window reset: resuming")
                self._schedule(run.id)

        asyncio.create_task(wake())

    async def _execute(self, run_id: str) -> None:
        cancelled = False
        async with self._sem:
            run = self._get(run_id)
            order = self.store.get_order(run.order_id)
            assert order is not None
            try:
                await self._drive(run, order)
            except asyncio.CancelledError:
                cancelled = True  # cancel() already persisted the cancelled status
                raise
            except Exception as exc:  # noqa: BLE001 - engine faults become a visible failure
                run.status = RunStatus.failed
                run.summary = f"engine error: {exc}"
                self.store.add_event(
                    run.id, EventKind.status, run.summary, data={"traceback": traceback.format_exc()[-4000:]}
                )
            finally:
                if not cancelled:
                    self.store.save_run(run)
                    self.store.save_order(order)
                    self._sync_order(run, order)
        # after the slot is released: learning never delays or fails a delivery (ADR-0021)
        if not cancelled and run.status == RunStatus.awaiting_feedback:
            await self._retro(run, order)
        if not cancelled:
            await self._seal(run, order)
            self._after_stop(run, order)

    def _after_stop(self, run: Run, order: Order) -> None:
        """Evaluation runs report to their evaluation (ADR-0025); never fails the run."""
        if not order.eval_run_id:
            return
        from agent_factory.evals import on_run_stopped

        try:
            on_run_stopped(self, self._get(run.id), self.store.get_order(order.id) or order)
        except Exception as exc:  # noqa: BLE001
            self.store.add_event(run.id, EventKind.log, f"evaluation update failed: {type(exc).__name__}: {exc}")

    async def _seal(self, run: Run, order: Order) -> None:
        """Seal the run's evidence at every stop (ADR-0023); never fails the run."""
        from agent_factory.evidence import seal

        try:
            await seal(self, run, order)
        except Exception as exc:  # noqa: BLE001 - a failed seal is visible, never raised into the run
            self.store.add_event(run.id, EventKind.log, f"evidence seal failed: {type(exc).__name__}: {exc}")

    async def _seal_after_cancel(self, run_id: str, task: asyncio.Task[None] | None) -> None:
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)  # let the station stop first
        run = self.store.get_run(run_id)
        order = self.store.get_order(run.order_id) if run else None
        if run and order:
            await self._seal(run, order)
            self._after_stop(run, order)

    async def _retro(self, run: Run, order: Order) -> None:
        from agent_factory.retro import run_retro

        cost = run.cost_usd
        try:
            await run_retro(self, run, order)
        except Exception as exc:  # noqa: BLE001 - a failed retro is logged, never raised into the run
            self.store.add_event(run.id, EventKind.log, f"retro failed: {type(exc).__name__}: {exc}")
        if run.cost_usd != cost:
            self.store.save_run(run)  # the retro's model spend belongs to this run

    async def _drive(self, run: Run, order: Order) -> None:
        run.status = RunStatus.running
        self.store.save_run(run)
        self._sync_order(run, order)
        product = self.cfg.product_lines[order.product_line]
        owner = str(getattr(self.cfg.policies.publish, "owner", "") or "")
        await self.ws.ensure_product_repo(order.product_slug, product.template, owner)
        # pinned: later workflow edits never affect this run
        flow = self.workflows[run.workflow_id or order.product_line].get(run.workflow_version)
        worktree = await self.ws.create_worktree(order.product_slug, run.id)
        deadline = run.created_at + timedelta(minutes=self.cfg.budgets.run_wall_clock_minutes)

        while run.current_station:
            sid = run.current_station
            station = flow.station(sid)
            if _now() > deadline and run.status != RunStatus.needs_input:
                return self._hold(run, sid, "wall-clock budget exhausted")
            run.attempts[sid] = run.attempts.get(sid, 0) + 1
            if run.attempts[sid] > self.cfg.budgets.max_attempts_per_station:
                return self._hold(run, sid, f"station '{sid}' exceeded its attempt budget")
            self.store.save_run(run)
            self.store.add_event(
                run.id, EventKind.station_started, f"{sid} started (attempt {run.attempts[sid]})", station=sid
            )

            ctx = StationContext(
                self.cfg, self.settings, self.store, self.ws, self.ex, self.agents, order, run, worktree, sid, flow
            )
            ctx.providers = self.providers.for_product_line(order.product_line)
            if self.sandbox is not None:
                ctx.untrusted_ex = self.sandbox.executor(run.id, sid)
            ctx.secrets = self.secrets
            if self.workflows.library:
                ctx.skill_pins = self.workflows.library.effective_pins(flow)
                ctx.imported_plugin = self.workflows.library.materialise(ctx.skill_pins)
            try:
                result = await STATIONS[station.resolved_handler()](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                result = StationResult(StationOutcome.failed, f"station crashed: {exc}", traceback.format_exc()[-3000:])
            self.store.add_event(
                run.id,
                EventKind.station_finished,
                f"{sid}: {result.outcome} — {result.summary}",
                station=sid,
                data={"outcome": result.outcome},
            )

            if result.outcome == StationOutcome.passed:
                if sid in flow.repair_targets() or station.only_on_fail:
                    run.last_failure = None  # the station that consumed the evidence succeeded
                nxt = station.next or flow.next_forward(sid)
                run.current_station = nxt
                if nxt is None:
                    run.status = RunStatus.awaiting_feedback
                    run.summary = result.summary
                    self.store.add_event(run.id, EventKind.status, "delivered — feedback gate open", station=sid)
                elif (
                    station.resolved_handler() == "design"
                    and flow.spec_gate_applies(run.iteration)
                    and not run.spec_approved_by
                ):
                    run.status = RunStatus.awaiting_approval
                    run.summary = "specification ready for your review"
                    self.store.save_run(run)
                    self.store.add_event(
                        run.id,
                        EventKind.status,
                        "spec review gate: waiting for a person to approve the spec before build",
                        station=sid,
                    )
                    return None
                self.store.save_run(run)
                continue

            if result.outcome == StationOutcome.failed:
                run.loops += 1
                target = station.on_fail
                if not target:
                    return self._hold(run, sid, f"{result.summary} (no repair route)", result.failure_evidence)
                if run.loops > self.cfg.budgets.max_loops_per_run:
                    return self._hold(run, sid, "fix-loop budget exhausted", result.failure_evidence)
                run.last_failure = result.failure_evidence or result.summary
                run.current_station = target
                self.store.add_event(
                    run.id,
                    EventKind.decision,
                    f"routing failure from {sid} to {target}",
                    station=sid,
                    data={"routed": {"from": sid, "to": target, "evidence": run.last_failure[-3000:]}},
                )
                self.store.save_run(run)
                continue

            if result.outcome == StationOutcome.needs_input:
                run.status = RunStatus.needs_input
                run.questions = result.questions
                run.answers = []
                run.attempts[sid] -= 1
                run.summary = "waiting for your answers"
                return None

            if result.outcome == StationOutcome.paused_limits:
                run.attempts[sid] -= 1
                run.status = RunStatus.paused_limits
                run.resume_at = (
                    datetime.fromtimestamp(result.resets_at, UTC)
                    if result.resets_at
                    else _now() + timedelta(minutes=30)
                )
                run.summary = f"paused for usage limits until {run.resume_at:%H:%M}"
                self.store.add_event(run.id, EventKind.status, run.summary, station=sid)
                self.store.save_run(run)
                self._wake_later(run)
                return None

            return self._hold(run, sid, result.summary, result.failure_evidence)
        return None

    def _hold(self, run: Run, sid: str, why: str, evidence: str | None = None) -> None:
        run.status = RunStatus.held
        run.summary = f"held at {sid}: {why}"
        run.last_failure = evidence or run.last_failure
        self.store.add_event(
            run.id, EventKind.status, run.summary, station=sid, data={"evidence": (evidence or "")[-3000:]}
        )
        return None
