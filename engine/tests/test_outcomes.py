"""Outcomes (ADR-0019): the status transition log and every metric's definition."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import ORDER, wait_run
from test_api import _client

from agent_factory.models import Order, Run, RunStatus, Transition
from agent_factory.outcomes import Facts, compute
from agent_factory.state.sqlite import SqliteStateStore

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
T0 = NOW - timedelta(days=2)
S = RunStatus


def _t(run: str, secs: int, old: RunStatus | None, new: RunStatus) -> Transition:
    return Transition(run_id=run, ts=T0 + timedelta(seconds=secs), from_status=old, to_status=new)


def _order(oid: str, by: str | None = "priya", archived: bool = False) -> Order:
    return Order(
        id=oid,
        title=f"Order {oid}",
        requirements="r",
        product_line="fastapi-service",
        product_slug=oid,
        created_at=T0,
        created_by=by,
        archived_at=NOW if archived else None,
    )


def _run(rid: str, oid: str, status: RunStatus, cost: float = 0.0, loops: int = 0, created: datetime = T0) -> Run:
    return Run(
        id=rid,
        order_id=oid,
        iteration=1,
        status=status,
        cost_usd=cost,
        loops=loops,
        workflow_id="fastapi-service",
        created_at=created,
        updated_at=created,
    )


def _facts() -> Facts:
    orders = {o.id: o for o in [_order("oA"), _order("oB"), _order("oC"), _order("oD", by="alice"), _order("oE")]}
    orders["oX"] = _order("oX", archived=True)
    runs = [
        _run("A", "oA", S.awaiting_feedback, cost=1.0, loops=1),  # clean delivery
        _run("B", "oB", S.awaiting_feedback, cost=2.0, loops=3),  # questions + held, then delivered
        _run("C", "oC", S.failed, cost=0.5),
        _run("D", "oD", S.awaiting_approval, cost=0.25),  # waiting on alice
        _run("E", "oE", S.paused_limits),  # waiting on the usage window
        _run("X", "oX", S.held),  # archived order: not on the board
        _run("OLD", "oA", S.awaiting_feedback, cost=9.0, created=NOW - timedelta(days=60)),  # outside the window
    ]
    transitions = {
        "A": [
            _t("A", 0, None, S.queued),
            _t("A", 60, S.queued, S.running),
            _t("A", 600, S.running, S.awaiting_feedback),
        ],
        "B": [
            _t("B", 0, None, S.queued),
            _t("B", 0, S.queued, S.running),
            _t("B", 100, S.running, S.needs_input),
            _t("B", 400, S.needs_input, S.queued),  # a person answered
            _t("B", 400, S.queued, S.running),
            _t("B", 700, S.running, S.held),
            _t("B", 1000, S.held, S.queued),  # a person rescued it
            _t("B", 1000, S.queued, S.running),
            _t("B", 1300, S.running, S.awaiting_feedback),
        ],
        "C": [_t("C", 0, None, S.queued), _t("C", 0, S.queued, S.running), _t("C", 200, S.running, S.failed)],
        "D": [
            _t("D", 0, None, S.queued),
            _t("D", 0, S.queued, S.running),
            _t("D", 500, S.running, S.awaiting_approval),
        ],
        "E": [_t("E", 0, None, S.queued), _t("E", 0, S.queued, S.running), _t("E", 300, S.running, S.paused_limits)],
    }
    return Facts(
        now=NOW,
        days=30,
        orders=orders,
        runs=runs,
        transitions=transitions,
        delivered_fallback={"OLD": NOW - timedelta(days=59)},
        readiness_level={"A": 3, "B": 2},
        traceability={
            "A": [
                {"id": "R1", "holdout": [{"scenario": "H1", "passed": True}]},
                {"id": "R2", "holdout": []},  # not covered by hidden scenarios: not "verified live"
            ],
            "B": [{"id": "R1", "holdout": [{"scenario": "H1", "passed": False}]}],
        },
    )


def test_delivery_metrics():
    o = compute(_facts())
    assert o.deliveries == 2, "A and B; OLD was delivered before the window"
    assert o.deliveries_per_week == round(2 / (30 / 7), 2)
    assert (o.lead_time.median_s, o.lead_time.p90_s, o.lead_time.n) == (950.0, 1300.0, 2)
    # finished in the window (not cancelled): A, B, C; B needed a rescue, C failed
    assert (o.finished, o.failed_or_rescued, o.change_failure_rate) == (3, 2, round(2 / 3, 4))
    assert o.recovery_time.median_s == 600.0, "B: held at +700, delivered at +1300"


def test_autonomy_and_human_touches():
    o = compute(_facts())
    assert o.autonomy_ratio == 0.5, "A needed nobody; B needed answers and a rescue"
    assert o.touches.answered_questions == 1 and o.touches.rescued == 1
    assert o.touches.restarts == 0 and o.touches.spec_reviews == 0
    assert o.unplanned_touches_per_delivery == 1.0


def test_cost_and_effort():
    o = compute(_facts())
    assert o.cost_total_usd == 3.75, "all spend in the window, failed runs included; OLD excluded"
    assert o.cost_per_delivery_usd == 1.875
    assert o.fix_loops_per_delivery == 2.0


def test_where_the_time_goes():
    o = compute(_facts())
    s = o.time_split
    # A: 600 agents. B: 700 agents, 600 person. C: 200 agents.
    # D: 500 agents, then waiting on a person until NOW. E: 300 agents, then the usage window until NOW.
    until_now = (NOW - T0).total_seconds()
    assert s.agents_s == 600 + 700 + 200 + 500 + 300
    assert s.person_s == 600 + (until_now - 500)
    assert s.system_s == until_now - 300
    assert o.runs_with_timeline == 5 and o.runs_in_window == 6, "X is in the window but has no timeline"


def test_the_waiting_board_names_who_must_act():
    o = compute(_facts())
    assert [(w.run_id, w.owner) for w in o.waiting] == [("D", "alice"), ("E", "system")]
    d = o.waiting[0]
    assert d.status == S.awaiting_approval and "Review the spec" in d.action
    assert d.waiting_s == (NOW - (T0 + timedelta(seconds=500))).total_seconds()
    no_owner = _facts()
    no_owner.orders["oD"].created_by = None
    assert compute(no_owner).waiting[0].owner == "an admin"


def test_quality_of_what_is_live():
    o = compute(_facts())
    assert o.products == 5, "archived orders are not live products"
    assert o.products_level3 == 1
    assert (o.requirements_total, o.requirements_verified_live) == (3, 1)


def test_by_workflow_and_weekly():
    o = compute(_facts())
    [wf] = o.by_workflow
    assert (wf.workflow_id, wf.deliveries, wf.autonomy_ratio, wf.cost_per_delivery_usd) == (
        "fastapi-service",
        2,
        0.5,
        1.875,
    )
    assert len(o.weekly) == 5 and sum(w.deliveries for w in o.weekly) == 2
    assert o.weekly[-1].deliveries == 2 and o.weekly[-1].cost_usd == 3.75


def test_empty_factory_has_no_made_up_numbers():
    o = compute(Facts(now=NOW, days=7, orders={}, runs=[], transitions={}))
    assert o.deliveries == 0 and o.autonomy_ratio is None and o.cost_per_delivery_usd is None
    assert o.change_failure_rate is None and o.lead_time.median_s is None and o.waiting == []


# --------------------------------------------------------- transition log --
def test_every_status_change_is_recorded_once(tmp_path):
    st = SqliteStateStore(tmp_path / "f.db")
    run = st.create_run(_run("r1", "o1", S.queued))
    run.status = S.running
    st.save_run(run)
    run.cost_usd = 1.0
    st.save_run(run)  # no status change: nothing recorded
    run.status = S.held
    st.save_run(run)
    got = [(t.from_status, t.to_status) for t in st.transitions(["r1"])["r1"]]
    assert got == [(None, S.queued), (S.queued, S.running), (S.running, S.held)]
    assert st.transitions([]) == {}


# ---------------------------------------------------------------- the API --
async def test_outcomes_api_after_a_real_run(make_factory):
    f = make_factory()
    w = f.workflows["fastapi-service"]
    w.draft.set_spec_review("first")
    w.draft.publish("gate on")
    order = f.manager.create_order(ORDER)
    order.created_by = "alice"
    f.store.save_order(order)
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == S.awaiting_approval

    app, ctx, c = await _client(f)
    async with c:
        o = (await c.get("/api/outcomes")).json()
        assert o["deliveries"] == 0 and o["runs_with_timeline"] == 1
        [waiting] = o["waiting"]
        assert (waiting["owner"], waiting["status"]) == ("alice", "awaiting_approval")
        assert (await c.post(f"/api/runs/{run.id}/spec/approve")).status_code == 200
        assert await wait_run(f, run.id) == S.awaiting_feedback
        o = (await c.get("/api/outcomes?days=7")).json()
        assert o["deliveries"] == 1 and o["window_days"] == 7
        assert o["autonomy_ratio"] == 1.0, "a spec review is a planned touch"
        assert o["touches"]["spec_reviews"] == 1 and o["waiting"] == []
        assert o["time_split"]["agents_s"] > 0 and o["lead_time"]["n"] == 1
        assert (await c.get("/api/outcomes?days=0")).status_code == 422
    await ctx.__aexit__(None, None, None)


def test_agent_effort_by_station():
    """ADR-0022: calls, cost, median turns/time, tool calls and denials per station, inside the window."""
    f = _facts()
    at = T0 + timedelta(seconds=10)

    def call(station: str, turns: int, dur: float, cost: float, tools: int, denied: int = 0):  # noqa: ANN202
        return ("A", at, {"station": station, "turns": turns, "duration_s": dur, "cost_usd": cost,
                          "tool_calls": tools, "denied": denied})  # fmt: skip

    f.calls = [
        call("implement", 10, 100.0, 0.5, 20, 1),
        call("implement", 30, 300.0, 1.5, 40),
        call("build", 20, 200.0, 1.0, 30),  # a version from before ADR-0028: counts as implement
        call("review", 4, 40.0, 0.25, 5),  # likewise code-review
        ("A", NOW - timedelta(days=40), {"station": "build", "turns": 99, "cost_usd": 9.0}),  # before the window
    ]
    f.denials = [("A", at, {"tool": "Bash"}), ("A", NOW - timedelta(days=40), {"tool": "Bash"})]
    o = compute(f)
    build, review = o.effort_by_station
    assert (build.station, build.calls, build.cost_usd, build.turns_median, build.duration_median_s) == (
        "implement", 3, 3.0, 20.0, 200.0,
    )  # fmt: skip
    assert (build.tool_calls, build.denied) == (90, 1)
    assert (review.station, review.calls, review.cost_usd) == ("code-review", 1, 0.25), "most expensive station first"
    assert o.guardrail_denials == 1
