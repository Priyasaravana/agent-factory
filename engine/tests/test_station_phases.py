"""Stations named after the DevOps loop (ADR-0028): new names, phases, and versions
stored with the old names that keep working unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PRODUCT, default_skill_names, wait_run
from test_api import _client

from agent_factory import actions
from agent_factory.engine.workflows import WorkflowError
from agent_factory.models import ChangeStatus
from agent_factory.outcomes import compute
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CHECK_HANDLERS,
    HANDLER_INFO,
    LEGACY_HANDLERS,
    PHASE_IDS,
    STAGE_ORDER,
    WorkflowDoc,
    WorkflowStation,
    export_workflow_dir,
    load_workflow_dir,
    validate_workflow,
    workflow_warnings,
)

WF = "fastapi-service"
REPO = Path(__file__).resolve().parents[2]
DEFAULT = REPO / "workflow-templates" / "default"
SKILLS = REPO / "plugin" / "skills"

# the default lane as it was stored before ADR-0028
OLD_IDS = {
    "requirements": "intake",
    "implement": "build",
    "test": "verify",
    "quality-gate": "readiness",
    "code-review": "review",
    "build": "package",
    "deploy-repair": "deploy_fix",
    "handover": "deliver",
}


def _legacy(doc: WorkflowDoc) -> WorkflowDoc:
    """The same workflow with every station id (and route) under its old name."""
    old = {**{s.id: s.id for s in doc.stations}, **OLD_IDS}
    stations = []
    for s in doc.model_dump()["stations"]:
        s["id"] = old[s["id"]]
        s["on_fail"] = old.get(s["on_fail"]) if s["on_fail"] else None
        s["next"] = old.get(s["next"]) if s["next"] else None
        stations.append(s)
    return WorkflowDoc.model_validate({**doc.model_dump(), "stations": stations})


# ---------------------------------------------------------------- names --
def test_every_handler_has_a_label_a_phase_and_a_hint() -> None:
    assert set(HANDLER_INFO) == AGENT_HANDLERS | CHECK_HANDLERS
    for name, info in HANDLER_INFO.items():
        assert info.label and info.hint, name
        assert info.kind == ("agent" if name in AGENT_HANDLERS else "check"), name
        assert info.phase in PHASE_IDS or (name == "agent" and info.phase is None), name
    phase_order = [PHASE_IDS.index(HANDLER_INFO[h].phase or "plan") for h in STAGE_ORDER]
    assert phase_order == sorted(phase_order), "the fixed stage order reads left to right through the loop"


def test_the_default_lane_reads_as_the_devops_loop() -> None:
    doc = load_workflow_dir(DEFAULT)
    assert validate_workflow(doc, SKILLS, default_skill_names()) == []
    phases = doc.phases()
    assert [phases[s.id] for s in doc.forward_stations()] == [
        "plan", "plan", "code", "test", "test", "test", "test", "test", "test", "test", "release", "deploy",
        "validate", "handover",
    ]  # fmt: skip
    assert {s.id: s.label() for s in doc.stations}["sast"] == "Semgrep (code)"
    assert phases["deploy-repair"] == "deploy"
    assert {s.id: s.label() for s in doc.stations}["quality-gate"] == "Quality gate"
    assert not [w for w in workflow_warnings(doc) if "out of order" in w]


def test_build_meant_the_coding_agent_and_now_means_the_image() -> None:
    """The one name that changed meaning: decided by the station's kind."""
    assert WorkflowStation(id="build", kind="agent", agent="developer").resolved_handler() == "implement"
    assert WorkflowStation(id="build", kind="check").resolved_handler() == "build"
    assert WorkflowStation(id="package", kind="check").resolved_handler() == "build"
    assert WorkflowStation(id="x", kind="agent", agent="a", handler="review").resolved_handler() == "code-review"
    assert WorkflowStation(id="lint-docs", kind="agent", agent="a").resolved_handler() == "agent"
    for kind, names in LEGACY_HANDLERS.items():
        known = AGENT_HANDLERS if kind == "agent" else CHECK_HANDLERS
        assert set(names.values()) <= known, kind


# --------------------------------------------------------------- phases --
def test_a_custom_step_sits_in_the_phase_before_it_unless_it_says_otherwise() -> None:
    doc = load_workflow_dir(DEFAULT)
    ids = [s.id for s in doc.stations]
    doc.stations.insert(
        ids.index("implement") + 1, WorkflowStation(id="security-review", kind="agent", agent="reviewer")
    )
    doc.stations.insert(0, WorkflowStation(id="triage", kind="agent", agent="intake"))
    phases = doc.phases()
    assert (phases["triage"], phases["security-review"]) == ("plan", "code")
    assert next(s for s in doc.stations if s.id == "security-review").label() == "security-review"

    doc.station("security-review").phase = "validate"
    warnings = [w for w in workflow_warnings(doc) if "out of order" in w]
    assert len(warnings) == 1 and warnings[0].startswith("station 'test' (test) comes after a later phase"), (
        "a step marked validate before the tests makes the lane read out of order (a warning, not an error)"
    )
    assert validate_workflow(doc, SKILLS, default_skill_names()) == []


def test_an_unknown_phase_is_rejected() -> None:
    with pytest.raises(ValueError, match="phase"):
        WorkflowStation(id="x", kind="agent", agent="a", phase="operate")  # type: ignore[arg-type]


async def test_the_editor_can_set_a_custom_steps_phase(make_factory) -> None:
    d = make_factory().workflows[WF].draft
    d.add_station(WorkflowStation(id="docs-check", kind="agent", agent="reviewer"), position=3)
    assert d.get()[1].phases()["docs-check"] == "code"
    d.update_station("docs-check", {"phase": "test"})
    assert d.get()[1].phases()["docs-check"] == "test"
    d.update_station("docs-check", {"phase": None})
    assert d.get()[1].phases()["docs-check"] == "code"
    with pytest.raises(WorkflowError, match="phase must be one of"):
        d.update_station("docs-check", {"phase": "monitor"})
    d.update_station("test", {"handler": "verify"})  # an old handler name is understood
    assert d.get()[1].station("test").resolved_handler() == "test"


# ------------------------------------------------- versions with old names --
async def test_a_version_stored_with_the_old_names_still_runs(make_factory, tmp_path) -> None:
    """Changes and evidence are pinned to station ids, so old versions keep theirs;
    people still read the new names and phases."""
    f = make_factory()
    legacy = _legacy(load_workflow_dir(DEFAULT))
    assert validate_workflow(legacy, SKILLS, default_skill_names()) == []
    export_workflow_dir(legacy, tmp_path / "old")
    f.workflows[WF].import_dir(tmp_path / "old", "as stored before ADR-0028")
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    views = {s.id: s for s in actions.get_change(f, change.id).stations}
    assert (views["build"].handler, views["build"].label, views["build"].phase) == ("implement", "Implement", "code")
    assert (views["package"].handler, views["package"].label, views["package"].phase) == ("build", "Build", "release")
    assert views["deliver"].label == "Handover" and views["deliver"].state == "passed"

    # effort per station counts old and new names together
    from agent_factory.outcomes import gather

    names = {e.station for e in compute(gather(f.store, 30)).effort_by_station}
    assert "implement" in names and "build" not in names


async def test_the_catalog_tells_the_editor_names_and_phases(make_factory) -> None:
    app, ctx, c = await _client(make_factory())
    async with c:
        cat = (await c.get("/api/catalog")).json()
        assert [p["id"] for p in cat["phases"]] == PHASE_IDS
        assert cat["handler_info"]["quality-gate"] == {
            "kind": "check",
            "label": "Quality gate",
            "phase": "test",
            "hint": HANDLER_INFO["quality-gate"].hint,
        }
        draft = (await c.get(f"/api/workflows/{WF}/draft")).json()
        first = draft["stations"][0]
        assert (first["id"], first["label"], first["phase"]) == ("requirements", "Requirements", "plan")
        r = await c.patch(f"/api/workflows/{WF}/draft/stations/design", json={"phase": "monitor"})
        assert r.status_code >= 400, "a phase must be one of the loop's"
    await ctx.__aexit__(None, None, None)


async def test_an_old_check_id_still_resolves_in_a_draft(make_factory) -> None:
    d = make_factory().workflows[WF].draft
    d.add_station(WorkflowStation(id="verify", kind="check"))  # an old id: resolves to today's handler
    assert d.get()[1].station("verify").resolved_handler() == "test"
    with pytest.raises(WorkflowError, match="check stations need a handler"):
        d.add_station(WorkflowStation(id="lint", kind="check"))
