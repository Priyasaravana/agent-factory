"""Intake hardening: an order that requires another stack is asked about, not silently
built in Python; every requirement is checked live by a hidden scenario or says why not."""

from __future__ import annotations

import copy

from conftest import ORDER, wait_run
from test_spec_driven import SpecRunner, _messages

from agent_factory import stack
from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.fake import _SPEC
from agent_factory.engine.stations import _validate_spec
from agent_factory.models import CreateOrderInput, RunStatus
from agent_factory.traceability import matrix

LINE = ["python", "fastapi", "postgres", "sqlalchemy", "docker", "helm", "kubernetes"]


# ------------------------------------------------------------ pure parts --
def test_conflicts_are_decided_on_canonical_names():
    assert stack.conflicts(["Python", "FastAPI", "PostgreSQL"], LINE) == []
    assert stack.conflicts(["Express.js", "NodeJS", "MongoDB"], LINE) == ["node.js", "mongodb"]
    assert stack.conflicts(["Golang"], LINE) == ["go"]
    assert stack.conflicts(["React"], []) == [], "a product line that declares no stack checks nothing"
    assert stack.conflicts(["Elm"], LINE) == ["elm"], "an unknown technology the order requires is still a conflict"


def test_mentions_use_word_boundaries():
    assert stack.mentions("Build it in Node.js with Express.js and MongoDB") == {"node.js", "mongodb"}
    assert stack.mentions("A React front end talking to the API") == {"react"}
    assert stack.mentions("Reactive updates; a javascripting note; go to the store") == set()
    assert stack.mentions("Use Spring Boot and MySQL") == {"java", "mysql"}


def test_every_requirement_is_checked_live_or_says_why_not():
    spec = copy.deepcopy(_SPEC)
    assert _validate_spec(spec) == []
    spec["holdout_scenarios"] = [h for h in spec["holdout_scenarios"] if h["id"] != "H2"]
    [problem] = _validate_spec(spec)
    assert problem.startswith("requirement R1 has no hidden scenario") and "no_live_check" in problem
    spec["requirements"][0]["no_live_check"] = "too short"
    assert len(_validate_spec(spec)) == 1, "a reason needs at least 10 characters"
    spec["requirements"][0]["no_live_check"] = "only observable in the database, not over HTTP"
    assert _validate_spec(spec) == []


# ------------------------------------------------------------ in the engine --
async def test_an_order_for_another_stack_is_asked_about_before_any_build(make_factory):
    agents = FakeAgentRunner(stack_required=[{"technology": "Node.js", "quote": "build it in Node.js"}])
    f = make_factory(agents=agents)
    order = f.manager.create_order(
        CreateOrderInput(title="Todo in Node", requirements="A to-do API; build it in Node.js with Express.")
    )
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == RunStatus.needs_input
    got = f.store.get_run(run.id)
    assert "requires node.js" in got.questions[0] and "python, fastapi" in got.questions[0]
    assert "build it in Node.js" in got.questions[0], "quotes the order"
    assert not [c for c in agents.calls if c.role in ("architect", "developer")], "nothing was built"
    assert any(m.startswith("stack conflict: the order requires node.js") for m in _messages(f, run.id))

    f.manager.answer(run.id, ["build it with python"])
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    assert any("answered by a person; building with python" in m for m in _messages(f, run.id))


async def test_a_mention_intake_did_not_report_is_recorded_not_blocking(make_factory):
    f = make_factory()
    order = f.manager.create_order(
        CreateOrderInput(title="Bookmarks", requirements="Save bookmarks; a React front end will call this API later.")
    )
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    assert "the order mentions react; intake judged it not a requirement of the build" in _messages(f, run.id)


async def test_a_spec_without_live_coverage_is_sent_back_once_then_held(make_factory):
    gap = copy.deepcopy(_SPEC)
    gap["holdout_scenarios"] = [h for h in gap["holdout_scenarios"] if h["id"] != "H2"]
    fixed = copy.deepcopy(_SPEC)
    agents = SpecRunner([gap, fixed])
    f = make_factory(agents=agents)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    second = [c for c in agents.calls if c.role == "intake"][1]
    assert "requirement R1 has no hidden scenario" in second.prompt, "the in-station correction names the gap"


def test_the_exemption_reaches_the_traceability_matrix(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.yaml").write_text(
        "- {id: R1, title: Persist, detail: d, no_live_check: only observable in the database}\n"
        "- {id: R2, title: List, detail: d}\n"
    )
    rows = {r["id"]: r for r in matrix(tmp_path, [], [])}
    assert rows["R1"]["no_live_check"] == "only observable in the database" and "no_live_check" not in rows["R2"]
    assert ORDER.product_line == "fastapi-service"
