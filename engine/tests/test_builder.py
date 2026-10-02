"""Lane builder: add, remove, reorder and re-route stations in a draft."""

from __future__ import annotations

import pytest
from conftest import ORDER, wait_run
from test_api import _client

from agent_factory import actions
from agent_factory.engine.workflows import WorkflowError
from agent_factory.models import RunStatus
from agent_factory.workflow import AgentSpec, WorkflowStation

WF = "fastapi-service"


def _reviewer() -> AgentSpec:
    return AgentSpec(
        id="security-reviewer",
        description="OWASP review",
        model="judgment",
        tools="reviewer",
        skills=["factory-station-contract"],
        max_turns=30,
        prompt="Review the change for security issues.",
    )


async def test_add_custom_station_publish_and_run(make_factory):
    f = make_factory()
    d = f.workflows[WF].draft
    d.upsert_agent(_reviewer())
    order = [s.id for s in d.get()[1].stations]
    d.add_station(
        WorkflowStation(id="security-review", kind="agent", agent="security-reviewer", on_fail="implement"),
        position=order.index("implement") + 1,
    )
    ids = [s.id for s in d.get()[1].stations]
    assert ids[ids.index("implement") + 1] == "security-review"
    assert d.problems() == []
    d.publish("add security review")
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    detail = actions.get_run(f, run.id)
    st = {s.id: s for s in detail.stations}
    assert st["security-review"].state == "passed" and st["security-review"].handler == "agent"


async def test_add_station_guardrails(make_factory):
    d = make_factory().workflows[WF].draft
    with pytest.raises(WorkflowError, match="already exists"):
        d.add_station(WorkflowStation(id="implement", kind="agent", agent="developer"))
    with pytest.raises(WorkflowError, match="must be 2-41 chars"):
        d.add_station(WorkflowStation(id="Bad Id", kind="agent", agent="developer"))
    with pytest.raises(WorkflowError, match="check stations need a handler"):
        d.add_station(WorkflowStation(id="lint", kind="check"))
    with pytest.raises(WorkflowError, match="agent 'ghost' not found"):
        d.add_station(WorkflowStation(id="extra", kind="agent", agent="ghost"))
    # a second test check under another id is fine
    d.add_station(WorkflowStation(id="test-again", kind="check", handler="test", on_fail="implement"))
    assert d.problems() == []


async def test_agent_station_without_agent_blocks_publish(make_factory):
    d = make_factory().workflows[WF].draft
    d.add_station(WorkflowStation(id="second-look", kind="agent"), position=3)
    assert any("must name an agent" in p for p in d.problems())
    with pytest.raises(WorkflowError):
        d.publish("incomplete")
    d.update_station("second-look", {"agent": "verifier"})
    assert d.problems() == []


async def test_remove_station_clears_routes(make_factory):
    d = make_factory().workflows[WF].draft
    doc = d.get()[1]
    pointing = [s.id for s in doc.stations if s.on_fail == "deploy-repair" or s.next == "deploy-repair"]
    assert pointing, "the default workflow routes deploy failures to deploy-repair"
    cleared = d.remove_station("deploy-repair")
    assert cleared and all(c.split(".")[0] in pointing for c in cleared)
    doc = d.get()[1]
    assert "deploy-repair" not in [s.id for s in doc.stations]
    assert all(s.on_fail != "deploy-repair" and s.next != "deploy-repair" for s in doc.stations)
    assert d.problems() == [], "removing an optional repair station leaves a valid workflow"
    with pytest.raises(WorkflowError, match="not found"):
        d.remove_station("deploy-repair")


async def test_reorder_and_update_routes(make_factory):
    d = make_factory().workflows[WF].draft
    ids = [s.id for s in d.get()[1].stations]
    with pytest.raises(WorkflowError, match="exactly once"):
        d.reorder_stations(ids[:-1])
    swapped = [s for s in ids if s != "test"]
    swapped.insert(swapped.index("build") + 1, "test")  # a check may run anywhere, e.g. after the image build
    d.reorder_stations(swapped)
    assert [s.id for s in d.get()[1].stations] == swapped
    assert d.problems() == [], "test may run anywhere"
    bad = swapped.copy()
    i, j = bad.index("build"), bad.index("deploy")
    bad[i], bad[j] = bad[j], bad[i]
    d.reorder_stations(bad)
    assert any("'build' (build) must come before 'deploy'" in p for p in d.problems())
    d.reorder_stations(swapped)

    with pytest.raises(WorkflowError, match="existing station"):
        d.update_station("test", {"on_fail": "nowhere"})
    with pytest.raises(WorkflowError, match="itself"):
        d.update_station("test", {"on_fail": "test"})
    with pytest.raises(WorkflowError, match="cannot change"):
        d.update_station("test", {"id": "x"})
    with pytest.raises(WorkflowError, match="unknown check handler"):
        d.update_station("test", {"handler": "implement"})
    d.update_station("test", {"on_fail": None})
    assert d.get()[1].station("test").on_fail is None


async def test_role_check_applies_to_new_stations(make_factory):
    d = make_factory().workflows[WF].draft
    d.upsert_agent(_reviewer())
    # a reviewer (observe-only) can't be the implementer
    d.update_station("implement", {"agent": "security-reviewer"})
    assert any("needs an agent that can write files" in p for p in d.problems())


async def test_station_endpoints(make_factory):
    app, ctx, c = await _client(make_factory())
    base = f"/api/workflows/{WF}/draft/stations"
    async with c:
        r = await c.post(base, json={"id": "test-2", "kind": "check", "handler": "test", "position": 4})
        assert r.status_code == 200, r.text
        assert [s["id"] for s in r.json()["stations"]][4] == "test-2"
        r = await c.patch(f"{base}/test-2", json={"on_fail": "implement"})
        v2 = next(s for s in r.json()["stations"] if s["id"] == "test-2")
        assert r.status_code == 200 and v2["on_fail"] == "implement"
        r = await c.patch(f"{base}/test-2", json={"on_fail": None})
        v2 = next(s for s in r.json()["stations"] if s["id"] == "test-2")
        assert v2["on_fail"] is None
        order = [s["id"] for s in r.json()["stations"]]
        order.insert(0, order.pop(order.index("test-2")))
        r = await c.put(f"{base}/order", json={"order": order})
        assert r.status_code == 200 and r.json()["stations"][0]["id"] == "test-2"
        r = await c.delete(f"{base}/test-2")
        assert r.status_code == 200 and "test-2" not in [s["id"] for s in r.json()["stations"]]
        r = await c.post(base, json={"id": "implement", "kind": "agent", "agent": "developer"})
        assert r.status_code == 409 and "already exists" in r.text
    await ctx.__aexit__(None, None, None)
