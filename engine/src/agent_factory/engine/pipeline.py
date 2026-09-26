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
from agent_factory.settings import Settings
from agent_factory.state.base import StateStore


class FactoryError(Exception):
    """A request the factory refuses (surfaced as HTTP 409/404)."""


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
    ) -> None:
        self.cfg, self.settings, self.store = cfg, settings, store
        self.workflows = workflows
        self.ws, self.ex, self.agents = ws, ex, agents
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sem = asyncio.Semaphore(cfg.factory.max_concurrent_runs)

    # ------------------------------------------------------------ queries --
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def is_active(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return bool(t and not t.done())

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
        used = {o.node_port for o in orders}
        free = [p for p in line.node_ports if p not in used]
        if not free:
            raise FactoryError("no free app ports on this product line (see config node_ports)")
        node_port = free[0]
        order = Order(
            id=uuid.uuid4().hex[:12],
            title=data.title,
            requirements=data.requirements,
            product_line=data.product_line,
            product_slug=slug,
            created_at=_now(),
            node_port=node_port,
            host_port=self.settings.app_host_port_base + line.node_ports.index(node_port),
        )
        return self.store.create_order(order)

    def start_run(self, order: Order, change_request: str | None = None) -> Run:
        runs = self.store.list_runs(order.id)
        if any(self.is_active(r.id) for r in runs):
            raise FactoryError("a run for this order is already in progress")
        now = _now()
        run = Run(
            id=uuid.uuid4().hex[:12],
            order_id=order.id,
            iteration=len(runs) + 1,
            status=RunStatus.queued,
            workflow_id=order.product_line,
            workflow_version=(version := self.workflows[order.product_line].active_version()),
            current_station=self.workflows[order.product_line].get(version).forward_stations()[0].id,
            change_request=change_request,
            created_at=now,
            updated_at=now,
        )
        self.store.create_run(run)
        order.latest_run_id, order.latest_status = run.id, run.status
        self.store.save_order(order)
        self.store.add_event(run.id, EventKind.status, f"run queued (iteration {run.iteration})")
        self._schedule(run.id)
        return run

    # ----------------------------------------------------- human actions ---
    def answer(self, run_id: str, answers: list[str]) -> Run:
        run = self._get(run_id)
        if run.status != RunStatus.needs_input:
            raise FactoryError("run is not waiting for answers")
        run.answers = answers
        run.status = RunStatus.queued
        self.store.save_run(run)
        self.store.add_event(run.id, EventKind.decision, "human answered intake questions", data={"answers": answers})
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

    def cancel(self, run_id: str) -> Run:
        run = self._get(run_id)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        run.status = RunStatus.cancelled
        self.store.save_run(run)
        self._sync_order(run)
        self.store.add_event(run.id, EventKind.status, "run cancelled by human")
        return run

    def feedback(self, order_id: str, text: str) -> Run:
        order = self.store.get_order(order_id)
        if not order:
            raise FactoryError("order not found")
        latest = self.store.get_run(order.latest_run_id) if order.latest_run_id else None
        if latest and latest.status not in {
            RunStatus.awaiting_feedback,
            RunStatus.held,
            RunStatus.failed,
            RunStatus.cancelled,
        }:
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
        async with self._sem:
            run = self._get(run_id)
            order = self.store.get_order(run.order_id)
            assert order is not None
            cancelled = False
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

    async def _drive(self, run: Run, order: Order) -> None:
        run.status = RunStatus.running
        self.store.save_run(run)
        self._sync_order(run, order)
        product = self.cfg.product_lines[order.product_line]
        await self.ws.ensure_product_repo(order.product_slug, product.template)
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
            if flow.skill_pins and self.workflows.library:
                ctx.imported_plugin = self.workflows.library.materialise(flow.skill_pins)
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
                self.store.add_event(run.id, EventKind.decision, f"routing failure from {sid} to {target}", station=sid)
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
