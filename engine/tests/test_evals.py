"""Evaluation harness (ADR-0025): a version is measured on the workflow's fixed
suite against the active version; `block` keeps it a candidate until it passes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from conftest import REPO
from test_api import _client

from agent_factory import evals
from agent_factory.agents import FakeAgentRunner
from agent_factory.models import EvalCaseResult, EvalRun, EvalSide, EvalSummary, EvalVerdict
from agent_factory.workflow import EvalCase, load_workflow_dir, validate_workflow

WF = "fastapi-service"


# ------------------------------------------------------------ pure parts --
def _res(status: str, cost: float = 1.0, loops: int = 0, lead: float | None = 100.0, **kw) -> EvalCaseResult:  # noqa: ANN003
    return EvalCaseResult(
        case_id="c", title="t", status=status, final=True, cost_usd=cost, fix_loops=loops, lead_time_s=lead, **kw
    )


def test_summarize():
    s = evals.summarize(
        [
            _res("delivered", 1.0, 1, 100, readiness_level=3, requirements=4, requirements_verified=4),
            _res(
                "delivered",
                2.0,
                0,
                300,
                readiness_level=2,
                requirements=2,
                requirements_verified=1,
                unplanned_touch=True,
            ),
            _res("failed", 0.5, 3, None),
        ]
    )
    assert (s.cases, s.delivered, s.pass_rate) == (3, 2, 0.6667)
    assert (s.cost_usd, s.cost_per_delivery_usd, s.fix_loops_per_case) == (3.5, 1.75, 1.3333)
    assert (s.autonomy, s.level3_share, s.verified_live_share) == (0.5, 0.5, round(5 / 6, 4))
    assert s.lead_time_median_s == 200.0
    empty = evals.summarize([_res("failed", 0.2, 0, None)])
    assert empty.cost_per_delivery_usd is None and empty.autonomy is None and empty.level3_share is None


def _sum(**kw) -> EvalSummary:  # noqa: ANN003
    base = {
        "cases": 3,
        "delivered": 3,
        "pass_rate": 1.0,
        "autonomy": 1.0,
        "cost_usd": 3.0,
        "cost_per_delivery_usd": 1.0,
        "fix_loops_per_case": 0.5,
        "lead_time_median_s": 600.0,
        "level3_share": 1.0,
        "verified_live_share": 1.0,
    }
    return EvalSummary(**{**base, **kw})


def test_judge_regressions_block_and_warnings_do_not():
    v = evals.judge(_sum(), _sum(), 1)
    assert v.passed and v.compared_to == 1 and not v.regressions and not v.warnings
    v = evals.judge(_sum(), _sum(pass_rate=0.6667, delivered=2), 1)
    assert not v.passed and "pass rate fell from 100% to 67%" in v.regressions
    v = evals.judge(_sum(), _sum(cost_per_delivery_usd=1.2), 1)
    assert v.passed and "within the margin" in v.warnings[0], "+20%: a warning"
    v = evals.judge(_sum(), _sum(cost_per_delivery_usd=1.3), 1)
    assert not v.passed and "cost per delivery rose from $1.00 to $1.30" in v.regressions
    assert evals.judge(_sum(cost_per_delivery_usd=0.4), _sum(cost_per_delivery_usd=0.6), 1).passed, "under the $ floor"
    v = evals.judge(_sum(), _sum(fix_loops_per_case=1.1), 1)
    assert not v.passed and v.regressions[0].startswith("fix loops per case rose")
    assert evals.judge(_sum(), _sum(verified_live_share=0.95), 1).passed, "within the 10-point tolerance"
    assert not evals.judge(_sum(), _sum(verified_live_share=0.8), 1).passed
    assert not evals.judge(_sum(), _sum(autonomy=0.6667), 1).passed
    assert not evals.judge(_sum(), _sum(level3_share=0.6667), 1).passed
    v = evals.judge(_sum(), _sum(lead_time_median_s=1000.0), 1)
    assert v.passed and v.warnings == ["median lead time rose from 600s to 1000s"]
    first = evals.judge(None, _sum())
    assert first.passed and first.compared_to is None and "baseline" in first.warnings[0]


def test_the_suite_is_validated():
    doc = load_workflow_dir(REPO / "workflow-templates" / "default")
    assert [c.id for c in doc.evals] == ["bookmarks", "todo", "shortener"] and doc.eval_gate == "block"
    assert validate_workflow(doc) == []
    bad = doc.model_copy(update={"evals": [*doc.evals, doc.evals[0]]})
    assert "evaluation case ids must be unique" in validate_workflow(bad)
    none = doc.model_copy(update={"evals": []})
    assert "eval_gate 'block' needs at least one evaluation case" in validate_workflow(none)
    odd = doc.model_copy(update={"evals": [EvalCase(id="Bad Id", title="xyz", requirements="0123456789")]})
    assert any("must be lowercase" in p for p in validate_workflow(odd))
    assert doc.eval_suite_hash() != odd.eval_suite_hash()


# ------------------------------------------------------------ the engine --
async def _wait_eval(f, eval_id: str, timeout: float = 60) -> EvalRun:  # noqa: ANN001
    async def poll() -> EvalRun:
        while True:
            e = f.store.get_eval(eval_id)
            if e and e.status != "running" and not f.manager.is_active(f"eval-{eval_id}"):
                return e
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout)


async def test_a_gated_publish_is_a_candidate_until_its_evaluation_passes(make_factory):
    f = make_factory()
    app, ctx, c = await _client(f)
    async with c:
        r = await c.patch(f"/api/workflows/{WF}/draft/settings", json={"learn_from_runs": False})
        assert r.json()["eval_gate"] == "block" and len(r.json()["evals"]) == 3
        pub = await c.post(f"/api/workflows/{WF}/draft/publish", json={"note": "no learning"})
        assert pub.status_code == 201 and pub.json()["version"] == 2
        assert pub.json()["evaluation"].startswith("evaluation ") and "against version 1" in pub.json()["evaluation"]
        versions = {v["version"]: v for v in (await c.get(f"/api/workflows/{WF}/versions")).json()}
        assert versions[1]["active"] and not versions[2]["active"] and versions[2]["evaluation"] == "evaluating"
        assert (await c.get(f"/api/workflows/{WF}/draft")).json()["dirty"], "the draft is kept until activation"
        [running] = (await c.get(f"/api/workflows/{WF}/evals")).json()
        assert len(running["candidate"]["results"]) == 3 and len(running["baseline"]["results"]) == 3
        assert (await c.get("/api/products")).json() == [], "evaluation orders are not listed"
        refused = await c.post(f"/api/workflows/{WF}/versions/2/activate")
        assert refused.status_code == 409 and "no finished evaluation" in refused.json()["detail"]

        e = await _wait_eval(f, running["id"])
        assert e.status == "done" and e.verdict and e.verdict.passed and e.activated
        assert e.candidate.summary.pass_rate == 1.0 and e.baseline.summary.pass_rate == 1.0
        assert all(r.status == "delivered" and r.readiness_level == 3 for r in e.candidate.results)
        assert f.workflows[WF].active_version() == 2
        assert f.store.get_workflow_draft(WF) is None, "the draft is dropped once its version is active"
        assert all(f.store.get_product(r.product_id).archived_at for _, r in evals._results(e)), "ports freed"
        assert (await c.get("/api/outcomes")).json()["changes_in_window"] == 0, "not on Outcomes"
        versions = {v["version"]: v for v in (await c.get(f"/api/workflows/{WF}/versions")).json()}
        assert versions[2]["active"] and versions[2]["evaluation"] == "evaluation passed"

        # the next version reuses version 2's measured result as its baseline: 3 builds, not 6
        await c.patch(f"/api/workflows/{WF}/draft/settings", json={"learn_from_runs": True})
        pub = await c.post(f"/api/workflows/{WF}/draft/publish", json={"note": "learning back on"})
        [nxt, _] = (await c.get(f"/api/workflows/{WF}/evals")).json()
        assert nxt["baseline_from"] == e.id and nxt["baseline"]["results"] == []
        e3 = await _wait_eval(f, nxt["id"])
        assert e3.verdict.passed and f.workflows[WF].active_version() == 3
    await ctx.__aexit__(None, None, None)


async def test_a_failed_evaluation_blocks_activation_unless_an_admin_gives_a_reason(make_factory):
    f = make_factory()
    w = f.workflows[WF]
    w.draft.set_learn_from_runs(False)
    w.draft.publish("candidate", activate=False)
    failed = EvalRun(
        id="e1",
        workflow_id=WF,
        suite_hash=w.get(2).eval_suite_hash(),
        trigger="publish",
        started_by="saravana",
        created_at=datetime.now(UTC),
        status="done",
        candidate=EvalSide(version=2),
        verdict=EvalVerdict(passed=False, compared_to=1, regressions=["pass rate fell from 100% to 67%"]),
    )
    f.store.save_eval(failed)
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post(f"/api/workflows/{WF}/versions/2/activate")
        assert r.status_code == 409 and "pass rate fell" in r.json()["detail"]
        assert (
            await c.post(f"/api/workflows/{WF}/versions/2/activate", json={"override_reason": "short"})
        ).status_code == 422
        ok = await c.post(
            f"/api/workflows/{WF}/versions/2/activate",
            json={"override_reason": "flaky case; checked the transcripts by hand"},
        )
        assert ok.status_code == 200 and ok.json()["active"]
        assert f.store.get_eval("e1").override.reason.startswith("flaky case")
        states = {v["version"]: v["evaluation"] for v in (await c.get(f"/api/workflows/{WF}/versions")).json()}
        assert states[2].startswith("evaluation failed; activated by")
        # rolling back to an older version is never gated
        assert (await c.post(f"/api/workflows/{WF}/versions/1/activate")).status_code == 200
    await ctx.__aexit__(None, None, None)


async def test_warn_activates_at_once_and_reports(make_factory):
    f = make_factory()
    app, ctx, c = await _client(f)
    async with c:
        await c.patch(f"/api/workflows/{WF}/draft/settings", json={"eval_gate": "warn"})
        pub = await c.post(f"/api/workflows/{WF}/draft/publish", json={"note": "warn only"})
        assert f.workflows[WF].active_version() == 2 and "running" in pub.json()["evaluation"]
        [e] = (await c.get(f"/api/workflows/{WF}/evals")).json()
        done = await _wait_eval(f, e["id"])
        assert done.verdict.passed and not done.activated and done.baseline.version == 1
    await ctx.__aexit__(None, None, None)


async def test_human_gates_in_an_evaluation(make_factory):
    """The spec gate is approved by the evaluation (planned); blocking questions get the
    case's answers and count as an unplanned touch."""
    f = make_factory(agents=FakeAgentRunner(intake_questions=["Which database?"]))
    w = f.workflows[WF]
    w.draft.set_spec_review("always")
    w.draft.publish("gate on")
    e = evals.start(f.manager, WF, 2, "saravana")  # the active version itself: no baseline
    assert e.baseline is None
    e = await _wait_eval(f, e.id)
    assert e.verdict.passed and "baseline" in e.verdict.warnings[0]
    assert all(r.status == "delivered" for r in e.candidate.results)
    assert sum(r.unplanned_touch for r in e.candidate.results) == 1 and e.candidate.summary.autonomy == 0.6667
    events = [ev.message for r in e.candidate.results for ev in f.store.list_events(r.change_id)]
    assert any(m == "spec approved by evaluation" for m in events)
    assert any(m == "evaluation answered intake questions" for m in events)


async def test_a_case_without_answers_ends_as_needed_input(make_factory):
    f = make_factory(agents=FakeAgentRunner(intake_questions=["Which database?"]))
    w = f.workflows[WF]
    w.draft.set_evals([EvalCase(id="one", title="Only case", requirements="Save and tag bookmarks.")])
    w.draft.publish("one case, no answers")
    e = await _wait_eval(f, evals.start(f.manager, WF, 2, "saravana").id)
    [r] = e.candidate.results
    assert r.status == "needed_input" and "blocking questions" in r.note
    assert e.candidate.summary.pass_rate == 0.0
    assert f.store.get_product(r.product_id).archived_at, "archived even though it stopped waiting"


async def test_cancel_and_one_evaluation_at_a_time(make_factory):
    f = make_factory()
    e = evals.start(f.manager, WF, 1, "saravana")
    from agent_factory.engine.pipeline import FactoryError

    with pytest.raises(FactoryError, match="already running"):
        evals.start(f.manager, WF, 1, "saravana")
    done = await evals.cancel(f.manager, f.store.get_eval(e.id))
    assert done.status == "cancelled" and done.verdict is None
    assert all(f.store.get_product(r.product_id).archived_at for r in done.candidate.results)
    assert evals.version_states(f.manager, WF)[1] == "evaluation cancelled"


async def test_a_restart_cancels_a_running_evaluation(make_factory):
    f = make_factory()
    e = evals.start(f.manager, WF, 1, "saravana")
    await f.manager.shutdown()
    f.manager.recover_on_startup()
    assert await evals.recover_on_startup(f.manager) == [e.id]
    got = f.store.get_eval(e.id)
    assert got.status == "cancelled" and all(
        f.store.get_product(r.product_id).archived_at for r in got.candidate.results
    )


async def test_an_evaluation_starts_all_or_nothing_and_counts_only_mapped_ports(make_factory):
    """Live check: an older cluster mapped 5 ports, so the 3rd case had no port; 2 orphan
    products kept running. Now ports are counted as the cluster maps them, before anything starts."""
    from agent_factory.engine.pipeline import FactoryError

    f = make_factory()
    line = f.cfg.blueprints[WF]
    f.manager.preflight.mapped_node_ports = set(line.node_ports[:2])
    with pytest.raises(FactoryError, match=r"needs 3 free app ports and 2 are usable.*make reset-cluster"):
        evals.start(f.manager, WF, 1, "saravana")
    assert f.store.list_products() == [], "nothing was created"

    # a failure half way (here: the 3rd order) cancels and archives what was created
    real = f.manager.create_product
    calls = {"n": 0}

    def flaky(data):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            raise FactoryError("no free app port")
        return real(data)

    f.manager.preflight.mapped_node_ports = None
    f.manager.create_product = flaky  # type: ignore[method-assign]
    with pytest.raises(FactoryError):
        evals.start(f.manager, WF, 1, "saravana")
    await asyncio.sleep(0.2)
    products = f.store.list_products()
    assert len(products) == 2 and all(o.archived_at for o in products)
    assert all(r.status.value == "cancelled" for o in products for r in f.store.list_changes(o.id))


async def test_a_gated_publish_that_cannot_evaluate_says_why(make_factory):
    f = make_factory()
    f.manager.preflight.mapped_node_ports = set(f.cfg.blueprints[WF].node_ports[:2])
    app, ctx, c = await _client(f)
    async with c:
        await c.patch(f"/api/workflows/{WF}/draft/settings", json={"learn_from_runs": False})
        pub = (await c.post(f"/api/workflows/{WF}/draft/publish", json={"note": "no learning"})).json()
        assert pub["evaluation"].startswith("evaluation not started: an evaluation needs 6 free app ports")
        states = {v["version"]: v["evaluation"] for v in (await c.get(f"/api/workflows/{WF}/versions")).json()}
        assert states[2].startswith("evaluation not started: an evaluation needs 6") and "reset-cluster" in states[2]
        assert not (await c.get(f"/api/workflows/{WF}/versions")).json()[0]["active"]
        # fix the cause, then run it from the Evaluation card
        f.manager.preflight.mapped_node_ports = None
        r = await c.post(f"/api/workflows/{WF}/evals", json={"version": 2})
        assert r.status_code == 201
        e = await _wait_eval(f, r.json()["id"])
        assert e.verdict.passed
    await ctx.__aexit__(None, None, None)


async def test_orphan_evaluation_orders_are_cleaned_up_on_start(make_factory):
    from agent_factory.models import CreateProductInput

    f = make_factory()
    product = f.manager.create_product(
        CreateProductInput(title="[eval x v4] Bookmarks", requirements="Save bookmarks.")
    )
    product.eval_run_id = "gone"
    f.store.save_product(product)
    change = f.manager.start_change(product)
    await f.manager.shutdown()
    assert await evals.recover_on_startup(f.manager) == [product.id]
    assert f.store.get_product(product.id).archived_at and f.store.get_change(change.id).status.value == "cancelled"
