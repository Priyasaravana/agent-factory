"""The G0 baselines (vision G0, phase A item 2): the six measures the goals are set against, from the live runs so
far, recorded once in docs/outcomes.md. Pure: an OutcomesView in, a markdown section out.

`agent-factory baselines --write docs/outcomes.md` (`make baselines`) runs it on the install that did the live runs
and replaces the section between the markers, so the record says where and when it was taken and from how many
changes. Targets are the vision's (docs/planning/vision.md, "Targets to hold across the goals").
"""

from __future__ import annotations

from datetime import datetime

from agent_factory.models import DurationStat, OutcomesView

BEGIN = "<!-- baselines:begin (written by `agent-factory baselines`; don't edit by hand) -->"
END = "<!-- baselines:end -->"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _mins(s: float | None) -> str:
    if s is None:
        return "n/a"
    return f"{s / 60:.0f} min" if s < 3 * 3600 else f"{s / 3600:.1f} h"


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.2f}"


def _lead(d: DurationStat) -> str:
    return f"p50 {_mins(d.median_s)}, p90 {_mins(d.p90_s)}"


def section(v: OutcomesView, *, install: str, version: str, taken: datetime) -> str:
    """The baselines section: one row per G0 measure with its value, what it was computed from, and its target."""
    gw = v.gateway_cost_per_delivery_usd
    cost = _usd(v.cost_per_delivery_usd) + (f" (gateway: {_usd(gw)})" if gw is not None else " (no gateway meter)")
    req = _pct(v.requirements_verified_live / v.requirements_total) if v.requirements_total else "n/a"
    lvl3 = _pct(v.products_level3 / v.products) if v.products else "n/a"
    rows = [
        ("Autonomy (no unplanned human touch)", _pct(v.autonomy_ratio), f"{v.deliveries} deliveries", "≥ 80%"),
        ("Change failure rate", _pct(v.change_failure_rate),
         f"{v.finished} finished, {v.failed_or_rescued} failed or rescued", "≤ 15%"),
        ("Lead time", _lead(v.lead_time), f"{v.lead_time.n} deliveries", "p50 ≤ 60 min (feature change)"),
        ("Cost per delivered change, failures included", cost,
         f"${v.cost_total_usd:.2f} over {v.deliveries} deliveries", "≤ $10, both meters agreeing"),
        ("Requirements verified live", req,
         f"{v.requirements_verified_live} of {v.requirements_total}, live products' latest delivery", "100%"),
        ("Share of live products at Level 3", lvl3, f"{v.products_level3} of {v.products} live products", "100%"),
    ]  # fmt: skip
    lines = [
        BEGIN,
        "## Baselines (G0)",
        "",
        f"Recorded {taken:%Y-%m-%d %H:%M} UTC on {install} (factory {version}), over the last {v.window_days} days "
        f"(since {v.since:%Y-%m-%d}): every change in the store, evaluation products excluded. The goals' targets "
        "are set against these; re-record them only deliberately (a new baseline is a decision, not a refresh).",
        "",
        "| Measure | Baseline | From | Target |",
        "|---|---|---|---|",
        *[f"| {m} | {val} | {src} | {t} |" for m, val, src, t in rows],
        "",
        f"Also recorded: {v.deliveries_per_week:.1f} deliveries a week; recovery time of held changes "
        f"{_lead(v.recovery_time)}; {v.fix_loops_per_delivery or 0:.1f} fix loops per delivery; "
        f"{v.unplanned_touches_per_delivery or 0:.2f} unplanned touches per delivery.",
        END,
    ]
    return "\n".join(lines) + "\n"


def write_into(doc: str, new: str) -> str:
    """`doc` with its baselines section replaced by `new`, or `new` added before "## Using it" (else at the end)."""
    if BEGIN in doc and END in doc:
        head, rest = doc.split(BEGIN, 1)
        return head + new.rstrip("\n") + rest.split(END, 1)[1]
    marker = "\n## Using it"
    if marker in doc:
        head, tail = doc.split(marker, 1)
        return head.rstrip("\n") + "\n\n" + new + marker + tail
    return doc.rstrip("\n") + "\n\n" + new
