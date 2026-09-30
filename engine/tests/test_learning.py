"""Learning from runs that needed help (ADR-0021): signals, vetting, the retro, and the admin decision."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from conftest import ORDER, wait_run
from test_api import _client

from agent_factory.agents import FakeAgentRunner
from agent_factory.executor import FakeExecutor
from agent_factory.identity import Identity, _current
from agent_factory.models import Event, EventKind, Run, RunStatus
from agent_factory.retro import signals, vet
from agent_factory.workflow import load_workflow_dir

REPO = Path(__file__).resolve().parents[2]
WF = "fastapi-service"
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _ev(i: int, kind: EventKind, message: str, data: dict | None = None) -> Event:
    return Event(id=i, run_id="r", ts=NOW, kind=kind, message=message, data=data or {})


def _run(**kw) -> Run:
    return Run(
        id="r", order_id="o", iteration=1, status=RunStatus.awaiting_feedback, created_at=NOW, updated_at=NOW, **kw
    )


# ---------------------------------------------------------------- signals --
def test_signals_come_from_recorded_evidence_only():
    events = [
        _ev(1, EventKind.decision, "routing failure from verify to build",
            {"routed": {"from": "verify", "to": "build", "evidence": "FAILED test_tags: 500"}}),
        _ev(2, EventKind.status, "held at deploy: fix-loop budget exhausted", {"evidence": "helm timeout"}),
        _ev(3, EventKind.decision, "spec: 2 numbered requirements"),
    ]  # fmt: skip
    s = signals(_run(questions=["Which auth?"], answers=["none"]), events)
    assert s.routed == [{"from": "verify", "to": "build", "evidence": "FAILED test_tags: 500"}]
    assert s.held == ["held at deploy: fix-loop budget exhausted\nhelm timeout"]
    assert s.questions == ["Which auth?"] and s.needs_retro()
    assert not signals(_run(questions=["unanswered"]), [events[2]]).needs_retro(), "a clean run needs no retro"


# ------------------------------------------------------------------- vet --
def _doc():
    doc = load_workflow_dir(REPO / "workflow-templates" / "default")
    doc.agents["developer"].learnings = "- Always validate query parameters with Pydantic and return 422."
    return doc


def _p(agent="developer", lesson="Return 201 with a Location header when creating a resource.", **kw):
    return {"agent": agent, "lesson": lesson, "why": "spec says 201", "evidence": "review: R1 partial", **kw}


def test_vet_keeps_new_well_formed_lessons():
    kept, rejected = vet([_p()], _doc(), {})
    assert [k["lesson"] for k in kept] == ["Return 201 with a Location header when creating a resource."]
    assert rejected == []


def test_vet_rejects_bad_duplicate_and_check_weakening_lessons():
    doc = _doc()
    proposals = [
        _p(agent="ghost"),
        _p(lesson="too short"),
        _p(evidence=""),
        _p(lesson="If tests are flaky, skip the failing tests so the build can finish."),
        _p(lesson="Disable the readiness check for small services to save time."),
        _p(lesson="Always validate query parameters with Pydantic, returning 422."),  # ~ existing learning
        _p(agent="architect", lesson="Name every endpoint in docs/openapi.yaml before writing the design doc."),
    ]
    kept, rejected = vet(proposals[:6], doc, {})
    assert kept == []
    assert rejected == [
        "unknown agent 'ghost'",
        "lesson for 'developer' must be 20-300 characters",
        "lesson for 'developer' cites no evidence",
        "lesson for 'developer' would weaken a check: " + proposals[3]["lesson"],
        "lesson for 'developer' would weaken a check: " + proposals[4]["lesson"],
        "lesson for 'developer' repeats an existing or pending one",
    ]
    pending = {"architect": ["Name every endpoint in docs/openapi.yaml before the design."]}
    assert vet(proposals[6:], doc, pending)[1] == ["lesson for 'architect' repeats an existing or pending one"]
    assert vet(proposals, doc, {})[1][-1] == "1 further suggestion(s) ignored (limit 6)", "never silently dropped"


def test_vet_caps_proposals_and_respects_full_learnings():
    many = [_p(lesson=f"Lesson number {i}: document every environment variable in README.md.") for i in range(5)]
    kept, _ = vet(many, _doc(), {})
    assert len(kept) == 1, "near-identical lessons collapse to one"
    distinct = [
        _p(lesson="Return 201 with a Location header when creating a resource."),
        _p(lesson="Paginate every list endpoint with limit and offset query parameters."),
        _p(lesson="Log one JSON line per request with method, path, status and duration."),
        _p(lesson="Reject unknown JSON fields in request bodies with a 422 response."),
    ]
    assert len(vet(distinct, _doc(), {})[0]) == 3
    full = _doc()
    full.agents["developer"].learnings = "- x" * 1500
    assert vet([_p()], full, {})[1] == ["agent 'developer' learnings are full; condense them in the editor"]


# ------------------------------------------------------------- the retro --
async def _proposals(f, status="pending", n=1, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        got = f.store.list_proposals(WF, status)
        if len(got) >= n:
            return got
        await asyncio.sleep(0.05)
    return f.store.list_proposals(WF, status)


async def test_a_clean_run_asks_for_no_retro(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    await asyncio.sleep(0.2)
    assert not any(c.role == "retro" for c in agents.calls) and f.store.list_proposals(WF) == []


async def test_a_fix_loop_leads_to_a_suggested_learning(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(FakeExecutor(fail_on=["make verify"]), agents)
    order = f.manager.create_order(ORDER)
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    [p] = await _proposals(f)
    assert (p.agent, p.run_id, p.order_id, p.workflow_version) == ("developer", run.id, order.id, 1)
    routed = next(e for e in f.store.list_events(run.id) if "routed" in e.data)
    assert routed.data["routed"]["from"] == "verify" and routed.data["routed"]["evidence"], "evidence is kept"
    retro = next(c for c in agents.calls if c.role == "retro")
    assert retro.observe_only and "verify failed, routed to build" in retro.prompt
    assert any(e.message.startswith("retro: 1 learning(s) suggested") for e in f.store.list_events(run.id))

    # the same lesson again is not suggested twice
    run2 = f.manager.start_run(f.manager.create_order(ORDER))
    f.manager.ex.fail_on.append("make verify")  # type: ignore[attr-defined]
    assert await wait_run(f, run2.id) == RunStatus.awaiting_feedback
    for _ in range(100):
        if any(e.message.startswith("retro:") for e in f.store.list_events(run2.id)):
            break
        await asyncio.sleep(0.05)
    assert len(f.store.list_proposals(WF, "pending")) == 1


async def test_learning_can_be_turned_off_per_workflow(make_factory):
    f = make_factory(FakeExecutor(fail_on=["make verify"]))
    app, ctx, c = await _client(f)
    async with c:
        r = await c.patch(f"/api/workflows/{WF}/draft/settings", json={"learn_from_runs": False})
        assert r.status_code == 200 and r.json()["learn_from_runs"] is False and r.json()["spec_review"] == "off"
        assert (await c.post(f"/api/workflows/{WF}/draft/publish", json={"note": "no learning"})).status_code == 201
    await ctx.__aexit__(None, None, None)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    await asyncio.sleep(0.3)
    assert f.store.list_proposals(WF) == []


# ------------------------------------------------------ admin decision --
async def test_only_an_admin_accepts_and_it_lands_in_the_draft(make_factory):
    f = make_factory(FakeExecutor(fail_on=["make verify"] * 2))
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    [p] = await _proposals(f)
    from agent_factory.actions import accept_learning

    token = _current.set(Identity("bob", "member"))
    try:
        try:
            accept_learning(f, WF, p.id)
            raise AssertionError("a member must not change what agents are taught")
        except PermissionError:
            pass
    finally:
        _current.reset(token)

    app, ctx, c = await _client(f)
    async with c:
        listed = (await c.get(f"/api/workflows/{WF}/learnings")).json()
        assert [x["id"] for x in listed] == [p.id]
        r = await c.post(f"/api/workflows/{WF}/learnings/{p.id}/accept")
        assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["decided_by"]
        draft = (await c.get(f"/api/workflows/{WF}/draft")).json()
        dev = next(a for a in draft["agents"] if a["spec"]["id"] == "developer")
        assert f"- {p.lesson}" in dev["spec"]["learnings"], "accepted lessons go to the draft, not the live version"
        live = f.workflows[WF].get(f.workflows[WF].active_version())
        assert p.lesson not in live.agents["developer"].learnings, "nothing changes until the draft is published"
        assert (await c.post(f"/api/workflows/{WF}/learnings/{p.id}/accept")).status_code == 409
        assert (await c.get(f"/api/workflows/{WF}/learnings")).json() == []
        assert len((await c.get(f"/api/workflows/{WF}/learnings?status=accepted")).json()) == 1
    await ctx.__aexit__(None, None, None)


async def test_reject(make_factory):
    f = make_factory(FakeExecutor(fail_on=["make verify"]))
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    [p] = await _proposals(f)
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post(f"/api/workflows/{WF}/learnings/{p.id}/reject")
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        assert (await c.post(f"/api/workflows/{WF}/learnings/nope/reject")).status_code == 404
    await ctx.__aexit__(None, None, None)
    draft = f.workflows[WF].draft.get()[1]
    assert p.lesson not in draft.agents["developer"].learnings
