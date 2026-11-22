"""Run and maintain delivered products (roadmap J4): the part that acts.

`Operations` does for every live product the factory built what a station does for a change, on
a schedule and outside any change: it reads the product's health and judges it against the
availability target (`watch`), backs its data up (`backup`), proves the newest backup by restoring
it (`restore_drill`), and checks its dependencies and live image again (`upkeep`). The rules are in
`operate.py` (pure); the commands are the stack pack's (`stack.operate`), run through the deploy
provider, never invented here.

What it finds goes where a person already looks: an availability breach is the alert that opens an
incident in the product's work ledger (`ledger.fold_watch`), which "Needs you" routes to the
product's owner and the Next iteration card turns into a fix change. What it did is appended to the
product's operation records (`store.add_operation`): a product's history after delivery.

A backup holds the product's data: it is written under `<data>/backups/<product>/`, readable by
the engine only, and neither it nor a command's output is ever logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shlex
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory import base_images, image_scan, ledger, operate, packs, validation
from agent_factory import respond as rules
from agent_factory.engine.stations import StationContext, _lockfiles
from agent_factory.executor import CommandResult, OutputCutOff
from agent_factory.models import (
    AvailabilityView,
    Change,
    ChangeStatus,
    EventKind,
    HealthSample,
    IncidentResponse,
    OperationRecord,
    OperationsView,
    Product,
    ProductTarget,
    ResponseMode,
    WorkItem,
)
from agent_factory.providers.base import ImageRef, StepResult
from agent_factory.secret_refs import REDACTOR

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import ChangeManager
    from agent_factory.packs import PackData, PackOperate

log = logging.getLogger(__name__)

STATION = "operate"
# a change in one of these states has stopped: it no longer stands between an item and its check
STOPPED = {ChangeStatus.awaiting_feedback, ChangeStatus.failed, ChangeStatus.cancelled}
# stations that replace or test what production runs: a reading taken meanwhile says nothing
DEPLOYING = {"deploy", "deploy-repair", "promote"}
RETRY_FAILED_HOURS = 1.0  # a scheduled step that failed is tried again after this, not a full interval
DRILLS_OPEN = "restore_drills_open"  # meta: scratch releases a drill deployed and hasn't removed yet
SCRATCH_DATABASE = packs.SCRATCH_DATABASE
READY_TRIES = 20
PROBES_AT_ONCE = 8  # health readings taken side by side in one pass of the watch
SIGXFSZ_EXIT = 128 + 25  # the shell's status for a command killed for writing past `ulimit -f`
# incident response (ADR-0049 §4, slice 0)
SETTLE_SECONDS = 120  # after a ladder step, how long its effect is given before the next step is taken
DRILL_PASSED = "factory_restore_drill"  # meta: the install's factory restore drill (JSON: passed, at, by, note)
RECONCILE = "respond_reconcile"  # meta: products whose release record a reading must match after a restore
RESTORED_MARKER = "factory-restored"  # a file scripts/restore.sh leaves in the data folder


class OperateError(Exception):
    """An operation that can't be done for this product, with the reason a person reads."""


@dataclass
class OpsContext(StationContext):
    """A station context for a step outside any change. `quiet`: commands run once and nothing is
    written to a change's event log (a health reading every minute would bury it)."""

    quiet: bool = False

    async def cmd(  # noqa: PLR0913 - the station context's signature
        self,
        command: str,
        timeout: float = 900,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        untrusted: bool = False,
        retry: bool = True,
        record: bool = True,
    ) -> CommandResult:
        if not self.quiet:
            return await super().cmd(command, timeout, env, cwd, untrusted, retry, record)
        ex = (self.untrusted_ex or self.ex) if untrusted else self.ex
        return await ex.run(command, cwd=cwd or self.worktree, timeout=timeout, env=env)

    async def emit(self, kind: EventKind, message: str, data: dict[str, Any] | None = None) -> None:
        if not self.quiet:
            await super().emit(kind, message, data)


def _now() -> datetime:
    return datetime.now(UTC)


def _short(text: str, limit: int = 200) -> str:
    """The last line of a failed command, for a reading's detail: an error, never a response body."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "no answer")[:limit]


def _whole(res: CommandResult) -> str | None:
    """A command's complete output for a decision; None when only its tail is known."""
    try:
        return res.whole()
    except OutputCutOff:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Operations:
    def __init__(self, manager: ChangeManager) -> None:
        self.m = manager
        self._task: asyncio.Task[None] | None = None
        self._busy: set[str] = set()  # products with a backup, drill or upkeep in flight
        self._jobs: set[asyncio.Task[Any]] = set()
        self._probed: dict[str, datetime] = {}
        self._responding: set[str] = set()  # products with a response step in flight
        self._urgent: set[str] = set()  # products with an incident being responded to: read more often

    # ------------------------------------------------------------- who/what --
    @property
    def store(self) -> Any:
        return self.m.store

    @property
    def cfg(self) -> Any:
        return self.m.cfg.operate

    def pack(self, product: Product) -> PackOperate | None:
        bp = self.m.cfg.blueprints.get(product.blueprint)
        return bp.operate if bp is not None else None

    def target(self, product: Product) -> operate.Target | None:
        """The product's availability target: its stack's default, tightened by the product's own."""
        op = self.pack(product)
        if op is None:
            return None
        a = op.availability
        default = operate.Target(a.success, a.window_minutes, a.min_samples, a.max_error_rate)
        own = product.availability
        if own is None:
            return default
        return operate.tighten(default, own.success, own.max_error_rate)[0]

    def why_not(self, product: Product) -> str | None:
        """Why the factory doesn't run this product, or None: it does."""
        if product.target != ProductTarget.new:
            return "the factory runs only the products it built and deployed"
        if product.eval_run_id:
            return "an evaluation product is not run after its evaluation"
        if product.retirement is not None:
            return "the product is retired" if product.retirement.done else "the product is being retired"
        if product.archived_at:
            return "the product is archived"
        if product.release is None:
            return "nothing is live yet: the product has no delivered release"
        providers = self.m.providers.for_blueprint(product.blueprint)
        if product.release.environment != providers.environment:
            return f"its release runs in '{product.release.environment}', not this environment"
        if not callable(getattr(providers.deploy, "read", None)):
            return f"the deploy target ({getattr(providers.deploy, 'name', '?')}) gives no way to read the live app"
        if self.pack(product) is None:
            return "its blueprint names no stack pack, so nothing says how it is run"
        return None

    def _release_change(self, product: Product) -> Change:
        """The change whose release is live: its checkout is the code that runs."""
        ids = [product.release.change_id if product.release else None, product.latest_change_id]
        for cid in ids:
            change = self.store.get_change(cid) if cid else None
            if change is not None:
                return change
        raise OperateError("the product has no change to act from")

    def ctx(self, product: Product, *, quiet: bool = False, slot: str | None = None) -> OpsContext:
        change = self._release_change(product)
        flow = self.m.workflows[change.workflow_id or ""].get(change.workflow_version)
        ctx = OpsContext(
            self.m.cfg, self.m.settings, self.store, self.m.ws, self.m.ex, self.m.agents, product, change,
            self.m.ws.change_dir(change.id), STATION, flow,
        )  # fmt: skip
        ctx.providers = self.m.providers.for_blueprint(product.blueprint)
        ctx.secrets = self.m.secrets
        ctx.slot = slot
        ctx.quiet = quiet
        return ctx

    def _record(  # noqa: PLR0913 - one record's fields
        self,
        product: Product,
        kind: str,
        ok: bool,
        summary: str,
        by: str,
        data: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> OperationRecord:
        rec = OperationRecord.model_validate(
            {
                "id": uuid.uuid4().hex[:12],
                "product_id": product.id,
                "kind": kind,
                "at": at or _now(),
                "ok": ok,
                "summary": summary,
                "by": by,
                "data": data or {},
            }
        )
        self.store.add_operation(rec)
        return rec

    def _running(self, product: Product) -> set[str]:
        """Changes of the product that haven't stopped (running, queued, held, waiting on a person)."""
        return {c.id for c in self.store.list_changes(product.id) if c.status not in STOPPED}

    def _deploying(self, product: Product) -> bool:
        """A change is replacing or testing what production runs right now."""
        for c in self.store.list_changes(product.id):
            if not self.m.is_active(c.id):
                continue
            if c.deployed is not None:  # validating in place: production runs an unaccepted version
                return True
            flow = self.m.workflows[c.workflow_id or ""].get(c.workflow_version)
            station = next((s for s in flow.stations if s.id == c.current_station), None)
            if station is not None and station.resolved_handler() in DEPLOYING:
                return True
        return False

    # ---------------------------------------------------------------- watch --
    async def probe(self, product: Product, now: datetime | None = None) -> HealthSample:
        """One reading of the live product: its liveness path, and its request counters when the
        pack names a metrics page. Read-only; production is never written to."""
        now = now or _now()
        ctx = self.ctx(product, quiet=True)
        deploy = ctx.providers.deploy
        res = await deploy.read(ctx, ctx.blueprint.health_path)  # type: ignore[attr-defined]
        sample = HealthSample(
            product_id=product.id, at=now, ok=res.ok, detail="" if res.ok else _short(res.output[-400:])
        )
        op = self.pack(product)
        if res.ok and op is not None and op.metrics is not None:
            with tempfile.TemporaryDirectory(prefix="metrics-") as tmp:
                page = Path(tmp) / "metrics.txt"
                got = await deploy.read(ctx, op.metrics.path, page)  # type: ignore[attr-defined]
                text = page.read_text(errors="replace") if got.ok and page.is_file() else ""
            counts = operate.request_counts(text, op.metrics.counter, op.metrics.status_label) if text else None
            if counts is not None:
                sample.requests, sample.errors = counts
        return sample

    def availability(self, product: Product, now: datetime | None = None) -> operate.Availability:
        """The readings since the live release was delivered, judged against the target: what an
        earlier release did says nothing about this one."""
        now = now or _now()
        target = self.target(product)
        if target is None:
            return operate.Availability("unknown")
        since = now - timedelta(minutes=target.window_minutes)
        if product.release is not None and product.release.at > since:
            since = product.release.at
        rows = self.store.list_health_samples(product.id, since)
        samples = [operate.Sample(r.at, r.ok, r.detail, r.requests, r.errors) for r in rows]
        return operate.evaluate(samples, target, now)

    async def watch(
        self, product: Product, now: datetime | None = None, *, act: bool = True
    ) -> operate.Availability | None:
        """Read the product once, judge the window, and keep the ledger in step: a breach opens (or
        reopens) the product's incident, a window within target closes it. Then respond to an open
        incident (`respond`; `act=False`: the caller schedules it). None: not read (the factory
        doesn't run it, or a change is deploying to it right now)."""
        now = now or _now()
        if self.why_not(product) is not None or self._deploying(product):
            return None
        sample = await self.probe(product, now)
        self.store.add_health_sample(sample)
        self._probed[product.id] = now
        if not sample.ok and self.cfg.respond != "off":
            self._urgent.add(product.id)  # a failed reading: read every few seconds, so a breach is confirmed fast
        a = self.availability(product, now)
        self._fold_incident(product, a, now)
        await self._reconcile(product)
        if act:
            await self.respond(product, now)
        return a

    def _fold_incident(self, product: Product, a: operate.Availability, now: datetime) -> None:
        target = self.target(product)
        if a.state == "unknown" or target is None:
            return  # too few readings: neither an alert nor proof of health
        items = self.store.list_work_items(product.id)
        change = self._release_change(product)
        candidate = operate.incident_candidate(a, target, product.app_url)
        if candidate is None and (open_ := self._open_incident(product, items)) is not None:
            ep = self.episode(product, open_)
            if ep is not None and ep.tried and open_.status == "open":
                return  # the response restored it: it is resolved after the quiet period (`respond`)
        item, what = ledger.fold_watch(
            product.id,
            items,
            ledger.INCIDENT,
            candidate,
            now=now,
            iteration=change.iteration,
            running=self._running(product),
        )
        if item is None:
            return
        self.store.save_work_items([item])
        owner = product.owner or product.created_by or "an admin"
        if what in ("new", "reopened"):
            why = "; ".join(a.reasons)
            first_bad = a.since or now
            cause = rules.caused_by(product.release, first_bad, self.cfg.incident_change_hours)
            self._record(product, "alert", False, f"outside its availability target: {why}", "factory",
                         {"incident": item.id, "owner": owner, **a.as_data(), "first_bad_at": first_bad.isoformat(),
                          "caused_by": cause}, at=now)  # fmt: skip
            self.store.add_event(
                change.id,
                EventKind.decision,
                f"alert: the live product is outside its availability target ({why}); incident {item.id} "
                f"opened for {owner}",
                station=STATION,
                data={"incident": item.id, "availability": a.as_data(), "owner": owner},
            )
        elif what == "fixed":
            self._record(product, "recovered", True, "within its availability target again; incident closed",
                         "factory", {"incident": item.id, **a.as_data()}, at=now)  # fmt: skip
            self.store.add_event(
                change.id,
                EventKind.decision,
                f"the live product is within its availability target again; incident {item.id} closed",
                station=STATION,
                data={"incident": item.id, "availability": a.as_data()},
            )

    # ---------------------------------------------------- incident response --
    def response_mode(self, product: Product) -> tuple[rules.Mode, str]:
        """The mode this product's incidents are responded to in now, and why it isn't the configured one."""
        return rules.effective_mode(self.cfg.respond, self.drill_passed(), product.id not in self._pending())

    def drill_passed(self) -> bool:
        try:
            got = json.loads(self.store.get_meta(DRILL_PASSED) or "{}")
        except ValueError:
            return False
        return isinstance(got, dict) and got.get("passed") is True

    def _pending(self) -> set[str]:
        try:
            got = json.loads(self.store.get_meta(RECONCILE) or "[]")
        except ValueError:
            return set()
        return {str(x) for x in got} if isinstance(got, list) else set()

    def note_restore(self) -> bool:
        """The factory was restored from a backup (`scripts/restore.sh` leaves a marker): until a reading has
        matched each product's recorded release with the live one, its incidents are responded to in ask mode."""
        marker = Path(self.m.ws.data_dir) / RESTORED_MARKER
        if not marker.is_file():
            return False
        ids = [p.id for p in self.store.list_products() if p.release is not None]
        self.store.set_meta(RECONCILE, json.dumps(sorted(set(ids) | self._pending())))
        marker.unlink(missing_ok=True)
        log.info("operate: the factory was restored; %d product(s) respond in ask mode until reconciled", len(ids))
        return True

    async def _reconcile(self, product: Product) -> None:
        pending = self._pending()
        if product.id not in pending or product.release is None:
            return
        live = await self._live(product)
        if live.error or rules.stale_record(product.release, live):
            return  # still unknown or stale: stays in ask mode, and an incident is held as stale record
        pending.discard(product.id)
        self.store.set_meta(RECONCILE, json.dumps(sorted(pending)))
        self.store.add_event(
            self._release_change(product).id,
            EventKind.decision,
            f"after the factory restore, the recorded release (revision {product.release.revision}) matches the "
            "live one: incident response may run as configured again",
            station=STATION,
            data={"reconciled": product.release.revision},
        )

    def _last_ok(self, product: Product) -> bool:
        rows = self.store.list_health_samples(product.id, _now() - timedelta(minutes=10))
        return not rows or rows[-1].ok

    def _open_incident(self, product: Product, items: list[WorkItem] | None = None) -> WorkItem | None:
        items = items if items is not None else self.store.list_work_items(product.id)
        return next((i for i in items if i.category == ledger.INCIDENT and i.status in ledger.OPEN), None)

    def episode(self, product: Product, item: WorkItem) -> rules.Episode | None:
        records = [r for r in self.store.list_operations(product.id, None, 500) if r.data.get("incident") == item.id]
        return rules.episode(records, item.id)

    async def _live(self, product: Product) -> rules.Live:
        ctx = self.ctx(product, quiet=True)
        reader = getattr(ctx.providers.deploy, "live_state", None)
        if product.release is None or not callable(reader):
            return rules.Live(error="the deploy target gives no way to read the live release")
        try:
            return await reader(ctx, product.release)
        except Exception as exc:  # noqa: BLE001 - an unreadable state decides nothing
            return rules.Live(error=f"reading it raised {type(exc).__name__}")

    async def respond(self, product: Product, now: datetime | None = None) -> IncidentResponse | None:
        """Respond to the product's open incident (ADR-0049 §4, slice 0): verify a step already taken, resolve
        what stayed healthy, notice a recurrence, or take the ladder's next step (snapshot first). In ask mode
        the step is proposed to the owner (`respond_now`). None: there is nothing to respond to."""
        now = now or _now()
        if self.cfg.respond == "off" or self.why_not(product) is not None or self._deploying(product):
            self._urgent.discard(product.id)
            return None
        item = self._open_incident(product)
        if item is None or product.id in self._responding:
            if item is None and self._last_ok(product):
                self._urgent.discard(product.id)
            return None
        self._responding.add(product.id)
        try:
            ep = await self._respond(product, item, now, by="factory", forced=False)
        finally:
            self._responding.discard(product.id)
        if ep is None or ep.state == "resolved":
            self._urgent.discard(product.id)
            return None if ep is None else IncidentResponse.model_validate(rules.view(ep))
        self._urgent.add(product.id)
        return IncidentResponse.model_validate(rules.view(ep))

    async def respond_now(self, product: Product, by: str) -> IncidentResponse:
        """The owner runs the step the ladder proposes (ask mode, or a step the factory held for them): the
        same checks and limits as the factory's own, run now."""
        if (why := self.why_not(product)) is not None:
            raise OperateError(why)
        if self._deploying(product):
            raise OperateError("a change is deploying to the product: its result decides first")
        item = self._open_incident(product)
        if item is None:
            raise OperateError("the product has no open incident")
        if product.id in self._responding:
            raise OperateError("a response step for this product is already running")
        self._responding.add(product.id)
        try:
            ep = await self._respond(product, item, _now(), by=by, forced=True)
        finally:
            self._responding.discard(product.id)
        if ep is None:
            raise OperateError("the incident has no alert to respond to")
        return IncidentResponse.model_validate(rules.view(ep))

    async def _respond(  # noqa: PLR0911 - one outcome per branch
        self, product: Product, item: WorkItem, now: datetime, *, by: str, forced: bool
    ) -> rules.Episode | None:
        ep = self.episode(product, item)
        if ep is None:
            return None
        k = self.cfg.restored_after
        if ep.state == "mitigating" and ep.last_action_at is not None:
            samples = self.store.list_health_samples(product.id, ep.last_action_at)
            if (at := rules.restored_at(samples, ep.last_action_at, k)) is not None:
                self._restored(product, item, ep, at)
                return self.episode(product, item)
            if not forced and (now - ep.last_action_at).total_seconds() < SETTLE_SECONDS:
                return ep  # the step is still taking effect
            after = [r for r in samples if r.at > ep.last_action_at]
            if not forced and after and after[-1].ok:
                return ep  # answering again, not yet `restored_after` times in a row: no next step
        if ep.state == "restored" and ep.restored_at is not None:
            target = self.target(product)
            rows = self.store.list_health_samples(product.id, ep.restored_at)
            since = [operate.Sample(r.at, r.ok, r.detail, r.requests, r.errors) for r in rows if r.at > ep.restored_at]
            again = operate.evaluate(since, target, now) if target is not None else operate.Availability("unknown")
            if again.state == "breached":
                why = "; ".join(again.reasons)
                self._record(product, "alert", False, f"the incident came back after it was restored: {why}",
                             "factory", {"incident": item.id, "reopened": True, **again.as_data(),
                                         "first_bad_at": (again.since or now).isoformat()}, at=now)  # fmt: skip
                ep = self.episode(product, item) or ep
            elif rules.resolve_due(ep, now, self.cfg.resolve_after_hours):
                self._resolve(product, item, ep, now)
                return self.episode(product, item)
            else:
                return ep
        mode, _why = self.response_mode(product)
        if forced:
            mode = "auto"  # a person runs it: the drill gate is for the factory acting alone
        records = self.store.list_operations(product.id, "respond", 200)
        live = await self._live(product)
        step = rules.next_step(
            product.release,
            live,
            ep.tried,
            rules.actions_since(records, now - timedelta(hours=1)),
            self.cfg.max_actions_per_hour,
            mode,
        )
        if step.action is None:
            if step.hold and step.reason != ep.held:
                data: dict[str, Any] = {"hold": step.hold, "reason": step.reason, "live": live.as_data()}
                summary = f"held for the owner: {step.reason}"
                self._respond_record(product, item, now, by, ok=False, summary=summary, data=data)
            return self.episode(product, item)
        if step.ask:
            if step.action != ep.proposed:
                data = {"ask": True, "step": step.action, "reason": step.reason, "live": live.as_data()}
                summary = f"proposed to the owner: {rules.ACTION_TEXT[step.action]} ({step.reason})"
                self._respond_record(product, item, now, by, ok=True, summary=summary, data=data)
            return self.episode(product, item)
        await self._act(product, item, step, live, now, by)
        return self.episode(product, item)

    def _respond_record(  # noqa: PLR0913 - one record's fields
        self, product: Product, item: WorkItem, now: datetime, by: str, *, ok: bool, summary: str,
        data: dict[str, Any],
    ) -> OperationRecord:  # fmt: skip
        rec = self._record(product, "respond", ok, summary, by, {"incident": item.id, **data}, at=now)
        self.store.add_event(
            self._release_change(product).id, EventKind.decision, f"incident {item.id}: {summary}",
            station=STATION, data={"incident": item.id, "respond": {k: v for k, v in data.items() if k != "live"}},
        )  # fmt: skip
        return rec

    def incidents_dir(self, product: Product) -> Path:
        return Path(self.m.ws.data_dir) / "incidents" / product.id

    async def _snapshot(self, ctx: OpsContext, product: Product, item: WorkItem, live: rules.Live) -> dict[str, Any]:
        """Snapshot first: what production looked like before anything was applied (pods, events, failing pods'
        log tails, the release history, live against recorded), redacted, kept under the factory's data folder
        for the owner and a later diagnosis, never in an event. Returns where it is and its sha256."""
        assert product.release is not None  # noqa: S101 - only a product with a release is responded to
        rec = product.release
        parts = [
            f"incident {item.id} · product {product.slug} · {_now().isoformat()}",
            f"recorded release: revision {rec.revision}, {rec.image}, {rec.digest}, delivered {rec.at.isoformat()}",
            "live: " + json.dumps(live.as_data(), sort_keys=True),
            "differs: " + ("; ".join(rules.drift(rec, live)) or "nothing"),
            "unhealthy: " + ("; ".join(rules.unhealthy(live)) or "nothing"),
        ]
        try:
            parts.append("--- diagnostics ---\n" + await ctx.providers.deploy.diagnostics(ctx))
        except Exception as exc:  # noqa: BLE001 - a snapshot that is partial still comes first
            parts.append(f"--- diagnostics ---\ncould not be read ({type(exc).__name__})")
        folder = self.incidents_dir(product)
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / f"{item.id}-{_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:4]}.txt"
        path.write_text(REDACTOR.text("\n".join(parts)) + "\n")
        path.chmod(0o600)
        return {"file": path.name, "sha256": _sha256(path)}

    async def _act(  # noqa: PLR0913 - the step and what it was decided from
        self, product: Product, item: WorkItem, step: rules.Step, live: rules.Live, now: datetime, by: str
    ) -> OperationRecord:
        """Take one ladder step: snapshot first, then re-apply or restart through the deploy target's
        fail-closed checks, under the engine's own `rollback:<environment>` capability."""
        assert step.action is not None and product.release is not None  # noqa: S101 - decided by next_step
        ctx = self.ctx(product)
        ctx.grant = frozenset({f"rollback:{ctx.providers.environment}"})
        snapshot = await self._snapshot(self.ctx(product, quiet=True), product, item, live)
        deploy = ctx.providers.deploy
        try:
            if step.action == "reapply":
                res = await deploy.rollback(ctx, product.release)  # type: ignore[attr-defined]
            else:
                restart = getattr(deploy, "restart", None)
                res = (
                    await restart(ctx, product.release)
                    if callable(restart)
                    else StepResult(False, "the deploy target can't restart the app")
                )
        except Exception as exc:  # noqa: BLE001 - a step that raised is recorded as failed, never retried here
            res = StepResult(False, f"the step raised {type(exc).__name__}: {exc}")
        verb = rules.ACTION_TEXT[step.action]
        summary = f"{verb}: {res.summary}" if res.ok else f"{verb} failed: {res.summary}"
        return self._respond_record(
            product, item, now, by, ok=res.ok, summary=summary + f" (because {step.reason})",
            data={"step": step.action, "ran": True, "reason": step.reason, "snapshot": snapshot,
                  "refused": res.data.get("refused", []), "verify": res.data.get("verify", []),
                  "live": live.as_data()},
        )  # fmt: skip

    def _restored(self, product: Product, item: WorkItem, ep: rules.Episode, at: datetime) -> None:
        ep.state, ep.restored_at = "restored", at
        ep.restored_by = ep.tried[-1] if ep.tried else ""
        timings = ep.timings()
        mins = (timings["time_to_restore_s"] or 0) / 60
        summary = (
            f"service restored ({self.cfg.restored_after} healthy readings in a row) after "
            f"{', '.join(rules.ACTION_TEXT[t] for t in ep.tried)}; {mins:.1f} minutes from the first bad reading"
        )
        self._record(product, "restored", True, summary, "factory",
                     {"incident": item.id, "by_step": ep.restored_by, **timings,
                      "classification": rules.classify(ep), "caused_by": ep.caused_by}, at=at)  # fmt: skip
        self.store.add_event(
            self._release_change(product).id, EventKind.decision, f"incident {item.id}: {summary}", station=STATION,
            data={"incident": item.id, "restored": timings},
        )  # fmt: skip

    def _resolve(self, product: Product, item: WorkItem, ep: rules.Episode, now: datetime) -> None:
        note = f"{rules.classify(ep)}; no recurrence for {self.cfg.resolve_after_hours:g} hours"
        fixed = ledger.resolve_watch(item, now, note, iteration=self._release_change(product).iteration)
        if fixed is not None:
            self.store.save_work_items([fixed])
        self._record(product, "resolved", True, f"incident resolved: {note}", "factory",
                     {"incident": item.id, "classification": rules.classify(ep), **ep.timings(),
                      "caused_by": ep.caused_by}, at=now)  # fmt: skip
        self.store.add_event(
            self._release_change(product).id, EventKind.decision, f"incident {item.id} resolved: {note}",
            station=STATION, data={"incident": item.id, "resolved": ep.timings()},
        )  # fmt: skip

    def responses(self, product: Product, items: list[WorkItem]) -> list[IncidentResponse]:
        out = []
        for it in items:
            if it.category == ledger.INCIDENT and it.status in ledger.OPEN:
                ep = self.episode(product, it)
                if ep is not None:
                    out.append(IncidentResponse.model_validate(rules.view(ep)))
        return out

    # --------------------------------------------------------------- backup --
    def backup_dir(self, product: Product) -> Path:
        return Path(self.m.ws.data_dir) / "backups" / product.id

    def _data(self, product: Product) -> PackData:
        if (why := self.why_not(product)) is not None:
            raise OperateError(why)
        return self._data_commands(product)

    def _data_commands(self, product: Product) -> PackData:
        """The stack's data commands and a deploy target that can run them (a product being retired
        is no longer watched, but its data is still exported: `engine/retirement.py`)."""
        op = self.pack(product)
        if op is None or op.data is None:
            raise OperateError("the product's stack declares no data to back up")
        if not callable(getattr(self.m.providers.for_blueprint(product.blueprint).deploy, "kube", None)):
            raise OperateError("the deploy target gives no way to run the stack's data commands")
        return op.data

    @staticmethod
    def _exec(data: PackData, ctx: StationContext, command: str, database: str, extra: str = "") -> str:
        """`kubectl exec` arguments running one of the pack's data commands in the release's workload."""
        workload = data.workload.replace("{release}", ctx.release_name)
        body = command.replace("{database}", database).replace("{release}", ctx.release_name)
        stdin = "-i " if extra.startswith("<") else ""
        return f"exec {stdin}{shlex.quote(workload)} -- sh -c {shlex.quote(body)}{(' ' + extra) if extra else ''}"

    async def _fingerprint(self, data: PackData, ctx: StationContext, database: str) -> str:
        res = await ctx.providers.deploy.kube(  # type: ignore[attr-defined]
            ctx, self._exec(data, ctx, data.fingerprint, database), "read what the product's data looks like", 120
        )
        text = _whole(res) if res.ok else None
        return operate.normal_fingerprint(text or "")

    async def backup(self, product: Product, by: str = "factory") -> OperationRecord:
        """Back the product's data up with its stack's command, into the factory's data folder. The
        data is fingerprinted before and after: a backup taken while the data moved has nothing a
        restore could be compared with, so it is taken again (three tries)."""
        data = self._data(product)
        if product.id in self._busy:
            raise OperateError("a backup, restore drill or upkeep check of this product is already running")
        self._busy.add(product.id)
        try:
            return await self._backup(product, data, by)
        finally:
            self._busy.discard(product.id)

    async def _backup(self, product: Product, data: PackData, by: str) -> OperationRecord:
        ctx = self.ctx(product)
        kube = ctx.providers.deploy.kube  # type: ignore[attr-defined]
        folder = self.backup_dir(product)
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        bid = _now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        path = folder / f"{bid}.backup"
        ok, note, fp, size = False, "", "", 0
        cap = self.cfg.max_backup_mb * 1024 * 1024
        for _attempt in range(3):
            before = await self._fingerprint(data, ctx, data.database)
            res = await kube(
                ctx, self._exec(data, ctx, data.backup, data.database, f"> {shlex.quote(str(path))}"),
                "back the product's data up", 900, max_bytes=cap,
            )  # fmt: skip
            size = path.stat().st_size if path.is_file() else 0
            if size >= cap or res.returncode == SIGXFSZ_EXIT:
                ok, note = (
                    False,
                    (
                        f"the backup reached operate.max_backup_mb ({self.cfg.max_backup_mb} MB) and was stopped, "
                        "so the factory's disk isn't filled; raise the limit if the product's data really is that large"
                    ),
                )
                break
            after = await self._fingerprint(data, ctx, data.database) if res.ok else ""
            fp = before if before == after else ""
            ok, note = operate.judge_backup(res.returncode, size, fp or (None if before != after else before))
            if not res.ok:
                note += f": {_short(res.output[-400:])}"
                break
            if ok or size <= 0:
                break
            note = "the data kept changing while it was backed up, so a restore couldn't be checked against it"
        if path.is_file():
            path.chmod(0o600)
        if not ok:
            path.unlink(missing_ok=True)
        rec = self._record(
            product,
            "backup",
            ok,
            f"backup {bid}: {note}" if ok else f"backup failed: {note}",
            by,
            {"backup": bid, "file": path.name, "size": size, "sha256": _sha256(path) if ok else "",
             "fingerprint": fp, "database": data.database, "release": product.release.image if product.release else ""},
        )  # fmt: skip
        self.store.add_event(
            ctx.change.id,
            EventKind.decision,
            rec.summary + f" (asked by {by})",
            station=STATION,
            data={"backup": bid, "ok": ok, "size": size},
        )
        if ok:
            self._prune(product)
        return rec

    def backups(self, product: Product) -> list[OperationRecord]:
        """Good backups whose file is still there, newest first."""
        folder = self.backup_dir(product)
        return [
            r
            for r in self.store.list_operations(product.id, "backup", 500)
            if r.ok and r.data.get("file") and (folder / str(r.data["file"])).is_file()
        ]

    def _prune(self, product: Product) -> None:
        kept = self.backups(product)
        rows = [operate.BackupRecord(r.id, r.at, True) for r in kept]
        drop = {b.id for b in operate.to_prune(rows, self.cfg.keep_backups)}
        folder = self.backup_dir(product)
        for r in kept:
            if r.id in drop:
                (folder / str(r.data["file"])).unlink(missing_ok=True)

    # -------------------------------------------------------- restore drill --
    async def restore_drill(self, product: Product, by: str = "factory") -> OperationRecord:
        """Prove the newest backup: restore it somewhere that is not production and compare what
        comes back with the fingerprint taken at backup time. Where the deploy target can give a
        second release, the product's live image runs on the restored data in a scratch release of
        its own and must report ready; otherwise the backup is restored into a scratch database
        beside production's (the app doesn't read it, and the record says so)."""
        data = self._data(product)
        kept = self.backups(product)
        if not kept:
            raise OperateError("there is no backup to restore yet")
        if product.id in self._busy:
            raise OperateError("a backup, restore drill or upkeep check of this product is already running")
        self._busy.add(product.id)
        try:
            return await self._drill(product, data, kept[0], by)
        finally:
            self._busy.discard(product.id)

    def _open_drills(self) -> dict[str, str]:
        try:
            got = json.loads(self.store.get_meta(DRILLS_OPEN) or "{}")
        except ValueError:
            got = {}
        return got if isinstance(got, dict) else {}

    def _mark_drill(self, product: Product, slot: str | None) -> None:
        drills = self._open_drills()
        if slot is None:
            drills.pop(product.id, None)
        else:
            drills[product.id] = slot
        self.store.set_meta(DRILLS_OPEN, json.dumps(drills))

    async def _drill(self, product: Product, data: PackData, backup: OperationRecord, by: str) -> OperationRecord:
        base = self.ctx(product)
        deploy = base.providers.deploy
        path = self.backup_dir(product) / str(backup.data["file"])
        expected = str(backup.data.get("fingerprint") or "")
        separate = bool(getattr(deploy, "validates_separately", False)) and product.release is not None
        slot = validation.release_name(product.slug, "drill") if separate else None
        ctx = self.ctx(product, slot=slot)
        database = data.database if separate else SCRATCH_DATABASE
        where = f"scratch release {slot}" if separate else f"scratch database {database} beside production's"
        ok, note, ready = False, "", not separate
        try:
            if _sha256(path) != backup.data.get("sha256"):
                raise OperateError("the backup file is not the one that was recorded (its hash differs)")
            if separate:
                assert slot is not None and product.release is not None  # noqa: S101
                self._mark_drill(product, slot)
                await deploy.undeploy(ctx)  # a drill that was cut off left its release: start clean
                repo, _, tag = product.release.image.rpartition(":")
                res = await deploy.deploy(ctx, ImageRef(repo, tag))
                if not res.ok:
                    raise OperateError(f"the scratch release didn't deploy: {res.summary}")
            elif data.scratch_create and data.scratch_drop:
                await deploy.kube(ctx, self._exec(data, ctx, data.scratch_drop, database), "restore drill", 120)  # type: ignore[attr-defined]
                made = await deploy.kube(  # type: ignore[attr-defined]
                    ctx, self._exec(data, ctx, data.scratch_create, database), "restore drill", 120
                )  # type: ignore[attr-defined]
                if not made.ok:
                    raise OperateError(f"the scratch database couldn't be made: {_short(made.output[-400:])}")
            else:
                raise OperateError(
                    "the deploy target can't give the drill a release of its own and the stack declares no "
                    "scratch database, so the only place to restore would be production"
                )
            restored = await deploy.kube(  # type: ignore[attr-defined]
                ctx, self._exec(data, ctx, data.restore, database, f"< {shlex.quote(str(path))}"),
                "restore the backup for a drill", 900,
            )  # fmt: skip
            if not restored.ok:
                raise OperateError(f"the restore command failed: {_short(restored.output[-400:])}")
            got = await self._fingerprint(data, ctx, database)
            if separate:
                ready = await self._ready(ctx)
            ok, note = operate.judge_restore(expected, got, ready)
        except OperateError as exc:
            note = str(exc)
        except Exception as exc:  # noqa: BLE001 - a drill never takes the watch down; it is recorded
            note = f"the drill raised {type(exc).__name__}: {exc}"
        finally:
            with contextlib.suppress(Exception):
                if separate:
                    gone = await deploy.undeploy(ctx)
                    if gone.ok:
                        self._mark_drill(product, None)
                elif data.scratch_drop:
                    await deploy.kube(ctx, self._exec(data, ctx, data.scratch_drop, database), "restore drill", 120)  # type: ignore[attr-defined]
        rec = self._record(
            product,
            "restore",
            ok,
            (f"restore proven in {where}: {note}" if ok else f"restore drill failed in {where}: {note}"),
            by,
            {"backup": backup.data.get("backup"), "where": "release" if separate else "database",
             "app_ran_on_it": bool(separate and ready), "target": slot or database},
        )  # fmt: skip
        self.store.add_event(
            base.change.id, EventKind.decision, rec.summary + f" (asked by {by})", station=STATION,
            data={"restore": rec.id, "ok": ok, "backup": backup.data.get("backup")},
        )  # fmt: skip
        return rec

    async def _ready(self, ctx: OpsContext) -> bool:
        """The app in the scratch release reports ready on the restored data."""
        quiet = self.ctx(ctx.product, quiet=True, slot=ctx.slot)
        live = getattr(self.m.settings, "factory_mode", "live") == "live"
        for _ in range(READY_TRIES if live else 1):
            res = await quiet.providers.deploy.read(quiet, quiet.blueprint.ready_path)  # type: ignore[attr-defined]
            if res.ok:
                return True
            await asyncio.sleep(3 if live else 0)
        return False

    async def sweep_drills(self) -> None:
        """Remove scratch releases a drill left behind (the engine stopped in the middle of one)."""
        for pid, slot in list(self._open_drills().items()):
            product = self.store.get_product(pid)
            if product is None:
                self.store.set_meta(DRILLS_OPEN, json.dumps({k: v for k, v in self._open_drills().items() if k != pid}))
                continue
            with contextlib.suppress(Exception):
                ctx = self.ctx(product, slot=slot)
                if (await ctx.providers.deploy.undeploy(ctx)).ok:
                    self._mark_drill(product, None)

    # --------------------------------------------------------------- upkeep --
    async def upkeep(self, product: Product, by: str = "factory") -> OperationRecord:
        """Check the live product again for what has aged since it was built: its image is scanned
        again (a CVE published since), and its dependencies are compared with their newest releases.
        What is found goes into the work ledger; nothing is changed here."""
        if (why := self.why_not(product)) is not None:
            raise OperateError(why)
        if product.id in self._busy:
            raise OperateError("a backup, restore drill or upkeep check of this product is already running")
        self._busy.add(product.id)
        try:
            return await self._upkeep(product, by)
        finally:
            self._busy.discard(product.id)

    async def _upkeep(self, product: Product, by: str) -> OperationRecord:
        ctx = self.ctx(product)
        now = _now()
        notes: list[str] = []
        gaps: list[str] = []
        data: dict[str, Any] = {}
        # 1. the live image, scanned again
        image = product.release.image if product.release else ""
        scan = await ctx.providers.scan.scan(ctx, image)
        report = scan.data.get("report")
        if report is None or not scan.ok or scan.data.get("returncode") not in (0, None, 1):
            gaps.append(f"the image was not scanned again ({scan.summary})")
        else:
            try:
                findings = image_scan.parse(str(report), image_scan.locked_packages(_lockfiles(ctx.worktree)))
            except image_scan.NotAnImageReport:
                gaps.append("the image scan's report couldn't be read (not Trivy JSON)")
            else:
                dockerfile = ctx.worktree / "Dockerfile"
                base = image_scan.base_image(dockerfile.read_text(errors="replace")) if dockerfile.is_file() else ""
                items = self.store.list_work_items(product.id)
                changed, stats = ledger.fold_image_scan(
                    product.id,
                    items,
                    [
                        {
                            "key": f.key,
                            "title": image_scan.ledger_title(f),
                            "severity": f.severity,
                            "detail": image_scan.ledger_detail(f),
                            "at": [f.path or f.ecosystem],
                            "rule": f.cve,
                            "origin": f.origin,
                            "base_image": base_images.image_name(base) if base else "",
                        }
                        for f in findings
                    ],
                    change_id=ctx.change.id,
                    iteration=ctx.change.iteration,
                    now=now,
                )
                if changed:
                    self.store.save_work_items(changed)
                data["image"] = {"findings": len(findings), **stats}
                notes.append(f"image {image}: {len(findings)} finding(s), {operate.summary_counts(stats)}")
        # 2. dependencies against their newest releases, in the product's sandbox
        op = self.pack(product)
        if op is None or op.upkeep is None:
            gaps.append("the stack declares no dependency check")
        else:
            rows = await self._outdated(ctx, op.upkeep.outdated, op.upkeep.format, op.upkeep.answers)
            if rows is None:
                gaps.append("the dependency check didn't answer (it failed, or printed no list)")
            else:
                items = self.store.list_work_items(product.id)
                item, what = ledger.fold_watch(
                    product.id, items, ledger.UPKEEP, operate.upkeep_candidate(rows),
                    now=now, iteration=ctx.change.iteration, running=self._running(product),
                )  # fmt: skip
                if item is not None:
                    self.store.save_work_items([item])
                data["dependencies"] = {"behind": len(rows), "major": sum(r.major for r in rows), "ledger": what}
                notes.append(f"{len(rows)} dependenc{'y' if len(rows) == 1 else 'ies'} behind")
        data["gaps"] = gaps
        summary = "; ".join(notes) or "nothing was checked"
        if gaps:
            summary += " · not checked: " + "; ".join(gaps)
        rec = self._record(product, "upkeep", bool(notes), summary, by, data)
        self.store.add_event(
            ctx.change.id, EventKind.decision, f"upkeep check: {summary} (asked by {by})", station=STATION, data=data
        )
        return rec

    async def _outdated(
        self, ctx: OpsContext, command: str, fmt: str, answers: list[int]
    ) -> list[operate.Outdated] | None:
        """The pack's outdated command over the product's code. It runs the product's own package
        manager on an agent-written repo, so it runs where agent-written code runs: the sandbox.
        None: the check didn't answer (an exit status the pack doesn't list, a cut-off output, no
        JSON), so nothing it would have said is claimed: an open item stays open."""
        sandbox = self.m.sandbox
        if sandbox is not None:
            ctx.untrusted_ex = sandbox.executor(ctx.change.id, STATION, ctx.pack_egress)
        try:
            res = await ctx.cmd(command, timeout=900, untrusted=True, retry=False, record=False)
        finally:
            if sandbox is not None:
                with contextlib.suppress(Exception):
                    await sandbox.remove_change(ctx.change.id)
        text = _whole(res)
        if text is None or res.returncode not in answers:
            return None
        return operate.OUTDATED_PARSERS[fmt](operate.json_part(text))

    async def start_upkeep(self, product: Product) -> Change | None:
        """`upkeep_start: auto`: start the upkeep change for what the checks found, when nothing else
        is running on the product and it isn't in an incident (a fix comes first). None: not started."""
        if self.cfg.upkeep_start != "auto":
            return None
        latest = self.store.get_change(product.latest_change_id) if product.latest_change_id else None
        if latest is None or latest.status != ChangeStatus.awaiting_feedback:
            return None
        groups = ledger.next_iteration(self.store.list_work_items(product.id))
        if groups["incidents"]:
            return None
        from agent_factory.engine.pipeline import FactoryError
        from agent_factory.models import ChangeSource

        scheduled = {c.id for c in self.store.list_changes(product.id) if c.source == ChangeSource.schedule}
        picked, left = operate.auto_upkeep_pick([*groups["security"], *groups["upkeep"]], scheduled)
        key = f"upkeep_left_for_owner:{product.id}"
        ids = ",".join(sorted(i.id for i in left))
        if left and self.store.get_meta(key) != ids:  # said once, not on every cycle
            self.store.set_meta(key, ids)
            self.store.add_event(
                latest.id,
                EventKind.decision,
                f"upkeep: {len(left)} item(s) a scheduled change didn't fix are left for the owner "
                f"({ids}); the factory doesn't start the same fix again by itself",
                station=STATION,
                data={"left_for_owner": sorted(i.id for i in left)},
            )
        if not picked:
            return None

        try:
            await self.m.preflight.gate(product.blueprint, "scheduled upkeep")
            return self.m.feedback(
                product.id,
                "Scheduled upkeep: bring the dependencies and the image up to date. Keep the behaviour as it is.",
                kind=None,
                items=[i.id for i in picked],
                source=ChangeSource.schedule,
                requested_by="factory",
            )
        except FactoryError as exc:
            self._record(product, "upkeep", False, f"the upkeep change was not started: {exc}", "factory")
            return None

    # ------------------------------------------------------------- schedule --
    def _due(self, product: Product, kind: str, every_hours: float, now: datetime) -> bool:
        last = self.store.list_operations(product.id, kind, 1)
        if not last:
            return True
        wait = every_hours if last[0].ok else min(every_hours, RETRY_FAILED_HOURS)
        return now - last[0].at >= timedelta(hours=wait)

    def _spawn(self, coro: Any) -> None:
        async def guarded() -> None:
            try:
                await coro
            except OperateError as exc:
                log.info("operate: %s", exc)
            except Exception:  # noqa: BLE001 - one product's step never stops the others'
                log.warning("operate: a scheduled step failed", exc_info=True)

        task = asyncio.create_task(guarded(), name="operate-job")
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _maintain(self, product: Product, now: datetime) -> None:
        """What is due for one product, one step after another: backup, restore drill, upkeep."""
        op = self.pack(product)
        has_data = op is not None and op.data is not None
        with contextlib.suppress(OperateError):
            if has_data and self._due(product, "backup", self.cfg.backup_every_hours, now):
                await self.backup(product)
            if (
                has_data
                and self.backups(product)
                and self._due(product, "restore", self.cfg.restore_drill_every_days * 24, now)
            ):
                await self.restore_drill(product)
            if self._due(product, "upkeep", self.cfg.upkeep_every_hours, now):
                await self.upkeep(product)
                await self.start_upkeep(product)

    async def tick(self, now: datetime | None = None, *, wait: bool = False) -> None:
        """One pass over every live product: read its health when a reading is due, and start what
        maintenance is due (in the background, unless `wait`)."""
        now = now or _now()
        live = [p for p in self.store.list_products() if self.why_not(p) is None]
        # readings are taken side by side (bounded): one product that doesn't answer waits out its
        # own timeout, never every other product's reading, and the pass stays within its interval
        gate = asyncio.Semaphore(PROBES_AT_ONCE)

        async def read(product: Product) -> None:
            last = self._probed.get(product.id)
            every = self.cfg.respond_probe_seconds if product.id in self._urgent else self.cfg.probe_seconds
            if last is not None and (now - last).total_seconds() < every - 1:
                return
            async with gate:
                try:
                    await self.watch(product, now, act=False)
                except Exception:  # noqa: BLE001 - one product never stops the watch of the others
                    log.warning("operate: product %s could not be watched", product.id, exc_info=True)
                    return
            if product.id not in self._responding:
                if wait:
                    await self.respond(product, now)
                else:
                    self._spawn(self.respond(product, now))

        await asyncio.gather(*(read(p) for p in live))
        for product in live:
            try:
                if product.id not in self._busy and not self._deploying(product):
                    if wait:
                        await self._maintain(product, now)
                    else:
                        self._spawn(self._maintain(product, now))
            except Exception:  # noqa: BLE001 - one product never stops the others' upkeep
                log.warning("operate: product %s could not be maintained", product.id, exc_info=True)
        self.store.prune_health_samples(now - timedelta(hours=self.cfg.keep_samples_hours))
        try:
            self.m.retirer.purge_expired_exports(now)  # retired products' exports past their retention
        except Exception:  # noqa: BLE001 - the watch goes on; the next pass tries again
            log.warning("operate: expired exports could not be deleted", exc_info=True)

    async def run(self) -> None:
        await self.sweep_drills()
        self.note_restore()
        while True:
            await self.tick()
            # an incident being responded to is read every few seconds, so restored service is seen quickly
            await asyncio.sleep(self.cfg.respond_probe_seconds if self._urgent else self.cfg.probe_seconds)

    def start(self) -> None:
        """The watch runs in a live factory only: dry-run has nothing to read."""
        if not self.cfg.enabled or getattr(self.m.settings, "factory_mode", "live") != "live":
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="operate-watch")

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._jobs) if t is not None and not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._task = None

    # ----------------------------------------------------------------- view --
    def view(self, product: Product, now: datetime | None = None) -> OperationsView:
        now = now or _now()
        why = self.why_not(product)
        op = self.pack(product)
        target = self.target(product) if op is not None else None
        items = self.store.list_work_items(product.id)
        records = self.store.list_operations(product.id, None, 60)

        def last(kind: str, ok_only: bool = False) -> OperationRecord | None:
            return next((r for r in records if r.kind == kind and (r.ok or not ok_only)), None) or next(
                (r for r in self.store.list_operations(product.id, kind, 20) if r.ok or not ok_only), None
            )

        av = AvailabilityView()
        if target is not None:
            floor = op.availability.success if op is not None else None
            av = AvailabilityView(
                target=target.text(),
                success=target.success,
                max_error_rate=target.max_error_rate,
                floor=floor,
                chosen_by="product" if product.availability is not None else "pack",
            )
            if why is None:
                a = self.availability(product, now)
                rows = self.store.list_health_samples(product.id, now - timedelta(hours=self.cfg.keep_samples_hours))
                newest = rows[-1].at if rows else None
                av.state = a.state  # type: ignore[assignment]
                av.samples, av.measured, av.error_rate, av.reasons = a.samples, a.success, a.error_rate, a.reasons
                av.last_at = newest
                av.watching = self.cfg.enabled and operate.fresh(newest, self.cfg.probe_seconds, now)
        good_restore = last("restore", ok_only=True)
        good_backup = last("backup", ok_only=True)
        mode, mode_why = self.response_mode(product)
        return OperationsView(
            product_id=product.id,
            owner=product.owner or product.created_by or None,
            owner_named=bool(product.owner),
            live=why is None,
            why_not=why,
            availability=av,
            incidents=[i for i in items if i.category == ledger.INCIDENT and i.status in ledger.OPEN],
            data_declared=op is not None and op.data is not None,
            last_backup=last("backup"),
            last_restore=last("restore"),
            restore_proven=good_restore is not None,
            restore_overdue=operate.restore_due(
                good_restore.at if good_restore else None,
                good_backup.at if good_backup else None,
                self.cfg.restore_drill_every_days,
                now,
            ),
            backups=len(self.backups(product)),
            upkeep_declared=op is not None and op.upkeep is not None,
            last_upkeep=last("upkeep"),
            upkeep_items=[i for i in items if i.category == ledger.UPKEEP and i.status in ledger.OPEN],
            records=records,
            retirement=product.retirement,
            respond=ResponseMode(configured=self.cfg.respond, mode=mode, why=mode_why),
            responses=self.responses(product, items),
        )
