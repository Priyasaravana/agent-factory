"""One workflow per product line, templates, role/tool checks and migration
from the pre-rename 'line' tables."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from conftest import _CREATED, ORDER, default_skill_names, make_seeds, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.app_factory import build_factory
from agent_factory.config import load_config
from agent_factory.engine.workflows import WorkflowError
from agent_factory.executor import FakeExecutor
from agent_factory.github import Fetched
from agent_factory.models import CreateOrderInput, EventKind, RunStatus
from agent_factory.settings import Settings
from agent_factory.state import SqliteStateStore
from agent_factory.workflow import load_workflow_dir, validate_workflow, workflow_warnings

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "workflow-templates"
SKILLS = REPO / "plugin" / "skills"
DEFAULTS = default_skill_names()  # imported on first start (config: default_skills)
WF = "fastapi-service"


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
    doc = load_workflow_dir(TEMPLATES / "default")
    doc.agents[agent].tools = preset
    assert any(message in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_narrowing_a_preset_can_break_a_role() -> None:
    doc = load_workflow_dir(TEMPLATES / "default")
    doc.agents["developer"].disallowed_tools = ["Bash"]
    assert any("needs shell access" in p for p in validate_workflow(doc, SKILLS, DEFAULTS))


def test_warnings_are_advice_not_errors() -> None:
    doc = load_workflow_dir(TEMPLATES / "default")
    doc.agents["verifier"].skills = ["factory-station-contract"]
    doc.agents["intake"].model = "fast"
    doc.agents["spare"] = doc.agents["intake"].model_copy(update={"id": "spare"})
    warnings = workflow_warnings(doc)
    assert validate_workflow(doc, SKILLS, DEFAULTS) == []
    assert any("agent-watchdog" in w for w in warnings)
    assert any("fast tier" in w for w in warnings)
    assert any("'spare' is not used" in w for w in warnings)


# -------------------------------------------------------------- templates --
def test_builtin_templates_are_valid() -> None:
    for t in [p for p in TEMPLATES.iterdir() if p.is_dir()]:
        assert validate_workflow(load_workflow_dir(t), SKILLS, DEFAULTS) == [], t.name


async def test_start_from_template_then_publish_and_run(make_factory):
    f = make_factory()
    names = [n for n, _ in f.workflows.templates()]
    assert {"default", "api-security-review"} <= set(names)
    w = f.workflows[WF]
    w.draft.replace_with(f.workflows.template("api-security-review"))
    assert w.draft.dirty() and w.draft.problems() == []
    info = w.draft.publish("switch to security review template")
    assert info.version == 2
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    done = [e.station for e in f.store.list_events(run.id) if e.kind == EventKind.station_finished]
    assert "security-review" in done


async def test_import_template_from_github(make_factory, monkeypatch, tmp_path):
    src = tmp_path / "repo" / "workflows" / "secure"
    shutil.copytree(TEMPLATES / "api-security-review", src)
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


# ------------------------------------------------ one workflow per product --
@pytest.fixture
def two_products(tmp_path):
    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    base = cfg.product_lines[WF]
    cfg.product_lines["secure-api"] = base.model_copy(
        update={"workflow_template": "workflow-templates/api-security-review", "node_ports": [30083, 30084]}
    )
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


async def test_each_product_line_runs_its_own_workflow(two_products):
    f = two_products
    assert f.workflows.ids() == [WF, "secure-api"]
    a = f.manager.start_run(f.manager.create_order(ORDER))
    b = f.manager.start_run(
        f.manager.create_order(
            CreateOrderInput(
                title="Secure notes", requirements="Notes API with tags and search.", product_line="secure-api"
            )
        )
    )
    assert a.workflow_id == WF and b.workflow_id == "secure-api"
    assert await wait_run(f, a.id) == RunStatus.awaiting_feedback
    assert await wait_run(f, b.id) == RunStatus.awaiting_feedback
    sa = {e.station for e in f.store.list_events(a.id) if e.kind == EventKind.station_finished}
    sb = {e.station for e in f.store.list_events(b.id) if e.kind == EventKind.station_finished}
    assert "security-review" not in sa and "security-review" in sb


async def test_publishing_one_workflow_leaves_the_other_alone(two_products):
    f = two_products
    d = f.workflows[WF].draft
    _, doc, _ = d.get()
    d.upsert_agent(doc.agents["developer"].model_copy(update={"learnings": "Use typed SQLAlchemy models."}))
    d.publish("developer learnings")
    assert f.workflows[WF].active_version() == 2
    assert f.workflows["secure-api"].active_version() == 1


# ------------------------------------------------------------- migration --
def test_legacy_line_tables_and_runs_migrate(tmp_path):
    db = tmp_path / "factory.db"
    doc = load_workflow_dir(TEMPLATES / "default").model_dump(mode="json")
    doc["blueprint"] = doc.pop("template")
    doc["blueprint_hash"] = doc.pop("template_hash")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE line_versions (line_id TEXT NOT NULL, version INTEGER NOT NULL, doc TEXT NOT NULL,
          note TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (line_id, version));
        CREATE TABLE line_active (line_id TEXT PRIMARY KEY, version INTEGER NOT NULL);
        CREATE TABLE line_drafts (line_id TEXT PRIMARY KEY, base_version INTEGER NOT NULL, doc TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        """
    )
    for v in (1, 2):
        con.execute(
            "INSERT INTO line_versions VALUES (?,?,?,?,?)", ("default", v, json.dumps(doc), f"v{v}", "2026-09-26")
        )
    con.execute("INSERT INTO line_active VALUES ('default', 2)")
    con.commit()
    con.close()

    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    settings = Settings(
        factory_mode="dry-run",
        factory_home=str(REPO),
        data_dir=str(tmp_path),
        skill_seeds_dir=str(make_seeds(tmp_path / "seeds")),
        _env_file=None,
    )
    f = build_factory(settings, cfg, SqliteStateStore(db), FakeExecutor(), FakeAgentRunner())
    w = f.workflows[WF]
    assert w.active_version() == 2 and [v.version for v in w.versions()] == [2, 1]
    assert w.get(1).template == "default", "old 'blueprint' field is read as 'template'"

    from agent_factory.models import Run

    old = Run.model_validate(
        {
            "id": "r",
            "order_id": "o",
            "iteration": 1,
            "status": "held",
            "line_version": 2,
            "created_at": "2026-09-26T00:00:00Z",
            "updated_at": "2026-09-26T00:00:00Z",
        }
    )
    assert old.workflow_version == 2 and old.workflow_id is None
