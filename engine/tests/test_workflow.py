"""Lines as versioned data: blueprint parsing, validation, seeding, pinning,
export/import and the generic spec-driven agent station."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ORDER, default_skill_names, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.engine.workflows import WorkflowError
from agent_factory.models import EventKind, RunStatus
from agent_factory.workflow import (
    AgentSpec,
    WorkflowDoc,
    export_workflow_dir,
    load_workflow_dir,
    parse_agent_md,
    render_agent_md,
    validate_workflow,
)

WF = "fastapi-service"
REPO = Path(__file__).resolve().parents[2]
BLUEPRINT = REPO / "workflow-templates" / "default"
SKILLS = REPO / "plugin" / "skills"
DEFAULTS = default_skill_names()  # imported on first start (config: default_skills)


def test_default_blueprint_is_valid_and_matches_the_mvp_line() -> None:
    doc = load_workflow_dir(BLUEPRINT)
    assert validate_workflow(doc, SKILLS, DEFAULTS) == []
    assert [s.id for s in doc.forward_stations()] == [
        "intake",
        "design",
        "build",
        "verify",
        "package",
        "deploy",
        "acceptance",
        "deliver",
    ]
    assert doc.next_forward("deploy") == "acceptance"
    assert doc.station("deploy_fix").next == "deploy"
    assert doc.agents["verifier"].observe_only
    assert "Task" in doc.agents["developer"].effective_tools()
    assert doc.repair_targets() == {"build", "deploy_fix"}


def test_agent_md_roundtrip() -> None:
    spec = load_workflow_dir(BLUEPRINT).agents["architect"]
    assert parse_agent_md(render_agent_md(spec)) == spec


@pytest.mark.parametrize(
    "mutate, problem",
    [
        (lambda d: d.stations[3].__setattr__("on_fail", "nowhere"), "unknown station"),
        (lambda d: d.stations[0].__setattr__("agent", "ghost"), "unknown agent"),
        (lambda d: d.agents["verifier"].__setattr__("tools", "builder"), "observe-only"),
        (lambda d: d.agents["intake"].skills.append("no-such-skill"), "unknown skill"),
        (lambda d: d.stations[6].__setattr__("next", None), "needs `next`"),
    ],
)
def test_validation_catches_broken_lines(mutate, problem) -> None:
    doc = load_workflow_dir(BLUEPRINT)
    mutate(doc)
    assert any(problem in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_presets_cannot_be_widened_to_dangerous_tools() -> None:
    with pytest.raises(ValueError):
        AgentSpec(id="x-agent", tools="observer", extra_tools=["Bash"], prompt="p")
    spec = AgentSpec(id="x-agent", tools="builder", disallowed_tools=["Task"], extra_tools=["WebSearch"], prompt="p")
    assert "Task" not in spec.effective_tools() and "WebSearch" in spec.effective_tools()


def _custom_line(tmp_path: Path) -> Path:
    """The default blueprint plus a security-reviewer station after build."""
    doc = load_workflow_dir(BLUEPRINT)
    doc.agents["security-reviewer"] = AgentSpec(
        id="security-reviewer",
        description="OWASP review",
        model="default",
        tools="reviewer",
        skills=["factory-station-contract"],
        produces=[],
        prompt="Review the code for OWASP issues.\n",
    )
    stations = doc.model_dump()["stations"]
    stations.insert(3, {"id": "security-review", "kind": "agent", "agent": "security-reviewer", "on_fail": "build"})
    out = tmp_path / "custom"
    export_workflow_dir(WorkflowDoc.model_validate({**doc.model_dump(), "stations": stations}), out)
    return out


async def test_seeded_on_first_start_and_never_overwritten(make_factory) -> None:
    f = make_factory()
    assert [v.version for v in f.workflows[WF].versions()] == [1]
    assert f.workflows[WF].ensure_seeded() is None  # second start: no new version
    assert f.workflows[WF].get().template == "default"
    assert not f.workflows[WF].template_update_available()


async def test_invalid_import_is_rejected_and_nothing_stored(make_factory, tmp_path) -> None:
    f = make_factory()
    path = _custom_line(tmp_path)
    (path / "workflow.yaml").write_text(
        (path / "workflow.yaml").read_text().replace("on_fail: build", "on_fail: nope", 1)
    )
    with pytest.raises(WorkflowError):
        f.workflows[WF].import_dir(path, "broken")
    assert len(f.workflows[WF].versions()) == 1


async def test_runs_are_pinned_to_their_line_version(make_factory, tmp_path) -> None:
    f = make_factory()
    order = f.manager.create_order(ORDER)
    run1 = f.manager.start_run(order)
    v2 = f.workflows[WF].import_dir(_custom_line(tmp_path), "add security-review after build")
    assert v2 == 2 and f.workflows[WF].active_version() == 2
    assert await wait_run(f, run1.id) == RunStatus.awaiting_feedback
    assert f.store.get_run(run1.id).workflow_version == 1
    stations1 = {e.station for e in f.store.list_events(run1.id) if e.kind == EventKind.station_finished}
    assert "security-review" not in stations1, "v1 run must not pick up the v2 station"

    run2 = f.manager.feedback(order.id, "tighten input validation")
    assert run2.workflow_version == 2
    assert await wait_run(f, run2.id) == RunStatus.awaiting_feedback
    stations2 = [e.station for e in f.store.list_events(run2.id) if e.kind == EventKind.station_finished]
    assert stations2.index("security-review") == stations2.index("build") + 1


async def test_generic_agent_failure_routes_findings_to_its_on_fail(make_factory, tmp_path) -> None:
    agents = FakeAgentRunner(fail_once=["security-reviewer"])
    f = make_factory(agents=agents)
    f.workflows[WF].import_dir(_custom_line(tmp_path), "add security-review")
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    builds = [c for c in agents.calls if c.role == "developer"]
    assert len(builds) == 2 and "simulated finding" in builds[1].prompt
    reviewer = next(c for c in agents.calls if c.role == "security-reviewer")
    assert reviewer.observe_only and "Write" not in reviewer.tools
    assert reviewer.protected_paths, "custom agents never see holdout scenarios"


async def test_rollback_by_activating_an_old_version(make_factory, tmp_path) -> None:
    f = make_factory()
    f.workflows[WF].import_dir(_custom_line(tmp_path), "v2")
    f.workflows[WF].activate(1)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert run.workflow_version == 1
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback


def test_export_import_roundtrip(tmp_path) -> None:
    doc = load_workflow_dir(BLUEPRINT)
    export_workflow_dir(doc, tmp_path / "x")
    again = load_workflow_dir(tmp_path / "x")
    assert again.stations == doc.stations and again.agents == doc.agents
