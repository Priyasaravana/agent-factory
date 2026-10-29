"""Suggested next features after a delivery (roadmap J2, slice 2): the architect and, with route
screenshots, the designer propose; the engine vets and folds them into the work ledger."""

from __future__ import annotations

from pathlib import Path

from agent_factory import ledger, suggest
from agent_factory.models import Change, ChangeKind, ChangeStatus, Product, ProductTarget

GOOD = {
    "title": "Search kudos",
    "detail": "People find a kudos by words in its message.",
    "value": "medium",
    "size": "M",
    "requirements": ["Anyone can search kudos by message"],
}


def _s(title: str, value: str = "medium", **more) -> dict:  # type: ignore[no-untyped-def]
    return {**GOOD, "title": title, "value": value, **more}


def test_each_suggestion_is_vetted_on_its_own_and_the_best_five_are_kept():
    kept, dropped = suggest.vet(
        {
            "architect": [
                _s("Weekly digest", "low"),
                _s("Search kudos", "high"),
                {"title": "x", "detail": "short", "value": "urgent", "size": "XL", "requirements": []},
                _s("Reactions", "medium"),
                _s("Export", "low"),
                _s("Badges", "low"),  # the architect's 5th valid one: over its share of 4
            ],
            "designer": [_s("search KUDOS", "high"), _s("One-row phone header", "high"), _s("Empty states", "medium")],
        }
    )
    assert [(s["title"], s["value"], s["by"]) for s in kept] == [
        ("Search kudos", "high", "architect"),
        ("One-row phone header", "high", "designer"),
        ("Reactions", "medium", "architect"),
        ("Empty states", "medium", "designer"),
        ("Weekly digest", "low", "architect"),
    ]
    assert dropped == [
        "architect: suggestion 1: needs a title",
        "architect: more than 4 suggestions, the rest were dropped",
        "designer: 'search KUDOS' was already suggested",
        "architect: 'Export' ranked below the top 5",
    ]
    assert suggest.vet(
        {
            "architect": [_s(f"Feature {n}", "low") for n in range(4)],
            "designer": [_s("Kudos of the week"), _s("Profile pages")],
        }
    )[1] == ["architect: 'Feature 3' ranked below the top 5"]
    assert suggest.vet({"architect": "not a list"}) == ([], [])  # type: ignore[dict-item]


def test_only_a_delivered_factory_product_gets_suggestions():
    delivered = Change.model_construct(status=ChangeStatus.awaiting_feedback, kind=ChangeKind.feature)
    app = Product.model_construct(target=ProductTarget.new, eval_run_id=None)
    assert suggest.wanted(delivered, app)
    assert not suggest.wanted(delivered.model_copy(update={"status": ChangeStatus.held}), app)
    assert not suggest.wanted(delivered.model_copy(update={"kind": ChangeKind.assess}), app)
    assert not suggest.wanted(delivered, app.model_copy(update={"target": ProductTarget.repo})), "team repos: J3"
    assert not suggest.wanted(delivered, app.model_copy(update={"eval_run_id": "e1"})), "evaluation products"


def test_the_prompts_carry_earlier_decisions_and_the_screenshots(tmp_path: Path):
    from datetime import UTC, datetime

    now = datetime(2026, 10, 29, tzinfo=UTC)
    items: list = []
    ledger.fold_features(
        "p", items, [{**_s("Search kudos"), "by": "architect"}, {**_s("Reactions"), "by": "architect"}],
        change_id="c1", iteration=1, now=now,
    )  # fmt: skip
    ledger.decide(items, [items[1].id], "wont_do", reason="the team doesn't want emoji noise", by="admin", now=now)
    product = Product.model_construct(title="Kudos board", requirements="A board where people thank each other.")
    change = Change.model_construct(iteration=2, change_request="Make controls 44 px on phones")
    text = suggest.architect_prompt(product, change, items)
    assert "- [open] Search kudos" in text
    assert "- [rejected] Reactions (rejected: the team doesn't want emoji noise)" in text
    assert "Make controls 44 px on phones" in text and "at most 4 suggestions" in text

    for run in ("preview", "live"):
        (tmp_path / "screenshots" / run).mkdir(parents=True)
        (tmp_path / "screenshots" / run / "home-phone-dark.png").write_bytes(b"png")
    shots = suggest.screenshots(tmp_path)
    assert shots == [str(tmp_path / "screenshots" / "live" / "home-phone-dark.png")], "the deployed app first"
    assert shots[0] in suggest.designer_prompt(product, change, items, shots)
    assert suggest.screenshots(tmp_path / "none") == []


# ------------------------------------------------------------ end to end --
async def test_a_delivered_product_gets_next_features_on_its_work_ledger(make_factory):
    from conftest import PRODUCT, wait_run

    from agent_factory.agents import FakeAgentRunner

    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    asked = [c for c in agents.calls if c.station == "suggest"]
    assert [c.role for c in asked] == ["architect"], "a FastAPI product has no screenshots: no designer"
    assert asked[0].observe_only and asked[0].tools == ["Read", "Glob", "Grep"]
    features = [i for i in f.store.list_work_items(product.id) if i.category == ledger.FEATURE]
    assert sorted((i.title, i.severity, i.effort, i.status) for i in features) == [
        ("Export bookmarks", "low", "S", "open"),
        ("Search bookmarks", "high", "M", "open"),
    ]
    event = next(e for e in f.store.list_events(change.id) if e.message.startswith("next features:"))
    assert event.message == "next features: 2 suggested (architect 2), 2 new on the work ledger"
    assert event.station == "suggest" and event.data["suggestions"]["dropped"] == []
    groups = ledger.next_iteration(f.store.list_work_items(product.id))
    assert [i.title for i in groups["features"]] == ["Search bookmarks", "Export bookmarks"], "best value first"

    # the next iteration builds one; the delivery closes it, and suggesting it again doesn't reopen it
    search = next(i for i in features if i.title == "Search bookmarks")
    second = f.manager.feedback(product.id, "Build the suggested search", ChangeKind.feature, [search.id])
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback
    after = {i.title: i for i in f.store.list_work_items(product.id) if i.category == ledger.FEATURE}
    assert (after["Search bookmarks"].status, after["Search bookmarks"].resolved_by) == ("fixed", second.id)
    assert after["Export bookmarks"].status == "open" and after["Export bookmarks"].seen_in == [1, 2]


async def test_a_web_product_also_gets_the_designers_improvements(make_factory):
    from conftest import wait_run
    from test_ui_review import SHOTS, WEB, ShootingExecutor

    from agent_factory.agents import FakeAgentRunner

    agents = FakeAgentRunner()
    f = make_factory(ShootingExecutor(), agents)
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback

    designer = next(c for c in agents.calls if c.station == "suggest" and c.role == "designer")
    assert all(f"screenshots/live/{name}" in designer.prompt for name in SHOTS)
    titles = {i.title for i in f.store.list_work_items(product.id) if i.category == ledger.FEATURE}
    assert titles == {"Search bookmarks", "Export bookmarks", "Clearer phone header"}


async def test_bad_suggestions_never_touch_the_delivery(make_factory):
    from conftest import PRODUCT, wait_run

    from agent_factory.agents import FakeAgentRunner

    agents = FakeAgentRunner(suggestions={"architect": [{"title": "x"}]})
    f = make_factory(agents=agents)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert not [i for i in f.store.list_work_items(product.id) if i.category == ledger.FEATURE]
    event = next(e for e in f.store.list_events(change.id) if e.message.startswith("next features:"))
    assert event.message == "next features: none suggested (1 dropped)"


# --------------------------------------------- the next iteration over the API (slice 3) --
def _cve(product_id: str, title: str, detail: str):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from agent_factory.models import WorkItem

    now = datetime(2026, 10, 29, 20, tzinfo=UTC)
    fp = ledger.image_fingerprint(title)
    return WorkItem(
        id=ledger.item_id(product_id, fp),
        product_id=product_id,
        kind="issue",
        fingerprint=fp,
        title=title,
        severity="high",
        category=ledger.IMAGE,
        detail=detail,
        created_at=now,
        updated_at=now,
    )


async def test_the_next_iteration_is_listed_and_the_engine_picks_its_kind(make_factory):
    from conftest import PRODUCT, wait_run
    from test_api import _client

    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    fixable = _cve(product.id, "CVE-2026-1111 in openssl", "fixed in 3.5.2")
    f.store.save_work_items([fixable, _cve(product.id, "CVE-2026-2222 in zlib", "no fix released yet")])

    app, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/products/{product.id}/next-iteration")).json()
        assert [i["title"] for i in view["features"]] == ["Search bookmarks", "Export bookmarks"]
        assert [i["id"] for i in view["security"]] == [fixable.id], "only a CVE with a released fix"
        assert view["quality"] and {i["category"] for i in view["quality"]} == {ledger.ROADMAP}, "deferred pillar work"
        assert (view["can_start"], view["latest_change_id"], view["latest_status"]) == (
            True,
            first.id,
            "awaiting_feedback",
        )
        assert (await c.get("/api/products/nope/next-iteration")).status_code == 404

        # security fixes alone: upkeep, picked by the engine and recorded as such
        r = await c.post(f"/api/products/{product.id}/feedback", json={"text": "Patch it", "items": [fixable.id]})
        assert r.status_code == 201, r.text
        upkeep = r.json()
        assert upkeep["kind"] == "upkeep"
        event = next(e for e in f.store.list_events(upkeep["id"]) if e.kind == "feedback")
        assert (event.data["kind"], event.data["kind_by"]) == ("upkeep", "engine")
        busy = (await c.get(f"/api/products/{product.id}/next-iteration")).json()
        assert busy["can_start"] is False and busy["security"] == [], "claimed items leave the card"
        assert await wait_run(f, upkeep["id"]) == ChangeStatus.awaiting_feedback

        # a feature with a fix: a feature carrying the fix; no items at all: a feature (free text)
        groups = (await c.get(f"/api/products/{product.id}/next-iteration")).json()
        search = groups["features"][0]
        assert groups["can_start"] is True
        r = await c.post(
            f"/api/products/{product.id}/feedback", json={"text": "Search, please", "items": [search["id"]]}
        )
        assert r.status_code == 201 and r.json()["kind"] == "feature"
        assert await wait_run(f, r.json()["id"]) == ChangeStatus.awaiting_feedback
        r = await c.post(f"/api/products/{product.id}/feedback", json={"text": "Add a dark mode"})
        assert r.status_code == 201 and r.json()["kind"] == "feature"
        event = next(e for e in f.store.list_events(r.json()["id"]) if e.kind == "feedback")
        assert event.data["kind_by"] == "engine"
        assert await wait_run(f, r.json()["id"]) == ChangeStatus.awaiting_feedback

        # a kind a person gives is kept, and checked as before
        r = await c.post(f"/api/products/{product.id}/feedback", json={"text": "Fix the typo", "kind": "bug"})
        assert r.status_code == 201 and r.json()["kind"] == "bug"
        event = next(e for e in f.store.list_events(r.json()["id"]) if e.kind == "feedback")
        assert event.data["kind_by"] == "person"
        assert await wait_run(f, r.json()["id"]) == ChangeStatus.awaiting_feedback
        r = await c.post(f"/api/products/{product.id}/feedback", json={"text": "Assess it", "kind": "assess"})
        assert r.status_code == 422 and "can be: feature, bug, upkeep" in r.text
    await ctx.__aexit__(None, None, None)
