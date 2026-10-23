"""Product spend (spend.py): model spend across all of a product's changes, by phase and station."""

from __future__ import annotations

from conftest import PRODUCT, wait_run

from agent_factory import actions
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.spend import product_spend

COLS = {
    "c1": {"requirements": "design", "design": "design", "implement": "develop", "code-review": "verify"},
    "c2": {"implement": "develop", "acceptance": "deliver"},
}


def test_spend_adds_up_by_phase_in_lifecycle_order():
    calls = [
        ("c1", "code-review", 1.26),
        ("c1", "requirements", 0.22),
        ("c1", "design", 0.74),
        ("c1", "implement", 0.90),
        ("c1", "retro", 0.18),
        ("c2", "implement", 0.50),
        ("c2", "acceptance", 0.35),
    ]
    got = product_spend(calls, COLS, {"c1": 3.30, "c2": 0.85})
    assert [(p["phase"], p["title"], p["cost_usd"]) for p in got["phases"]] == [
        ("design", "Design", 0.96),
        ("develop", "Develop", 1.40),
        ("verify", "Verify", 1.26),
        ("deliver", "Deliver", 0.35),
        ("learn", "Learning", 0.18),
    ]
    assert got["total_usd"] == 4.15 and got["changes"] == 2
    develop = got["phases"][1]["stations"]
    assert develop == [{"station": "implement", "cost_usd": 1.40}], "the same station across changes is one row"
    assert [s["station"] for s in got["phases"][0]["stations"]] == ["design", "requirements"], "dearest first"


def test_recorded_cost_without_calls_shows_as_other_so_the_total_matches():
    got = product_spend([("c1", "implement", 1.0)], COLS, {"c1": 1.5, "old": 2.0})
    assert got["phases"][-1] == {"phase": "other", "title": "Other", "cost_usd": 2.5, "stations": []}
    assert got["total_usd"] == 3.5


def test_a_station_outside_the_lane_is_other_and_nothing_is_zero():
    got = product_spend([("c9", "mystery", 0.2)], {}, {"c9": 0.2})
    assert [(p["phase"], p["cost_usd"]) for p in got["phases"]] == [("other", 0.2)]
    assert product_spend([], {}, {}) == {"total_usd": 0.0, "changes": 0, "phases": []}


async def test_the_product_view_carries_spend_and_the_spec_its_assumptions(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    before = actions.get_product(f, product.id).spend.total_usd
    f.store.add_event(  # a priced call, as the agent runner records one (ADR-0022)
        change.id,
        EventKind.log,
        "agent call: reviewer",
        "code-review",
        {"agent_call": {"station": "code-review", "cost_usd": 0.5}},
    )
    detail = actions.get_product(f, product.id)
    assert detail.spend.changes == 1
    verify = next(p for p in detail.spend.phases if p.phase == "verify")
    assert verify.title == "Verify" and verify.stations[0].station == "code-review"
    assert round(detail.spend.total_usd - before, 4) == 0.5
    spec = actions.get_change_spec(f, change.id)
    assert spec.assumptions == ["No authentication in v0 (single user, local cluster)."]
