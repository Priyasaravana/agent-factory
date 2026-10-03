"""Quality pillars (ADR-0024): every signal has one pillar; reports, evidence and
Outcomes group by pillar; uncovered pillars are shown as uncovered, never 100%."""

from __future__ import annotations

import json

from conftest import PRODUCT, wait_run
from test_outcomes import _facts
from test_readiness import _generated_repo

from agent_factory import evidence
from agent_factory.models import ChangeStatus
from agent_factory.outcomes import compute
from agent_factory.pillars import IDS, PILLARS, evidence_pillar, tally
from agent_factory.readiness import PILLAR_OF, SECRET_SCAN, SIGNALS, score

# The agreed primary pillar of every signal. Changing one is a deliberate decision.
MAPPING = {
    "security": {"secret_scan", "container_nonroot", "codeowners", "dependency_updates"},
    "reliability": {"health_endpoints"},
    "performance": set(),
    "operability": {"structured_logs", "metrics", "tracing"},
    "cost": set(),
    "interoperability": {"design_docs"},
    "usability": set(),
    "maintainability": {
        "readme",
        "linter",
        "formatter",
        "unit_tests",
        "agents_md",
        "verify_target",
        "precommit",
        "ci",
        "coverage_gate",
    },  # fmt: skip
    "portability": {"lockfile"},
    "compliance": {"acceptance_tests", "requirements_traced", "e2e_tests"},
}


def test_ten_pillars_in_a_fixed_order():
    assert IDS == list(MAPPING) and len(set(IDS)) == 10
    assert all(p.question.endswith("?") for p in PILLARS)


def test_every_signal_has_exactly_one_known_pillar():
    for s in [*SIGNALS, SECRET_SCAN]:
        assert s.pillar in IDS, f"{s.id}: unknown pillar {s.pillar!r}"
    got = {p: {sid for sid, q in PILLAR_OF.items() if q == p} for p in IDS}
    assert got == MAPPING


def test_the_scorecard_reports_each_pillar_and_the_level_gate_is_unchanged(tmp_path):
    repo = _generated_repo(tmp_path)
    (repo / "README.md").unlink()
    card = score(repo, secret_scan_ok=True)
    assert card.level == 0, "pillars never change the level"
    data = card.as_data()
    pillars = {p["id"]: (p["passed"], p["applicable"]) for p in data["pillars"]}
    assert pillars["maintainability"] == (8, 9) and pillars["security"] == (4, 4)
    assert pillars["performance"] == pillars["cost"] == pillars["usability"] == (0, 0), "uncovered"
    assert all("pillar" in s for s in data["signals"])


def test_old_scorecards_without_pillars_are_tallied_by_signal_id():
    t = tally([{"id": "metrics", "ok": True}, {"id": "readme", "ok": False}, {"id": "gone", "ok": True}], PILLAR_OF)
    assert t["operability"] == {"passed": 1, "applicable": 1}
    assert t["maintainability"] == {"passed": 0, "applicable": 1}
    assert sum(v["applicable"] for v in t.values()) == 2, "unknown signals are skipped"


def test_evidence_files_have_a_primary_pillar():
    assert evidence_pillar("sbom.spdx.json") == "security"
    assert evidence_pillar("traceability.json") == evidence_pillar("spec/spec.md") == "compliance"
    assert evidence_pillar("sessions/01-build-developer.jsonl") == "operability"
    assert evidence_pillar("spec") is None and evidence_pillar("notes.txt") is None


async def test_the_sealed_evidence_has_a_pillar_index(make_factory):
    f = make_factory()
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    folder = f.manager.ws.data_dir / "artifacts" / change.id
    index = json.loads((folder / evidence.PILLAR_INDEX).read_text())
    by = {p["id"]: p for p in index["pillars"]}
    assert list(by) == IDS
    assert {"readiness.json", "traceability.json", "review.json", "provenance.json"} <= set(by["compliance"]["files"])
    assert "events.jsonl" in by["operability"]["files"]
    assert {s["id"] for s in by["security"]["signals"]} == MAPPING["security"]
    assert by["security"]["passed"] == by["security"]["applicable"] == 4
    assert by["performance"]["applicable"] == 0
    seal = evidence.latest_seal(f.store, change.id)
    assert evidence.PILLAR_INDEX in {x["path"] for x in json.loads((folder / "manifest.json").read_text())["files"]}
    assert evidence.verify(folder, seal["sha256"]).intact, "the index is one more sealed file"


def test_outcomes_pillar_coverage_of_live_apps():
    """A: every signal passes. B: an old scorecard (no pillar keys) missing the README. X is archived."""
    f = _facts()
    every = [{"id": sid, "ok": True, "pillar": p} for sid, p in PILLAR_OF.items()]
    f.readiness_signals = {
        "A": every,
        "B": [{"id": sid, "ok": sid != "readme"} for sid in PILLAR_OF],
    }
    o = compute(f)
    by = {p.pillar: p for p in o.quality_pillars}
    assert list(by) == IDS
    m = by["maintainability"]
    assert (m.signals, m.passed, m.applicable, m.coverage, m.apps_full) == (9, 17, 18, round(17 / 18, 4), 1)
    s = by["security"]
    assert (s.signals, s.passed, s.applicable, s.coverage, s.apps_full) == (4, 8, 8, 1.0, 2)
    for uncovered in ("performance", "cost", "usability"):
        u = by[uncovered]
        assert (u.signals, u.applicable, u.coverage, u.apps_full) == (0, 0, None, 0), "uncovered, not 100%"
    assert [a.change_id for a in o.quality_by_app] == ["A", "B"], "by app title"
    b = {p.pillar: p for p in o.quality_by_app[1].pillars}
    assert (b["maintainability"].passed, b["maintainability"].applicable) == (8, 9)


def test_outcomes_without_scorecards_have_no_made_up_coverage():
    f = _facts()
    o = compute(f)
    assert o.quality_by_app == [] and all(p.coverage is None for p in o.quality_pillars)
    assert {p.pillar: p.signals for p in o.quality_pillars}["security"] == 4
