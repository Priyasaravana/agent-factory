"""Phase registry and capability boundaries (ADR-0039 §2–§5, 1b-3b): built-in phases, org
look-and-decide columns, pack phases, what each station may use and give (publish), what the
engine grants it (runtime: tools and credentials), and lane scope with entry artifacts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import PRODUCT, default_skill_names, wait_run

from agent_factory import contract
from agent_factory.agents import FakeAgentRunner
from agent_factory.config import FactoryConfig, OrgColumn, PhasesConfig
from agent_factory.contract import BUILTIN_REGISTRY, Phase, PhaseRegistry, capability_problem, grants
from agent_factory.engine.handoff import ledger_for
from agent_factory.engine.pipeline import InvalidRequestError
from agent_factory.engine.stations import StationContext
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.phases import PackError, load_pack, registry_for
from agent_factory.workflow import (
    AgentSpec,
    WorkflowDoc,
    WorkflowStation,
    export_workflow_dir,
    load_workflow_dir,
    scope_errors,
    tools_within,
    validate_workflow,
)

REPO = Path(__file__).resolve().parents[2]
LANES = REPO / "workflow-templates" / "standard" / "lanes"
SKILLS = REPO / "plugin" / "skills"
WF = "new-product"


def _lane(name: str = "new-product") -> WorkflowDoc:
    return load_workflow_dir(LANES / f"{name}.yaml")


def _errors(doc: WorkflowDoc, phases: PhaseRegistry = BUILTIN_REGISTRY) -> list[str]:
    return validate_workflow(doc, SKILLS, default_skill_names(), tuple(contract.BUILTIN_ROLES), phases)


def _column(cid: str = "compliance", after: str = "verify", allows=("read-repo", "run-scanners")) -> Phase:
    return Phase(cid, cid.title(), frozenset(allows), frozenset({"report"}), source="org", after=after)


def _insert(doc: WorkflowDoc, station: WorkflowStation, before: str) -> WorkflowDoc:
    doc.stations.insert([s.id for s in doc.stations].index(before), station)
    return doc


def _custom(sid: str, phase: str, agent: str = "checker", **kw) -> WorkflowStation:
    return WorkflowStation(id=sid, type="agent", agent=agent, phase=phase, **kw)


# ---------------------------------------------------------- the registry --
def test_builtin_phases_are_the_seven_columns_in_order() -> None:
    assert BUILTIN_REGISTRY.ids == contract.COLUMN_IDS
    assert all(p.builtin for p in BUILTIN_REGISTRY.phases)


def test_every_builtin_type_uses_only_what_its_phase_allows() -> None:
    for t in contract.STATION_TYPES.values():
        phase = BUILTIN_REGISTRY.get(t.column)
        extra = {c for c in t.uses if not grants(phase.allows, c)}
        # the one exception: deploy repair re-enters verify, so it may write code from deliver
        assert extra <= ({"write-code"} if t.id == "deploy-repair" else set()), (t.id, extra)
        assert t.gives_types() - phase.gives <= ({"branch"} if t.id == "deploy-repair" else set()), t.id


@pytest.mark.parametrize("lane", ["new-product", "product-change", "repo-assessment", "repo-change"])
def test_factory_default_lanes_publish_on_any_install(lane: str) -> None:
    """The default lanes use only built-in phases and stations, so they run whatever the org adds."""
    org = PhaseRegistry([_column(), _column("release-approval", "verify", ["approve"])], [_ops_phase()])
    assert _errors(_lane(lane)) == []
    assert _errors(_lane(lane), org) == []


def test_capabilities_are_checked_by_name() -> None:
    assert capability_problem("write-code") is None
    assert capability_problem("deploy:prod") is None
    assert capability_problem("deploy:*") is None
    assert "unknown capability" in (capability_problem("root") or "")
    assert "names its environment" in (capability_problem("deploy") or "")
    assert "only deploy and rollback" in (capability_problem("push-image:prod") or "")
    assert grants({"deploy:*"}, "deploy:prod") and not grants({"deploy:dev"}, "deploy:prod")


def test_org_columns_sit_after_their_builtin_phase() -> None:
    reg = PhaseRegistry([_column("compliance", "verify"), _column("ux-review", "design")])
    assert reg.ids == [
        "discover",
        "design",
        "ux-review",
        "plan",
        "develop",
        "verify",
        "compliance",
        "deliver",
        "operate",
    ]
    assert reg.get("compliance").source == "org"


@pytest.mark.parametrize(
    ("column", "message"),
    [
        (_column(allows=["read-repo", "write-code"]), "only looks and decides"),
        (_column(allows=["deploy:prod"]), "comes only from a reviewed pack"),
        (_column(after="operate"), "comes only from a pack"),
        (_column(after="nowhere"), "`after` must be a built-in phase"),
        (_column("verify"), "is a built-in phase name"),
        (_column("test"), "is a built-in phase name"),  # an ADR-0028 phase name still means verify
        (Phase("x-ray", "X", frozenset({"read-repo"}), frozenset({"image"}), "org", "verify"), "only reports"),
    ],
)
def test_org_columns_look_and_decide_only(column: Phase, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PhaseRegistry([column])


def _ops_phase() -> Phase:
    return Phase(
        "observe-act",
        "Observe & act",
        frozenset({"read-metrics", "read-logs", "rollback:*"}),
        frozenset({"report"}),
        source="ops-pack@1.0.0",
    )


def test_pack_phases_are_appended_and_may_act() -> None:
    reg = PhaseRegistry([_column()], [_ops_phase()])
    assert reg.ids[-2:] == ["operate", "observe-act"]
    assert grants(reg.get("observe-act").allows, "rollback:prod")
    with pytest.raises(ValueError, match="names its pack"):
        PhaseRegistry([], [Phase("observe-act", "O", frozenset(), frozenset({"report"}), source="org")])


def test_the_shipped_ops_pack_loads_and_installs_through_the_config() -> None:
    pack = load_pack(REPO / "packs" / "ops-pack")
    assert pack.source == "ops-pack@1.0.0"
    cfg = FactoryConfig.model_validate(
        {
            "blueprints": {},
            "phases": PhasesConfig(
                columns=[OrgColumn(id="compliance", title="Compliance", after="verify")], packs=["packs/ops-pack"]
            ).model_dump(),
        }
    )
    reg = registry_for(cfg, REPO)
    assert reg.ids[-3:] == ["deliver", "operate", "observe-act"]
    assert "compliance" in reg


def test_a_bad_column_in_the_config_stops_the_registry(tmp_path: Path) -> None:
    cfg = FactoryConfig.model_validate(
        {"blueprints": {}, "phases": {"columns": [{"id": "hotfix", "title": "Hotfix", "after": "develop"}]}}
    )
    cfg.phases.columns[0].allows = ["write-code"]
    with pytest.raises(PackError, match="only looks and decides"):
        registry_for(cfg, tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "pack.yaml").write_text("id: p\nversion: '1'\nstations: []\n")
    with pytest.raises(PackError, match="come with the workflow builder"):
        load_pack(tmp_path / "p")


# ------------------------------------------------- publish: capabilities --
def test_a_build_image_step_in_develop_is_refused() -> None:
    """ADR-0039 §3's example: push-image belongs to Deliver."""
    step = _custom("build-image", "develop", uses=["read-repo", "push-image"], gives=["report.build-image@1"])
    errors = _errors(_insert(_lane(), step, "test"))
    assert any("uses push-image, which Develop does not allow (it belongs to Deliver)" in e for e in errors)


def test_a_step_cant_give_what_its_phase_does_not() -> None:
    step = _custom("early-image", "design", gives=["image@1"])
    errors = _errors(_insert(_lane(), step, "design"))
    assert any("gives image, which a Design station may not give" in e for e in errors)


def test_custom_steps_are_observe_only_unless_granted() -> None:
    doc = _lane()
    doc.agents["security-reviewer"] = AgentSpec(id="security-reviewer", tools="reviewer", prompt="Review.\n")
    step = _custom("security-review", "verify", agent="security-reviewer", on_fail="implement")
    errors = _errors(_insert(doc, step, "change-risk"))
    assert any("can use Bash, but the station is granted only read-repo" in e for e in errors)
    step.uses = ["read-repo", "run-sandbox"]
    assert _errors(doc) == []


def test_a_writing_agent_in_verify_is_refused() -> None:
    """Even an admin can't grant write-code in Verify: the phase is the ceiling."""
    doc = _lane()
    step = _custom("auto-fix", "verify", agent="developer", uses=["read-repo", "write-code", "run-sandbox"])
    errors = _errors(_insert(doc, step, "change-risk"))
    assert any("uses write-code, which Verify does not allow (it belongs to Develop)" in e for e in errors)


def test_unknown_capabilities_and_phases_are_named() -> None:
    doc = _insert(_lane(), _custom("odd", "compliance", uses=["root"]), "change-risk")
    errors = _errors(doc)
    assert any("unknown capability 'root'" in e for e in errors)
    assert any("unknown phase 'compliance'" in e for e in errors)
    t = doc.station("test")
    t.uses = ["read-repo"]
    assert any("uses are fixed by its type 'test'" in e for e in _errors(doc))


def test_a_step_in_an_org_column() -> None:
    reg = PhaseRegistry([_column("compliance", "verify")])
    step = _custom("pci-review", "compliance", uses=["read-repo"], on_fail="implement")
    doc = _insert(_lane(), step, "build")
    assert _errors(doc, reg) == []
    assert doc.columns()["pci-review"] == "compliance"
    # a column orders like any other: Compliance can't come after a Deliver station
    late = _insert(_lane(), _custom("pci-review", "compliance", uses=["read-repo"]), "handover")
    assert any("sits in compliance but comes after a deliver station" in e for e in _errors(late, reg))


# ------------------------------------------------------- runtime: grants --
def test_grants_are_uses_inside_the_phase() -> None:
    doc = _lane()
    assert doc.grant("implement") == {"read-repo", "write-code", "run-sandbox"}
    assert doc.grant("requirements") == {"read-repo"}
    assert doc.grant("deploy") == {"deploy:*"}
    assert doc.grant("handover") == {"read-repo", "open-pr"}
    assert doc.grant("deploy-repair") == {"read-repo", "write-code", "run-sandbox"}  # re-enters verify


def test_tools_follow_the_grant() -> None:
    kept, denied = tools_within(["Read", "Write", "Bash", "Task", "WebFetch"], {"read-repo", "write-docs"})
    assert kept == ["Read", "Write", "WebFetch"]
    assert denied == ["Bash", "Task"]


async def _ctx(f, grant: frozenset[str] | None) -> StationContext:
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    await wait_run(f, change.id)
    doc = f.workflows[WF].get()
    ctx = StationContext(
        f.cfg, f.settings, f.store, f.manager.ws, f.manager.ex, f.manager.agents,
        f.store.get_product(change.product_id), f.store.get_change(change.id), f.manager.ws.data_dir,
        "requirements", doc,
    )  # fmt: skip
    ctx.grant = grant
    return ctx


async def test_a_credential_outside_the_grant_is_denied(make_factory, monkeypatch) -> None:
    monkeypatch.setenv("BOUNDARY_TOKEN", "s3cret-value")
    f = make_factory()
    ctx = await _ctx(f, frozenset({"read-repo"}))
    assert ctx.secret("env://BOUNDARY_TOKEN", "push", "push-image") is None
    assert ctx.secret("env://BOUNDARY_TOKEN", "anything") is None  # undeclared: denied too
    denied = [e for e in f.store.list_events(ctx.change.id) if "denied secret" in e.message]
    assert [e.data["denied"] for e in denied] == ["push-image", "undeclared"]
    assert all("s3cret" not in e.message for e in denied)
    ctx.grant = frozenset({"push-image"})
    assert ctx.secret("env://BOUNDARY_TOKEN", "push", "push-image") == "s3cret-value"
    ctx.grant = None  # an engine step outside any station
    assert ctx.secret("env://BOUNDARY_TOKEN", "archive") == os.environ["BOUNDARY_TOKEN"]


async def test_an_agent_gets_only_granted_tools(make_factory) -> None:
    runner = FakeAgentRunner()
    f = make_factory(agents=runner)
    ctx = await _ctx(f, frozenset({"read-repo"}))
    ctx.workflow_doc.agents["intake"] = ctx.workflow_doc.agents["intake"].model_copy(update={"tools": "builder"})
    runner.calls.clear()
    await ctx.agent("hello")
    req = runner.calls[-1]
    assert req.tools == ["Read", "Glob", "Grep"]
    assert req.observe_only
    assert any("denied tools" in e.message for e in f.store.list_events(ctx.change.id))


async def test_a_run_grants_each_station_its_boundary(make_factory) -> None:
    runner = FakeAgentRunner()
    f = make_factory(agents=runner)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    by_station = {r.station: r for r in runner.calls}
    assert "Write" not in by_station["requirements"].tools and by_station["requirements"].observe_only
    assert {"Write", "Bash"} <= set(by_station["implement"].tools)
    assert not any("denied" in e.message for e in f.store.list_events(change.id))


# ------------------------------------------------------ scope and entry --
def _delivery_only(entry: list[str]) -> WorkflowDoc:
    doc = _lane()
    doc.stations = [s for s in doc.stations if s.id == "handover"]
    doc.entry = entry
    doc.eval_gate = "off"
    doc.evals = []
    doc.agents = {}
    doc.docs = {}
    return doc


def test_a_scope_that_changes_code_includes_verify() -> None:
    doc = _lane("product-change")
    doc.scope = ["design", "develop"]
    errors = scope_errors(doc)
    assert "a scope that changes code must include Verify (test, a secret scan and change-risk)" in errors
    assert any("outside the lane's scope" in e for e in errors)


def test_delivery_without_develop_starts_from_the_controls_evidence() -> None:
    bare = _delivery_only(["request@1", "branch@1"])
    assert any("must start from report.test, report.risk" in e for e in scope_errors(bare))
    nothing = _delivery_only(["request@1"])
    assert any("must start from image or branch, report.test, report.risk" in e for e in scope_errors(nothing))
    ok = _delivery_only(["request@1", "branch@1", "report.test@1", "report.risk@1"])
    ok.scope = ["deliver", "deliver"]
    assert _errors(ok) == []
    assert ok.scope_phases() == ["deliver"]


def test_scope_must_name_two_phases_in_order() -> None:
    doc = _lane()
    doc.scope = ["verify", "design"]
    assert any("give the first phase first" in e for e in _errors(doc))
    doc.scope = ["nowhere", "verify"]
    assert any("unknown phase nowhere" in e for e in _errors(doc))


_TEST = {"kind": "test", "station": "ci", "summary": "green", "evidence": [], "data": {"ok": True}}
_RISK = {
    "kind": "risk",
    "station": "risk-tool",
    "summary": "no holds",
    "evidence": [{"path": "risk.json", "sha256": "0" * 64}],
    "data": {"digest": "abc", "holds": 0},
}


async def _delivery_factory(make_factory, tmp_path: Path):
    f = make_factory()
    out = tmp_path / "delivery-only"
    export_workflow_dir(_delivery_only(["request@1", "branch@1", "report.test@1", "report.risk@1"]), out)
    f.workflows[WF].import_dir(out, "delivery only")
    return f


async def test_entry_artifacts_come_from_your_own_tools(make_factory, tmp_path: Path) -> None:
    f = await _delivery_factory(make_factory, tmp_path)
    missing = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, missing.id) == ChangeStatus.held
    held = f.store.get_change(missing.id)
    assert "report.test@1: not provided" in (held.last_failure or "")

    product = f.manager.create_product(PRODUCT.model_copy(update={"title": "Second bookmarks"}))
    with pytest.raises(InvalidRequestError, match="doesn't start from image"):
        f.manager.start_change(product, provided={"image": {}})
    change = f.manager.start_change(product, provided={"report.test": _TEST, "report.risk": _RISK})
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    entries = [e for e in ledger_for(await _ctx_for(f, change.id)).entries() if e["station"] == "entry"]
    provided = {e["artifact"]: e["ref"] for e in entries if e["ref"].startswith("provided by")}
    assert set(provided) == {"report.test@1", "report.risk@1"}


async def test_a_provided_risk_report_with_holds_is_refused(make_factory, tmp_path: Path) -> None:
    f = await _delivery_factory(make_factory, tmp_path)
    risky = {**_RISK, "data": {"digest": "abc", "holds": 2}}
    change = f.manager.start_change(
        f.manager.create_product(PRODUCT), provided={"report.test": _TEST, "report.risk": risky}
    )
    assert await wait_run(f, change.id) == ChangeStatus.held
    assert "2 change-risk finding(s) still wait for approval" in (f.store.get_change(change.id).last_failure or "")
    assert not any(e.kind == EventKind.station_started for e in f.store.list_events(change.id))


async def _ctx_for(f, change_id: str) -> StationContext:
    change = f.store.get_change(change_id)
    return StationContext(
        f.cfg, f.settings, f.store, f.manager.ws, f.manager.ex, f.manager.agents,
        f.store.get_product(change.product_id), change, f.manager.ws.data_dir, "handover", f.workflows[WF].get(),
    )  # fmt: skip
