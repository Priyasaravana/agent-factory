from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_factory.agents import FakeAgentRunner
from agent_factory.app_factory import Factory, build_factory
from agent_factory.config import load_config
from agent_factory.executor import FakeExecutor
from agent_factory.models import TERMINAL, CreateOrderInput, RunStatus
from agent_factory.settings import Settings
from agent_factory.state import SqliteStateStore

REPO = Path(__file__).resolve().parents[2]
WAIT_STATES = TERMINAL | {RunStatus.held, RunStatus.needs_input, RunStatus.paused_limits}


@pytest.fixture
def cfg():
    return load_config(REPO / ".agent-factory" / "config.yaml")


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


async def wait_run(f: Factory, run_id: str, timeout: float = 30) -> RunStatus:
    async def poll() -> RunStatus:
        while True:
            run = f.store.get_run(run_id)
            if run and run.status in WAIT_STATES and not f.manager.is_active(run_id):
                return run.status
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout)


ORDER = CreateOrderInput(
    title="Bookmarks service",
    requirements="Save bookmarks with url, title, notes and tags; list and filter by tag.",
)
