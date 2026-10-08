"""The workflow controller: a deterministic state machine that moves a change through
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
    AVAILABLE_KINDS,
    AVAILABLE_TARGETS,
    ITERATION_KINDS,
    RESUMABLE,
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    CreateProductInput,
    EventKind,
    Product,
    RiskApproval,
    RiskHold,
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


class InvalidRequestError(FactoryError):
    """A request that can never succeed as sent, e.g. a kind of change or target that is
    not available yet (ADR-0030, HTTP 422)."""


class RefusedError(FactoryError):
    """Work the factory never builds: an acceptable-use rule matched (ADR-0027, HTTP 422)."""


# a change in one of these states has stopped: feedback may start the next iteration
FEEDBACK_OPEN = {ChangeStatus.awaiting_feedback, ChangeStatus.held, ChangeStatus.failed, ChangeStatus.cancelled}


def _now() -> datetime:
    return datetime.now(UTC)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "app")[:40].strip("-")


class ChangeManager:
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
        self._sem = asyncio.Semaphore(cfg.factory.max_concurrent_changes)

    # ------------------------------------------------------------ queries --
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def is_active(self, change_id: str) -> bool:
        t = self._tasks.get(change_id)
        return bool(t and not t.done())

    def host_suspended(self, start: datetime, end: datetime) -> None:
        """The factory host slept (hostclock.py): record it, and tell every active run."""
        from agent_factory.hostclock import human

        self.store.add_host_pause(start, end)
        secs = (end - start).total_seconds()
        for change_id, task in list(self._tasks.items()):
            if task.done() or not self.store.get_change(change_id):
                continue  # finished, or not a change (evaluation and seal tasks)
            self.store.add_event(
                change_id,
                EventKind.decision,
                f"factory host was asleep for {human(secs)} (the computer slept or Docker was paused): "
                "nothing ran, and this time is not counted as agent time",
                data={"host_suspended": {"start": start.isoformat(), "end": end.isoformat(), "seconds": secs}},
            )

    async def shutdown(self) -> None:
        """Stop in-flight changes; they are marked interrupted on next startup."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------ products ---
    def screen(self, text: str, what: str) -> None:
        """Refuse abuse apps before anything is built (ADR-0027). Every refusal is
        appended to `audit/refusals.jsonl` with the matched rule; nothing else is stored."""
        import json

        from agent_factory import risk
        from agent_factory.identity import current_identity

        extra = [risk.AupRule(r.id, r.title, r.pattern, r.unless) for r in self.cfg.change_risk.acceptable_use]
        refusal = risk.screen_request(text, extra)
        if refusal is None:
            return
        audit = self.ws.data_dir / "audit"
        audit.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": _now().isoformat(),
            "by": current_identity().user,
            "what": what,
            "rule": refusal.rule,
            "title": refusal.title,
            "matched": refusal.matched,
        }
        with (audit / "refusals.jsonl").open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        raise RefusedError(refusal.message())

    def create_product(self, data: CreateProductInput) -> Product:
        line = self.cfg.blueprints.get(data.blueprint)
        if not line:
            raise FactoryError(f"unknown blueprint '{data.blueprint}'")
        if data.target not in AVAILABLE_TARGETS:
            raise InvalidRequestError(
                f"target '{data.target}' is not available yet: only new products can be built today"
            )
        self.screen(f"{data.title}\n{data.requirements}", "product")
        products = self.store.list_products()
        taken_slugs = {o.slug for o in products}
        slug, n = slugify(data.title), 2
        base = slug
        while slug in taken_slugs:
            slug, n = f"{base}-{n}", n + 1
        node_port = host_port = None
        if self.uses_node_ports(data.blueprint):
            free = self.usable_node_ports(data.blueprint)
            if not free:
                raise FactoryError("no free app port: archive a product you no longer need (see config node_ports)")
            node_port = free[0]
            host_port = self.settings.app_host_port_base + line.node_ports.index(node_port)
        product = Product(
            id=uuid.uuid4().hex[:12],
            title=data.title,
            requirements=data.requirements,
            blueprint=data.blueprint,
            slug=slug,
            requirements_format=data.requirements_format,
            target=data.target,
            created_at=_now(),
            node_port=node_port,
            host_port=host_port,
        )
        return self.store.create_product(product)

    def free_node_ports(self, blueprint: str) -> list[int]:
        """App ports not held by a live (non-archived) order of this blueprint."""
        line = self.cfg.blueprints[blueprint]
        used = {o.node_port for o in self.store.list_products() if not o.archived_at}
        return [p for p in line.node_ports if p not in used]

    def uses_node_ports(self, blueprint: str) -> bool:
        """Local kind needs one node port per app; an ingress-based target does not (ADR-0026)."""
        deploy = self.providers.for_blueprint(blueprint).deploy
        return bool(getattr(deploy, "uses_node_ports", True))

    def usable_node_ports(self, blueprint: str) -> list[int]:
        """Free app ports that the local cluster actually maps (older clusters map fewer)."""
        mapped = self.preflight.mapped_node_ports
        return [p for p in self.free_node_ports(blueprint) if mapped is None or p in mapped]

    async def archive(self, product_id: str, by: str) -> Product:
        """Remove the product's app from its deploy target and free its port. The
        product repo, runs, events and feedback are kept; no new iterations."""
        product = self.store.get_product(product_id)
        if not product:
            raise FactoryError("product not found")
        if product.archived_at:
            return product
        changes = self.store.list_changes(product.id)
        # a stopped run may still be finishing post-run work (retro, evidence seal);
        # that never blocks the next iteration
        if any(self.is_active(r.id) and r.status not in FEEDBACK_OPEN for r in changes):
            raise FactoryError("a change to this product is in progress: cancel it first")
        latest = (
            self.store.get_change(product.latest_change_id)
            if product.latest_change_id
            else (changes[0] if changes else None)
        )
        if latest is not None:
            ctx = StationContext(
                self.cfg,
                self.settings,
                self.store,
                self.ws,
                self.ex,
                self.agents,
                product,
                latest,
                self.ws.change_dir(latest.id),
                "archive",
                self.workflows[latest.workflow_id or product.blueprint].get(latest.workflow_version),
            )
            ctx.providers = self.providers.for_blueprint(product.blueprint)
            res = await ctx.providers.deploy.undeploy(ctx)
            if not res.ok:
                raise FactoryError(f"archive stopped: {res.summary}\n{res.detail}")
            if self.sandbox is not None:
                for r in changes:
                    await self.sandbox.remove_change(r.id)
            self.store.add_event(
                latest.id,
                EventKind.decision,
                f"product archived by {by}: {res.summary}"
                + (f"; app port {product.host_port} freed" if product.host_port else ""),
                station="archive",
                data={"archived_by": by, "app_url": product.app_url},
            )
        product.archived_at, product.archived_by, product.app_url = _now(), by, None
        self.store.save_product(product)
        return product

    def start_change(
        self,
        product: Product,
        change_request: str | None = None,
        version: int | None = None,
        *,
        kind: ChangeKind | None = None,
        source: ChangeSource = ChangeSource.ui,
        requested_by: str | None = None,
    ) -> Change:
        """Start a change to a product (ADR-0030). The kind defaults to `new` for the
        first change and `feature` after it; the requester to the signed-in user."""
        from agent_factory.identity import current_identity

        if product.archived_at:
            raise FactoryError("this product is archived: create a new product instead")
        changes = self.store.list_changes(product.id)
        # a stopped run may still be finishing post-run work (retro, evidence seal);
        # that never blocks the next iteration
        if any(self.is_active(r.id) and r.status not in FEEDBACK_OPEN for r in changes):
            raise FactoryError("a change to this product is already in progress")
        kind = kind or (ChangeKind.feature if changes else ChangeKind.new)
        if kind not in AVAILABLE_KINDS:
            raise InvalidRequestError(f"'{kind}' changes are not available yet")
        if (kind == ChangeKind.new) != (not changes):
            raise InvalidRequestError(
                "a product's first change builds it ('new')"
                if not changes
                else "this product is built: ask for a feature, bug fix or upkeep"
            )
        now = _now()
        change = Change(
            id=uuid.uuid4().hex[:12],
            product_id=product.id,
            iteration=len(changes) + 1,
            status=ChangeStatus.queued,
            workflow_id=product.blueprint,
            workflow_version=(version := version or self.workflows[product.blueprint].active_version()),
            current_station=self.workflows[product.blueprint].get(version).forward_stations()[0].id,
            change_request=change_request,
            kind=kind,
            source=source,
            requested_by=requested_by or current_identity().user,
            created_at=now,
            updated_at=now,
        )
        self.store.create_change(change)
        product.latest_change_id, product.latest_status = change.id, change.status
        self.store.save_product(product)
        self.store.add_event(change.id, EventKind.status, f"change queued (iteration {change.iteration})")
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"work item: {change.kind} from {change.source}, asked by {change.requested_by}",
            data={"work_item": {"kind": change.kind, "source": change.source, "requested_by": change.requested_by}},
        )
        env = self.providers.for_blueprint(product.blueprint)
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"delivery environment: {env.environment} — "
            + ", ".join(f"{cap}: {who}" for cap, who in env.describe().items()),
            data={"environment": env.environment, **env.describe()},
        )
        self._schedule(change.id)
        return change

    # ----------------------------------------------------- human actions ---
    def answer(self, change_id: str, answers: list[str], by: str = "human") -> Change:
        change = self._get(change_id)
        if change.status != ChangeStatus.needs_input:
            raise FactoryError("change is not waiting for answers")
        change.answers = answers
        change.status = ChangeStatus.queued
        self.store.save_change(change)
        self.store.add_event(
            change.id, EventKind.decision, f"{by} answered intake questions", data={"answers": answers}
        )
        self._schedule(change.id)
        return change

    def resume(self, change_id: str) -> Change:
        change = self._get(change_id)
        if change.status not in RESUMABLE or self.is_active(change_id):
            raise FactoryError(f"change in status '{change.status}' cannot be resumed")
        change.attempts = {}  # a human resume authorises a fresh budget
        change.loops = 0
        change.status = ChangeStatus.queued
        self.store.save_change(change)
        self.store.add_event(change.id, EventKind.decision, "change resumed by human", station=change.current_station)
        self._schedule(change.id)
        return change

    # ------------------------------------------------------- spec review ---
    def _awaiting_spec(self, change_id: str) -> Change:
        change = self._get(change_id)
        if change.status != ChangeStatus.awaiting_approval:
            raise FactoryError(f"change is '{change.status}', not waiting for spec review")
        return change

    def approve_spec(self, change_id: str, by: str) -> Change:
        change = self._awaiting_spec(change_id)
        change.spec_approved_by = by
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(change.id, EventKind.decision, f"spec approved by {by}", data={"approved_by": by})
        self._schedule(change.id)
        return change

    def request_spec_changes(self, change_id: str, by: str, comment: str) -> Change:
        """Back to intake with the reviewer's notes; design follows, then the gate again."""
        change = self._awaiting_spec(change_id)
        product = self.store.get_product(change.product_id)
        flow = self.workflows[change.workflow_id or (product.blueprint if product else "")].get(change.workflow_version)
        first = flow.forward_stations()[0].id
        change.review_notes.append(f"{comment} (by {by})")
        for sid in [s.id for s in flow.forward_stations()]:
            change.attempts.pop(sid, None)
            if flow.station(sid).resolved_handler() == "design":
                break
        change.current_station = first
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(
            change.id, EventKind.decision, f"spec changes requested by {by}: {comment}", data={"requested_by": by}
        )
        self._schedule(change.id)
        return change

    async def edit_spec(self, change_id: str, by: str, product: str | None, technical: str | None) -> Change:
        change = self._awaiting_spec(change_id)
        wt = self.ws.change_dir(change.id)
        changed = []
        for rel, text in (("docs/spec.md", product), ("docs/design.md", technical)):
            if text is not None and text != ((wt / rel).read_text() if (wt / rel).exists() else None):
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                (wt / rel).write_text(text)
                changed.append(rel)
        if changed:
            await self.ws.commit_all(wt, f"docs: spec edited in review by {by}")
            self.store.add_event(
                change.id, EventKind.decision, f"spec edited by {by}: {', '.join(changed)}", data={"files": changed}
            )
        return change

    # ------------------------------------------------------- change risk ---
    def _awaiting_risk(self, change_id: str) -> tuple[Change, RiskHold]:
        change = self._get(change_id)
        if change.status != ChangeStatus.awaiting_risk_approval or change.risk_hold is None:
            raise FactoryError(f"change is '{change.status}', not waiting for a change-risk approval")
        return change, change.risk_hold

    def approve_risk(self, change_id: str, by: str, reason: str, now: datetime | None = None) -> Change:
        """A second admin accepts the risky changes with a reason (ADR-0027). The
        requester may approve their own only as break-glass: allowed by config, after
        the cooling-off delay, and flagged in the evidence."""
        change, hold = self._awaiting_risk(change_id)
        policy = self.cfg.change_risk
        now = now or _now()
        own = bool(hold.requested_by) and by == hold.requested_by
        if own:
            if not policy.break_glass:
                raise FactoryError("you asked for this change, so another admin must approve it (break-glass is off)")
            ready = hold.since + timedelta(minutes=policy.cooling_off_minutes)
            if now < ready:
                wait = int((ready - now).total_seconds() // 60) + 1
                raise FactoryError(
                    f"you asked for this change: another admin can approve it now, or you can approve it "
                    f"yourself as break-glass in {wait} minute(s) (cooling-off {policy.cooling_off_minutes} min)"
                )
        approval = RiskApproval(digest=hold.digest, by=by, reason=reason.strip(), at=now, break_glass=own)
        change.risk_approvals.append(approval)
        change.risk_hold = None
        change.last_failure = None  # the findings were accepted, not handed to a repair station
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"risky changes approved by {by}" + (" (break-glass)" if own else "") + f": {approval.reason}",
            station=hold.station,
            data={"risk_approval": approval.model_dump(mode="json")},
        )
        self._schedule(change.id)
        return change

    def send_back_risk(self, change_id: str, by: str, reason: str) -> Change:
        """The risky changes are not accepted: back to the station's repair route with
        the findings and the reason, or cancelled when there is none."""
        change, hold = self._awaiting_risk(change_id)
        product = self.store.get_product(change.product_id)
        flow = self.workflows[change.workflow_id or (product.blueprint if product else "")].get(change.workflow_version)
        target = flow.station(hold.station).on_fail
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"risky changes sent back by {by}: {reason.strip()}",
            station=hold.station,
            data={"risk_sent_back": {"by": by, "reason": reason.strip(), "digest": hold.digest}},
        )
        change.risk_hold = None
        if not target:
            self.store.save_change(change)
            return self.cancel(change.id)
        change.last_failure = (
            f"An admin did not accept these risky changes: {reason.strip()}\n"
            f"Remove them, or make the change without them.\n\n{change.last_failure or ''}"
        ).strip()
        change.current_station = target
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self._schedule(change.id)
        return change

    def cancel(self, change_id: str) -> Change:
        change = self._get(change_id)
        task = self._tasks.get(change_id)
        if task and not task.done():
            task.cancel()
        if self.sandbox is not None:
            asyncio.get_running_loop().create_task(self.sandbox.remove_change(change_id))
        change.status = ChangeStatus.cancelled
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(change.id, EventKind.status, "change cancelled by human")
        self._tasks[change_id] = asyncio.get_running_loop().create_task(
            self._seal_after_cancel(change_id, task), name=f"seal-{change_id}"
        )
        return change

    def feedback(self, product_id: str, text: str, kind: ChangeKind = ChangeKind.feature) -> Change:
        """A change to a delivered product: a feature, a bug fix or upkeep (ADR-0030)."""
        if kind not in ITERATION_KINDS:
            raise InvalidRequestError(
                f"'{kind}' is not a change to a delivered product: ask for a feature, bug fix or upkeep"
            )
        product = self.store.get_product(product_id)
        if not product:
            raise FactoryError("product not found")
        latest = self.store.get_change(product.latest_change_id) if product.latest_change_id else None
        if latest and latest.status not in FEEDBACK_OPEN:
            raise FactoryError(f"latest change is '{latest.status}'; feedback opens after delivery")
        self.screen(text, f"feedback on {product_id}")
        self.store.add_feedback(product_id, latest.id if latest else None, text)
        change = self.start_change(product, change_request=text, kind=kind)
        self.store.add_event(change.id, EventKind.feedback, text, data={"kind": kind})
        return change

    def recover_on_startup(self) -> list[str]:
        """Builder `factory-recover` rule: never silently restart; mark and let a
        human resume, unless the recover policy is explicitly auto."""
        touched = []
        for change in self.store.changes_with_status({ChangeStatus.running, ChangeStatus.queued}):
            change.status = ChangeStatus.interrupted
            self.store.save_change(change)
            self._sync_product(change)
            self.store.add_event(
                change.id, EventKind.status, "engine restarted: change interrupted", station=change.current_station
            )
            touched.append(change.id)
            if self.cfg.policies.recover.mode == "auto":
                self.resume(change.id)
        for change in self.store.changes_with_status({ChangeStatus.paused_limits}):
            self._wake_later(change)
        return touched

    # -------------------------------------------------------- internals ----
    def _get(self, change_id: str) -> Change:
        change = self.store.get_change(change_id)
        if not change:
            raise FactoryError("change not found")
        return change

    def _schedule(self, change_id: str) -> None:
        self._tasks[change_id] = asyncio.create_task(self._execute(change_id), name=f"run-{change_id}")

    def _sync_product(self, change: Change, product: Product | None = None) -> None:
        product = product or self.store.get_product(change.product_id)
        if product and product.latest_change_id == change.id:
            product.latest_status = change.status
            self.store.save_product(product)

    def _wake_later(self, change: Change) -> None:
        async def wake() -> None:
            delay = max(5.0, ((change.resume_at or _now()) - _now()).total_seconds())
            await asyncio.sleep(delay)
            current = self.store.get_change(change.id)
            if current and current.status == ChangeStatus.paused_limits:
                current.status = ChangeStatus.queued
                self.store.save_change(current)
                self.store.add_event(change.id, EventKind.status, "usage window reset: resuming")
                self._schedule(change.id)

        asyncio.create_task(wake())

    async def _execute(self, change_id: str) -> None:
        cancelled = False
        async with self._sem:
            change = self._get(change_id)
            product = self.store.get_product(change.product_id)
            assert product is not None
            try:
                await self._drive(change, product)
            except asyncio.CancelledError:
                cancelled = True  # cancel() already persisted the cancelled status
                raise
            except Exception as exc:  # noqa: BLE001 - engine faults become a visible failure
                change.status = ChangeStatus.failed
                change.summary = f"engine error: {exc}"
                self.store.add_event(
                    change.id, EventKind.status, change.summary, data={"traceback": traceback.format_exc()[-4000:]}
                )
            finally:
                if not cancelled:
                    self.store.save_change(change)
                    self.store.save_product(product)
                    self._sync_product(change, product)
        # after the slot is released: learning never delays or fails a delivery (ADR-0021)
        if not cancelled and change.status == ChangeStatus.awaiting_feedback:
            await self._retro(change, product)
        if not cancelled:
            await self._seal(change, product)
            self._after_stop(change, product)

    def _after_stop(self, change: Change, product: Product) -> None:
        """Evaluation runs report to their evaluation (ADR-0025); never fails the change."""
        if not product.eval_run_id:
            return
        from agent_factory.evals import on_change_stopped

        try:
            on_change_stopped(self, self._get(change.id), self.store.get_product(product.id) or product)
        except Exception as exc:  # noqa: BLE001
            self.store.add_event(change.id, EventKind.log, f"evaluation update failed: {type(exc).__name__}: {exc}")

    async def _seal(self, change: Change, product: Product) -> None:
        """Seal the change's evidence at every stop (ADR-0023); never fails the change."""
        from agent_factory.evidence import seal

        try:
            await seal(self, change, product)
        except Exception as exc:  # noqa: BLE001 - a failed seal is visible, never raised into the change
            self.store.add_event(change.id, EventKind.log, f"evidence seal failed: {type(exc).__name__}: {exc}")

    async def _seal_after_cancel(self, change_id: str, task: asyncio.Task[None] | None) -> None:
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)  # let the station stop first
        change = self.store.get_change(change_id)
        product = self.store.get_product(change.product_id) if change else None
        if change and product:
            await self._seal(change, product)
            self._after_stop(change, product)

    async def _retro(self, change: Change, product: Product) -> None:
        from agent_factory.retro import run_retro

        cost = change.cost_usd
        try:
            await run_retro(self, change, product)
        except Exception as exc:  # noqa: BLE001 - a failed retro is logged, never raised into the change
            self.store.add_event(change.id, EventKind.log, f"retro failed: {type(exc).__name__}: {exc}")
        if change.cost_usd != cost:
            self.store.save_change(change)  # the retro's model spend belongs to this change

    async def _drive(self, change: Change, product: Product) -> None:
        change.status = ChangeStatus.running
        self.store.save_change(change)
        self._sync_product(change, product)
        blueprint = self.cfg.blueprints[product.blueprint]
        owner = str(getattr(self.cfg.policies.publish, "owner", "") or "")
        await self.ws.ensure_product_repo(product.slug, blueprint.template, owner)
        # pinned: later workflow edits never affect this change
        flow = self.workflows[change.workflow_id or product.blueprint].get(change.workflow_version)
        worktree = await self.ws.create_worktree(product.slug, change.id)
        deadline = change.created_at + timedelta(minutes=self.cfg.budgets.change_wall_clock_minutes)

        while change.current_station:
            sid = change.current_station
            station = flow.station(sid)
            if _now() > deadline and change.status != ChangeStatus.needs_input:
                return self._hold(change, sid, "wall-clock budget exhausted")
            change.attempts[sid] = change.attempts.get(sid, 0) + 1
            if change.attempts[sid] > self.cfg.budgets.max_attempts_per_station:
                return self._hold(change, sid, f"station '{sid}' exceeded its attempt budget")
            self.store.save_change(change)
            self.store.add_event(
                change.id, EventKind.station_started, f"{sid} started (attempt {change.attempts[sid]})", station=sid
            )

            ctx = StationContext(
                self.cfg, self.settings, self.store, self.ws, self.ex, self.agents, product, change, worktree, sid, flow
            )
            ctx.providers = self.providers.for_blueprint(product.blueprint)
            if self.sandbox is not None:
                ctx.untrusted_ex = self.sandbox.executor(change.id, sid)
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
                change.id,
                EventKind.station_finished,
                f"{sid}: {result.outcome} — {result.summary}",
                station=sid,
                data={"outcome": result.outcome},
            )

            if result.outcome == StationOutcome.passed:
                if sid in flow.repair_targets() or station.only_on_fail:
                    change.last_failure = None  # the station that consumed the evidence succeeded
                nxt = station.next or flow.next_forward(sid)
                change.current_station = nxt
                if nxt is None:
                    change.status = ChangeStatus.awaiting_feedback
                    change.summary = result.summary
                    self.store.add_event(change.id, EventKind.status, "delivered — feedback gate open", station=sid)
                elif (
                    station.resolved_handler() == "design"
                    and flow.spec_gate_applies(change.iteration)
                    and not change.spec_approved_by
                ):
                    change.status = ChangeStatus.awaiting_approval
                    change.summary = "specification ready for your review"
                    self.store.save_change(change)
                    self.store.add_event(
                        change.id,
                        EventKind.status,
                        "spec review gate: waiting for a person to approve the spec before build",
                        station=sid,
                    )
                    return None
                self.store.save_change(change)
                continue

            if result.outcome == StationOutcome.failed:
                change.loops += 1
                target = station.on_fail
                if not target:
                    return self._hold(change, sid, f"{result.summary} (no repair route)", result.failure_evidence)
                if change.loops > self.cfg.budgets.max_loops_per_change:
                    return self._hold(change, sid, "fix-loop budget exhausted", result.failure_evidence)
                change.last_failure = result.failure_evidence or result.summary
                change.current_station = target
                self.store.add_event(
                    change.id,
                    EventKind.decision,
                    f"routing failure from {sid} to {target}",
                    station=sid,
                    data={"routed": {"from": sid, "to": target, "evidence": change.last_failure[-3000:]}},
                )
                self.store.save_change(change)
                continue

            if result.outcome == StationOutcome.needs_input:
                change.status = ChangeStatus.needs_input
                change.questions = result.questions
                change.answers = []
                change.attempts[sid] -= 1
                change.summary = "waiting for your answers"
                return None

            if result.outcome == StationOutcome.needs_approval:
                change.attempts[sid] -= 1  # waiting for a person is not an attempt
                change.status = ChangeStatus.awaiting_risk_approval
                product_now = self.store.get_product(change.product_id) or product
                change.risk_hold = RiskHold(
                    digest=result.approval_digest or "",
                    station=sid,
                    since=_now(),
                    findings=result.approval_findings,
                    requested_by=product_now.created_by,
                )
                change.last_failure = result.failure_evidence
                change.summary = result.summary
                self.store.save_change(change)
                self.store.add_event(
                    change.id, EventKind.status, "change risk: waiting for an admin to approve", station=sid
                )
                return None

            if result.outcome == StationOutcome.paused_limits:
                change.attempts[sid] -= 1
                change.status = ChangeStatus.paused_limits
                change.resume_at = (
                    datetime.fromtimestamp(result.resets_at, UTC)
                    if result.resets_at
                    else _now() + timedelta(minutes=30)
                )
                change.summary = f"paused for usage limits until {change.resume_at:%H:%M}"
                self.store.add_event(change.id, EventKind.status, change.summary, station=sid)
                self.store.save_change(change)
                self._wake_later(change)
                return None

            return self._hold(change, sid, result.summary, result.failure_evidence)
        return None

    def _hold(self, change: Change, sid: str, why: str, evidence: str | None = None) -> None:
        change.status = ChangeStatus.held
        change.summary = f"held at {sid}: {why}"
        change.last_failure = evidence or change.last_failure
        self.store.add_event(
            change.id, EventKind.status, change.summary, station=sid, data={"evidence": (evidence or "")[-3000:]}
        )
        return None
