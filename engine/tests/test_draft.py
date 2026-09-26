"""Editing the line through a draft: agents, station mapping, reference docs,
publishing and the context an agent actually receives."""

from __future__ import annotations

import pytest
from conftest import ORDER, wait_run
from test_api import _client  # noqa: E402

from agent_factory.agents import FakeAgentRunner
from agent_factory.engine.lines import LineError
from agent_factory.line import AgentSpec, RefDoc
from agent_factory.models import RunStatus


def _reviewer(**kw) -> AgentSpec:
    return AgentSpec(
        id="strict-verifier",
        description="stricter acceptance",
        model="judgment",
        tools="reviewer",
        skills=["agent-watchdog", "factory-station-contract"],
        max_turns=40,
        prompt="# Role: strict verifier\nCheck every scenario twice.\n",
        **kw,
    )


async def test_draft_starts_as_copy_and_is_not_dirty(make_factory):
    f = make_factory()
    base, doc, stamp = f.lines.draft.get()
    assert base == 1 and stamp is None and not f.lines.draft.dirty()
    assert doc == f.lines.get(1)
    with pytest.raises(LineError, match="nothing to publish"):
        f.lines.draft.publish("no-op")


async def test_add_agent_map_it_to_a_station_and_publish(make_factory):
    f = make_factory()
    d = f.lines.draft
    d.upsert_agent(_reviewer())
    d.set_station_agent("acceptance", "strict-verifier")
    assert d.dirty() and d.problems() == []
    info = d.publish("acceptance uses strict-verifier")
    assert info.version == 2 and info.active
    assert f.lines.get(2).station("acceptance").agent == "strict-verifier"
    assert f.lines.get(1).station("acceptance").agent == "verifier", "v1 is immutable"
    assert f.store.get_line_draft("default") is None, "publishing clears the draft"


async def test_guardrails_on_editing(make_factory):
    d = make_factory().lines.draft
    with pytest.raises(LineError, match="used by stations"):
        d.delete_agent("developer")
    with pytest.raises(LineError, match="deterministic check"):
        d.set_station_agent("verify", "developer")
    with pytest.raises(LineError, match="already exists"):
        d.duplicate_agent("developer", "architect")
    d.duplicate_agent("developer", "developer-lite")
    d.delete_agent("developer-lite")  # unused -> allowed
    # a writer on the acceptance station is saved in the draft but blocks publishing
    d.set_station_agent("acceptance", "developer")
    assert any("observe-only" in p for p in d.problems())
    with pytest.raises(LineError, match="observe-only"):
        d.publish("unsafe")


async def test_docs_attach_detach_and_caps(make_factory):
    d = make_factory().lines.draft
    d.upsert_doc(RefDoc(id="security-standards", title="Security standards", content="Never log tokens.\n"))
    _, doc, _ = d.get()
    spec = doc.agents["developer"].model_copy(
        update={"context_docs": [*doc.agents["developer"].context_docs, "security-standards"]}
    )
    d.upsert_agent(spec)
    with pytest.raises(LineError, match="attached to agents"):
        d.delete_doc("security-standards")
    with pytest.raises(ValueError):
        RefDoc(id="huge", title="Huge", content="x" * 20_001)


async def test_agent_receives_docs_learnings_and_earlier_iterations(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    d = f.lines.draft
    _, doc, _ = d.get()
    d.upsert_agent(
        doc.agents["developer"].model_copy(update={"learnings": "Always add an index on columns used for filtering."})
    )
    d.publish("developer learnings")

    order = f.manager.create_order(ORDER)
    run1 = f.manager.start_run(order)
    assert await wait_run(f, run1.id) == RunStatus.awaiting_feedback
    first = next(c for c in agents.calls if c.role == "developer")
    assert "REST API conventions" in first.system_prompt  # blueprint doc attached to developer
    assert "Always add an index" in first.system_prompt
    assert "Earlier iterations" not in first.system_prompt  # nothing earlier yet

    run2 = f.manager.feedback(order.id, "Add full-text search over notes.")
    assert await wait_run(f, run2.id) == RunStatus.awaiting_feedback
    intake2 = [c for c in agents.calls if c.role == "intake"][-1]
    assert "### Iteration 1" in intake2.system_prompt and "assumption:" in intake2.system_prompt
    verifier = [c for c in agents.calls if c.role == "verifier"][-1]
    assert "Earlier iterations" not in verifier.system_prompt, "verifier asked for no history"


async def test_draft_over_http(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        cat = (await c.get("/api/catalog")).json()
        assert "builder" in cat["presets"] and cat["model_tiers"]["judgment"] == "opus"
        assert any(s["name"] == "plow-ahead" and s["vendored"] for s in cat["skills"])

        spec = _reviewer().model_dump()
        r = await c.put("/api/line/draft/agents/strict-verifier", json=spec)
        assert r.status_code == 200 and r.json()["dirty"]
        assert (await c.put("/api/line/draft/agents/other-id", json=spec)).status_code == 409
        bad = {**spec, "extra_tools": ["Bash"]}
        assert (await c.put("/api/line/draft/agents/strict-verifier", json=bad)).status_code == 422
        r = await c.delete("/api/line/draft/agents/developer")
        assert r.status_code == 409 and "used by stations" in r.json()["detail"]

        r = await c.put("/api/line/draft/stations/acceptance/agent", json={"agent": "strict-verifier"})
        assert next(s for s in r.json()["stations"] if s["id"] == "acceptance")["role"] == "strict-verifier"
        pub = await c.post("/api/line/draft/publish", json={"note": "strict acceptance"})
        assert pub.status_code == 201 and pub.json()["version"] == 2
        after = (await c.get("/api/line/draft")).json()
        assert after["base_version"] == 2 and not after["dirty"]
    await ctx.__aexit__(None, None, None)
