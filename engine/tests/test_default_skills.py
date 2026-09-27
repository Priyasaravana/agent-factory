"""Default skills (config: default_skills) are imported on first start, pinned,
and then managed like any other imported skill."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from conftest import ORDER, REPO, make_seeds, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.app_factory import build_factory
from agent_factory.config import load_config
from agent_factory.engine.workflows import WorkflowError
from agent_factory.executor import FakeExecutor
from agent_factory.github import Fetched, GitHubError
from agent_factory.models import RunStatus
from agent_factory.settings import Settings
from agent_factory.state import SqliteStateStore

WF = "fastapi-service"
CFG = load_config(REPO / ".agent-factory" / "config.yaml")
SRC = CFG.default_skills[0]


def _factory(tmp_path, seeds: Path, db: str = ":memory:", agents=None):
    settings = Settings(
        factory_mode="dry-run",
        factory_home=str(REPO),
        data_dir=str(tmp_path / "data"),
        skill_seeds_dir=str(seeds),
        _env_file=None,
    )
    return build_factory(settings, CFG, SqliteStateStore(db), FakeExecutor(), agents or FakeAgentRunner())


def test_builderio_skills_are_not_in_the_repo() -> None:
    names = {p.name for p in (REPO / "plugin" / "skills").iterdir() if p.is_dir()}
    assert names == {"factory-station-contract", "fastapi-golden-path", "helm-kind-deploy"}
    assert SRC.repo == "BuilderIO/skills" and len(SRC.sha) == 40


async def test_first_start_installs_defaults_pinned(tmp_path):
    runner = FakeAgentRunner()
    f = _factory(tmp_path, make_seeds(tmp_path / "seeds"), agents=runner)
    installed = {r.name: r for r in f.workflows.library.imported()}
    assert set(installed) == {Path(p).name for p in SRC.paths}
    rec = installed["agent-watchdog"]
    assert (rec.repo, rec.path, rec.ref, rec.sha) == (SRC.repo, "skills/agent-watchdog", "main", SRC.sha)
    assert f.workflows[WF].get(1).skill_pins["agent-watchdog"] == SRC.sha
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    verifier = next(c for c in runner.calls if c.role == "verifier")
    assert "agent-watchdog" in verifier.imported_skills and "agent-watchdog" not in verifier.skills
    assert (verifier.imported_plugin / "skills" / "agent-watchdog" / "SKILL.md").exists()
    await f.manager.shutdown()


async def test_removed_or_updated_defaults_are_left_alone(tmp_path):
    db = str(tmp_path / "f.db")
    seeds = make_seeds(tmp_path / "seeds")
    f = _factory(tmp_path, seeds, db)
    f.workflows.library.remove("quick-recap")  # unused by the default workflow
    await f.manager.shutdown()
    f2 = _factory(tmp_path, seeds, db)  # restart
    assert "quick-recap" not in f2.workflows.library.imported_names()
    assert len(f2.workflows.library.versions("agent-watchdog")) == 1
    await f2.manager.shutdown()


def test_without_seeds_fetches_the_pinned_commit(tmp_path):
    f = _factory(tmp_path, make_seeds(tmp_path / "seeds"))
    lib = f.workflows.library
    for name in list(lib.imported_names()):
        f.store._exec("DELETE FROM skill_versions WHERE name=?", (name,))  # pretend: never installed
        lib.store.set_current_skill(name, None)
    calls = []
    src_root = make_seeds(tmp_path / "gh")

    def fetch(repo, path, ref):
        calls.append(ref)
        keep = tmp_path / "tmp" / path.replace("/", "_")
        keep.mkdir(parents=True, exist_ok=True)
        return Fetched(repo, path, ref, ref, src_root / repo / SRC.sha / path, keep)

    assert lib.ensure_defaults(CFG.default_skills, tmp_path / "no-seeds", fetch) == []
    assert calls == [SRC.sha] * len(SRC.paths), "fetches the pinned commit, never the moving branch"
    assert lib.get("plow-ahead").ref == "main"


def test_offline_first_start_explains_itself(tmp_path, monkeypatch):
    import agent_factory.app_factory as af

    def no_network(*_a, **_k):
        raise GitHubError("could not reach github.com")

    monkeypatch.setattr(af, "fetch_dir", no_network)
    with pytest.raises(WorkflowError, match="default skills are missing") as err:
        _factory(tmp_path, tmp_path / "empty-seeds")
    assert any("could not reach github.com" in p for p in err.value.problems)


async def test_versions_from_before_pins_use_the_installed_default(tmp_path):
    """Workflow versions created while BuilderIO skills shipped in the image have no
    pins; their runs load the installed default instead of failing."""
    f = _factory(tmp_path, make_seeds(tmp_path / "seeds"))
    doc = f.workflows[WF].get(1).model_copy(update={"skill_pins": {}})
    pins = f.workflows.library.effective_pins(doc)
    assert pins["agent-watchdog"] == SRC.sha and "factory-station-contract" not in pins
    await f.manager.shutdown()


def test_cli_cache_writes_seeds_and_notice(tmp_path, monkeypatch):
    import agent_factory.cli as cli
    import agent_factory.github as gh

    src_root = make_seeds(tmp_path / "gh")

    def fetch(repo, path, ref, token=None):
        keep = tmp_path / "tmp" / path.replace("/", "_")
        keep.mkdir(parents=True, exist_ok=True)
        root = keep / "root"
        shutil.copytree(src_root / repo / SRC.sha / path, root)
        return Fetched(repo, path, ref, ref, root, keep)

    monkeypatch.setattr(gh, "fetch_dir", fetch)
    monkeypatch.setenv("FACTORY_CONFIG", str(REPO / ".agent-factory" / "config.yaml"))
    monkeypatch.setattr(sys, "argv", ["agent-factory", "skills", "cache", "--out", str(tmp_path / "out")])
    cli.main()
    base = tmp_path / "out" / SRC.repo / SRC.sha
    assert (base / "skills" / "plow-ahead" / "SKILL.md").exists()
    assert "MIT" in (base / "NOTICE").read_text()


def test_unrelated_upstream_commit_is_not_an_update(tmp_path):
    f = _factory(tmp_path, make_seeds(tmp_path / "seeds"))
    root = make_seeds(tmp_path / "gh") / SRC.repo / SRC.sha / "skills/plow-ahead"
    keep = tmp_path / "keep"
    keep.mkdir()
    pv = f.workflows.library.preview(Fetched(SRC.repo, "skills/plow-ahead", "main", "f" * 40, root, keep))
    assert pv.up_to_date and pv.diff is None and pv.installed_sha == SRC.sha
