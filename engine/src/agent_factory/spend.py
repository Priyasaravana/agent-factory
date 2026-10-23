"""What a product has cost so far, by phase and by station (pure).

Spend is model spend: every agent call records its cost (ADR-0022). Tests, scans, builds and
deploys run on the factory's own machine and cost nothing extra today, so they show as $0 until
a priced provider (a cloud runner, Snyk, a registry) reports a cost.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agent_factory import contract

# stations the engine runs outside the lane (no phase column of their own)
ENGINE_STATIONS = {"retro": ("learn", "Learning")}
OTHER = ("other", "Other")


def _phase(station: str, columns: Mapping[str, str]) -> tuple[str, str]:
    if station in columns:
        col = columns[station]
        return col, dict(contract.COLUMNS).get(col, col.replace("-", " ").capitalize())
    return ENGINE_STATIONS.get(station, OTHER)


def product_spend(
    calls: Iterable[tuple[str, str, float]],
    columns: Mapping[str, Mapping[str, str]],
    change_totals: Mapping[str, float],
) -> dict[str, Any]:
    """`calls`: (change id, station, cost) of every agent call; `columns`: each change's station ->
    phase column (its pinned lane version); `change_totals`: each change's recorded cost.
    Phases come out in lifecycle order. A change whose recorded cost is more than its calls (spend
    from before calls were recorded) shows the difference under Other, so the parts add up."""
    phases: dict[str, dict[str, Any]] = {}
    per_change: dict[str, float] = {}
    for change_id, station, cost in calls:
        pid, title = _phase(station, columns.get(change_id, {}))
        p = phases.setdefault(pid, {"phase": pid, "title": title, "cost_usd": 0.0, "stations": {}})
        p["cost_usd"] += cost
        p["stations"][station] = p["stations"].get(station, 0.0) + cost
        per_change[change_id] = per_change.get(change_id, 0.0) + cost
    for change_id, total in change_totals.items():
        gap = total - per_change.get(change_id, 0.0)
        if gap > 0.005:
            p = phases.setdefault(OTHER[0], {"phase": OTHER[0], "title": OTHER[1], "cost_usd": 0.0, "stations": {}})
            p["cost_usd"] += gap
    order = [*contract.COLUMN_IDS, "learn", OTHER[0]]
    rows = sorted(phases.values(), key=lambda p: order.index(p["phase"]) if p["phase"] in order else len(order))
    for p in rows:
        p["cost_usd"] = round(p["cost_usd"], 4)
        p["stations"] = [
            {"station": s, "cost_usd": round(c, 4)} for s, c in sorted(p["stations"].items(), key=lambda kv: -kv[1])
        ]
    return {
        "total_usd": round(sum(p["cost_usd"] for p in rows), 4),
        "changes": len(change_totals),
        "phases": rows,
    }
