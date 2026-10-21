"""Station names (ADR-0028) and phases, which are the contract's columns (ADR-0039: discover,
design, plan, develop, verify, deliver, operate). Names from before ADR-0028 are no longer read."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import default_skill_names
from test_api import _client

from agent_factory.engine.workflows import WorkflowError
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CHECK_HANDLERS,
    HANDLER_INFO,
    PHASE_IDS,
    WorkflowStation,
    load_workflow_dir,
    validate_workflow,
)

WF = "new-product"
REPO = Path(__file__).resolve().parents[2]
DEFAULT = REPO / "workflow-templates" / "standard" / "lanes" / "new-product.yaml"
SKILLS = REPO / "plugin" / "skills"


# ---------------------------------------------------------------- names --
def test_every_handler_has_a_label_a_phase_and_a_hint() -> None:
    assert set(HANDLER_INFO) == AGENT_HANDLERS | CHECK_HANDLERS
    for name, info in HANDLER_INFO.items():
        assert info.label and info.hint, name
        assert info.kind == ("agent" if name in AGENT_HANDLERS else "check"), name
        assert info.phase in PHASE_IDS or (name == "agent" and info.phase is None), name


def test_the_default_lane_reads_in_phase_order() -> None:
    doc = load_workflow_dir(DEFAULT)
    assert validate_workflow(doc, SKILLS, default_skill_names()) == []
    phases = doc.phases()
    assert [phases[s] for s in doc.plan().stations] == [
        "design", "design", "develop", "verify", "verify", "verify", "verify", "verify", "verify", "verify", "verify",
        "deliver", "deliver", "deliver", "deliver",
    ]  # fmt: skip
    assert PHASE_IDS == ["discover", "design", "plan", "develop", "verify", "deliver", "operate"]
    assert {s.id: s.label() for s in doc.stations}["code-scan"] == "Code scan"
    assert phases["deploy-repair"] == "deliver"
    assert {s.id: s.label() for s in doc.stations}["quality-gate"] == "Quality gate"


def test_names_from_before_adr_0028_are_not_read() -> None:
    assert WorkflowStation(id="build", kind="check").resolved_handler() == "build"
    assert WorkflowStation(id="package", kind="check").resolved_handler() == "package"  # unknown: refused at publish
    assert WorkflowStation(id="lint-docs", kind="agent", agent="a").resolved_handler() == "agent"


# --------------------------------------------------------------- phases --
def test_a_custom_step_sits_in_the_phase_before_it_unless_it_says_otherwise() -> None:
    doc = load_workflow_dir(DEFAULT)
    ids = [s.id for s in doc.stations]
    doc.stations.insert(
        ids.index("implement") + 1,
        WorkflowStation(id="security-review", kind="agent", agent="reviewer", uses=["read-repo", "run-sandbox"]),
    )
    doc.stations.insert(0, WorkflowStation(id="triage", kind="agent", agent="intake"))
    phases = doc.phases()
    assert (phases["triage"], phases["security-review"]) == ("discover", "develop")
    assert next(s for s in doc.stations if s.id == "security-review").label() == "security-review"

    assert validate_workflow(doc, SKILLS, default_skill_names()) == []
    doc.station("security-review").phase = "deliver"
    # a step in deliver: the verify stations after it no longer fit (ADR-0037)
    assert any(
        "'test' sits in verify but comes after a deliver station" in e
        for e in validate_workflow(doc, SKILLS, default_skill_names())
    )


def test_an_unknown_phase_is_rejected() -> None:
    """Phases come from the registry (ADR-0039 §2), so publish names the unknown one."""
    doc = load_workflow_dir(DEFAULT)
    doc.stations.insert(0, WorkflowStation(id="x", kind="agent", agent="intake", phase="staging"))
    assert any("unknown phase 'staging'" in e for e in validate_workflow(doc, SKILLS, default_skill_names()))


async def test_the_editor_can_set_a_custom_steps_phase(make_factory) -> None:
    d = make_factory().workflows[WF].draft
    at = [s.id for s in d.get()[1].stations].index("implement") + 1
    d.add_station(WorkflowStation(id="docs-check", kind="agent", agent="reviewer"), position=at)
    assert d.get()[1].phases()["docs-check"] == "develop"
    d.update_station("docs-check", {"phase": "verify"})
    doc = d.get()[1]
    assert doc.phases()["docs-check"] == "verify"
    # moved to another phase box: it goes to the end of that box (ADR-0039 1c)
    after = [s.id for s in doc.stations][[s.id for s in doc.stations].index("docs-check") + 1]
    assert doc.phases()[after] == "deliver"
    d.update_station("docs-check", {"phase": None})
    assert d.get()[1].phases()["docs-check"] == "verify"  # the phase of the station before it
    with pytest.raises(WorkflowError, match="phase must be one of"):
        d.update_station("docs-check", {"phase": "monitor"})
    with pytest.raises(WorkflowError, match="unknown check handler"):
        d.update_station("test", {"handler": "verify"})  # names from before ADR-0028 are gone


async def test_the_catalog_tells_the_editor_names_and_phases(make_factory) -> None:
    app, ctx, c = await _client(make_factory())
    async with c:
        cat = (await c.get("/api/catalog")).json()
        assert [p["id"] for p in cat["phases"]] == PHASE_IDS
        assert cat["handler_info"]["quality-gate"] == {
            "kind": "check",
            "label": "Quality gate",
            "phase": "verify",
            "hint": HANDLER_INFO["quality-gate"].hint,
        }
        draft = (await c.get(f"/api/workflows/{WF}/draft")).json()
        first = draft["stations"][0]
        assert (first["id"], first["label"], first["phase"]) == ("requirements", "Requirements", "design")
        r = await c.patch(f"/api/workflows/{WF}/draft/stations/design", json={"phase": "monitor"})
        assert r.status_code >= 400, "a phase must be one of the loop's"
    await ctx.__aexit__(None, None, None)
