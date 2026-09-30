"""Spec review station (ADR-0020): the reviewer reports per requirement, the engine judges."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import ORDER, default_skill_names, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.models import EventKind, RunStatus
from agent_factory.review import judge
from agent_factory.workflow import load_workflow_dir, validate_workflow

REPO = Path(__file__).resolve().parents[2]


def _report(*statuses: tuple[str, str], findings: list[dict] | None = None) -> dict:
    return {
        "summary": "s",
        "requirements": [{"id": i, "status": st, "where": f"app/main.py ({i})"} for i, st in statuses],
        "findings": findings or [],
    }


# ------------------------------------------------------------------ judge --
def test_all_implemented_with_minor_findings_passes():
    j = judge(["R1", "R2"], _report(("R1", "implemented"), ("R2", "implemented"), findings=[
        {"severity": "minor", "message": "rename helper", "file": "app/util.py"},
    ]))  # fmt: skip
    assert j.passed and j.complete and (j.implemented, j.total) == (2, 2)
    assert j.minor == ["minor: rename helper (app/util.py)"] and j.blocking == []
    assert j.headline() == "review: 2/2 requirements implemented, 0 blocking, 1 minor"


def test_partial_missing_or_major_findings_block():
    j = judge(["R1", "R2"], _report(("R1", "partial"), ("R2", "implemented"), findings=[
        {"severity": "major", "message": "returns 200, spec says 201", "file": "app/main.py", "requirement": "R2"},
    ]))  # fmt: skip
    assert not j.passed and j.complete
    assert j.blocking == [
        "R1 partial: app/main.py (R1)",
        "major: [R2] returns 200, spec says 201 (app/main.py)",
    ]


def test_a_review_that_skips_or_invents_requirements_is_incomplete():
    j = judge(["R1", "R2", "R3"], _report(("R1", "implemented"), ("R9", "implemented")))
    assert not j.complete and not j.passed
    assert j.problems == [
        "requirement R2 not reviewed",
        "requirement R3 not reviewed",
        "review cites unknown requirement R9",
    ]
    assert not judge([], _report()).complete, "nothing to review against is not a pass"


# ---------------------------------------------------------------- station --
class Reviewer(FakeAgentRunner):
    """Fake agents whose review reports are scripted per call."""

    def __init__(self, reports: list) -> None:
        super().__init__()
        self.reports = reports

    def _structured(self, req):  # noqa: ANN001
        props = (req.output_schema or {}).get("properties", {})
        if "findings" in props and "requirements" in props and self.reports:
            return self.reports.pop(0)
        return super()._structured(req)


def _events(f, run_id: str, prefix: str) -> list:
    return [e for e in f.store.list_events(run_id) if e.kind == EventKind.decision and e.message.startswith(prefix)]


async def test_review_passes_and_leaves_evidence(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    [ev] = _events(f, run.id, "review:")
    assert ev.message == "review: 2/2 requirements implemented, 0 blocking, 1 minor"
    evidence = json.loads((f.manager.ws.data_dir / "artifacts" / run.id / "review.json").read_text())
    assert evidence["judgement"]["passed"] and evidence["report"]["requirements"][0]["id"] == "R1"
    from agent_factory.actions import get_run_spec

    view = get_run_spec(f, run.id).review
    assert view and view.passed and (view.implemented, view.total) == (2, 2)
    assert [r.status for r in view.requirements] == ["implemented", "implemented"]
    assert view.findings[0].severity == "minor"
    review_call = next(c for c in agents.calls if c.role == "reviewer")
    assert "R2: Filter by tag" in review_call.prompt and "git diff --stat main...HEAD" in review_call.prompt


async def test_blocking_findings_go_back_to_build_as_evidence(make_factory):
    agents = Reviewer([
        _report(("R1", "implemented"), ("R2", "missing")),
        _report(("R1", "implemented"), ("R2", "implemented")),
    ])  # fmt: skip
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    builds = [c for c in agents.calls if c.role == "developer"]
    assert len(builds) == 2, "the builder got a second go"
    assert "R2 missing" in builds[-1].prompt and "Spec review found problems" in builds[-1].prompt
    reviews = [c for c in agents.calls if c.role == "reviewer"]
    assert "R2 missing" in reviews[-1].prompt, "the second review checks the earlier findings"
    assert [e.message.split(",")[0] for e in _events(f, run.id, "review:")] == [
        "review: 1/2 requirements implemented",
        "review: 2/2 requirements implemented",
    ]


async def test_an_incomplete_review_is_retried_once_then_held(make_factory):
    ok = _report(("R1", "implemented"), ("R2", "implemented"))
    agents = Reviewer([_report(("R1", "implemented")), ok])
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    retry = [c for c in agents.calls if c.role == "reviewer"][1]
    assert "YOUR PREVIOUS REPORT WAS INCOMPLETE" in retry.prompt and "R2 not reviewed" in retry.prompt

    agents2 = Reviewer([_report(("R1", "implemented"))] * 2)
    f2 = make_factory(agents=agents2)
    run2 = f2.manager.start_run(f2.manager.create_order(ORDER))
    assert await wait_run(f2, run2.id) == RunStatus.held, "the reviewer's gap is not the builder's to fix"
    assert len([c for c in agents2.calls if c.role == "developer"]) == 1
    assert "requirement R2 not reviewed" in (f2.store.get_run(run2.id).last_failure or "")
    assert any(e.kind == EventKind.decision and "review incomplete" in e.message for e in f2.store.list_events(run2.id))


# ------------------------------------------------------------- workflow --
def test_the_review_station_must_be_observe_only_and_after_build():
    doc = load_workflow_dir(REPO / "workflow-templates" / "default")
    skills, defaults = REPO / "plugin" / "skills", default_skill_names()
    assert validate_workflow(doc, skills, defaults) == []
    doc.agents["reviewer"].tools = "builder"
    assert any("must use an observe-only agent" in p for p in validate_workflow(doc, skills, defaults))
    doc = load_workflow_dir(REPO / "workflow-templates" / "default")
    ids = [s.id for s in doc.stations]
    doc.stations.insert(ids.index("build"), doc.stations.pop(ids.index("review")))
    assert any("'build' (build) must come before 'review'" in p for p in validate_workflow(doc, skills, defaults))
