"""Needs you (`attention.pending_actions`, `GET /api/attention`): what a change waits for, as actions."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import PRODUCT, wait_run
from test_api import _client

from agent_factory.attention import pending_actions
from agent_factory.models import Change, ChangeStatus, Product, ProductTarget, RequiredAction, RiskHold

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
S = ChangeStatus


def _product(by: str | None = "saravana", archived: bool = False) -> Product:
    return Product(
        id="p1",
        title="Book lending",
        requirements="r",
        blueprint="nextjs-web",
        slug="book-lending",
        created_at=NOW,
        created_by=by,
        archived_at=NOW if archived else None,
    )


def _change(status: ChangeStatus, **kw: object) -> Change:
    return Change.model_validate(
        {
            "id": "c1",
            "product_id": "p1",
            "iteration": 1,
            "status": status,
            "current_station": "acceptance",
            "created_at": NOW,
            "updated_at": NOW,
            **kw,
        }
    )


def _one(status: ChangeStatus, product: Product | None = None, **kw: object):
    got = pending_actions(product or _product(), _change(status, **kw), NOW)
    assert len(got) == 1, got
    return got[0]


def test_questions_go_to_the_requester() -> None:
    a = _one(S.needs_input, questions=["Which currency?", "Public?"])
    assert (a.kind, a.owner, a.panel, a.blocking) == ("answer_questions", "saravana", "questions", True)
    assert "2 answers" in a.detail


def test_review_gate_is_one_align_action_unless_the_architect_is_replying() -> None:
    a = _one(S.awaiting_approval, decisions_pending=True)
    assert (a.kind, a.panel) == ("align", "decisions")
    assert "design decisions" in a.detail
    replying = _one(S.awaiting_approval, discuss_pending="why postgres?")
    assert (replying.kind, replying.owner, replying.blocking) == ("wait", "system", False)


def test_risky_change_waits_for_an_admin() -> None:
    hold = RiskHold(digest="d", station="change-risk", since=NOW, findings=3)
    a = _one(S.awaiting_risk_approval, risk_hold=hold)
    assert (a.kind, a.owner, a.panel) == ("approve_risk", "an admin", "risk")
    assert "3 change-risk findings" in a.detail


def test_holds_name_what_unblocks_them() -> None:
    need = RequiredAction(kind="accept_risk", text="Accept or fix the 1 critical CVE", items=["w1"])
    risk = _one(S.held, required_action=need)
    assert (risk.kind, risk.panel, risk.detail) == ("accept_risk", "work-ledger", need.text)

    infra = _one(S.held, hold_class="infra", summary="held at acceptance: e2e target unreachable")
    assert (infra.kind, infra.owner, infra.panel) == ("factory_hold", "system", "hold")
    assert "e2e target unreachable" in infra.detail

    work = _one(S.held, hold_class="work", summary="held at build: attempt budget used")
    assert (work.kind, work.owner, work.detail) == ("resume", "saravana", "held at build: attempt budget used")


def test_delivered_is_optional_and_running_needs_nothing() -> None:
    a = _one(S.awaiting_feedback)
    # a factory product picks its next iteration on the "Next iteration" card (roadmap J2)
    assert (a.kind, a.panel, a.blocking) == ("feedback", "next-iteration", False)
    assert "next iteration" in a.title
    team = _product()
    team.target = ProductTarget.repo
    assert _one(S.awaiting_feedback, product=team).panel == "feedback"
    assessed = _one(S.awaiting_feedback, kind="assess")
    assert assessed.panel == "work-ledger"
    for s in (S.queued, S.running, S.cancelled, S.failed):
        assert pending_actions(_product(), _change(s), NOW) == []


def test_archived_products_and_unknown_creators() -> None:
    assert pending_actions(_product(archived=True), _change(S.held), NOW) == []
    assert _one(S.held, product=_product(by=None)).owner == "an admin"


async def test_attention_over_http(make_factory) -> None:
    f = make_factory()
    app, ctx, c = await _client(f)
    async with c:
        product = f.manager.create_product(PRODUCT)
        change = f.manager.start_change(product)
        await wait_run(f, change.id)
        done = f.store.get_change(change.id)
        assert done is not None and done.status == S.awaiting_feedback
        # delivered: nothing blocks, so the header count is empty unless optional actions are asked for
        assert (await c.get("/api/attention")).json() == []
        items = (await c.get("/api/attention", params={"include_optional": True})).json()
        assert [(i["kind"], i["product_id"], i["blocking"]) for i in items] == [("feedback", product.id, False)]
        assert (await c.get("/api/attention", params={"product_id": "nope", "include_optional": True})).json() == []

        held = f.store.get_change(change.id)
        assert held is not None
        held.status, held.summary = S.held, "held at test: budget"
        f.store.save_change(held)
        items = (await c.get("/api/attention")).json()
        assert [(i["kind"], i["change_id"], i["title"]) for i in items] == [
            ("resume", change.id, "Read the evidence, then resume or change the request")
        ]
        detail = (await c.get(f"/api/changes/{change.id}")).json()
        assert [a["kind"] for a in detail["actions"]] == ["resume"]
        events = (await c.get(f"/api/changes/{change.id}/events")).json()
        assert all(e["level"] in ("error", "warn", "info", "debug") for e in events)
    await ctx.__aexit__(None, None, None)
