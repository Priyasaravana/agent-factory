"""Deploy strategy per product (ADR-0046 slice 4): in place, or blue/green in production's release.

Blue/green stages the tested image on the colour users don't reach, tests it at its preview address,
then switches the Service; the colour that served before keeps running, so a rollback to the last
healthy release's revision switches users back without restarting it. Pure decisions first, then
the pipeline on the `helm` target (FakeHelm keeps each revision's values and each colour's pods).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from conftest import PRODUCT, wait_run
from test_api import _client

from agent_factory import blue_green, rollback
from agent_factory.executor import FakeExecutor, FakeHelm
from agent_factory.models import ChangeStatus, EventKind, Product, Release
from agent_factory.providers import HelmProvider, LocalProvider

NOW = datetime(2026, 11, 1, tzinfo=UTC)
PROD = "http://bookmarks-service.localtest.me:8180"
PREVIEW = "http://bookmarks-service-next.localtest.me:8180"


def _healthy(image: str, color: str | None = "blue", iteration: int = 1) -> Release:
    return Release(change_id="c1", iteration=iteration, image=image, environment="local-ingress", color=color, at=NOW)


# ---------------------------------------------------------------- pure --
def test_the_product_chooses_else_its_pack_else_in_place():
    assert blue_green.chosen("blue-green", "in-place") == ("blue-green", "product")
    assert blue_green.chosen(None, "blue-green") == ("blue-green", "pack")
    assert blue_green.chosen(None, None) == ("in-place", "factory")


def test_layout_reads_what_production_runs_from_its_helm_values():
    in_place = json.dumps({"image": {"repository": "app", "tag": "1"}, "service": {"type": "ClusterIP"}})
    assert blue_green.layout(in_place) == blue_green.Layout("in-place", "blue", {"blue": "app:1"})
    bg = {"strategy": "blue-green", "blueGreen": {"live": "green", "blue": {"repository": "app", "tag": "1"},
                                                   "green": {"repository": "app", "tag": "2"}}}  # fmt: skip
    found = blue_green.layout(json.dumps(bg))
    assert found is not None and found.live == "green" and found.live_image == "app:2"
    assert rollback.values_image(json.dumps(bg)) == "app:2", "a rollback checks the image users reach"
    bg["blueGreen"]["live"] = "purple"
    assert blue_green.layout(json.dumps(bg)) is None, "an unknown colour is never guessed at"
    assert blue_green.layout("Error: release: not found") is None


def test_the_plan_stages_the_idle_colour_and_keeps_the_live_one():
    first, problems = blue_green.plan(None, "app:2", None)
    assert first == blue_green.Plan("blue", "blue", {"blue": "app:2"}, first=True) and not problems

    serving = blue_green.Layout("blue-green", "blue", {"blue": "app:1", "green": "app:0"})
    plan, _ = blue_green.plan(serving, "app:2", _healthy("app:1"))
    assert plan == blue_green.Plan("blue", "green", {"blue": "app:1", "green": "app:2"}), "the older green is replaced"

    moving = blue_green.Layout("in-place", "blue", {"blue": "app:1"})
    plan, _ = blue_green.plan(moving, "app:2", _healthy("app:1", color=None))
    assert plan is not None and (plan.live, plan.idle) == ("blue", "green"), "in place becomes blue, unrestarted"


def test_the_plan_refuses_a_production_it_did_not_record():
    serving = blue_green.Layout("blue-green", "green", {"blue": "app:1", "green": "app:9"})
    plan, problems = blue_green.plan(serving, "app:2", _healthy("app:1"))
    assert plan is None and problems == ["production serves app:9, not the recorded healthy app:1"]
    plan, problems = blue_green.plan(None, "app:2", _healthy("app:1"))
    assert plan is None and "no release" in problems[0]


def test_helm_values_set_both_colours_and_leave_an_absent_one_empty():
    out = blue_green.sets("blue", {"blue": "reg:5001/app:1"}, "a-next.example.com")
    assert "blueGreen.live=blue" in out and "blueGreen.blue.repository=reg:5001/app" in out
    assert "blueGreen.blue.tag=1" in out and "blueGreen.green.repository=" in out


def test_why_a_blue_green_product_is_promoted_in_place():
    helm = HelmProvider("kind-ingress", {"ingress_domain": "localtest.me"})
    local = LocalProvider.__new__(LocalProvider)
    assert blue_green.in_place_reason(helm, True, True) is None
    assert "can't give the idle colour its own address" in (blue_green.in_place_reason(local, True, True) or "")
    assert "validated in place" in (blue_green.in_place_reason(helm, False, True) or "")
    assert "predates blue/green" in (blue_green.in_place_reason(helm, True, False) or "")
    assert "Bookmarks next" in (blue_green.in_place_reason(helm, True, True, "Bookmarks next") or "")


def test_a_chart_renders_colours_only_when_it_knows_them():
    assert blue_green.renders_colors("app.kubernetes.io/color: blue\n...\napp.kubernetes.io/color: green")
    assert not blue_green.renders_colors("kind: Deployment\napp.kubernetes.io/component: api")


# ------------------------------------------------------------ pipeline --
def _helm_target(cfg):  # noqa: ANN001, ANN202
    cfg.blueprints["fastapi-service"].environment = "local-ingress"
    return cfg


def _messages(f, change_id: str) -> list[str]:
    return [e.message for e in f.store.list_events(change_id)]


def _prod_upgrades(ex: FakeExecutor) -> list[str]:
    return [c for c in ex.calls if "upgrade --install bookmarks-service " in c]


async def _blue_green_product(f, strategy: str = "blue-green") -> tuple[Product, str]:
    product = f.manager.create_product(PRODUCT)
    product.deploy_strategy = strategy  # type: ignore[assignment]
    f.store.save_product(product)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback, _messages(f, first.id)[-5:]
    return f.store.get_product(product.id), first.id


async def test_the_first_blue_green_release_goes_in_as_blue(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, first = await _blue_green_product(f)

    [promoted] = _prod_upgrades(ex)
    assert "strategy=blue-green" in promoted and "blueGreen.live=blue" in promoted
    rel = product.release
    assert rel is not None and rel.color == "blue" and rel.digest == FakeHelm.digest(rel.image)
    assert any(m.startswith("promoting ") and "first release, as blue" in m for m in _messages(f, first))
    assert any(m.endswith(", serving as blue") for m in _messages(f, first))


async def test_a_new_version_is_tested_on_the_idle_colour_then_users_switch(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f)
    healthy = product.release
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback, _messages(f, second.id)[-5:]

    staged, switched = _prod_upgrades(ex)[1:]
    assert "blueGreen.live=blue" in staged and "blueGreen.live=green" in switched, "staged with blue serving"
    assert f"blueGreen.blue.tag={healthy.image.rsplit(':', 1)[1]}" in staged, "blue keeps the healthy image"
    e2e = [c for c in ex.calls if "E2E_BASE_URL=" in c]
    assert PREVIEW in e2e[-1], "the new colour is tested at its preview address, before users reach it"
    assert ex.calls.index(e2e[-1]) < ex.calls.index(switched), "tested before the switch"

    now = f.store.get_product(product.id).release
    assert now is not None and now.change_id == second.id and now.color == "green"
    assert now.digest == f.store.get_change(second.id).validation.digest, "users get exactly what was tested"
    assert ex.helm.latest_image("bookmarks-service") == now.image
    values = ex.helm.releases["bookmarks-service"][-1]["values"]
    assert FakeHelm.images(values)["blue"] == healthy.image, "the old colour keeps running for a switch back"
    messages = _messages(f, second.id)
    assert "users switched from blue to green" in messages
    assert not [c for c in ex.calls if " rollback " in c]


async def test_a_failure_on_the_idle_colour_never_reaches_users(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f)
    healthy = product.release
    ex.fail_on.append(f"E2E_BASE_URL={PREVIEW} ")  # passes in the temporary environment, fails on green
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held

    held = f.store.get_change(second.id)
    assert "before the switch (users still on blue)" in held.summary
    assert not any("blueGreen.live=green" in c for c in _prod_upgrades(ex)), "users were never switched"
    assert f.store.get_product(product.id).release == healthy
    assert ex.helm.latest_image("bookmarks-service") == healthy.image


async def test_a_failure_after_the_switch_sends_users_back_without_a_restart(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f)
    healthy = product.release
    # in this change, production is probed from the sandbox only after the switch: it fails every time
    probe = f'-o /dev/null ${{HTTP_PROXY:+--proxy "$HTTP_PROXY"}} {PROD}/healthz'
    ex.fail_on.extend([probe] * 10)
    f.manager.cfg.budgets.infra_backoff_seconds = [0.0]
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held
    assert any("blueGreen.live=green" in c for c in _prod_upgrades(ex)), "users had been switched to green"

    [cmd] = [c for c in ex.calls if " rollback " in c]
    assert f"rollback bookmarks-service {healthy.revision} " in cmd, "back to the recorded healthy revision"
    assert ex.helm.latest_image("bookmarks-service") == healthy.image, "users are on blue again"
    pods = [c for c in ex.calls if "get pods" in c and "app.kubernetes.io/color=blue" in c]
    assert pods, "the restored colour's own pods are checked against the recorded digest"
    assert f.store.get_product(product.id).release == healthy
    assert any(m.startswith("production rolled back: rolled back to revision") for m in _messages(f, second.id))


async def test_a_production_that_isnt_the_recorded_one_is_never_switched(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f)
    product.release = product.release.model_copy(update={"image": "someone/else:9"})
    f.store.save_product(product)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held

    held = f.store.get_change(second.id)
    assert held.hold_class == "infra" and "not the recorded healthy someone/else:9" in held.summary
    assert len(_prod_upgrades(ex)) == 1, "production untouched after the first release"


async def test_a_chart_without_colours_is_promoted_in_place_and_says_why(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor(fail_on=["blueGreen.blue.repository=probe/app"])  # the render check
    f = make_factory(ex)
    product, first = await _blue_green_product(f)

    [promoted] = _prod_upgrades(ex)
    assert "strategy=blue-green" not in promoted and "image.repository=" in promoted
    assert product.release is not None and product.release.color is None
    why = next(m for m in _messages(f, first) if m.startswith("blue/green (product's choice) not possible"))
    assert "predates blue/green" in why


async def test_one_product_runs_blue_green_while_another_stays_in_place(make_factory, cfg):
    """J2 "Done when" (2026-11-01): the strategy is per product."""
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    bg, _ = await _blue_green_product(f)
    other = f.manager.create_product(PRODUCT.model_copy(update={"title": "Notes service"}))
    first = f.manager.start_change(other)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    in_place = f.store.get_product(other.id)

    assert bg.release.color == "blue" and in_place.release.color is None
    [notes] = [c for c in ex.calls if f"upgrade --install {in_place.slug} " in c]
    assert "strategy=blue-green" not in notes


async def test_the_strategy_is_set_per_product_through_the_api(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)
    app, ctx, c = await _client(f)
    async with c:
        url = f"/api/products/{product.id}/deploy-strategy"
        got = (await c.get(url)).json()
        assert (got["strategy"], got["chosen_by"], got["pack_default"]) == ("in-place", "pack", "in-place")
        assert got["options"] == ["in-place", "blue-green"]
        got = (await c.post(url, json={"strategy": "blue-green"})).json()
        assert (got["strategy"], got["chosen_by"]) == ("blue-green", "product") and "instant" in got["note"]
        assert f.store.get_product(product.id).deploy_strategy == "blue-green"
        assert (await c.post(url, json={"strategy": "canary"})).status_code == 422
        got = (await c.post(url, json={"strategy": None})).json()
        assert (got["strategy"], got["chosen_by"]) == ("in-place", "pack"), "back to the pack's default"
    await ctx.__aexit__(None, None, None)


@pytest.mark.parametrize("pack", ["nextjs-web", "fastapi-service"])
def test_both_stack_packs_offer_blue_green_and_default_to_in_place(pack: str):
    from pathlib import Path

    from agent_factory.packs import binding_of, load_pack

    b = binding_of(load_pack(Path(__file__).parents[2] / "packs" / pack))
    assert b.deploy_strategy == "in-place" and b.deploy_strategies == ("in-place", "blue-green")


def test_the_event_level_of_a_switch():
    from agent_factory.levels import level_of

    assert level_of(EventKind.decision, "users switched from blue to green", {"switch": {"ok": True}}) is not None
