"""Station contract (ADR-0037 step 1a): station types, artifact schemas, the per-change ledger
and the publish checks (container fit: column + data + floor; role ids)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import PRODUCT, default_skill_names, wait_run
from jsonschema import Draft202012Validator

from agent_factory import artifacts, contract
from agent_factory.agents import FakeAgentRunner
from agent_factory.artifacts import Ledger
from agent_factory.config import FactoryConfig, load_config
from agent_factory.contract import STATION_TYPES, TYPE_OF_HANDLER, parse_need, parse_ref
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CHECK_HANDLERS,
    AgentSpec,
    WorkflowDoc,
    WorkflowStation,
    export_workflow_dir,
    load_workflow_dir,
    validate_workflow,
)

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "workflow-templates"
SKILLS = REPO / "plugin" / "skills"
SCHEMAS = REPO / "engine" / "src" / "agent_factory" / "schemas" / "artifacts"
WF = "new-product"


def _default() -> WorkflowDoc:
    return load_workflow_dir(TEMPLATES / "standard" / "lanes" / "new-product.yaml")


def _lane(name: str) -> WorkflowDoc:
    return load_workflow_dir(TEMPLATES / "standard" / "lanes" / f"{name}.yaml")


LANES = ["new-product", "product-change", "repo-assessment", "repo-change"]


def _errors(doc: WorkflowDoc, roles: tuple[str, ...] = tuple(contract.BUILTIN_ROLES)) -> list[str]:
    return validate_workflow(doc, SKILLS, default_skill_names(), roles)


def _without(doc: WorkflowDoc, *ids: str) -> WorkflowDoc:
    doc.stations = [s for s in doc.stations if s.id not in ids]
    for s in doc.stations:
        s.on_fail = None if s.on_fail in ids else s.on_fail
    return doc


# ------------------------------------------------------------- the library --
def test_every_builtin_handler_has_a_station_type() -> None:
    builtin = (AGENT_HANDLERS | CHECK_HANDLERS) - {"agent"}
    assert set(TYPE_OF_HANDLER) == builtin
    for t in STATION_TYPES.values():
        assert t.column in contract.COLUMN_IDS
        assert t.gives, f"{t.id} gives nothing"
        for h in t.implementations.values():
            assert h is None or h in AGENT_HANDLERS | CHECK_HANDLERS


def test_handler_pairs_share_one_contract() -> None:
    for factory, repo in [
        ("implement", "repo-implement"),
        ("test", "repo-test"),
        ("code-review", "repo-review"),
        ("handover", "pull-request"),
    ]:
        assert TYPE_OF_HANDLER[factory] == TYPE_OF_HANDLER[repo] == factory


@pytest.mark.parametrize(
    "text, why",
    [
        ("spec", "not an artifact reference"),
        ("spec@2", "no version 2"),
        ("widget@1", "unknown artifact type"),
        ("report@1", "only report takes a kind"),
        ("spec.x@1", "only report takes a kind"),
    ],
)
def test_bad_artifact_references_are_refused(text: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        parse_ref(text)


def test_references_and_alternatives() -> None:
    assert str(parse_ref("report.test@1")) == "report.test@1" and parse_ref("report.test@1").key == "report.test"
    assert [str(r) for r in parse_need("design@1 | request@1")] == ["design@1", "request@1"]


# -------------------------------------------------------------- the schemas --
def test_one_valid_schema_per_artifact_type() -> None:
    files = sorted(p.name for p in SCHEMAS.glob("*.json"))
    assert files == sorted(f"{t}.v1.json" for t in contract.ARTIFACT_TYPES)
    for f in files:
        Draft202012Validator.check_schema(json.loads((SCHEMAS / f).read_text()))


SHA = "a" * 64
GOOD = {
    "request@1": {"text": "Save bookmarks", "kind": "new", "source": "ui", "requested_by": "ana"},
    "spec@1": {
        "path": "docs/spec.md",
        "requirements": [{"id": "R1", "title": "save"}],
        "acceptance_scenarios": 2,
        "holdout": {"scenarios": 1, "sha256": SHA},
    },
    "design@1": {"files": [{"path": "docs/design.md", "sha256": SHA}]},
    "stories@1": {"stories": [{"id": "S1", "title": "save", "acceptance": ["saved"], "covers": ["R1"]}]},
    "branch@1": {"target": "factory", "repo": "bookmarks", "ref": "run/x", "commit": "abc1234", "base": "main"},
    "report.risk@1": {
        "kind": "risk",
        "station": "change-risk",
        "summary": "",
        "evidence": [{"path": "change-risk.json", "sha256": SHA}],
        "data": {"digest": "d", "holds": 0},
    },
    "report.security-review@1": {"kind": "security-review", "station": "x", "summary": "", "evidence": [], "data": {}},
    "image@1": {"ref": "reg/app:abc", "sbom": None, "provenance": None},
    "url@1": {"url": "http://localhost:8081", "internal": None},
    "pr@1": {"via": "pull-request", "branch": "run/x", "url": "https://github.com/o/r/pull/1", "base": "main"},
}
BAD = [
    ("request@1", {"text": "", "kind": "new", "source": "ui"}, "text"),
    ("spec@1", {**GOOD["spec@1"], "requirements": [{"id": "1", "title": "x"}]}, "requirements/0/id"),
    ("spec@1", {**GOOD["spec@1"], "holdout": {"scenarios": 1, "sha256": SHA, "text": "secret"}}, "holdout"),
    ("branch@1", {**GOOD["branch@1"], "commit": "dev"}, "commit"),
    ("report.risk@1", {**GOOD["report.risk@1"], "data": {}}, "data"),
    ("report.risk@1", {**GOOD["report.risk@1"], "evidence": []}, "evidence"),
    ("report.test@1", {"kind": "test", "station": "t", "summary": "", "evidence": [], "data": {"ok": False}}, "data"),
    ("report.scan@1", {**GOOD["report.risk@1"], "kind": "risk"}, "kind is 'risk'"),
    ("url@1", {"url": "localhost"}, "url"),
    ("design@1", {"files": []}, "files"),
]


@pytest.mark.parametrize("ref", list(GOOD))
def test_good_manifests_pass(ref: str) -> None:
    assert artifacts.problems(parse_ref(ref), GOOD[ref]) == []


@pytest.mark.parametrize("ref, manifest, where", BAD)
def test_bad_manifests_fail_with_where(ref: str, manifest: dict, where: str) -> None:
    got = artifacts.problems(parse_ref(ref), manifest)
    assert got and any(where in p for p in got), got


def test_the_ledger_appends_and_the_latest_wins(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    b = parse_ref("branch@1")
    ledger.record(b, GOOD["branch@1"], "entry", "run/x@abc1234")
    ledger.record(b, {**GOOD["branch@1"], "commit": "def5678"}, "implement", "run/x@def5678")
    assert ledger.have() == {b} and len(ledger.entries()) == 2
    latest = ledger.latest(b)
    assert latest and latest["station"] == "implement" and latest["sha256"] == artifacts.digest(latest["manifest"])
    assert ledger.summary() == {"entries": 2, "artifacts": ["branch@1"]}


# ---------------------------------------------------------- publish checks --
@pytest.mark.parametrize("lane", LANES)
def test_every_standard_lane_fits_the_container(lane: str) -> None:
    assert _errors(_lane(lane)) == []


def test_data_rule_deploy_before_build_does_not_fit() -> None:
    doc = _default()
    ids = [s.id for s in doc.stations]
    i, j = ids.index("build"), ids.index("deploy")
    doc.stations[i], doc.stations[j] = doc.stations[j], doc.stations[i]
    assert any("station 'deploy' needs image@1, which no station to its left gives" in e for e in _errors(doc))


def test_data_rule_reads_the_lanes_entry_artifacts() -> None:
    doc = _default()
    doc.entry = ["branch@1"]  # no request: requirements has nothing to work from
    assert any("station 'requirements' needs request@1" in e for e in _errors(doc))
    doc.entry = ["sketch@1"]
    assert any("entry artifact: " in e and "sketch" in e for e in _errors(doc))


@pytest.mark.parametrize(
    "control, text", [("test", "test"), ("quality-gate", "a secret scan"), ("change-risk", "change-risk")]
)
def test_floor_each_control_is_mandatory_when_code_changes(control: str, text: str) -> None:
    errors = _errors(_without(_default(), control))
    assert any("station 'build' gives image but" in e and text in e and "ADR-0037 §5" in e for e in errors), errors


def test_floor_a_branch_change_after_the_controls_needs_them_again() -> None:
    doc = _default()
    doc.station("deploy-repair").next = "deploy"  # the pre-ADR-0037 route: repaired code skipped verify
    errors = _errors(doc)
    assert any(
        "station 'deploy' gives url but test, a secret scan, change-risk do not run after repair station "
        "'deploy-repair'" in e
        for e in errors
    ), errors


def test_floor_controls_route_back_never_forward() -> None:
    doc = _default()
    doc.station("change-risk").on_fail = "build"
    assert any("'change-risk' is a mandatory control" in e for e in _errors(doc))
    doc.station("change-risk").on_fail = None  # holding is fine
    assert _errors(doc) == []


def test_a_lane_that_changes_code_cannot_end_before_the_controls() -> None:
    doc = _default()
    doc.stations = doc.stations[: [s.id for s in doc.stations].index("implement") + 1]
    assert any(
        "the lane ends before test, a secret scan, change-risk run after 'implement'" in e for e in _errors(doc)
    ), _errors(doc)


def test_no_floor_when_nothing_changes_code() -> None:
    doc = _lane("repo-assessment")
    runs = [doc.station(s) for s in doc.plan().stations]
    assert not any(g.type == "branch" for s in runs for g in s.gives_refs())
    assert contract.floor_errors(runs) == []


def test_a_secret_scan_station_counts_for_the_floor() -> None:
    doc = _without(_lane("repo-change"), "secret-scan")
    assert any("a secret scan" in e for e in _errors(doc))
    doc = _without(_default(), "quality-gate")
    doc.stations.insert(
        [s.id for s in doc.stations].index("code-scan"),
        WorkflowStation(id="secret-scan", type="scan", check="secrets"),
    )
    assert _errors(doc) == []


def test_custom_stations_declare_their_contract_builtins_cannot() -> None:
    doc = _default()
    doc.agents["docs-writer"] = AgentSpec(id="docs-writer", tools="observer", prompt="Write the release notes.\n")
    at = [s.id for s in doc.stations].index("handover")
    custom = WorkflowStation(id="release-notes", type="agent", agent="docs-writer")
    doc.stations.insert(at, custom)
    assert custom.kind == "agent" and custom.gives_refs() == (parse_ref("report.release-notes@1"),)
    assert custom.column("deliver") == "deliver"
    assert _errors(doc) == []
    custom.needs = ["stories@1"]
    assert any("'release-notes' needs stories@1" in e for e in _errors(doc))
    custom.needs = ["url@1 | pr@1"]
    assert _errors(doc) == []
    custom.gives = ["notes@1"]
    assert any("unknown artifact type 'notes'" in e for e in _errors(doc))
    custom.gives = None
    doc.station("test").gives = ["report.test@1"]
    assert any("'test': gives are fixed by its type 'test'" in e for e in _errors(doc))
    doc.station("test").gives = None
    doc.station("test").phase = "deliver"
    assert any("its type 'test' sits in verify, not deliver" in e for e in _errors(doc))


def test_owners_are_role_ids() -> None:
    doc = _default()
    doc.station("change-risk").owner = "security"
    assert _errors(doc) == []
    doc.station("handover").owner = "release-manager"
    assert any("owner 'release-manager' is not a role" in e for e in _errors(doc))
    assert _errors(doc, (*contract.BUILTIN_ROLES, "release-manager")) == []


def test_config_adds_org_roles() -> None:
    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    assert cfg.role_ids()[:4] == ["product-owner", "tech-lead", "security", "qa"]
    extra = FactoryConfig.model_validate({**cfg.model_dump(), "roles": {"release-manager": "Release manager"}})
    assert "release-manager" in extra.role_ids()
    with pytest.raises(ValueError, match="role ids"):
        FactoryConfig.model_validate({**cfg.model_dump(), "roles": {"Release Manager": "x"}})


def test_lanes_survive_export(tmp_path: Path) -> None:
    doc = _lane("repo-change")
    export_workflow_dir(doc, tmp_path / "x")
    again = load_workflow_dir(tmp_path / "x")
    assert (again.works_on, again.kinds, again.entry) == (doc.works_on, doc.kinds, doc.entry)
    assert again.stations == doc.stations


# ------------------------------------------------------------------ lanes --
def test_a_type_picks_its_handler_per_target() -> None:
    doc = _default()
    impl, review, ship = doc.station("implement"), doc.station("code-review"), doc.station("handover")
    assert (impl.kind, impl.handler_for("factory"), impl.handler_for("repo")) == (
        "agent",
        "implement",
        "repo-implement",
    )
    assert (review.handler_for("repo"), ship.handler_for("repo")) == ("repo-review", "pull-request")
    assert doc.station("build").handler_for("repo") is None, "nothing is built for a team's repo"
    assert doc.agent_for("implement").id == "developer"
    assert _lane("repo-change").agent_for("implement").id == "repo-developer"
    assert doc.station("code-scan").fail_on == "critical"
    assert _lane("repo-assessment").station("code-scan").fail_on == "none"
    assert _lane("repo-change").station("code-scan").fail_on == "high"


@pytest.mark.parametrize(
    "lane, works_on, kinds, stations",
    [
        ("new-product", "factory", ["new"], ["requirements", "design", "implement", "test", "quality-gate",
         "code-scan", "dependency-scan", "config-scan", "code-review", "change-risk", "build", "deploy",
         "acceptance", "handover"]),
        ("product-change", "factory", ["feature", "bug", "upkeep"], None),
        ("repo-assessment", "repo", ["assess"], ["onboard", "repo-scan", "assess", "code-scan",
         "dependency-scan", "config-scan", "report"]),
        ("repo-change", "repo", ["feature", "bug", "upkeep"], ["onboard", "implement", "test", "secret-scan",
         "code-scan", "dependency-scan", "config-scan", "code-review", "change-risk", "handover"]),
    ],
)  # fmt: skip
def test_each_lane_runs_all_its_stations(lane: str, works_on: str, kinds: list[str], stations) -> None:
    doc = _lane(lane)
    plan = doc.plan()
    assert (doc.works_on, doc.kinds, plan.target) == (works_on, kinds, works_on)
    assert plan.skipped == [], "a lane shows only what runs"
    assert plan.stations == (stations or _default().plan().stations)
    assert all(doc.serves(works_on, k) for k in kinds) and not doc.serves(works_on, "other")


def test_lane_errors() -> None:
    doc = _lane("repo-change")
    doc.kinds = ["new", "feature", "feature", "nope"]
    errors = _errors(doc)
    assert any("an existing repo has no 'new' change" in e for e in errors)
    assert any("change kinds must be unique" in e for e in errors)
    assert any("unknown change kind 'nope'" in e for e in errors)
    doc = _default()
    doc.kinds = ["assess"]
    assert any("'assess' changes are for existing repos" in e for e in _errors(doc))
    doc = _lane("repo-change")
    doc.stations.insert(1, _default().station("build"))
    assert any(
        "station 'build'" in e and "does not work on an existing repo; it is for a new build" in e for e in _errors(doc)
    ), _errors(doc)


def test_a_stored_lane_with_a_foreign_station_skips_it_with_a_sentence() -> None:
    doc = _lane("repo-change")
    doc.stations.insert(1, _default().station("build"))  # stored before the publish check
    assert doc.plan().skipped == [("build", "Skipped: it does not work on an existing repo.")]


# ------------------------------------------------------- handoffs at runtime --
async def test_a_delivered_change_has_a_sealed_ledger(make_factory) -> None:
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    folder = f.manager.ws.data_dir / "artifacts" / change.id
    ledger = Ledger(folder)
    by_station = {(e["station"], e["artifact"]) for e in ledger.entries()}
    for pair in [
        ("entry", "request@1"), ("entry", "branch@1"), ("requirements", "spec@1"), ("design", "design@1"),
        ("implement", "branch@1"), ("test", "report.test@1"), ("quality-gate", "report.readiness@1"),
        ("code-scan", "report.scan@1"), ("code-review", "report.review@1"), ("change-risk", "report.risk@1"),
        ("build", "image@1"), ("deploy", "url@1"), ("acceptance", "report.acceptance@1"), ("handover", "pr@1"),
    ]:  # fmt: skip
        assert pair in by_station, pair
    for e in ledger.entries():
        assert artifacts.problems(parse_ref(e["artifact"]), e["manifest"]) == [], e
    spec = ledger.latest(parse_ref("spec@1"))
    assert spec and spec["manifest"]["holdout"]["scenarios"] >= 1
    holdout = (f.manager.ws.holdout_dir(product.slug) / "scenarios.yaml").read_text()
    assert holdout.strip() and holdout.strip()[:40] not in (folder / "ledger.json").read_text(), "holdout text"
    manifest = json.loads((folder / "manifest.json").read_text())
    assert {"ledger.json", "plan.json"} <= {x["path"] for x in manifest["files"]}
    plan = json.loads((folder / "plan.json").read_text())  # the lane's plan
    assert plan["target"] == "factory" and plan["skipped"] == []
    assert any(e.message.startswith("lane new-product: 14 stations") for e in f.store.list_events(change.id))
    assert manifest["artifacts"]["entries"] == len(ledger.entries())


def _with_station(tmp_path: Path, station: dict, before: str = "implement") -> Path:
    doc = _default()
    stations = doc.model_dump()["stations"]
    stations.insert([s["id"] for s in stations].index(before), station)
    out = tmp_path / "custom"
    export_workflow_dir(WorkflowDoc.model_validate({**doc.model_dump(), "stations": stations}), out)
    return out


async def test_an_invalid_output_fails_its_station(make_factory, tmp_path: Path) -> None:
    f = make_factory(agents=FakeAgentRunner())
    # a custom Deliver step that claims to give an image but builds none
    step = {
        "id": "fake-build",
        "kind": "agent",
        "agent": "reviewer",
        "phase": "deliver",
        "gives": ["image@1"],
        "uses": ["read-repo", "run-sandbox"],
    }
    f.workflows[WF].import_dir(_with_station(tmp_path, step, before="build"), "custom step claiming an image")
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held
    events = f.store.list_events(change.id)
    finished = [e for e in events if e.kind == EventKind.station_finished and e.station == "fake-build"]
    assert finished and finished[-1].data["outcome"] == "failed"
    assert "fake-build gave an invalid artifact" in finished[-1].message
    held = f.store.get_change(change.id)
    assert held and "image@1: the build recorded no image" in (held.last_failure or "")
    assert not any(
        e["station"] == "fake-build" for e in Ledger(f.manager.ws.data_dir / "artifacts" / change.id).entries()
    )
