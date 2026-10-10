"""End-to-end line behaviour in dry-run mode (fake agents + fake commands,
real git). These encode the factory's contract: routes, loops, holds, gates."""

from __future__ import annotations

from conftest import PRODUCT, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus, EventKind


def _finished(f, change_id):
    return [
        (e.station, e.data.get("outcome"))
        for e in f.store.list_events(change_id)
        if e.kind == EventKind.station_finished
    ]


async def test_happy_path_delivers_and_opens_feedback_gate(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    stations = [s for s, _ in _finished(f, change.id)]
    assert stations == [
        "requirements", "design", "implement", "test", "quality-gate", "sast", "dependencies", "iac",
        "code-review", "change-risk", "build", "deploy", "acceptance", "handover",
    ]  # fmt: skip
    product = f.store.get_product(product.id)
    assert product.latest_status == ChangeStatus.awaiting_feedback
    assert product.app_url == "http://localhost:8081"

    repo = f.manager.ws.product_dir(product.slug)
    wt = f.manager.ws.change_dir(change.id)
    assert (repo / "docs" / "spec.md").exists(), "run branch merged into main"
    assert (wt / "tests" / "acceptance" / "scenarios.yaml").exists()
    hold = f.manager.ws.holdout_dir(product.slug) / "scenarios.yaml"
    assert hold.exists() and not str(hold).startswith(str(wt)), "holdout lives outside the worktree"
    tags = await f.manager.ws.git("tag", repo)
    assert "v1" in tags.output
    scan = [c for c in ex.calls if "aquasec/trivy" in c]
    assert scan and "bookmarks-service:" in scan[0], "image scan runs as a container in dind"


async def test_verify_failure_routes_back_to_build_with_evidence(make_factory):
    ex = FakeExecutor(fail_on=["make verify"])
    agents = FakeAgentRunner()
    f = make_factory(ex, agents)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    fin = _finished(f, change.id)
    assert ("test", "failed") in fin
    builds = [c for c in agents.calls if c.role == "developer"]
    assert len(builds) == 2
    assert "simulated failure" in builds[1].prompt, "second build sees the failure evidence"


async def test_acceptance_failure_hides_scenarios_from_builder(make_factory):
    agents = FakeAgentRunner(fail_once=["verifier"])
    f = make_factory(agents=agents)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    fix_prompt = [c for c in agents.calls if c.role == "developer"][1].prompt
    assert "observed: GET /bookmarks?tag=x returned 500" in fix_prompt
    assert "only the 'x' bookmark is returned" not in fix_prompt, "holdout text must not leak"


async def test_deploy_failure_goes_through_repair_station(make_factory):
    f = make_factory(FakeExecutor(fail_on=["helm upgrade"]))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    seq = [s for s, _ in _finished(f, change.id)]
    i = seq.index("deploy")
    assert seq[i : i + 3] == ["deploy", "deploy-repair", "deploy"]


async def test_attempt_budget_holds_then_resume_continues(make_factory):
    ex = FakeExecutor(fail_on=["make verify"] * 3)
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert held.current_station in {"test", "implement"} and "held at" in held.summary

    f.manager.resume(change.id)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback


async def test_intake_questions_open_needs_input(make_factory):
    agents = FakeAgentRunner(intake_questions=["Should bookmarks be private per user?"])
    f = make_factory(agents=agents)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.needs_input
    assert f.store.get_change(change.id).questions == ["Should bookmarks be private per user?"]
    f.manager.answer(change.id, ["No, single user for now."])
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback


async def test_usage_limit_pauses_instead_of_failing(make_factory):
    f = make_factory(agents=FakeAgentRunner(rate_limit_once=True))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.paused_limits
    r = f.store.get_change(change.id)
    assert r.resume_at is not None and r.attempts.get("requirements", 0) == 0


async def test_feedback_starts_next_iteration(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    change1 = f.manager.start_change(product)
    await wait_run(f, change1.id)
    change2 = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert change2.iteration == 2 and change2.change_request.startswith("Add full-text")
    assert await wait_run(f, change2.id) == ChangeStatus.awaiting_feedback
    assert "v2" in (await f.manager.ws.git("tag", f.manager.ws.product_dir(product.slug))).output


async def test_restart_marks_running_runs_interrupted(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    f.manager._tasks[change.id].cancel()
    r = f.store.get_change(change.id)
    r.status = ChangeStatus.running
    f.store.save_change(r)
    assert f.manager.recover_on_startup() == [change.id]
    assert f.store.get_change(change.id).status == ChangeStatus.interrupted
