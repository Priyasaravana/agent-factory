"""What a delivered product is ready for (master plan principle 7, roadmap J2; decided 2026-10-31).

Every delivered product states its tier, **demo**, **pilot** or **production**, proved by the sealed
evidence and never by an agent's claim, plus what the next tier still needs:

- **demo**: delivered, agent-readiness Level 3, and every requirement verified live by the hidden
  scenarios (a requirement with a stated reason it can't be checked live counts when it has tests,
  and is named);
- **pilot**: demo, plus a named owner (accountable for incidents, upkeep and retirement) and a
  rollback proven on this product (production restored to the recorded release and verified);
- **production**: pilot, plus an availability target, an alert on it that is running, and a tested
  restore of its data (J4, `operate.production_proof`): the target is the stack pack's, tightened by
  the product; the alert counts while the Operate watch is reading the product; the restore counts
  once a drill restored a backup and the data matched (a stack that declares no data has nothing to
  restore, and the statement says so).

`assess` is pure. `gather` reads a delivered change's evidence (`readiness.json`,
`traceability.json`) and the product's rollback events, so the handover and the product page say
the same thing; the owner can change later, so the page works it out again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory import operate

if TYPE_CHECKING:
    from agent_factory.models import Change, Product
    from agent_factory.state.base import StateStore

TIERS = ("none", "demo", "pilot", "production")
LEVEL = 3
# what production needs when nothing about running the product is known (no stack pack)
PRODUCTION_NEEDS = [
    "an availability target for the product",
    "an alert when it is missed",
    "a tested restore of its data",
]


@dataclass(frozen=True)
class Inputs:
    delivered: bool
    level: int | None  # agent readiness level at the delivered change; None: not scored
    trace: list[dict[str, Any]] | None  # traceability rows; None: none recorded
    owner: str | None
    rollback: str | None  # the change whose verified rollback proves it; None: never proven
    # run and maintain (J4, `production`): target, watched, alert_proven, restore, data_declared;
    # None: nothing is known about running it
    production: dict[str, Any] | None = None


@dataclass
class ReadyFor:
    tier: str
    proven: list[str] = field(default_factory=list)  # what the evidence shows, in plain words
    next_tier: str | None = None
    needs: list[str] = field(default_factory=list)  # what the next tier still needs
    unchecked_live: list[str] = field(default_factory=list)  # requirements tested, not checkable live

    def headline(self) -> str:
        what = "not ready for a demo yet" if self.tier == "none" else f"ready for: {self.tier}"
        return what + (f"; {self.next_tier} needs: {'; '.join(self.needs)}" if self.next_tier else "")

    def as_data(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "proven": self.proven,
            "next_tier": self.next_tier,
            "needs": self.needs,
            "unchecked_live": self.unchecked_live,
        }


def _live(row: dict[str, Any]) -> str:
    holdout = row.get("holdout") or []
    if holdout and all(h.get("passed") for h in holdout):
        return "verified"
    if not holdout and row.get("no_live_check") and (row.get("tests") or 0) > 0:
        return "tested"
    return "open"


def assess(i: Inputs) -> ReadyFor:
    """The tier the evidence proves and what the next one needs. Pure."""
    demo_needs: list[str] = []
    proven: list[str] = []
    if not i.delivered:
        demo_needs.append("a delivered change")
    if i.level is None:
        demo_needs.append("an agent-readiness score")
    elif i.level < LEVEL:
        demo_needs.append(f"agent-readiness Level {LEVEL} (now Level {i.level})")
    else:
        proven.append(f"agent-readiness Level {i.level}")
    rows = i.trace or []
    states = {str(r.get("id")): _live(r) for r in rows}
    open_ = [k for k, v in states.items() if v == "open"]
    tested = [k for k, v in states.items() if v == "tested"]
    if not rows:
        demo_needs.append("requirements traced to hidden scenarios on the live app")
    elif open_:
        demo_needs.append(f"every requirement verified live ({', '.join(open_)} not yet)")
    else:
        verified = len(rows) - len(tested)
        proven.append(
            f"{verified}/{len(rows)} requirements verified live"
            + (f", {len(tested)} tested where a live check isn't possible" if tested else "")
        )
    pilot_needs: list[str] = []
    if i.owner:
        proven.append(f"owned by {i.owner}")
    else:
        pilot_needs.append("a named owner")
    if i.rollback:
        proven.append(f"rollback proven (change {i.rollback})")
    else:
        pilot_needs.append("a rollback proven on this product")
    if demo_needs:
        return ReadyFor("none", proven, "demo", demo_needs, tested)
    if pilot_needs:
        return ReadyFor("demo", proven, "pilot", pilot_needs, tested)
    if i.production is None:
        return ReadyFor("pilot", proven, "production", list(PRODUCTION_NEEDS), tested)
    p = i.production
    restore = p.get("restore") or (None if p.get("data_declared") else "the stack declares no data to restore")
    has, needs = operate.production_proof(p.get("target"), bool(p.get("watched")), p.get("alert_proven"), restore)
    if needs:
        return ReadyFor("pilot", proven + has, "production", needs, tested)
    return ReadyFor("production", proven + has, None, [], tested)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def proven_rollback(store: StateStore, product_id: str) -> str | None:
    """The latest change of this product whose rollback restored production and was verified."""
    for change in sorted(store.list_changes(product_id), key=lambda c: c.created_at, reverse=True):
        for e in store.list_events(change.id):
            rb = e.data.get("rollback") if isinstance(e.data, dict) else None
            if isinstance(rb, dict) and rb.get("ok") is True:
                return change.id
    return None


def owner_of(product: Product) -> str | None:
    """The named owner: set by an admin, else the person who created the product (master plan §3J)."""
    return product.owner or product.created_by or None


def production(store: StateStore, product: Product, pack: Any, cfg: Any, now: datetime | None = None) -> dict[str, Any]:
    """What was really measured about running this product (J4), from the store: its target (the
    pack's `stack.operate`, tightened by the product), whether the watch is reading it now, the
    last alert, and the last restore drill that passed. `cfg`: the factory's `operate` settings."""
    now = now or datetime.now(UTC)
    if pack is None:
        return {"target": None, "watched": False, "alert_proven": None, "restore": None, "data_declared": False}
    a = pack.availability
    target = operate.Target(a.success, a.window_minutes, a.min_samples, a.max_error_rate)
    if product.availability is not None:
        target = operate.tighten(target, product.availability.success, product.availability.max_error_rate)[0]
    recent = store.list_health_samples(product.id, now - timedelta(seconds=max(120, 3 * cfg.probe_seconds)))
    alert = store.list_operations(product.id, "alert", 1)
    drill = next((r for r in store.list_operations(product.id, "restore", 20) if r.ok), None)
    return {
        "target": target.text(),
        "watched": bool(cfg.enabled and recent and product.retirement is None and not product.archived_at),
        "alert_proven": f"it fired on {alert[0].at:%Y-%m-%d}" if alert else None,
        "restore": f"drill on {drill.at:%Y-%m-%d}" if drill else None,
        "data_declared": pack.data is not None,
    }


def gather(  # noqa: PLR0913 - the evidence and what is known about running the product
    store: StateStore,
    data_dir: Path,
    product: Product,
    change: Change,
    *,
    delivered: bool,
    running: dict[str, Any] | None = None,
) -> Inputs:
    folder = data_dir / "artifacts" / change.id
    readiness = _read(folder / "readiness.json")
    level = readiness.get("level") if isinstance(readiness, dict) else None
    trace = _read(folder / "traceability.json")
    return Inputs(
        delivered=delivered,
        level=level if isinstance(level, int) else None,
        trace=trace if isinstance(trace, list) else None,
        owner=owner_of(product),
        rollback=proven_rollback(store, product.id),
        production=running,
    )


def moved(before: str | None, after: str | None) -> dict[str, str] | None:
    """UX-22: a delivery that changes the product's address says so, old and new. Pure."""
    if before and after and before.rstrip("/") != after.rstrip("/"):
        return {"from": before, "to": after}
    return None
