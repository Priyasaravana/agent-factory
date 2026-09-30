"""Spec-driven development (ADR-0017): numbered requirements, traceability,
the optional spec review gate and bring-your-own-spec."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from conftest import ORDER, wait_run
from test_api import _client

from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.fake import _SPEC
from agent_factory.agents.runner import AgentResult
from agent_factory.identity import Identity, _current
from agent_factory.models import CreateOrderInput, EventKind, RunStatus
from agent_factory.traceability import coverage_problems, diff, matrix, untraced


def _messages(f, run_id: str) -> list[str]:
    return [e.message for e in f.store.list_events(run_id)]


def _set_gate(f, mode: str) -> None:
    w = f.workflows["fastapi-service"]
    w.draft.set_spec_review(mode)
    w.draft.publish(f"spec review {mode}")


class SpecRunner(FakeAgentRunner):
    """Fake agents whose intake output can be swapped per call."""

    def __init__(self, specs: list[dict]) -> None:
        super().__init__()
        self.specs = specs

    def _structured(self, req):  # noqa: ANN001
        if req.role == "intake" and self.specs:
            return self.specs.pop(0)
        return super()._structured(req)


# ------------------------------------------------------------ requirements --
async def test_intake_writes_numbered_requirements_and_traceable_scenarios(make_factory):
    f = make_factory()
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    repo = f.manager.ws.product_dir("bookmarks-service")
    assert "id: R1" in (repo / "docs" / "requirements.yaml").read_text()
    assert "covers:" in (repo / "tests" / "acceptance" / "scenarios.yaml").read_text()
    msgs = _messages(f, run.id)
    assert "spec: 2 numbered requirements" in msgs
    readiness = next(e for e in f.store.list_events(run.id) if e.message.startswith("agent readiness"))
    signals = {s["id"]: s["ok"] for s in readiness.data["readiness"]["signals"]}
    assert signals["requirements_traced"] is True


async def test_an_untraceable_spec_gets_one_correction_then_holds(make_factory):
    bad = copy.deepcopy(_SPEC)
    bad["acceptance_scenarios"] = bad["acceptance_scenarios"][:1]  # R2 uncovered
    agents = SpecRunner([bad, copy.deepcopy(_SPEC)])
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    retry = [c for c in agents.calls if c.role == "intake"][1]
    assert "requirement R2 has no acceptance scenario" in retry.prompt

    agents2 = SpecRunner([copy.deepcopy(bad), copy.deepcopy(bad)])
    f2 = make_factory(agents=agents2)
    run2 = f2.manager.start_run(f2.manager.create_order(ORDER))
    assert await wait_run(f2, run2.id) == RunStatus.held
    assert "requirement R2 has no acceptance scenario" in (f2.store.get_run(run2.id).last_failure or "")


async def test_feedback_updates_the_spec_first_and_reports_what_changed(make_factory):
    changed = copy.deepcopy(_SPEC)
    changed["requirements"].append({"id": "R3", "title": "Reset", "detail": "DELETE /bookmarks resets"})
    changed["acceptance_scenarios"].append(
        {"id": "A3", "given": "g", "when": "DELETE /bookmarks", "then": "204", "covers": ["R3"]}
    )
    changed["requirements"][0]["detail"] = "POST /bookmarks now also accepts notes"
    agents = SpecRunner([copy.deepcopy(_SPEC), changed])
    f = make_factory(agents=agents)
    order = f.manager.create_order(ORDER)
    assert await wait_run(f, f.manager.start_run(order).id) == RunStatus.awaiting_feedback
    run2 = f.manager.feedback(order.id, "add a reset endpoint and notes")
    assert await wait_run(f, run2.id) == RunStatus.awaiting_feedback
    assert "spec updated: +R3 ~R1" in _messages(f, run2.id)
    second_intake = [c for c in agents.calls if c.role == "intake"][-1]
    assert "id: R1" in second_intake.prompt, "current requirements are handed back so ids stay stable"


# ------------------------------------------------------------ traceability --
async def test_acceptance_records_the_requirement_matrix(make_factory):
    f = make_factory()
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    assert "traceability: 1/2 requirements verified live by hidden scenarios" in _messages(f, run.id)
    artifact = f.manager.ws.data_dir / "artifacts" / run.id / "traceability.json"
    assert '"R2"' in artifact.read_text(), "the matrix is kept as a file too"
    from agent_factory.actions import get_run_spec

    view = get_run_spec(f, run.id)
    rows = {r.id: r for r in view.traceability}
    assert rows["R1"].scenarios == ["A1"] and rows["R1"].tests == 1 and rows["R1"].holdout == []
    assert rows["R2"].holdout == [{"scenario": "H1", "passed": True}]
    assert view.holdout_count == 1 and [r.id for r in view.requirements] == ["R1", "R2"]
    assert not any((view.changes or {}).values()), "a first spec is not a change"


def test_traceability_rules(tmp_path: Path):
    reqs = [{"id": "R1"}, {"id": "R2"}]
    assert coverage_problems(reqs, [{"id": "A1", "covers": ["R1", "R9"]}], "acceptance") == [
        "acceptance scenario A1 covers unknown requirement R9",
        "requirement R2 has no acceptance scenario",
    ]
    assert diff([{"id": "R1", "title": "a"}, {"id": "R2", "title": "b"}], [{"id": "R1", "title": "a2"}]) == {
        "added": [],
        "changed": ["R1"],
        "removed": ["R2"],
    }
    assert untraced(tmp_path) == ["docs/requirements.yaml missing or empty"]
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.yaml").write_text("- {id: R1, title: t}\n- {id: R2, title: u}\n")
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    (tmp_path / "tests" / "acceptance" / "scenarios.yaml").write_text(
        "- {id: A1, given: g, when: w, then: t, covers: [R1, R2]}\n"
    )
    (tmp_path / "tests" / "test_x.py").write_text('@pytest.mark.req("R1")\ndef test_x(): ...\n')
    assert untraced(tmp_path) == ['R2 has no test tagged @pytest.mark.req("R2")']
    rows = matrix(tmp_path)
    assert [(r["id"], r["tests"], r["scenarios"]) for r in rows] == [("R1", 1, ["A1"]), ("R2", 0, ["A1"])]


# ------------------------------------------------------------- review gate --
async def test_gate_off_by_default_never_pauses(make_factory):
    f = make_factory()
    assert f.workflows["fastapi-service"].get(f.workflows["fastapi-service"].active_version()).spec_review == "off"
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback


async def test_first_iteration_gate_pauses_after_design_until_approved(make_factory):
    f = make_factory()
    _set_gate(f, "first")
    order = f.manager.create_order(ORDER)
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == RunStatus.awaiting_approval
    finished = [e.station for e in f.store.list_events(run.id) if e.kind == EventKind.station_finished]
    assert finished == ["intake", "design"], "nothing is built before approval"
    assert f.store.get_run(run.id).current_station == "build"

    app, ctx, c = await _client(f)
    async with c:
        spec = (await c.get(f"/api/runs/{run.id}/spec")).json()
        assert spec["gate"] == "first" and spec["status"] == "awaiting_approval"
        assert spec["product"].startswith("# Bookmarks") and spec["technical"].startswith("# Design")
        assert [r["id"] for r in spec["requirements"]] == ["R1", "R2"] and spec["holdout_count"] == 1
        assert (await c.post(f"/api/orders/{order.id}/feedback", json={"text": "x y z"})).status_code == 409
        r = await c.post(f"/api/runs/{run.id}/spec/approve")
        assert r.status_code == 200 and r.json()["spec_approved_by"] == "local"
        assert await wait_run(f, run.id) == RunStatus.awaiting_feedback  # before the app shuts down
    await ctx.__aexit__(None, None, None)
    assert "spec approved by local" in _messages(f, run.id)

    run2 = f.manager.feedback(order.id, "add a reset endpoint")
    assert await wait_run(f, run2.id) == RunStatus.awaiting_feedback, "'first' does not gate later iterations"


async def test_always_gate_request_changes_and_edit(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    _set_gate(f, "always")
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_approval

    f.manager.request_spec_changes(run.id, "priya", "Tags must be case-insensitive")
    assert await wait_run(f, run.id) == RunStatus.awaiting_approval, "back through intake + design, then the gate"
    intakes = [c for c in agents.calls if c.role == "intake"]
    designs = [c for c in agents.calls if c.role == "architect"]
    assert len(intakes) == 2 and "Tags must be case-insensitive (by priya)" in intakes[-1].prompt
    assert len(designs) == 2 and "Tags must be case-insensitive" in designs[-1].prompt

    await f.manager.edit_spec(run.id, "priya", "# Bookmarks (edited)\n", None)
    wt = f.manager.ws.run_dir(run.id)
    assert (wt / "docs" / "spec.md").read_text() == "# Bookmarks (edited)\n"
    log = await f.manager.ws.git("log --oneline -1", wt)
    assert "spec edited in review by priya" in log.output
    f.manager.approve_spec(run.id, "priya")
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback


async def test_only_the_creator_or_an_admin_decides_on_the_spec(make_factory):
    f = make_factory()
    _set_gate(f, "first")
    order = f.manager.create_order(ORDER)
    order.created_by = "alice"
    f.store.save_order(order)
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == RunStatus.awaiting_approval
    from agent_factory.actions import approve_spec

    token = _current.set(Identity("bob", "member"))
    try:
        with pytest.raises(PermissionError):
            await approve_spec(f, run.id)
    finally:
        _current.reset(token)
    token = _current.set(Identity("alice", "member"))
    try:
        assert (await approve_spec(f, run.id)).spec_approved_by == "alice"
    finally:
        _current.reset(token)


async def test_workflow_setting_via_api(make_factory):
    f = make_factory()
    app, ctx, c = await _client(f)
    async with c:
        r = await c.patch("/api/workflows/fastapi-service/draft/settings", json={"spec_review": "always"})
        assert r.status_code == 200 and r.json()["spec_review"] == "always"
        bad = await c.patch("/api/workflows/fastapi-service/draft/settings", json={"spec_review": "sometimes"})
        assert bad.status_code == 422
    await ctx.__aexit__(None, None, None)


# ---------------------------------------------------------- bring your spec --
async def test_bring_your_own_spec_keeps_its_wording(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    spec = CreateOrderInput(
        title="Bookmarks service",
        requirements="# Bookmarks\n1. Save a bookmark\n2. Filter by tag\n",
        requirements_format="spec",
    )
    order = f.manager.create_order(spec)
    assert order.requirements_format == "spec"
    assert await wait_run(f, f.manager.start_run(order).id) == RunStatus.awaiting_feedback
    intake = next(c for c in agents.calls if c.role == "intake")
    assert "EXISTING SPECIFICATION" in intake.prompt and "1:1" in intake.prompt


def test_unused_result_type_kept():
    assert AgentResult(ok=True).ok
