"""One workflow per blueprint, templates, role/tool checks and migration
from the pre-rename 'line' tables."""

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
WF = "standard"


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
    doc = load_workflow_dir(TEMPLATES / "standard")
    doc.agents[agent].tools = preset
    assert any(message in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_narrowing_a_preset_can_break_a_role() -> None:
    doc = load_workflow_dir(TEMPLATES / "standard")
    doc.agents["developer"].disallowed_tools = ["Bash"]
    assert any("needs shell access" in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_warnings_are_advice_not_errors() -> None:
    doc = load_workflow_dir(TEMPLATES / "standard")
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

    doc = load_workflow_dir(TEMPLATES / "standard")
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
            applies_when={"target": ["factory"]},
        ),
    )  # fmt: skip
    out = tmp_path / "secure"
    export_workflow_dir(doc.model_copy(update={"name": "secure"}), out)
    return out


# -------------------------------------------------------------- templates --
def test_builtin_templates_are_valid() -> None:
    for t in [p for p in TEMPLATES.iterdir() if p.is_dir()]:
        assert validate_workflow(load_workflow_dir(t), SKILLS, DEFAULTS) == [], t.name


async def test_start_from_template_then_publish_and_run(make_factory, tmp_path):
    f = make_factory()
    names = [n for n, _ in f.workflows.templates()]
    assert names == ["standard"], "one standard lane (ADR-0037)"
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
def two_workflows(tmp_path):
    """The standard plus a second org workflow (ADR-0037 §6), with `secure` as the default."""
    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    cfg.workflows["secure"] = str(_secure(tmp_path))
    cfg.default_workflow = "secure"
    settings = Settings(
        factory_mode="dry-run",
        factory_home=str(REPO),
        data_dir=str(tmp_path / "data"),
        skill_seeds_dir=str(make_seeds(tmp_path / "seeds")),
        _env_file=None,
    )
    f = build_factory(settings, cfg, SqliteStateStore(":memory:"), FakeExecutor(), FakeAgentRunner())
    _CREATED.append(f)
    return f


def test_every_blueprint_runs_the_org_workflow(cfg):
    assert cfg.workflow_templates() == {"standard": "workflow-templates/standard"}
    assert {cfg.workflow_for(b, "new") for b in cfg.blueprints} == {"standard"}
    assert cfg.blueprints_using("standard") == list(cfg.blueprints)
    assert cfg.eval_blueprint("standard") == "fastapi-service", "evaluations build new products"
    with pytest.raises(ValueError, match="default_workflow 'nope'"):
        type(cfg).model_validate({**cfg.model_dump(), "default_workflow": "nope"})


async def test_products_run_the_default_workflow_whatever_their_blueprint(two_workflows):
    f = two_workflows
    assert f.workflows.ids() == [WF, "secure"]
    a = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert a.workflow_id == "secure"
    assert await wait_run(f, a.id) == ChangeStatus.awaiting_feedback
    assert "security-review" in {e.station for e in f.store.list_events(a.id) if e.kind == EventKind.station_finished}


async def test_publishing_one_workflow_leaves_the_other_alone(two_workflows):
    f = two_workflows
    d = f.workflows[WF].draft
    _, doc, _ = d.get()
    d.upsert_agent(doc.agents["developer"].model_copy(update={"learnings": "Use typed SQLAlchemy models."}))
    d.publish("developer learnings")
    assert f.workflows[WF].active_version() == 2
    assert f.workflows["secure"].active_version() == 1


# ------------------------------------------------------------- fresh start --
def test_data_from_before_the_standard_lane_needs_a_reset(tmp_path):
    """ADR-0037 §7: no migration; the engine refuses old data and names the command."""
    from agent_factory import reset

    db = tmp_path / "factory.db"
    old = SqliteStateStore(db)
    old.create_workflow_version("fastapi-service", load_workflow_dir(TEMPLATES / "standard"), "v1 per blueprint")
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
    assert f.workflows[WF].active_version() == 1 and f.workflows[WF].get().windows, "reseeded from the standard"


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
    assert old.workflow_version == 2 and old.workflow_id is None and old.window is None
