from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_factory.agents import FakeAgentRunner
from agent_factory.app_factory import Factory, build_factory
from agent_factory.config import load_config
from agent_factory.executor import FakeExecutor
from agent_factory.models import TERMINAL, ChangeStatus, CreateProductInput
from agent_factory.settings import Settings
from agent_factory.state import SqliteStateStore

REPO = Path(__file__).resolve().parents[2]
WAIT_STATES = TERMINAL | {
    ChangeStatus.held,
    ChangeStatus.needs_input,
    ChangeStatus.paused_limits,
    ChangeStatus.awaiting_approval,
    ChangeStatus.awaiting_risk_approval,
}


@pytest.fixture
def cfg():
    return load_config(REPO / ".agent-factory" / "config.yaml")


def default_skill_names() -> set[str]:
    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    return {Path(p).name for src in cfg.default_skills for p in src.paths}


def make_seeds(root: Path) -> Path:
    """Stand-ins for the default skills the image caches at build time (no network in tests)."""
    from agent_factory.skills import seed_dir

    for src in load_config(REPO / ".agent-factory" / "config.yaml").default_skills:
        for p in src.paths:
            d = seed_dir(root, src.repo, src.sha, p)
            d.mkdir(parents=True, exist_ok=True)
            name = Path(p).name
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test stand-in for {name}\n---\n# {name}\n")
    return root


_CREATED: list[Factory] = []


@pytest.fixture(autouse=True)
async def _stop_runs_after_test():
    """Cancel runs a test left in flight, so the event loop can close cleanly."""
    yield
    while _CREATED:
        await _CREATED.pop().manager.shutdown()


@pytest.fixture
def make_factory(tmp_path, cfg):
    def make(executor: FakeExecutor | None = None, agents: FakeAgentRunner | None = None) -> Factory:
        settings = Settings(
            factory_mode="dry-run",
            factory_config=str(REPO / ".agent-factory" / "config.yaml"),
            factory_home=str(REPO),
            data_dir=str(tmp_path / "data"),
            skill_seeds_dir=str(make_seeds(tmp_path / "seeds")),
            _env_file=None,
        )
        f = build_factory(
            settings,
            cfg,
            SqliteStateStore(":memory:"),
            executor or FakeExecutor(),
            agents or FakeAgentRunner(),
        )
        _CREATED.append(f)
        return f

    return make


async def wait_run(f: Factory, change_id: str, timeout: float = 30) -> ChangeStatus:
    async def poll() -> ChangeStatus:
        while True:
            change = f.store.get_change(change_id)
            if change and change.status in WAIT_STATES and not f.manager.is_active(change_id):
                return change.status
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout)


PRODUCT = CreateProductInput(
    title="Bookmarks service",
    requirements="Save bookmarks with url, title, notes and tags; list and filter by tag.",
)
