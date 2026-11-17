"""End of life (roadmap J5): the part that acts. Rules and the record's shape are in `retire.py`.

`Retirer.retire` takes a product through its last steps and saves each one as it finishes, so a
step that fails holds the retirement there and the next call continues it. What remains of a
retired product is its history and the decommission record under `<data>/decommission/<product>/`:
sealed like a change's evidence (manifest and SHA256SUMS, `evidence.py`), naming every change's
sealed manifest, so the whole story, from first request to decommission, can be checked.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory import evidence, retire
from agent_factory.engine.operations import OperateError
from agent_factory.models import (
    Change,
    EventKind,
    OperationRecord,
    Product,
    ProductHistory,
    ProductTarget,
    Retirement,
)

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import ChangeManager

STATION = "retire"
RECORD = "record.json"


def _now() -> datetime:
    return datetime.now(UTC)


class Retirer:
    def __init__(self, manager: ChangeManager) -> None:
        self.m = manager
        self._locks: dict[str, asyncio.Lock] = {}  # one retirement at a time per product

    @property
    def store(self) -> Any:
        return self.m.store

    def folder(self, product: Product) -> Path:
        return Path(self.m.ws.data_dir) / "decommission" / product.id

    # -------------------------------------------------------------- history --
    def _seals(self, changes: list[Change]) -> dict[str, str]:
        out: dict[str, str] = {}
        for c in changes:
            seal = evidence.latest_seal(self.store, c.id)
            if seal and seal.get("sha256"):
                out[c.id] = str(seal["sha256"])
        return out

    def history(self, product: Product) -> ProductHistory:
        changes = self.store.list_changes(product.id)
        entries = retire.timeline(
            product,
            changes,
            self._seals(changes),
            self.store.list_work_items(product.id),
            self.store.list_operations(product.id, None, 5000),
        )
        r = product.retirement
        return ProductHistory(
            product_id=product.id,
            title=product.title,
            entries=entries,
            retired=bool(r and r.done),
            record_sha256=r.record_sha256 if r else None,
        )

    def verify(self, product: Product) -> dict[str, Any]:
        """The sealed decommission record, checked again: its facts, and whether the folder still
        matches the manifest and the manifest the digest stored on the product."""
        r = product.retirement
        folder = self.folder(product)
        v = evidence.verify(folder, r.record_sha256 if r else None)
        facts: dict[str, Any] = {}
        if (folder / RECORD).is_file():
            try:
                facts = json.loads((folder / RECORD).read_text())
            except ValueError:
                facts = {}
        return {
            "record": facts,
            "sealed": v.sealed,
            "intact": v.intact,
            "changed": v.changed,
            "missing": v.missing,
            "added": v.added,
            "manifest_sha256": r.record_sha256 if r else None,
        }

    # --------------------------------------------------------------- retire --
    def _log(self, product: Product, ok: bool, summary: str, by: str, data: dict[str, Any] | None = None) -> None:
        self.store.add_operation(
            OperationRecord(
                id=uuid.uuid4().hex[:12],
                product_id=product.id,
                kind="retire",
                at=_now(),
                ok=ok,
                summary=summary,
                by=by,
                data=data or {},
            )  # fmt: skip
        )

    def _save(self, product: Product, r: Retirement) -> Product:
        """The product as it is stored now, with the retirement's state (other steps save it too)."""
        fresh = self.store.get_product(product.id) or product
        fresh.retirement = r
        self.store.save_product(fresh)
        return fresh

    async def retire(
        self, product_id: str, by: str, reason: str, confirm: str, without_export: bool = False
    ) -> Product:
        """Retire a product, or continue a retirement that stopped. Refused while a change runs on
        it. Returns the product; `retirement.done` says whether every step finished. Requests for
        the same product run one after the other, and each reads the stored state only once it
        holds the lock, so a second request continues from where the first left the product, never
        from what it read before (and never saves stale steps over the first's)."""
        lock = self._locks.setdefault(product_id, asyncio.Lock())
        async with lock:
            return await self._retire(product_id, by, reason, confirm, without_export)

    async def _retire(self, product_id: str, by: str, reason: str, confirm: str, without_export: bool) -> Product:
        from agent_factory.engine.pipeline import FEEDBACK_OPEN, FactoryError, InvalidRequestError

        product = self.store.get_product(product_id)
        if not product:
            raise FactoryError("product not found")
        if product.retirement is not None and product.retirement.done:
            return product
        if problem := retire.confirm_problem(product, confirm):
            raise InvalidRequestError(problem)
        self.m._refuse_while_busy(product.id)
        changes = self.store.list_changes(product.id)
        if any(self.m.is_active(c.id) and c.status not in FEEDBACK_OPEN for c in changes):
            raise FactoryError("a change to this product is in progress: cancel it, or let it finish, first")
        if product.archived_at and product.retirement is None:
            raise FactoryError(
                "this product was archived before retirement existed: its app is already gone and its history is kept"
            )
        owner = product.owner or product.created_by or None
        r = product.retirement or retire.begin(by, reason, owner, _now())
        latest = max(changes, key=lambda c: c.iteration) if changes else None
        detail = ""

        async def step(step_id: str) -> tuple[str, str]:
            if step_id == "freeze":
                return "done", "no change can start on this product any more"
            if step_id == "export":
                return await self._export(product, by, without_export)
            if step_id == "undeploy":
                fresh = self.store.get_product(product.id) or product
                if fresh.archived_at:
                    return "done", "the app was already removed"
                await self.m.archive(product.id, by)
                return "done", "the app and its temporary environments were removed; its port is free"
            if step_id == "archive-repo":
                return await self._archive_repo(product, latest)
            return "done", self._seal(self.store.get_product(product.id) or product, r, changes)

        while (nxt := retire.next_step(r)) is not None:
            try:
                status, detail = await step(nxt.id)
            except (FactoryError, OperateError) as exc:
                status, detail = "failed", str(exc)
            except Exception as exc:  # noqa: BLE001 - recorded on the step; the next call continues there
                status, detail = "failed", f"{type(exc).__name__}: {exc}"
            retire.mark(r, nxt.id, status, detail, r.finished_at or _now())
            product = self._save(product, r)
            self._log(product, status != "failed", detail, by, {"step": nxt.id, "title": nxt.title, "status": status})
            if status == "failed":
                break
        if latest is not None:
            self.store.add_event(
                latest.id,
                EventKind.decision,
                (f"product retired by {by}: {r.reason}" if r.done else f"retirement by {by} stopped: {detail}"),
                station=STATION,
                data={"retired_by": by, "done": r.done, "record_sha256": r.record_sha256},
            )
        return product

    async def _export(self, product: Product, by: str, without: bool) -> tuple[str, str]:
        ops = self.m.operations
        op = ops.pack(product)
        if product.target != ProductTarget.new:
            return "skipped", "the factory holds no data of a team's repository"
        if op is None or op.data is None:
            return "skipped", "the product's stack declares no data"
        if product.release is None:
            return "skipped", "the product was never delivered: there is no data to export"
        if without:
            return "skipped", f"skipped by {by}: retired without a final data export"
        if product.id in ops._busy:
            raise OperateError(
                "a backup, restore drill or upkeep check of this product is running: continue in a minute"
            )
        ops._busy.add(product.id)
        try:
            # the retirement froze the product, which also stops the watch: export from its release directly
            rec = await ops._backup(product, ops._data_commands(product), by)
        except OperateError as exc:
            raise OperateError(
                f"the final data export failed ({exc}); fix it and continue, or retire without an export"
            ) from exc
        finally:
            ops._busy.discard(product.id)
        if not rec.ok:
            raise OperateError(f"{rec.summary}; fix it and continue, or retire without an export")
        # the export is the one backup that is kept: earlier ones go (the product's data isn't kept twice)
        keep = str(rec.data["file"])
        for old in ops.backup_dir(product).glob("*.backup"):
            if old.name != keep:
                old.unlink(missing_ok=True)
        return "done", f"exported {rec.data['size']} bytes (sha256 {str(rec.data['sha256'])[:12]}…) as {keep}"

    async def _archive_repo(self, product: Product, latest: Change | None) -> tuple[str, str]:
        if product.target != ProductTarget.new:
            return "skipped", "a team's repository is theirs: the factory never archives it"
        if not self.m.cfg.retire.archive_repo:
            return "skipped", "retire.archive_repo is off: the repository is left as it is"
        if not product.repo_url:
            return "skipped", "the product was never published: there is no repository to archive"
        if latest is None:
            return "skipped", "the product has no change to act from"
        fresh = self.store.get_product(product.id) or product
        ctx = self.m.operations.ctx(fresh)
        ctx.station_id = STATION
        archive = getattr(ctx.providers.publish, "archive_repo", None)
        if not callable(archive):
            return "skipped", "the publish integration can't archive a repository"
        res = await archive(ctx)
        if not res.ok:
            raise OperateError(f"{res.summary}. {res.detail}".strip())
        return "done", res.summary

    def _seal(self, product: Product, r: Retirement, changes: list[Change]) -> str:
        """Write the decommission record and seal its folder; returns what was sealed."""
        folder = self.folder(product)
        folder.mkdir(parents=True, exist_ok=True)
        data_dir = Path(self.m.ws.data_dir)
        ev: dict[str, dict[str, Any]] = {}
        for c in changes:
            seal = evidence.latest_seal(self.store, c.id)
            sha = str(seal["sha256"]) if seal and seal.get("sha256") else None
            v = evidence.verify(data_dir / "artifacts" / c.id, sha)
            ev[c.id] = {"sealed": v.sealed, "manifest_sha256": sha, "intact": v.intact}
        exports = [x for x in self.store.list_operations(product.id, "backup", 5) if x.ok]
        export_step = next(s for s in r.steps if s.id == "export")
        export = (
            {k: exports[0].data.get(k) for k in ("file", "size", "sha256", "fingerprint")}
            if exports and export_step.status == "done"
            else None
        )
        now = _now()
        # the record step is the one being finished: the record states it as done
        steps_done = r.model_copy(deep=True)
        retire.mark(steps_done, "record", "done", "sealed", now)
        facts = retire.record(product, steps_done, changes, ev, export, now)
        history = self.history(product)

        def dump(name: str, doc: Any) -> None:
            (folder / name).write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n")

        dump(RECORD, facts)
        dump("history.json", [e.model_dump(mode="json") for e in history.entries])
        dump("work-ledger.json", [i.model_dump(mode="json") for i in self.store.list_work_items(product.id)])
        dump(
            "operations.json",
            [o.model_dump(mode="json") for o in reversed(self.store.list_operations(product.id, None, 5000))],
        )
        manifest = evidence.build_manifest(
            folder,
            {"decommission": retire.SCHEMA, "product": product.id, "retired_by": r.requested_by},
            now,
        )
        r.record_sha256 = evidence.write_manifest(folder, manifest)
        r.finished_at = now  # the moment the record states
        intact = sum(1 for v in ev.values() if v["intact"])
        return (
            f"decommission record sealed (sha256 {r.record_sha256[:12]}…): {len(changes)} change(s), "
            f"{intact} with intact sealed evidence"
        )
