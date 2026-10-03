"""Level 3 golden path: readiness scorecard, the Quality gate station, CODEOWNERS
rendering and SBOM + provenance in the Package station."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import PRODUCT, REPO, wait_run

from agent_factory.engine.workspace import render_codeowners
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.readiness import SIGNALS, score

TEMPLATE = REPO / "templates" / "fastapi-service"


def _generated_repo(tmp_path: Path) -> Path:
    """The template plus what Intake/Design/Build add in every change."""
    repo = tmp_path / "repo"
    shutil.copytree(TEMPLATE, repo, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    (repo / "docs").mkdir()
    for f in ("spec.md", "design.md", "openapi.yaml"):
        (repo / "docs" / f).write_text("x")
    (repo / "docs" / "requirements.yaml").write_text("- {id: R1, title: Save, detail: d}\n")
    (repo / "tests" / "acceptance").mkdir(parents=True)
    (repo / "tests" / "acceptance" / "scenarios.yaml").write_text(
        "- {id: A1, given: g, when: w, then: t, covers: [R1]}\n"
    )
    (repo / "tests" / "test_acceptance.py").write_text(
        'import pytest\n\n\n@pytest.mark.req("R1")\ndef test_a() -> None:\n    assert True\n'
    )
    return repo


def test_the_golden_path_template_is_level_3_once_the_run_adds_its_docs(tmp_path):
    repo = _generated_repo(tmp_path)
    card = score(repo, secret_scan_ok=True)
    assert card.missing() == []
    assert card.level == 3 and card.points == card.max_points
    # the bare template (before Intake/Design/Build) is Level 2: docs and acceptance tests come from the change
    bare = score(TEMPLATE, secret_scan_ok=True)
    assert bare.level == 2
    assert {s.id for s in bare.missing()} == {"acceptance_tests", "design_docs", "requirements_traced"}


def test_each_missing_signal_drops_the_level_and_says_how_to_fix_it(tmp_path):
    repo = _generated_repo(tmp_path)
    (repo / "app" / "observability.py").write_text("# removed\n")
    card = score(repo, secret_scan_ok=True)
    missing = {s.id: s for s in card.missing()}
    assert card.level == 1  # JSON logging is a Level 2 signal
    assert {"structured_logs", "metrics", "tracing"} <= set(missing)
    assert "observability" in missing["metrics"].hint


def test_a_secret_in_the_repo_blocks_level_3(tmp_path):
    card = score(_generated_repo(tmp_path), secret_scan_ok=False)
    assert card.level == 2 and [s.id for s in card.missing()] == ["secret_scan"]


def test_signals_are_unique_and_levelled():
    ids = [s.id for s in SIGNALS]
    assert len(ids) == len(set(ids))
    assert {s.level for s in SIGNALS} == {1, 2, 3}


def test_codeowners_is_rendered_from_the_publishing_owner(tmp_path):
    for owner, expected in (("my-org/platform", "* @my-org/platform"), ("", "# * @your-org/your-team")):
        repo = tmp_path / (owner.replace("/", "-") or "none")
        shutil.copytree(TEMPLATE / ".github", repo / ".github")
        render_codeowners(repo, owner)
        assert expected in (repo / ".github" / "CODEOWNERS").read_text()


async def test_readiness_station_scores_every_run_and_records_sbom_and_provenance(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    events = f.store.list_events(change.id)
    card = next(e for e in events if e.message.startswith("agent readiness"))
    assert card.kind == EventKind.decision and card.data["readiness"]["level"] == 3
    assert any("gitleaks" in c and "--no-git" in c for c in ex.calls), "secret scan ran on the worktree"
    sbom = next(c for c in ex.calls if "anchore/syft" in c)
    out = f.manager.ws.data_dir / "artifacts" / change.id
    assert f"spdx-json={out}/sbom.spdx.json" in sbom
    prov = json.loads((out / "provenance.json").read_text())
    assert prov["source"]["branch"] == f"run/{change.id}" and prov["builder"]["workflow_version"] == 1
    assert prov["subject"]["image"].startswith("bookmarks-service:")


async def test_a_failed_secret_scan_sends_redacted_evidence_back_to_build(make_factory):
    ex = FakeExecutor(fail_on=["gitleaks"])  # fails once, then the "fixed" repo passes
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    finished = [
        (e.station, e.data.get("outcome"))
        for e in f.store.list_events(change.id)
        if e.kind == EventKind.station_finished
    ]
    assert ("quality-gate", "failed") in finished
    assert finished.index(("quality-gate", "failed")) < max(i for i, (s, _) in enumerate(finished) if s == "implement")
    build_calls = [c for c in f.manager.agents.calls if c.role == "developer"]
    assert "Secret scan" in build_calls[-1].prompt and "no secrets in the repo" in build_calls[-1].prompt
    assert "Security:\n- [L3] no secrets in the repo" in build_calls[-1].prompt, "missing signals grouped by pillar"
