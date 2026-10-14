"""The product work ledger (ADR-0040): items found across iterations, addressed by changes,
verified by later assessments, or decided against with a reason."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import wait_run
from pydantic import ValidationError
from test_api import _client
from test_existing_repos import _factory, _origin
from test_repo_changes import _assessed

from agent_factory import ledger
from agent_factory.engine.pipeline import InvalidRequestError
from agent_factory.models import ChangeKind, ChangeStatus, WorkItemDecisionInput

T0 = datetime(2026, 10, 14, 9, tzinfo=UTC)
P = "prod1"


def _assessment(issues=(), risks=(), gaps=(), recs=(), coverage=("code", "dependencies", "config")) -> dict:
    checks = [
        {"station": f"{c}-scan", "check": c, "title": f"{c} scan", "status": "tool", "via": "tool"}
        for c in ("code", "dependencies", "config")
    ]
    for c in checks:
        if c["check"] not in coverage:
            c["status"] = "agent"
    return {
        "issues": [
            {"id": f"I{n}", "title": t, "severity": sev, "category": cat, "detail": "", "at": ["a.js:1"], "sources": []}
            for n, (t, sev, cat) in enumerate(issues, 1)
        ],
        "risks": [
            {"severity": "high", "pillar": "security", "title": t, "detail": "d", "files": ["a.js"]} for t in risks
        ],
        "test_gaps": [{"area": a, "why": "untested", "files": ["a.js"]} for a in gaps],
        "recommendations": [
            {"title": t, "why": "w", "kind": "upkeep", "effort": "S", "severity": "high", "issues": ["I1"]}
            for t in recs
        ],
        "coverage": {"checks": checks},
    }


def _run(items, assessment, it, delivered=(), at=T0):
    return ledger.reconcile(P, items, assessment, change_id=f"a{it}", iteration=it, now=at, delivered=delivered)


def _by_title(items):
    return {i.title: i for i in items}


# ------------------------------------------------------------------- pure --
def test_an_assessment_adds_items_and_the_next_one_recognises_them():
    a = _assessment(
        issues=[("lodash RCE", "critical", "dependencies")],
        risks=["Personal SSH key used by Argo"],
        gaps=["scenario branches"],
        recs=["Upgrade vulnerable dependencies"],
    )
    items = _run([], a, 1)
    assert sorted(i.kind for i in items) == ["issue", "recommendation", "risk", "test_gap"]
    assert all(i.status == "open" and i.first_iteration == 1 for i in items)
    rec = _by_title(items)["Upgrade vulnerable dependencies"]
    assert rec.related == [_by_title(items)["lodash RCE"].id], "a recommendation points at its issues"
    again = _run(items, a, 2)
    assert len(again) == 4 and all(i.seen_in == [1, 2] for i in again)
    assert [h.event for h in _by_title(again)["lodash RCE"].history] == ["found", "seen"]


def test_the_assessor_rewording_a_risk_is_the_same_item():
    items = _run([], _assessment(risks=["Argo CD reads the repo with a personal SSH key"]), 1)
    items = _run(items, _assessment(risks=["Argo CD reads the repo with a developer's personal SSH key"]), 2)
    assert len(items) == 1 and items[0].seen_in == [1, 2]


def test_an_issue_is_fixed_only_when_its_check_really_ran_again():
    a = _assessment(issues=[("Container runs as root", "high", "config")])
    items = _run([], a, 1)
    items = _run(items, _assessment(coverage=("code", "dependencies")), 2)  # the config scan fell back to the agent
    assert items[0].status == "open", "not checked this time is not fixed"
    items = _run(items, _assessment(), 3)
    assert (items[0].status, items[0].resolved_by) == ("fixed", "a3")
    assert items[0].history[-1].note == "not found in iteration 3 (no longer found)"
    rules = _run([], _assessment(issues=[("No CI workflow", "medium", "rules:maintainability")]), 1)
    rules = _by_title(_run(rules, _assessment(coverage=()), 2))
    assert rules["No CI workflow"].status == "fixed", "the factory rules always run"
    assert rules["code scan: agent"].kind == "coverage_gap" and rules["code scan: agent"].status == "open"


def test_a_change_claims_items_and_the_next_assessment_verifies_them():
    a = _assessment(
        issues=[("Image tag latest", "high", "config"), ("Runs as root", "high", "config")],
        risks=["No graceful shutdown"],
    )
    items = _run([], a, 1)
    ids = [i.id for i in items]
    ledger.claim(items, ids, change_id="c2", iteration=2, now=T0, by="ada")
    assert all(i.status == "in_progress" and i.claimed_by == ["c2"] for i in items)
    ledger.release(items, change_id="c2", status="awaiting_feedback", now=T0, pr_url="https://gh/pr/7")
    assert all(i.history[-1].note == "pull request: https://gh/pr/7" for i in items)
    after = _assessment(issues=[("Runs as root", "high", "config")])  # one fixed, one still there, risk not repeated
    items = _by_title(_run(items, after, 3, delivered=["c2"]))
    assert (
        items["Image tag latest"].status == "fixed" and "after change c2" in items["Image tag latest"].history[-1].note
    )
    assert items["Runs as root"].status == "open" and items["Runs as root"].history[-1].event == "still-present"
    risk = items["No graceful shutdown"]
    assert risk.status == "fixed" and "not reported again by the assessor after change c2" in risk.history[-1].note


def test_judgement_items_are_not_closed_just_because_the_assessor_was_silent():
    items = _run([], _assessment(risks=["No graceful shutdown"], gaps=["error paths"]), 1)
    items = _run(items, _assessment(), 2)
    assert {i.status for i in items} == {"open"}


def test_a_failed_change_returns_its_items_and_a_fix_can_regress():
    items = _run([], _assessment(issues=[("XSS in app.js", "high", "code")]), 1)
    ledger.claim(items, [items[0].id], change_id="c2", iteration=2, now=T0, by=None)
    ledger.release(items, change_id="c2", status="failed", now=T0, pr_url=None)
    assert (items[0].status, items[0].claimed_by) == ("open", [])
    items = _run(items, _assessment(), 3)
    assert items[0].status == "fixed"
    items = _run(items, _assessment(issues=[("XSS in app.js", "high", "code")]), 4)
    assert (items[0].status, items[0].history[-1].event) == ("open", "reappeared")


def test_decisions_need_a_reason_and_accepted_risks_expire():
    items = _run([], _assessment(risks=["Single replica"], issues=[("Runs as root", "high", "config")]), 1)
    risk, issue = _by_title(items)["Single replica"], _by_title(items)["Runs as root"]
    with pytest.raises(ValidationError, match="say why"):
        WorkItemDecisionInput(items=[risk.id], decision="wont_do", reason="no")
    ledger.decide(
        items,
        [risk.id],
        "accepted_risk",
        reason="Demo app, one replica is fine",
        by="ada",
        now=T0,
        until=T0 + timedelta(days=30),
    )
    ledger.decide(items, [issue.id], "wont_do", reason="Base image needs root; tracked upstream", by="ada", now=T0)
    assert (risk.status, issue.status, issue.decision_by) == ("accepted_risk", "wont_do", "ada")
    items = _run(
        items,
        _assessment(risks=["Single replica"], issues=[("Runs as root", "high", "config")]),
        2,
        at=T0 + timedelta(days=31),
    )
    assert _by_title(items)["Single replica"].status == "open", "the acceptance expired"
    assert _by_title(items)["Runs as root"].status == "wont_do", "a decision is not undone by being found again"
    with pytest.raises(ValueError, match="reopen these first"):
        ledger.claim(items, [issue.id], change_id="c3", iteration=3, now=T0, by=None)
    ledger.decide(items, [issue.id], "reopen", reason="", by="ada", now=T0)
    ledger.claim(items, [issue.id], change_id="c3", iteration=3, now=T0, by=None)
    with pytest.raises(ValueError, match="being addressed"):
        ledger.decide(items, [issue.id], "wont_do", reason="changed my mind again", by="ada", now=T0)


def test_the_request_spells_out_the_items():
    items = _run([], _assessment(issues=[("Runs as root", "high", "config")]), 1)
    text = ledger.request_text("Harden the deployment", items)
    assert text.startswith("Harden the deployment\n\nWork items to address:")
    assert f"- [{items[0].id}] high · Runs as root (a.js:1)" in text


# ------------------------------------------------------------ end to end --
async def test_items_from_an_assessment_become_a_change_and_are_verified(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    product = await _assessed(f)
    items = f.store.list_work_items(product.id)
    assert items and {i.first_iteration for i in items} == {1}
    assert any(i.kind == "coverage_gap" for i in items), "dry-run scans are gaps, tracked like the rest"
    pick = [i for i in items if i.kind == "issue"][:2]
    with pytest.raises(InvalidRequestError, match="unknown work items"):
        f.manager.feedback(product.id, "Fix these", ChangeKind.upkeep, ["wi_nope"])
    change = f.manager.feedback(product.id, "Fix the top findings", ChangeKind.upkeep, [i.id for i in pick])
    assert change.work_items == [i.id for i in pick] and "Work items to address:" in (change.change_request or "")
    assert {i.status for i in f.store.list_work_items(product.id) if i.id in change.work_items} == {"in_progress"}
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    again = f.manager.feedback(product.id, "Assess the latest commit again", ChangeKind.assess)
    assert await wait_run(f, again.id) == ChangeStatus.awaiting_feedback
    now = {i.id: i for i in f.store.list_work_items(product.id)}
    for i in pick:  # dry-run changes nothing in the repo: the rules still find them
        assert now[i.id].status == "open" and now[i.id].history[-1].event == "still-present"
    assert any("work ledger:" in e.message for e in f.store.list_events(again.id))

    app, ctx, c = await _client(f)
    async with c:
        out = (await c.get(f"/api/changes/{change.id}/outcome")).json()
        assert [i["id"] for i in out["items"]] == [i.id for i in pick] and out["files"]
        assert {v["result"] for v in out["verified"]} == {"still-present"}
        assert any(g["station"] == "handover" for g in out["gathered"])
        listed = (await c.get(f"/api/products/{product.id}/work-items")).json()
        target = next(i for i in listed if i["kind"] == "risk" or i["kind"] == "test_gap" or i["status"] == "open")
        r = await c.post(
            f"/api/products/{product.id}/work-items/decide",
            json={"items": [target["id"]], "decision": "wont_do", "reason": "Out of scope for the demo"},
        )
        assert r.status_code == 200, r.text
        assert next(i for i in r.json() if i["id"] == target["id"])["status"] == "wont_do"
        assessment = (await c.get(f"/api/changes/{again.id}/outcome")).json()
        assert set(assessment["found"]) >= {"new", "fixed", "reopened"}
    await ctx.__aexit__(None, None, None)
