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

from agent_factory import base_images, image_scan, ledger, operate, validation
from agent_factory.engine.stations import StationContext, _lockfiles
from agent_factory.executor import CommandResult, OutputCutOff
from agent_factory.models import (
    AvailabilityView,
    Change,
    ChangeStatus,
    EventKind,
    HealthSample,
    OperationRecord,
    OperationsView,
    Product,
    ProductTarget,
)
from agent_factory.providers.base import ImageRef

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
SCRATCH_DATABASE = "restore_drill"
READY_TRIES = 20


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
        self, product: Product, kind: str, ok: bool, summary: str, by: str, data: dict[str, Any] | None = None
    ) -> OperationRecord:
        rec = OperationRecord.model_validate(
            {
                "id": uuid.uuid4().hex[:12],
                "product_id": product.id,
                "kind": kind,
                "at": _now(),
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

    async def watch(self, product: Product, now: datetime | None = None) -> operate.Availability | None:
        """Read the product once, judge the window, and keep the ledger in step: a breach opens (or
        reopens) the product's incident, a window within target closes it. None: not read (the
        factory doesn't run it, or a change is deploying to it right now)."""
        now = now or _now()
        if self.why_not(product) is not None or self._deploying(product):
            return None
        sample = await self.probe(product, now)
        self.store.add_health_sample(sample)
        self._probed[product.id] = now
        a = self.availability(product, now)
        self._fold_incident(product, a, now)
        return a

    def _fold_incident(self, product: Product, a: operate.Availability, now: datetime) -> None:
        target = self.target(product)
        if a.state == "unknown" or target is None:
            return  # too few readings: neither an alert nor proof of health
        items = self.store.list_work_items(product.id)
        change = self._release_change(product)
        candidate = operate.incident_candidate(a, target, product.app_url)
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
            self._record(product, "alert", False, f"outside its availability target: {why}", "factory",
                         {"incident": item.id, "owner": owner, **a.as_data()})  # fmt: skip
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
                         "factory", {"incident": item.id, **a.as_data()})  # fmt: skip
            self.store.add_event(
                change.id,
                EventKind.decision,
                f"the live product is within its availability target again; incident {item.id} closed",
                station=STATION,
                data={"incident": item.id, "availability": a.as_data()},
            )

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
        for _attempt in range(3):
            before = await self._fingerprint(data, ctx, data.database)
            res = await kube(
                ctx, self._exec(data, ctx, data.backup, data.database, f"> {shlex.quote(str(path))}"),
                "back the product's data up", 900,
            )  # fmt: skip
            size = path.stat().st_size if path.is_file() else 0
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
        picked = [*groups["security"], *groups["upkeep"]]
        if not picked:
            return None
        from agent_factory.engine.pipeline import FactoryError
        from agent_factory.models import ChangeSource

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
        for product in self.store.list_products():
            if self.why_not(product) is not None:
                continue
            try:
                last = self._probed.get(product.id)
                if last is None or (now - last).total_seconds() >= self.cfg.probe_seconds - 1:
                    await self.watch(product, now)
                if product.id not in self._busy and not self._deploying(product):
                    if wait:
                        await self._maintain(product, now)
                    else:
                        self._spawn(self._maintain(product, now))
            except Exception:  # noqa: BLE001 - one product never stops the watch of the others
                log.warning("operate: product %s could not be watched", product.id, exc_info=True)
        self.store.prune_health_samples(now - timedelta(hours=self.cfg.keep_samples_hours))

    async def run(self) -> None:
        await self.sweep_drills()
        while True:
            await self.tick()
            await asyncio.sleep(self.cfg.probe_seconds)

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
        )
