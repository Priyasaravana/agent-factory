"""What a delivered product is ready for (principle 7, roadmap J2): demo, pilot or production, from the
evidence, and what the next tier needs; the named owner; a moved address said at delivery (UX-22)."""

from __future__ import annotations

from conftest import PRODUCT, wait_run
from test_access import ADMIN, MEMBER
from test_api import _client

from agent_factory import ready_for
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.ready_for import Inputs, assess


def _row(rid: str, passed: bool | None = True, no_live: str = "", tests: int = 1) -> dict:
    holdout = [] if passed is None else [{"id": f"H-{rid}", "passed": passed}]
    return {"id": rid, "holdout": holdout, "no_live_check": no_live, "tests": tests}


GOOD = [_row("R1"), _row("R2"), _row("R3", passed=None, no_live="sends an email", tests=2)]


def test_demo_needs_delivery_level_3_and_every_requirement_verified_live():
    r = assess(Inputs(delivered=True, level=3, trace=GOOD, owner=None, rollback=None))
    assert r.tier == "demo" and r.next_tier == "pilot"
    assert r.proven == [
        "agent-readiness Level 3",
        "2/3 requirements verified live, 1 tested where a live check isn't possible",
    ]
    assert r.unchecked_live == ["R3"], "named, never hidden"
    assert r.needs == ["a named owner", "a rollback proven on this product"]
    assert r.headline() == "ready for: demo; pilot needs: a named owner; a rollback proven on this product"

    short = assess(
        Inputs(delivered=True, level=2, trace=[_row("R1"), _row("R2", passed=False)], owner="ann", rollback="c1")
    )
    assert short.tier == "none" and short.next_tier == "demo"
    assert short.needs == ["agent-readiness Level 3 (now Level 2)", "every requirement verified live (R2 not yet)"]
    assert short.headline().startswith("not ready for a demo yet; demo needs:")
    # a requirement with no live check and no tests isn't proved by anything
    untested = assess(
        Inputs(
            delivered=True, level=3, trace=[_row("R1", passed=None, no_live="x", tests=0)], owner=None, rollback=None
        )
    )
    assert untested.tier == "none"
    assert assess(Inputs(delivered=True, level=3, trace=None, owner=None, rollback=None)).tier == "none"
    assert assess(Inputs(delivered=False, level=3, trace=GOOD, owner=None, rollback=None)).needs == [
        "a delivered change"
    ]


def test_pilot_needs_an_owner_and_a_proven_rollback_and_production_waits_for_j4():
    assert assess(Inputs(True, 3, GOOD, owner="ann", rollback=None)).needs == ["a rollback proven on this product"]
    r = assess(Inputs(True, 3, GOOD, owner="ann", rollback="c7"))
    assert r.tier == "pilot" and r.next_tier == "production"
    assert "owned by ann" in r.proven and "rollback proven (change c7)" in r.proven
    assert r.needs == ready_for.PRODUCTION_NEEDS, "nothing is above pilot until J4 proves operation"


def test_a_moved_address_is_said_and_a_first_one_is_not():
    assert ready_for.moved("http://localhost:8083", "http://kudos.localtest.me:8180/") == {
        "from": "http://localhost:8083",
        "to": "http://kudos.localtest.me:8180/",
    }
    assert ready_for.moved(None, "http://a") is None, "a first delivery has no old address"
    assert ready_for.moved("http://a/", "http://a") is None


async def test_a_delivery_states_what_it_is_ready_for_and_the_page_follows_the_owner(make_factory):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post(
            "/api/products", json={"title": PRODUCT.title, "requirements": PRODUCT.requirements}, headers=MEMBER
        )
        product_id, change_id = r.json()["product"]["id"], r.json()["changes"][0]["id"]
        assert await wait_run(f, change_id, 60) == ChangeStatus.awaiting_feedback
        stated = next(e for e in f.store.list_events(change_id) if "ready_for" in e.data)
        assert stated.data["ready_for"]["tier"] == "demo" and stated.message.startswith("ready for: demo")
        assert (f.manager.ws.data_dir / "artifacts" / change_id / "ready-for.json").is_file(), "sealed evidence"

        view = (await c.get(f"/api/products/{product_id}/ready-for", headers=MEMBER)).json()
        assert (view["tier"], view["owner"], view["owner_set"]) == ("demo", "priya", False), "its creator by default"
        assert view["change_id"] == change_id and view["needs"] == ["a rollback proven on this product"]

        # only an admin names the owner
        assert (
            await c.post(f"/api/products/{product_id}/owner", json={"owner": "bob"}, headers=MEMBER)
        ).status_code == 403
        r = await c.post(f"/api/products/{product_id}/owner", json={"owner": "  Ezhil  K "}, headers=ADMIN)
        assert r.status_code == 200 and (r.json()["owner"], r.json()["owner_set"]) == ("Ezhil K", True)
        assert any(e.message == "owner named: Ezhil K (by saravana)" for e in f.store.list_events(change_id))

        # a verified rollback on this product makes it a pilot; production waits for J4
        f.store.add_event(change_id, EventKind.decision, "production rolled back", data={"rollback": {"ok": True}})
        view = (await c.get(f"/api/products/{product_id}/ready-for", headers=MEMBER)).json()
        assert view["tier"] == "pilot" and view["next_tier"] == "production"
        f.store.add_event(change_id, EventKind.decision, "not restored", data={"rollback": {"ok": False}})
        assert (await c.get(f"/api/products/{product_id}/ready-for", headers=MEMBER)).json()["tier"] == "pilot"
    await ctx.__aexit__(None, None, None)


async def test_a_second_delivery_that_moves_the_address_says_so(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id, 60) == ChangeStatus.awaiting_feedback
    product = f.store.get_product(product.id)
    product.app_url = "http://localhost:8083"  # where it lived before the install's ingress setting
    f.store.save_product(product)
    nxt = f.manager.start_change(product, "add tags")
    assert await wait_run(f, nxt.id, 60) == ChangeStatus.awaiting_feedback
    moved = next(e for e in f.store.list_events(nxt.id) if "address_moved" in e.data)
    assert moved.data["address_moved"]["from"] == "http://localhost:8083"
    assert "bookmarks and links to the old one break" in moved.message
    app, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/products/{product.id}/ready-for")).json()
        assert view["moved"]["from"] == "http://localhost:8083" and view["iteration"] == 2
    await ctx.__aexit__(None, None, None)
