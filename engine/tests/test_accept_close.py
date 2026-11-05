"""Accepted or closed (J2, UX-14): deployed is not accepted until a person says so, and a change that
ends short of acceptance is closed with the reason; both recorded once and sealed with the evidence."""

from __future__ import annotations

import json

from conftest import PRODUCT, wait_run
from test_access import MEMBER
from test_api import _client

from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus


def _sealed_events(f, change_id: str) -> str:
    return (f.manager.ws.data_dir / "artifacts" / change_id / "events.jsonl").read_text()


async def test_a_delivered_iteration_is_accepted_once_and_the_seal_carries_it(make_factory):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        product = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()["product"]
        cid = product["latest_change_id"]
        assert await wait_run(f, cid, 60) == ChangeStatus.awaiting_feedback
        assert f.store.get_change(cid).outcome is None, "delivered is not accepted"
        r = await c.post(f"/api/changes/{cid}/accept", json={"note": "  does what we  asked "}, headers=MEMBER)
        assert r.status_code == 200
        out = r.json()["outcome"]
        assert (out["state"], out["by"], out["note"]) == ("accepted", "priya", "does what we asked")
        assert r.json()["status"] == "awaiting_feedback", "the next iteration can still start"
        assert "iteration 1 accepted by priya: does what we asked" in _sealed_events(f, cid)
        again = await c.post(f"/api/changes/{cid}/accept", json={}, headers=MEMBER)
        assert again.status_code == 422 and "already accepted by priya" in again.json()["detail"]
        shut = await c.post(f"/api/changes/{cid}/close", json={"reason": "changed my mind"}, headers=MEMBER)
        assert shut.status_code == 422, "an accepted iteration stays accepted"
    await ctx.__aexit__(None, None, None)


async def test_a_delivered_iteration_closed_unaccepted_stays_live_and_says_so(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.post(f"/api/changes/{change.id}/close", json={"reason": "no"})).status_code == 422
        r = await c.post(f"/api/changes/{change.id}/close", json={"reason": "not what the team needs"})
        assert r.status_code == 200 and r.json()["status"] == "awaiting_feedback"
        assert r.json()["outcome"]["status_before"] == "awaiting_feedback"
        assert (await c.post(f"/api/changes/{change.id}/accept", json={})).status_code == 422
    await ctx.__aexit__(None, None, None)
    assert "delivered, not accepted; it stays live" in _sealed_events(f, change.id)


async def test_a_held_change_is_closed_with_its_reason_and_stops_like_cancel(make_factory):
    """The blue/green drill's held trial (Expense claims, e56fb20c67ad) had nothing to close it with."""
    ex = FakeExecutor(fail_on=["make verify"] * 10)
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.held
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.post(f"/api/changes/{change.id}/accept", json={})).status_code == 422, "not delivered"
        r = await c.post(f"/api/changes/{change.id}/close", json={"reason": "rollback drill finished"})
        assert r.status_code == 200
        assert (r.json()["status"], r.json()["outcome"]["status_before"]) == ("cancelled", "held")
    await ctx.__aexit__(None, None, None)
    await wait_run(f, change.id, 30)
    for _ in range(100):  # sealed after the cancel settles
        sealed = f.manager.ws.data_dir / "artifacts" / change.id / "events.jsonl"
        if sealed.is_file() and "rollback drill finished" in sealed.read_text():
            break
        import asyncio

        await asyncio.sleep(0.05)
    lines = [json.loads(x) for x in sealed.read_text().splitlines()]
    closed = next(e for e in lines if e["message"].startswith("closed by"))
    assert closed["message"] == "closed by local (held): rollback drill finished"
    assert any(e["message"] == "change cancelled by human" for e in lines)


async def test_a_running_change_is_never_closed_under_its_own_feet(make_factory):
    import pytest

    from agent_factory.engine.pipeline import InvalidRequestError

    f = make_factory()
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert f.store.get_change(change.id).status in (ChangeStatus.queued, ChangeStatus.running)
    with pytest.raises(InvalidRequestError, match="cancel it first"):
        await f.manager.close(change.id, "stop it", "priya")
    assert f.store.get_change(change.id).outcome is None
    await wait_run(f, change.id, 60)
