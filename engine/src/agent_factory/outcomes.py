"""Outcome metrics (ADR-0019): is the factory delivering, how autonomously, at what
cost, and who is it waiting on?

`gather()` reads the store; `compute()` is a pure function of those facts, so every
definition is tested exactly (tests/test_outcomes.py). Definitions, in plain words,
are in docs/outcomes.md and in the UI next to each number.

Time comes from the status transition log (`run_transitions`). Changes from before that
log existed still count for deliveries (their "delivered" event gives the time),
cost and quality, but not for the time split; `runs_with_timeline` says how many do.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_factory.hostclock import merge, overlap_s
from agent_factory.models import (
    AppQuality,
    Change,
    ChangeStatus,
    DurationStat,
    HumanTouches,
    OutcomesView,
    PillarScore,
    Product,
    ProductTarget,
    StationEffort,
    TimeSplit,
    Transition,
    WaitingItem,
    WeekPoint,
    WorkflowOutcome,
)
from agent_factory.pillars import BY_ID, IDS, tally
from agent_factory.readiness import PILLAR_OF
from agent_factory.state.base import StateStore
from agent_factory.workflow import LEGACY_HANDLERS

AGENTS = {ChangeStatus.queued, ChangeStatus.running}
PERSON = {
    ChangeStatus.needs_input,
    ChangeStatus.held,
    ChangeStatus.awaiting_approval,
    ChangeStatus.awaiting_risk_approval,
    ChangeStatus.interrupted,
}
SYSTEM = {ChangeStatus.paused_limits}
DONE = {ChangeStatus.awaiting_feedback, ChangeStatus.cancelled, ChangeStatus.failed}
# leaving one of these states means a person acted; spec review is a planned touch
UNPLANNED = {
    ChangeStatus.needs_input: "answered_questions",
    ChangeStatus.held: "rescued",
    ChangeStatus.interrupted: "restarts",
}
PLANNED = {ChangeStatus.awaiting_approval: "spec_reviews", ChangeStatus.awaiting_risk_approval: "risk_approvals"}

ACTIONS = {
    ChangeStatus.needs_input: "Answer the intake questions",
    ChangeStatus.held: "Read the evidence, then resume or change the request",
    ChangeStatus.awaiting_approval: "Review the spec: approve, edit or request changes",
    ChangeStatus.awaiting_risk_approval: "Risky changes: an admin other than the requester approves or sends them back",
    ChangeStatus.interrupted: "Resume the change (the factory restarted)",
    ChangeStatus.paused_limits: "Nothing: resumes when the model usage window resets",
}
DELIVERED_EVENT = "delivered"  # message prefix of the delivery event (fallback for runs without a timeline)


@dataclass
class Facts:
    now: datetime
    days: int
    products: dict[str, Product]
    changes: list[Change]
    transitions: dict[str, list[Transition]]
    delivered_fallback: dict[str, datetime] = field(default_factory=dict)  # runs without a timeline
    readiness_level: dict[str, int] = field(default_factory=dict)  # run id -> Level
    readiness_signals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # run id -> signals
    traceability: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # run id -> matrix rows
    calls: list[tuple[str, datetime, dict[str, Any]]] = field(default_factory=list)  # (run, time, agent_call)
    denials: list[tuple[str, datetime, dict[str, Any]]] = field(default_factory=list)  # (run, time, denied)
    pauses: list[tuple[datetime, datetime]] = field(default_factory=list)  # host suspended (hostclock.py)

    @property
    def since(self) -> datetime:
        return self.now - timedelta(days=self.days)


# ------------------------------------------------------------------ helpers --
def _stat(seconds: Iterable[float]) -> DurationStat:
    xs = sorted(s for s in seconds if s >= 0)
    if not xs:
        return DurationStat()
    p90 = xs[min(len(xs) - 1, max(0, round(0.9 * len(xs)) - 1))]
    return DurationStat(median_s=statistics.median(xs), p90_s=p90, n=len(xs))


def _ratio(num: int | float, den: int | float) -> float | None:
    return round(num / den, 4) if den else None


def delivered_at(change: Change, f: Facts) -> datetime | None:
    for t in f.transitions.get(change.id, []):
        if t.to_status == ChangeStatus.awaiting_feedback:
            return t.ts
    return f.delivered_fallback.get(change.id)


def finished_at(change: Change, f: Facts) -> datetime | None:
    for t in f.transitions.get(change.id, []):
        if t.to_status in DONE:
            return t.ts
    if change.status in DONE and change.id not in f.transitions:
        return delivered_at(change, f) or change.updated_at
    return None


def _first(change: Change, f: Facts, status: ChangeStatus) -> datetime | None:
    return next((t.ts for t in f.transitions.get(change.id, []) if t.to_status == status), None)


def _touches(change: Change, f: Facts, since: datetime | None = None) -> HumanTouches:
    h = HumanTouches()
    for t in f.transitions.get(change.id, []):
        if since and t.ts < since:
            continue
        kind = UNPLANNED.get(t.from_status) or PLANNED.get(t.from_status)  # type: ignore[arg-type]
        if kind and t.to_status in AGENTS:
            setattr(h, kind, getattr(h, kind) + 1)
    return h


def _unplanned(h: HumanTouches) -> int:
    return h.answered_questions + h.rescued + h.restarts


def _split(change: Change, f: Facts, start: datetime, end: datetime) -> TimeSplit:
    """Seconds this change spent per bucket, clipped to [start, end]."""
    out = TimeSplit()
    ts = f.transitions.get(change.id, [])
    for i, t in enumerate(ts):
        seg_end = ts[i + 1].ts if i + 1 < len(ts) else (end if t.to_status not in DONE else t.ts)
        a, b = max(t.ts, start), min(seg_end, end)
        if b <= a:
            continue
        asleep = overlap_s(a, b, f.pauses)
        out.suspended_s += asleep
        secs = (b - a).total_seconds() - asleep
        if t.to_status in AGENTS:
            out.agents_s += secs
        elif t.to_status in PERSON:
            out.person_s += secs
        elif t.to_status in SYSTEM:
            out.system_s += secs
    return out


def _owner(product: Product | None, status: ChangeStatus) -> str:
    if status in SYSTEM:
        return "system"
    return (product.created_by if product and product.created_by else None) or "an admin"


# ------------------------------------------------------------------ compute --
def compute(f: Facts) -> OutcomesView:
    since, now = f.since, f.now
    live_products = {oid: o for oid, o in f.products.items() if not o.archived_at}

    delivered = [(r, d) for r in f.changes if (d := delivered_at(r, f)) and since <= d <= now]
    deliveries = len(delivered)
    lead = _stat((d - r.created_at).total_seconds() for r, d in delivered)

    # change failure rate: of the iterations that finished (delivered or failed) in the
    # window, the share that failed or needed a person to rescue them from "held"
    finished = [
        r for r in f.changes if (fa := finished_at(r, f)) and since <= fa <= now and r.status != ChangeStatus.cancelled
    ]
    failed_or_rescued = [r for r in finished if r.status == ChangeStatus.failed or _first(r, f, ChangeStatus.held)]
    recovery = _stat(
        (d - held).total_seconds() for r, d in delivered if (held := _first(r, f, ChangeStatus.held)) and held < d
    )

    # autonomy: deliveries that needed no unplanned human touch in their whole life
    per_change_touches = {r.id: _touches(r, f) for r, _ in delivered}
    autonomous = sum(1 for r, _ in delivered if _unplanned(per_change_touches[r.id]) == 0)

    in_window = [r for r in f.changes if r.created_at >= since or (finished_at(r, f) or now) >= since]
    touches = HumanTouches()
    for r in in_window:
        h = _touches(r, f, since)
        for k in HumanTouches.model_fields:
            setattr(touches, k, getattr(touches, k) + getattr(h, k))

    created = [r for r in f.changes if since <= r.created_at <= now]
    cost_total = round(sum(r.cost_usd for r in created), 4)

    split = TimeSplit()
    with_timeline = [r for r in in_window if r.id in f.transitions]
    for r in with_timeline:
        s = _split(r, f, since, now)
        split.agents_s += s.agents_s
        split.person_s += s.person_s
        split.system_s += s.system_s
        split.suspended_s += s.suspended_s

    # quality of what is live: each product's latest delivered iteration
    products = level3 = req_total = req_live = 0
    latest_delivered: dict[str, Change] = {}
    for r in f.changes:
        if r.product_id in live_products and delivered_at(r, f):
            cur = latest_delivered.get(r.product_id)
            if cur is None or r.iteration > cur.iteration:
                latest_delivered[r.product_id] = r
    products = len(live_products)
    for r in latest_delivered.values():
        if f.readiness_level.get(r.id, 0) >= 3:
            level3 += 1
        for row in f.traceability.get(r.id, []):
            req_total += 1
            hold = row.get("holdout") or []
            if hold and all(h.get("passed") is True for h in hold):
                req_live += 1

    waiting = []
    for r in f.changes:
        if r.status in PERSON | SYSTEM and r.product_id in live_products:
            product = live_products[r.product_id]
            last = f.transitions.get(r.id, [])
            since_state = last[-1].ts if last else r.updated_at
            waiting.append(
                WaitingItem(
                    product_id=product.id,
                    product_title=product.title,
                    change_id=r.id,
                    iteration=r.iteration,
                    status=r.status,
                    since=since_state,
                    waiting_s=max(0.0, (now - since_state).total_seconds()),
                    owner=_owner(product, r.status),
                    action=ACTIONS[r.status],
                )
            )
    waiting.sort(key=lambda w: (w.owner == "system", -w.waiting_s))

    by_wf: dict[str, list[tuple[Change, datetime]]] = {}
    for r, d in delivered:
        wf = r.workflow_id or (f.products[r.product_id].blueprint if r.product_id in f.products else "unknown")
        by_wf.setdefault(wf, []).append((r, d))
    workflows = []
    for wf, items in sorted(by_wf.items()):
        runs_wf = {r.id for r, _ in items}
        cost_wf = sum(
            r.cost_usd
            for r in created
            if (r.workflow_id or (f.products[r.product_id].blueprint if r.product_id in f.products else "")) == wf
        )
        workflows.append(
            WorkflowOutcome(
                workflow_id=wf,
                deliveries=len(items),
                autonomy_ratio=_ratio(
                    sum(1 for rid in runs_wf if _unplanned(per_change_touches[rid]) == 0), len(items)
                ),
                cost_per_delivery_usd=_ratio(cost_wf, len(items)),
                lead_time_median_s=_stat((d - r.created_at).total_seconds() for r, d in items).median_s,
            )
        )

    weeks = max(1, -(-f.days // 7))
    weekly = []
    for k in range(weeks - 1, -1, -1):
        a, b = now - timedelta(days=7 * (k + 1)), now - timedelta(days=7 * k)

        def in_week(ts: datetime, a: datetime = a, b: datetime = b, last: bool = k == 0) -> bool:
            return a <= ts < b or (last and ts == b)

        dels = [(r, d) for r, d in delivered if in_week(d)]
        weekly.append(
            WeekPoint(
                week_start=a.date().isoformat(),
                deliveries=len(dels),
                cost_usd=round(sum(r.cost_usd for r in created if in_week(r.created_at)), 4),
                lead_time_median_s=_stat((d - r.created_at).total_seconds() for r, d in dels).median_s,
            )
        )

    return OutcomesView(
        window_days=f.days,
        since=since,
        generated_at=now,
        deliveries=deliveries,
        deliveries_per_week=round(deliveries / (f.days / 7), 2),
        lead_time=lead,
        change_failure_rate=_ratio(len(failed_or_rescued), len(finished)),
        finished=len(finished),
        failed_or_rescued=len(failed_or_rescued),
        recovery_time=recovery,
        autonomy_ratio=_ratio(autonomous, deliveries),
        touches=touches,
        unplanned_touches_per_delivery=_ratio(
            sum(_unplanned(per_change_touches[r.id]) for r, _ in delivered), deliveries
        ),
        cost_total_usd=cost_total,
        cost_per_delivery_usd=_ratio(cost_total, deliveries),
        fix_loops_per_delivery=_ratio(sum(r.loops for r, _ in delivered), deliveries),
        time_split=TimeSplit(**{k: round(v, 1) for k, v in split.model_dump().items()}),
        changes_with_timeline=len(with_timeline),
        changes_in_window=len(in_window),
        products=products,
        products_level3=level3,
        requirements_total=req_total,
        requirements_verified_live=req_live,
        waiting=waiting,
        **_quality(f, latest_delivered, live_products),
        effort_by_station=_effort(f),
        guardrail_denials=sum(1 for _, ts, _d in f.denials if since <= ts <= now),
        by_workflow=workflows,
        weekly=weekly,
    )


def _quality(f: Facts, latest: dict[str, Change], live: dict[str, Product]) -> dict[str, Any]:
    """Pillar coverage of live apps' latest delivered iterations (ADR-0024)."""
    signals_per_pillar = Counter(PILLAR_OF.values())
    totals = {p: {"passed": 0, "applicable": 0, "apps_full": 0} for p in IDS}
    apps = []
    for oid, r in sorted(latest.items(), key=lambda kv: live[kv[0]].title.lower()):
        if r.id not in f.readiness_signals:
            continue  # delivered before the Quality gate station existed
        t = tally(f.readiness_signals[r.id], PILLAR_OF)
        scores = []
        for p in IDS:
            passed, appl = t[p]["passed"], t[p]["applicable"]
            totals[p]["passed"] += passed
            totals[p]["applicable"] += appl
            totals[p]["apps_full"] += 1 if appl and passed == appl else 0
            scores.append(
                PillarScore(
                    pillar=p,
                    title=BY_ID[p].title,
                    signals=signals_per_pillar.get(p, 0),
                    passed=passed,
                    applicable=appl,
                    coverage=_ratio(passed, appl),
                )
            )
        apps.append(AppQuality(product_id=oid, product_title=live[oid].title, change_id=r.id, pillars=scores))
    pillars = [
        PillarScore(
            pillar=p,
            title=BY_ID[p].title,
            signals=signals_per_pillar.get(p, 0),
            coverage=_ratio(totals[p]["passed"], totals[p]["applicable"]),
            **totals[p],
        )
        for p in IDS
    ]
    return {"quality_pillars": pillars, "quality_by_app": apps}


def _effort(f: Facts) -> list[StationEffort]:
    """Agent calls in the window, per station: how many, what they cost, how long (ADR-0022)."""
    by: dict[str, list[dict[str, Any]]] = {}
    for _, ts, c in f.calls:
        if f.since <= ts <= f.now:
            # calls are agent stations; legacy ids (ADR-0028) count under today's name
            st = str(c.get("station", "?"))
            by.setdefault(LEGACY_HANDLERS["agent"].get(st, st), []).append(c)
    out = [
        StationEffort(
            station=st,
            calls=len(cs),
            cost_usd=round(sum(float(c.get("cost_usd", 0)) for c in cs), 4),
            turns_median=_stat(float(c.get("turns", 0)) for c in cs).median_s,
            duration_median_s=_stat(float(c.get("duration_s", 0)) for c in cs).median_s,
            tool_calls=sum(int(c.get("tool_calls", 0)) for c in cs),
            denied=sum(int(c.get("denied", 0)) for c in cs),
        )
        for st, cs in by.items()
    ]
    return sorted(out, key=lambda e: -e.cost_usd)


# ------------------------------------------------------------------- gather --
def gather(store: StateStore, days: int, now: datetime | None = None) -> Facts:
    now = now or datetime.now(UTC)
    # evaluation products (ADR-0025) measure workflow versions, not the factory's real work;
    # assessments of existing repos (ADR-0031) deliver a report, not software
    products = {o.id: o for o in store.list_products() if not o.eval_run_id and o.target == ProductTarget.new}
    changes = [r for r in store.all_changes() if r.product_id in products]
    ids = {r.id for r in changes}
    transitions = {k: v for k, v in store.transitions().items() if k in ids}
    f = Facts(now=now, days=days, products=products, changes=changes, transitions=transitions)
    for r in changes:
        if r.id not in transitions and r.status == ChangeStatus.awaiting_feedback:
            t = store.first_event_time(r.id, DELIVERED_EVENT)
            if t:
                f.delivered_fallback[r.id] = t
    latest: dict[str, Change] = {}
    for r in changes:
        if r.product_id in products and not products[r.product_id].archived_at and delivered_at(r, f):
            if r.product_id not in latest or r.iteration > latest[r.product_id].iteration:
                latest[r.product_id] = r
    active = [r.id for r in changes if r.created_at >= f.since or r.updated_at >= f.since]
    f.calls = store.events_with_key(active, "agent_call")
    f.denials = store.events_with_key(active, "denied")
    f.pauses = merge(store.host_pauses(f.since))
    for r in latest.values():
        if got := store.last_event_data(r.id, "readiness"):
            f.readiness_level[r.id] = int(got[1].get("level", 0))
            f.readiness_signals[r.id] = list(got[1].get("signals", []))
        if got := store.last_event_data(r.id, "traceability"):
            f.traceability[r.id] = list(got[1])
    return f


def outcomes(store: StateStore, days: int = 30, now: datetime | None = None) -> OutcomesView:
    return compute(gather(store, days, now))
