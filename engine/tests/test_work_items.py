"""Work items (ADR-0030): every request is a change to a product, with a kind, a
source and a requester; a product has a target."""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import PRODUCT, wait_run
from test_api import _client

from agent_factory import evidence
from agent_factory.agents import FakeAgentRunner
from agent_factory.engine.pipeline import InvalidRequestError
from agent_factory.identity import Identity, _current
from agent_factory.models import Change, ChangeKind, ChangeSource, ChangeStatus, CreateProductInput


def _calls(agents: FakeAgentRunner, role: str) -> list[str]:
    return [c.prompt for c in agents.calls if c.role == role]


async def test_first_change_is_new_and_records_who_asked(make_factory):
    f = make_factory()
    token = _current.set(Identity("ada", "member"))
    try:
        change = f.manager.start_change(f.manager.create_product(PRODUCT))
    finally:
        _current.reset(token)
    assert (change.kind, change.source, change.requested_by) == (ChangeKind.new, ChangeSource.ui, "ada")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    item = [e for e in f.store.list_events(change.id) if "work_item" in e.data]
    assert item and item[0].message == "work item: new from ui, asked by ada"
    for _ in range(200):  # the seal is written just after the change stops
        if evidence.latest_seal(f.store, change.id):
            break
        await asyncio.sleep(0.05)
    manifest = json.loads((f.manager.ws.data_dir / "artifacts" / change.id / "manifest.json").read_text())
    assert (manifest["run"]["kind"], manifest["run"]["requested_by"]) == ("new", "ada")


async def test_bug_change_tells_the_agents_to_reproduce_first(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    product = f.manager.create_product(PRODUCT)
    assert await wait_run(f, f.manager.start_change(product).id) == ChangeStatus.awaiting_feedback
    first_intake = _calls(agents, "intake")[-1]

    bug = f.manager.feedback(product.id, "tags with spaces are dropped", ChangeKind.bug)
    assert (bug.kind, bug.iteration) == (ChangeKind.bug, 2)
    assert await wait_run(f, bug.id) == ChangeStatus.awaiting_feedback
    intake = _calls(agents, "intake")[-1]
    assert "[bug] tags with spaces are dropped" in intake
    assert "Reproduce the bug first with a failing test" in intake
    assert "Reproduce the bug first" in _calls(agents, "developer")[-1]
    # a new product's prompts carry no kind guidance: the evaluated prompts are unchanged
    assert "Kind of change" not in first_intake
    assert [fb.text for fb in f.store.list_feedback(product.id)] == ["tags with spaces are dropped"]


async def test_feature_prompts_are_unchanged(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    product = f.manager.create_product(PRODUCT)
    assert await wait_run(f, f.manager.start_change(product).id) == ChangeStatus.awaiting_feedback
    change = f.manager.feedback(product.id, "add a reset endpoint")
    assert change.kind == ChangeKind.feature
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    intake = _calls(agents, "intake")[-1]
    assert "add a reset endpoint" in intake and "[feature]" not in intake and "Kind of change" not in intake


async def test_upkeep_keeps_behaviour(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    product = f.manager.create_product(PRODUCT)
    assert await wait_run(f, f.manager.start_change(product).id) == ChangeStatus.awaiting_feedback
    change = f.manager.feedback(product.id, "bump dependencies", ChangeKind.upkeep)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert "behaviour must stay the same" in _calls(agents, "intake")[-1]


@pytest.mark.parametrize("kind", [ChangeKind.new, ChangeKind.assess, ChangeKind.remove])
async def test_kinds_that_are_not_iterations_are_refused(make_factory, kind):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    assert await wait_run(f, f.manager.start_change(product).id) == ChangeStatus.awaiting_feedback
    with pytest.raises(InvalidRequestError):
        f.manager.feedback(product.id, "do something", kind)
    assert len(f.store.list_changes(product.id)) == 1
    assert f.store.list_feedback(product.id) == []


async def test_a_first_change_must_build_the_product(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    with pytest.raises(InvalidRequestError):
        f.manager.start_change(product, kind=ChangeKind.bug)
    assert f.store.list_changes(product.id) == []


def test_only_new_products_can_be_built_yet(make_factory):
    f = make_factory()
    with pytest.raises(InvalidRequestError, match="not available yet"):
        f.manager.create_product(PRODUCT.model_copy(update={"target": "repo"}))
    assert f.store.list_products() == []


def test_changes_stored_before_work_items_get_a_kind():
    base = {"id": "c1", "product_id": "p1", "status": "queued", "created_at": "2026-10-01T00:00:00Z"}
    base["updated_at"] = base["created_at"]
    assert Change.model_validate({**base, "iteration": 1}).kind == ChangeKind.new
    later = Change.model_validate({**base, "iteration": 3, "change_request": "add search"})
    assert (later.kind, later.source, later.requested_by) == (ChangeKind.feature, ChangeSource.ui, None)
    assert Change.model_validate({**base, "iteration": 2, "kind": "bug"}).kind == ChangeKind.bug


async def test_work_items_over_http(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        bad = await c.post(
            "/api/products",
            json={"title": "Repo work", "requirements": "Look after our repository.", "target": "repo"},
        )
        assert bad.status_code == 422 and "not available yet" in bad.json()["detail"]
        r = await c.post("/api/products", json={"title": "Bookmarks", "requirements": "Save and tag bookmarks."})
        assert r.status_code == 201
        detail = r.json()
        assert detail["product"]["target"] == "new" and detail["changes"][0]["kind"] == "new"
        pid = detail["product"]["id"]
        for _ in range(200):
            if (await c.get(f"/api/products/{pid}")).json()["product"]["latest_status"] == "awaiting_feedback":
                break
            await asyncio.sleep(0.05)
        assess = await c.post(f"/api/products/{pid}/feedback", json={"text": "assess it", "kind": "assess"})
        assert assess.status_code == 422
        bug = await c.post(f"/api/products/{pid}/feedback", json={"text": "search is slow", "kind": "bug"})
        assert bug.status_code == 201, bug.text
        assert bug.json()["kind"] == "bug" and bug.json()["requested_by"] == "local"
    await ctx.__aexit__(None, None, None)


def test_create_input_defaults_to_a_new_product():
    assert CreateProductInput(title="Notes", requirements="Keep short notes.").target == "new"
