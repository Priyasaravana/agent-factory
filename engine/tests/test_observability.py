"""Agent observability (ADR-0022): denials, per-call records, transcripts, effort."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import ORDER, wait_run
from test_api import _client

from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.runner import AgentResult
from agent_factory.executor import FakeExecutor
from agent_factory.models import EventKind, RunStatus
from agent_factory.observe import MAX_TRANSCRIPT_BYTES, AgentCall, read_transcript
from agent_factory.secret_refs import REDACTOR

WF = "fastapi-service"


# --------------------------------------------------------------- AgentCall --
async def test_a_call_records_tools_denials_and_a_redacted_transcript(tmp_path: Path):
    events: list[tuple] = []

    async def emit(kind, message, data):  # noqa: ANN001
        events.append((kind, message, data))

    forwarded: list[str] = []

    async def inner(kind, data):  # noqa: ANN001
        forwarded.append(kind)

    REDACTOR.add("s3cr3t-token-value")
    call = AgentCall(tmp_path, "build", "developer", "sonnet", emit)
    sink = call.wrap(inner)
    await sink("tool", {"tool": "Bash", "input": "make verify"})
    await sink("tool", {"tool": "Bash", "input": "pytest -q"})
    await sink("tool", {"tool": "Edit", "input": "app/main.py"})
    await sink("transcript", {"type": "tool_result", "id": "1", "content": "token=s3cr3t-token-value"})
    await sink("denied", {"tool": "Bash", "input": "git push", "reason": "blocked: pushing"})
    await sink("agent", {"text": "done"})
    summary = await call.finish(AgentResult(ok=True, turns=7, cost_usd=0.4321))

    assert forwarded == ["tool", "tool", "tool", "agent"], "denials and transcript lines are not log noise"
    assert summary["tools"] == {"Bash": 2, "Edit": 1} and summary["tool_calls"] == 3 and summary["denied"] == 1
    assert (summary["turns"], summary["cost_usd"], summary["transcript"]) == (7, 0.4321, "01-build-developer.jsonl")
    denial = next(e for e in events if e[0] == EventKind.decision)
    assert denial[1] == "guardrail denied Bash for developer: blocked: pushing"
    assert denial[2]["denied"]["station"] == "build"
    assert events[-1][1].startswith("agent call: developer (sonnet) 7 turns") and "1 denied" in events[-1][1]
    text = (tmp_path / "sessions" / "01-build-developer.jsonl").read_text()
    assert "s3cr3t-token-value" not in text and "token=" in text, "secrets never reach a transcript"


async def test_transcripts_are_capped_and_names_cannot_escape(tmp_path: Path, monkeypatch):
    import agent_factory.observe as ob

    monkeypatch.setattr(ob, "MAX_TRANSCRIPT_BYTES", 300)

    async def emit(*_):  # noqa: ANN002
        return None

    async def inner(*_):  # noqa: ANN002
        return None

    call = AgentCall(tmp_path, "build", "developer", "m", emit)
    sink = call.wrap(inner)
    for i in range(20):
        await sink("transcript", {"type": "text", "text": f"line {i} " + "x" * 40})
    summary = await call.finish(AgentResult(ok=True))
    entries = read_transcript(tmp_path, "01-build-developer.jsonl")
    assert summary["transcript_truncated"] and entries[-1]["type"] == "truncated"
    assert len((tmp_path / "sessions" / "01-build-developer.jsonl").read_bytes()) < 400
    for bad in ("../factory.db", "01-build-developer.jsonl/../../x", "notes.txt"):
        with pytest.raises(ValueError):
            read_transcript(tmp_path, bad)
    assert MAX_TRANSCRIPT_BYTES == 2_000_000


# ------------------------------------------------------------- end to end --
async def test_every_agent_call_of_a_run_is_recorded_with_its_transcript(make_factory):
    f = make_factory()
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    app, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/runs/{run.id}/calls")).json()
        roles = [x["role"] for x in view["calls"]]
        assert roles == ["intake", "architect", "developer", "reviewer", "verifier"]
        build = view["calls"][2]
        assert build["station"] == "build" and build["tools"] == {"Read": 1} and build["transcript"]
        assert view["denials"] == []
        entries = (await c.get(f"/api/runs/{run.id}/calls/{build['transcript']}/transcript")).json()
        assert [e["type"] for e in entries] == ["text", "tool_use", "tool_result"]
        assert (await c.get(f"/api/runs/{run.id}/calls/..%2Ffactory.db/transcript")).status_code == 404
        assert (await c.get(f"/api/runs/{run.id}/calls/99-x-y.jsonl/transcript")).status_code == 404
    await ctx.__aexit__(None, None, None)


async def test_a_denial_is_evidence_and_teaches(make_factory):
    agents = FakeAgentRunner(deny_once=["developer"])
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    decisions = [e.message for e in f.store.list_events(run.id) if e.kind == EventKind.decision]
    assert "guardrail denied Bash for developer: blocked: pushing is done by the engine after checks" in decisions
    for _ in range(100):  # the retro runs after delivery
        if f.store.list_proposals(WF):
            break
        await asyncio.sleep(0.05)
    retro = next(c for c in agents.calls if c.role == "retro")
    assert "git push origin main" in retro.prompt and "blocked: pushing" in retro.prompt
    assert "build / developer:" in retro.prompt, "call stats are context for efficiency lessons"
    assert f.store.list_proposals(WF), "a denial alone is reason enough to learn"
    from agent_factory.outcomes import outcomes

    o = outcomes(f.store, 7)
    assert o.guardrail_denials == 1
    build = next(e for e in o.effort_by_station if e.station == "build")
    assert (build.calls, build.denied, build.tool_calls) == (1, 1, 1)
    assert {e.station for e in o.effort_by_station} >= {"intake", "design", "build", "review", "acceptance", "retro"}


async def test_the_retro_call_is_recorded_too(make_factory):
    f = make_factory(FakeExecutor(fail_on=["make verify"]))
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    for _ in range(100):
        calls = [v for _, _, v in f.store.events_with_key([run.id], "agent_call")]
        if any(c["station"] == "retro" for c in calls):
            break
        await asyncio.sleep(0.05)
    retro = next(c for c in calls if c["station"] == "retro")
    sessions = f.manager.ws.data_dir / "artifacts" / run.id / "sessions"
    assert retro["transcript"] and (sessions / retro["transcript"]).is_file()
    assert json.loads((sessions / retro["transcript"]).read_text().splitlines()[0])["type"] == "text"
