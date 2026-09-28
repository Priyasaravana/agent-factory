"""Preloaded skills: the skill's instructions go into the system prompt, so the
agent always follows them instead of deciding whether to open the skill."""

from __future__ import annotations

import pytest
from conftest import ORDER, wait_run
from pydantic import ValidationError

from agent_factory.agents import FakeAgentRunner
from agent_factory.models import RunStatus
from agent_factory.workflow import AgentSpec

WF = "fastapi-service"


def test_preload_must_be_a_listed_skill():
    with pytest.raises(ValidationError, match="must also be listed in skills"):
        AgentSpec(id="checker", skills=["plow-ahead"], preload_skills=["agent-watchdog"], prompt="p")


async def test_preloaded_skills_are_in_the_prompt_and_not_on_demand(make_factory):
    runner = FakeAgentRunner()
    f = make_factory(agents=runner)
    d = f.workflows[WF].draft
    doc = d.get()[1]
    verifier = doc.agents["verifier"]
    d.upsert_agent(verifier.model_copy(update={"preload_skills": ["agent-watchdog", "factory-station-contract"]}))
    assert d.problems() == []
    d.publish("preload skills for acceptance")
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback

    req = next(c for c in runner.calls if c.role == "verifier")
    assert "### Skill: agent-watchdog" in req.system_prompt, "imported skill text (pinned copy) is preloaded"
    assert "### Skill: factory-station-contract" in req.system_prompt, "built-in skill text is preloaded"
    assert "agent-watchdog" not in req.imported_skills and "factory-station-contract" not in req.skills
    assert "name: agent-watchdog" not in req.system_prompt, "frontmatter is stripped"
    other = next(c for c in runner.calls if c.role == "developer")
    assert "### Skill:" not in other.system_prompt
    logs = [e.message for e in f.store.list_events(run.id) if e.message.startswith("preloaded skills")]
    assert logs == ["preloaded skills: ['agent-watchdog', 'factory-station-contract']"]
