"""ClaudeAgentRunner wiring, with the SDK's query() stubbed (no model calls)."""

from __future__ import annotations

from pathlib import Path

import claude_agent_sdk
from claude_agent_sdk import RateLimitEvent, RateLimitInfo, ResultMessage

from agent_factory.agents.runner import AgentRequest, ClaudeAgentRunner
from agent_factory.line import load_line_dir

REPO = Path(__file__).resolve().parents[2]
LINE = load_line_dir(REPO / "blueprints" / "default")


def _req(tmp_path: Path, role: str = "developer", schema=None) -> AgentRequest:
    spec = LINE.agents[role]
    return AgentRequest(
        run_id="r1",
        station="build",
        role=role,
        system_prompt=spec.prompt,
        tools=spec.effective_tools(),
        observe_only=spec.observe_only,
        prompt="do it",
        cwd=tmp_path,
        model="sonnet",
        skills=["plow-ahead"],
        skill_overlay="overlay text",
        output_schema=schema,
        protected_paths=["/data/holdout"],
        subagent_model="haiku",
    )


async def _noop_sink(kind, data):
    return None


async def test_options_are_scoped_per_role(tmp_path, monkeypatch):
    seen = {}

    async def fake_query(prompt, options):
        seen["options"] = options
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=3,
            session_id="s",
            total_cost_usd=0.12,
            result="done",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    res = await ClaudeAgentRunner(REPO).run(_req(tmp_path), _noop_sink)
    o = seen["options"]
    assert res.ok and res.cost_usd == 0.12 and res.turns == 3
    assert o.tools == ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Task"]
    assert o.skills == ["agent-factory:plow-ahead"]
    assert o.plugins[0]["path"].endswith("plugin")
    assert o.agents["mechanic"].model == "haiku"
    assert "PreToolUse" in o.hooks
    assert "overlay text" in o.system_prompt and "Role: Developer" in o.system_prompt


async def test_verifier_gets_no_subagents_and_structured_output(tmp_path, monkeypatch):
    seen = {}

    async def fake_query(prompt, options):
        seen["options"] = options
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            structured_output={"passed": True},
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    res = await ClaudeAgentRunner(REPO).run(_req(tmp_path, "verifier", {"type": "object"}), _noop_sink)
    assert res.structured == {"passed": True}
    assert seen["options"].agents is None
    assert seen["options"].output_format["type"] == "json_schema"


async def test_missing_structured_output_is_a_failure(tmp_path, monkeypatch):
    async def fake_query(prompt, options):
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="I think it passed",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    res = await ClaudeAgentRunner(REPO).run(_req(tmp_path, "verifier", {"type": "object"}), _noop_sink)
    assert not res.ok and "missing evidence" in res.error


async def test_rate_limit_rejection_is_reported(tmp_path, monkeypatch):
    async def fake_query(prompt, options):
        yield RateLimitEvent(
            rate_limit_info=RateLimitInfo(status="rejected", resets_at=1_900_000_000, utilization=1.0),
            uuid="u",
            session_id="s",
        )
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=0, session_id="s"
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    res = await ClaudeAgentRunner(REPO).run(_req(tmp_path), _noop_sink)
    assert res.rate_limited and res.limit_resets_at == 1_900_000_000
