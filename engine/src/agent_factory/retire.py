"""End of life (roadmap J5), the pure part: the steps of a retirement, the product's history from
its first request to its decommission, and the decommission record that is sealed last.

A retirement is asked for by a named person with a reason and done in fixed steps, each recorded on
the product as it finishes: nothing new starts (freeze), the data is exported one last time, the
app leaves the cluster, the repository is archived, and the record is sealed. A step that fails
stops the retirement there; asking again continues at that step, never from the start.

`engine/retirement.py` acts; these functions decide and describe.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from agent_factory.models import (
    Change,
    HistoryEntry,
    OperationRecord,
    Product,
    Retirement,
    RetireStep,
    WorkItem,
)

SCHEMA = "agent-factory/decommission-record/v1"
STEPS: tuple[tuple[str, str], ...] = (
    ("freeze", "Freeze: nothing new starts on the product"),
    ("export", "Export the product's data one last time"),
    ("undeploy", "Remove the app from the cluster"),
    ("archive-repo", "Archive the product's repository"),
    ("record", "Seal the decommission record"),
)
# ledger categories whose history is the product's own story after delivery
OPERATED = ("incident", "upkeep")


def confirm_problem(product: Product, typed: str) -> str | None:
    """Retiring can't be undone: the person types the product's name (its title or slug). Pure."""
    said = typed.strip().casefold()
    if said in (product.title.strip().casefold(), product.slug.casefold()):
        return None
    return f"type the product's name ('{product.title}') to confirm: retiring can't be undone"


def begin(by: str, reason: str, owner: str | None, now: datetime) -> Retirement:
    return Retirement(
        requested_by=by,
        reason=reason.strip(),
        owner=owner,
        started_at=now,
        steps=[RetireStep(id=i, title=t) for i, t in STEPS],
    )


def next_step(r: Retirement) -> RetireStep | None:
    """The first step that isn't done or skipped: where a retirement starts, or continues. Pure."""
    return next((s for s in r.steps if s.status in ("pending", "failed")), None)


def mark(r: Retirement, step_id: str, status: str, detail: str, now: datetime) -> RetireStep:
    step = next(s for s in r.steps if s.id == step_id)
    step.status, step.detail, step.at = status, detail[:1000], now  # type: ignore[assignment]
    return step


def _first_line(text: str | None, limit: int = 200) -> str:
    return ((text or "").strip().split("\n", 1)[0])[:limit]


def timeline(  # noqa: PLR0913 - everything that is stored about one product
    product: Product,
    changes: Iterable[Change],
    seals: Mapping[str, str],
    items: Iterable[WorkItem],
    operations: Iterable[OperationRecord],
) -> list[HistoryEntry]:
    """The product's history, oldest first, from what is stored: its request, every change with its
    outcome and sealed evidence (`seals`: change id -> manifest sha256), incidents and upkeep from the
    work ledger, what the factory did for it (backups, restore drills, upkeep checks, alerts), and
    its retirement. Pure."""
    out: list[HistoryEntry] = [
        HistoryEntry(
            at=product.created_at,
            kind="requested",
            title=f"Product requested: {product.title}",
            detail=_first_line(product.requirements),
            by=product.created_by,
        )
    ]
    for c in sorted(changes, key=lambda c: c.iteration):
        asked = _first_line(c.change_request) or ("first build" if c.iteration == 1 else "")
        out.append(
            HistoryEntry(
                at=c.created_at,
                kind="change",
                title=f"Iteration {c.iteration} ({c.kind}) started",
                detail=asked,
                by=c.requested_by,
                change_id=c.id,
            )
        )
        out.append(
            HistoryEntry(
                at=c.updated_at,
                kind="delivered" if str(c.status) == "awaiting_feedback" else "stopped",
                title=f"Iteration {c.iteration}: "
                + ("delivered" if str(c.status) == "awaiting_feedback" else str(c.status).replace("_", " ")),
                detail=_first_line(c.summary),
                change_id=c.id,
                evidence=seals.get(c.id),
            )
        )
        if c.outcome is not None:
            out.append(
                HistoryEntry(
                    at=c.outcome.at,
                    kind=c.outcome.state,
                    title=f"Iteration {c.iteration} {c.outcome.state}",
                    detail=_first_line(c.outcome.note),
                    by=c.outcome.by,
                    change_id=c.id,
                )
            )
    for it in items:
        if it.category not in OPERATED:
            continue
        for ev in it.history:
            out.append(
                HistoryEntry(
                    at=ev.at,
                    kind=it.category,
                    title=f"{it.category.capitalize()} {ev.event}: {it.title.removeprefix('Incident: ')}",
                    detail=_first_line(ev.note),
                    by=ev.by,
                    change_id=ev.change_id,
                )
            )
    ret = product.retirement
    for r in operations:
        if r.kind in ("alert", "recovered") or (r.kind == "retire" and r.data.get("step") == "record"):
            continue  # the incident's own history, and the decommission entry, say it
        if r.kind == "backup" and ret is not None and r.at >= ret.started_at:
            continue  # the final export: its retirement step says it
        if r.kind == "retire":
            status = str(r.data.get("status") or ("done" if r.ok else "failed"))
            out.append(
                HistoryEntry(
                    at=r.at,
                    kind="retire",
                    title=f"Retirement: {r.data.get('title') or r.data.get('step')}"
                    + ("" if status == "done" else f" ({status})"),
                    detail=_first_line(r.summary),
                    by=r.by,
                )
            )
            continue
        out.append(
            HistoryEntry(
                at=r.at,
                kind=r.kind,
                title={
                    "backup": "Backup",
                    "restore": "Restore drill",
                    "upkeep": "Upkeep check",
                    "retire": "Retirement",
                    "owner": "Owner",
                }.get(r.kind, r.kind)
                + ("" if r.ok else " (failed)"),
                detail=_first_line(r.summary),
                by=r.by,
            )  # fmt: skip
        )
    if product.retirement is not None and product.retirement.done:
        r2 = product.retirement
        assert r2.finished_at is not None  # noqa: S101 - `done`
        out.append(
            HistoryEntry(
                at=r2.finished_at,
                kind="decommissioned",
                title="Product decommissioned",
                detail=r2.reason,
                by=r2.requested_by,
                evidence=r2.record_sha256,
            )
        )
    return sorted(out, key=lambda e: e.at)


def record(  # noqa: PLR0913 - the record names everything it stands on
    product: Product,
    retirement: Retirement,
    changes: list[Change],
    evidence: Mapping[str, Mapping[str, Any]],
    export: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """The decommission record's facts (the manifest lists and hashes the files beside it).
    `evidence`: per change id, its sealed manifest's sha256 and whether the folder still matches it.
    No requirement text, data or secret is in here: names, ids, hashes and what was done. Pure."""
    delivered = [c for c in changes if c.outcome is not None or str(c.status) == "awaiting_feedback"]
    return {
        "decommission": SCHEMA,
        "product": {
            "id": product.id,
            "title": product.title,
            "slug": product.slug,
            "blueprint": product.blueprint,
            "pack": product.pack,
            "created_at": product.created_at.isoformat(),
            "created_by": product.created_by,
            "repo_url": product.repo_url,
        },
        "owner": retirement.owner,
        "retired_by": retirement.requested_by,
        "reason": retirement.reason,
        "started_at": retirement.started_at.isoformat(),
        "finished_at": now.isoformat(),
        "steps": [s.model_dump(mode="json") for s in retirement.steps],
        "changes": [
            {
                "id": c.id,
                "iteration": c.iteration,
                "kind": str(c.kind),
                "status": str(c.status),
                "outcome": c.outcome.state if c.outcome else None,
                "evidence": dict(evidence.get(c.id) or {"sealed": False}),
            }
            for c in sorted(changes, key=lambda c: c.iteration)
        ],
        "totals": {"changes": len(changes), "deliveries": len(delivered)},
        "data_export": dict(export) if export else None,
    }
