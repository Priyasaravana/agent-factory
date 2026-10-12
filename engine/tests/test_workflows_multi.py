"""Factory default lanes and org workflows (ADR-0039): templates, role/tool checks, copies,
product assignments, back to factory default, and the fresh start."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import _CREATED, PRODUCT, default_skill_names, make_seeds, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.app_factory import build_factory
from agent_factory.config import load_config
from agent_factory.engine.workflows import WorkflowError
from agent_factory.executor import FakeExecutor
from agent_factory.github import Fetched
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.settings import Settings
from agent_factory.state import SqliteStateStore
from agent_factory.workflow import load_workflow_dir, validate_workflow, workflow_warnings

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "workflow-templates"
SKILLS = REPO / "plugin" / "skills"
DEFAULTS = default_skill_names()  # imported on first start (config: default_skills)
WF = "new-product"


# ------------------------------------------------------------ role checks --
@pytest.mark.parametrize(
    "agent, preset, message",
    [
        ("architect", "observer", "needs an agent that can write files"),
        ("developer", "author", "needs shell access"),
        ("intake", "author", "must use an observe-only agent"),
        ("verifier", "builder", "must use an observe-only agent"),
        ("verifier", "observer", "needs shell access"),
    ],
)
def test_station_role_requirements(agent, preset, message) -> None:
    doc = load_workflow_dir(TEMPLATES / "standard" / "lanes" / "new-product.yaml")
    doc.agents[agent].tools = preset
    assert any(message in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_narrowing_a_preset_can_break_a_role() -> None:
    doc = load_workflow_dir(TEMPLATES / "standard" / "lanes" / "new-product.yaml")
    doc.agents["developer"].disallowed_tools = ["Bash"]
    assert any("needs shell access" in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_warnings_are_advice_not_errors() -> None:
    doc = load_workflow_dir(TEMPLATES / "standard" / "lanes" / "new-product.yaml")
    doc.agents["verifier"].skills = ["factory-station-contract"]
    doc.agents["intake"].model = "fast"
    doc.agents["spare"] = doc.agents["intake"].model_copy(update={"id": "spare"})
    warnings = workflow_warnings(doc)
    assert validate_workflow(doc, SKILLS, DEFAULTS) == []
    assert any("agent-watchdog" in w for w in warnings)
    assert any("fast tier" in w for w in warnings)
    assert any("'spare' is not used" in w for w in warnings)


def _secure(tmp_path: Path) -> Path:
    """The standard lane plus an OWASP review after implement, as a template folder."""
    from agent_factory.workflow import AgentSpec, WorkflowStation, export_workflow_dir

    doc = load_workflow_dir(TEMPLATES / "standard" / "lanes" / "new-product.yaml")
    doc.agents["security-reviewer"] = AgentSpec(
        id="security-reviewer",
        tools="reviewer",
        skills=["factory-station-contract"],
        prompt="Review the code for OWASP issues.\n",
    )
    at = [x.id for x in doc.stations].index("implement") + 1
    doc.stations.insert(
        at,
        WorkflowStation(
            id="security-review", type="agent", agent="security-reviewer", on_fail="implement",
        ),
    )  # fmt: skip
    out = tmp_path / "secure"
    export_workflow_dir(doc.model_copy(update={"name": "secure"}), out)
    return out


# -------------------------------------------------------------- templates --
def test_builtin_templates_are_valid() -> None:
    lanes = sorted(TEMPLATES.glob("*/lanes/*.yaml"))
    assert [p.stem for p in lanes] == ["new-product", "product-change", "repo-assessment", "repo-change"]
    for t in lanes:
        assert validate_workflow(load_workflow_dir(t), SKILLS, DEFAULTS) == [], t.name


async def test_start_from_template_then_publish_and_run(make_factory, tmp_path):
    f = make_factory()
    names = [n for n, _ in f.workflows.templates()]
    assert names == [f"standard/{n}" for n in ("new-product", "product-change", "repo-assessment", "repo-change")]
    w = f.workflows[WF]
    w.draft.replace_with(load_workflow_dir(_secure(tmp_path)))
    assert w.draft.dirty() and w.draft.problems() == []
    info = w.draft.publish("switch to security review template")
    assert info.version == 2
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    done = [e.station for e in f.store.list_events(change.id) if e.kind == EventKind.station_finished]
    assert "security-review" in done


async def test_import_template_from_github(make_factory, monkeypatch, tmp_path):
    src = tmp_path / "repo" / "workflows" / "secure"
    shutil.copytree(_secure(tmp_path), src)
    tmp = tmp_path / "keep"
    tmp.mkdir()

    def fake_fetch(repo, path, ref, token):
        assert token is None and repo == "acme/factory-templates"
        return Fetched(repo=repo, path=path, ref=ref, sha="a" * 40, root=src, _tmp=tmp)

    import agent_factory.actions as actions

    monkeypatch.setattr(actions, "fetch_dir", fake_fetch)
    f = make_factory()
    from agent_factory.models import FromGitHubInput

    view = actions.draft_from_github(f, WF, FromGitHubInput(repo="acme/factory-templates", path="workflows/secure"))
    assert view.dirty and view.template == f"github:acme/factory-templates/workflows/secure@{'a' * 12}"
    assert "security-review" in [s.id for s in view.stations]


async def test_unknown_template_and_workflow(make_factory):
    f = make_factory()
    with pytest.raises(WorkflowError, match="not found"):
        f.workflows.template("nope")
    with pytest.raises(WorkflowError, match="not found"):
        f.workflows["no-such-product"]


# ------------------------------------------- workflows belong to the org --
@pytest.fixture
def secure(make_factory, tmp_path):
    """A factory with an org workflow `secure`: a copy of New product plus a security review."""
    f = make_factory()
    svc = f.workflows.copy(WF, "secure", "copied from new-product")
    svc.draft.replace_with(load_workflow_dir(_secure(tmp_path)))
    svc.draft.publish("add a security review")
    return f


def test_the_factory_ships_four_default_lanes(cfg):
    assert set(cfg.workflow_templates()) == {"new-product", "product-change", "repo-assessment", "repo-change"}
    assert cfg.blueprints_for("repo") == ["existing-repo"]
    assert "existing-repo" not in cfg.blueprints_for("factory")
    assert cfg.eval_blueprint("factory") == "fastapi-service", "evaluations build new products"


async def test_a_product_runs_its_assigned_workflow_others_the_factory_default(secure):
    import agent_factory.actions as actions
    from agent_factory.models import AssignWorkflowInput, UnassignWorkflowInput

    f = secure
    assert f.workflows.ids()[-1] == "secure" and not f.workflows.is_factory("secure")
    assert f.workflows["secure"].active_version() == 2
    mine = f.manager.create_product(PRODUCT)
    other = f.manager.create_product(PRODUCT.model_copy(update={"title": "Other service"}))
    view = actions.assign_workflow(f, "secure", AssignWorkflowInput(product_id=mine.id))
    assert view.products == [mine.slug]
    assert f.store.get_product(mine.id).workflows == {"new": "secure"}
    a = f.manager.start_change(f.store.get_product(mine.id))
    b = f.manager.start_change(other)
    assert (a.workflow_id, a.workflow_version, b.workflow_id) == ("secure", 2, WF)
    assert await wait_run(f, a.id) == ChangeStatus.awaiting_feedback
    assert "security-review" in {e.station for e in f.store.list_events(a.id) if e.kind == EventKind.station_finished}
    actions.unassign_workflow(f, "secure", UnassignWorkflowInput(product_id=mine.id))
    assert f.store.get_product(mine.id).workflows == {}


async def test_assignments_are_checked(secure):
    import agent_factory.actions as actions
    from agent_factory.engine.pipeline import InvalidRequestError
    from agent_factory.models import AssignWorkflowInput

    f = secure
    p = f.manager.create_product(PRODUCT)
    with pytest.raises(Exception, match="factory default lanes serve every product"):
        actions.assign_workflow(f, WF, AssignWorkflowInput(product_id=p.id))
    with pytest.raises(InvalidRequestError, match="does not serve feature"):
        actions.assign_workflow(f, "secure", AssignWorkflowInput(product_id=p.id, kinds=["feature"]))


async def test_use_factory_defaults_overrides_every_assignment(secure):
    import agent_factory.actions as actions
    from agent_factory.models import AssignWorkflowInput

    f = secure
    p = f.manager.create_product(PRODUCT)
    actions.assign_workflow(f, "secure", AssignWorkflowInput(product_id=p.id))
    f.cfg.use_factory_defaults = True
    assert f.manager.start_change(f.store.get_product(p.id)).workflow_id == WF


async def test_copies_need_a_new_id(secure):
    f = secure
    with pytest.raises(WorkflowError, match="already exists"):
        f.workflows.copy(WF, "secure", "again")
    with pytest.raises(WorkflowError, match="lowercase"):
        f.workflows.copy(WF, "Secure Two", "bad id")


async def test_the_factory_default_is_always_one_step_away(make_factory):
    f = make_factory()
    d = f.workflows[WF].draft
    _, doc, _ = d.get()
    d.upsert_agent(doc.agents["developer"].model_copy(update={"learnings": "Use typed SQLAlchemy models."}))
    d.publish("developer learnings")  # an edit is a new version
    assert f.workflows[WF].active_version() == 2
    assert f.workflows.restore_factory(WF) == 3
    restored = f.workflows[WF].get()
    assert (
        restored.agents["developer"].learnings == "" and restored.template_hash == f.workflows[WF].get(1).template_hash
    )
    assert {v.version: v.note for v in f.workflows[WF].versions()}[3] == "back to factory default"
    f.workflows.copy(WF, "mine", "x")
    with pytest.raises(WorkflowError, match="org workflow"):
        f.workflows.restore_factory("mine")


async def test_org_workflows_are_found_again_at_restart(secure):
    from agent_factory.engine.workflows import WorkflowRegistry

    f = secure
    lanes = {w: f.workflows[w].template_dir for w in f.workflows.factory_ids}
    again = WorkflowRegistry(f.store, lanes, f.workflows.templates_dir, f.workflows.skills_dir)
    assert "secure" in again.ids() and again["secure"].active_version() == 2


async def test_publishing_one_workflow_leaves_the_other_alone(secure):
    f = secure
    d = f.workflows[WF].draft
    _, doc, _ = d.get()
    d.upsert_agent(doc.agents["developer"].model_copy(update={"learnings": "Use typed SQLAlchemy models."}))
    d.publish("developer learnings")
    assert f.workflows[WF].active_version() == 2
    assert f.workflows["secure"].active_version() == 2


# ------------------------------------------------------------- fresh start --
def test_data_from_before_the_standard_lane_needs_a_reset(tmp_path):
    """ADR-0037 §7: no migration; the engine refuses old data and names the command."""
    from agent_factory import reset

    db = tmp_path / "factory.db"
    old = SqliteStateStore(db)
    old.create_workflow_version(
        "fastapi-service", load_workflow_dir(TEMPLATES / "standard" / "lanes" / "new-product.yaml"), "v1 per blueprint"
    )
    (tmp_path / "artifacts" / "c1").mkdir(parents=True)
    (tmp_path / "artifacts" / "c1" / "events.jsonl").write_text("{}\n")

    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    settings = Settings(
        factory_mode="dry-run",
        factory_home=str(REPO),
        data_dir=str(tmp_path),
        skill_seeds_dir=str(make_seeds(tmp_path / "seeds")),
        _env_file=None,
    )
    with pytest.raises(reset.ResetRequired, match="agent-factory reset --confirm"):
        build_factory(settings, cfg, SqliteStateStore(db), FakeExecutor(), FakeAgentRunner())
    assert reset.plan(old, tmp_path) == {**old.count_work(), "artifacts/": 1}, "nothing deleted without --confirm"
    done = reset.reset(old, tmp_path)
    assert done["workflow_versions"] == 1 and not (tmp_path / "artifacts").exists()
    f = build_factory(settings, cfg, SqliteStateStore(db), FakeExecutor(), FakeAgentRunner())
    _CREATED.append(f)
    assert f.workflows[WF].active_version() == 1 and f.workflows[WF].get().kinds == ["new"], (
        "reseeded from the factory default"
    )


def test_old_change_records_keep_their_aliases():
    from agent_factory.models import Change

    old = Change.model_validate(
        {
            "id": "r",
            "product_id": "o",
            "iteration": 1,
            "status": "held",
            "line_version": 2,
            "created_at": "2026-09-26T00:00:00Z",
            "updated_at": "2026-09-26T00:00:00Z",
        }
    )
    assert old.workflow_version == 2 and old.workflow_id is None
