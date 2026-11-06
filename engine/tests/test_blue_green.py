"""Deploy strategy per product (ADR-0046 slice 4): in place, or blue/green in production's release.

Blue/green stages the tested image on the colour users don't reach, tests it at its preview address,
then switches the Service; the colour that served before keeps running, so a rollback to the last
healthy release's revision switches users back without restarting it. Pure decisions first, then
the pipeline on the `helm` target (FakeHelm keeps each revision's values and each colour's pods).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import PRODUCT, wait_run
from test_api import _client

from agent_factory import blue_green, rollback, validation
from agent_factory.attention import pending_actions
from agent_factory.engine.pipeline import FactoryError
from agent_factory.executor import FakeExecutor, FakeHelm
from agent_factory.models import ChangeStatus, EventKind, Product, Release, SchemaApproval
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


def test_in_place_is_refused_once_production_keeps_a_second_colour():
    """L-3 (2026-11-04): a fallback reason never removes a colour production keeps."""
    why = "the product's chart predates blue/green: it renders no colours"
    assert blue_green.in_place_refused(None, why) is None, "nothing installed"
    assert blue_green.in_place_refused(blue_green.Layout("in-place", "blue", {"blue": "a:1"}), why) is None
    assert blue_green.in_place_refused(blue_green.Layout("blue-green", "blue", {"blue": "a:1"}), why) is None
    kept = blue_green.in_place_refused(blue_green.Layout("blue-green", "green", {"blue": "a:1", "green": "a:2"}), why)
    assert kept and "users on green, blue kept for the switch back" in kept and "the colour users are on" in kept
    assert why in kept and "In place under Releases" in kept and "Nothing was changed" in kept
    back = blue_green.in_place_refused(blue_green.Layout("blue-green", "blue", {"blue": "a:3", "green": "a:2"}), why)
    assert back and "green, the rollback target" in back
    alone = blue_green.in_place_refused(blue_green.Layout("blue-green", "green", {"green": "a:2"}), why)
    assert alone and "users on green)" in alone, "green alone serving is removed by in place too"


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
    assert not [c for c in ex.calls if "E2E_BASE_URL=" in c and ("-next." in c or PROD in c)], "no e2e on users' data"
    reads = [c for c in ex.calls if f'-o /dev/null ${{HTTP_PROXY:+--proxy "$HTTP_PROXY"}} {PREVIEW}/' in c]
    assert [c.rsplit(PREVIEW, 1)[1] for c in reads] == ["/healthz", "/readyz"], "the new colour, read-only"
    assert ex.calls.index(reads[-1]) < ex.calls.index(switched), "checked before the switch"
    additive = "schema is additive against iteration 1: no schema change"
    assert any(m.startswith(additive) for m in _messages(f, second.id))

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
    ex.fail_on.append(f"{PREVIEW}/readyz")  # green doesn't get ready
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


async def _two_colours(f, ex: FakeExecutor) -> tuple[Product, Release]:
    """A blue/green product whose second iteration switched users to green, blue kept."""
    product, _ = await _blue_green_product(f)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback, _messages(f, second.id)[-5:]
    product = f.store.get_product(product.id)
    values = ex.helm.releases["bookmarks-service"][-1]["values"]
    assert product.release.color == "green" and set(FakeHelm.images(values)) == {"blue", "green"}
    return product, product.release


def _assert_kept(f, ex: FakeExecutor, change_id: str, healthy: Release, upgrades: int, why: str) -> None:
    held = f.store.get_change(change_id)
    assert held.status == ChangeStatus.held and held.hold_class == "decision", _messages(f, change_id)[-5:]
    assert held.required_action is not None and held.required_action.kind == "colors_in_place"
    assert "users on green, blue kept for the switch back" in held.summary and why in held.summary
    assert len(_prod_upgrades(ex)) == upgrades, "production untouched"
    values = ex.helm.releases["bookmarks-service"][-1]["values"]
    assert FakeHelm.images(values) == {"blue": FakeHelm.images(values)["blue"], "green": healthy.image}
    assert f.store.get_product(held.product_id).release == healthy
    assert not [c for c in ex.calls if " rollback " in c], "nothing to roll back: nothing was touched"
    assert not any(", promoting in place: " in m for m in _messages(f, change_id)), "never says it did"
    [action] = pending_actions(f.store.get_product(held.product_id), held, NOW)
    assert action.kind == "approve_schema" and action.title == "Fix the cause and resume, or release in place"


async def test_a_fallback_never_removes_the_kept_colour(make_factory, cfg):
    """L-3 / UX-20 (2026-11-03): the chart seemed to render no colours, and promote fell back to in
    place over production's two colours, removing the rollback target. Now it holds instead."""
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, healthy = await _two_colours(f, ex)
    upgrades = len(_prod_upgrades(ex))
    ex.fail_on.append("blueGreen.blue.repository=probe/app")  # the render check misses the colours
    third = f.manager.feedback(product.id, "Export notes as CSV.")
    assert await wait_run(f, third.id) == ChangeStatus.held
    _assert_kept(f, ex, third.id, healthy, upgrades, "predates blue/green")
    ex.fail_on.append("blueGreen.blue.repository=probe/app")  # the cause is still there
    f.manager.resume(third.id)  # decided 2026-11-04: Resume runs the blue/green checks again
    assert await wait_run(f, third.id) == ChangeStatus.held
    _assert_kept(f, ex, third.id, healthy, upgrades, "predates blue/green")

    product = f.store.get_product(product.id)
    product.deploy_strategy = "in-place"  # a person releases it in place, knowing blue goes
    f.store.save_product(product)
    f.manager.resume(third.id)
    assert await wait_run(f, third.id) == ChangeStatus.awaiting_feedback, _messages(f, third.id)[-5:]
    assert "strategy=blue-green" not in _prod_upgrades(ex)[-1]
    assert f.store.get_product(product.id).release.change_id == third.id


async def test_a_change_validated_in_place_never_removes_the_kept_colour(make_factory, cfg, monkeypatch):
    """L-3: a lane or target without a temporary environment deploys over production at `deploy`;
    on two colours that holds before production is touched."""
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, healthy = await _two_colours(f, ex)
    upgrades = len(_prod_upgrades(ex))
    monkeypatch.setattr(HelmProvider, "validates_separately", False)
    third = f.manager.feedback(product.id, "Export notes as CSV.")
    assert await wait_run(f, third.id) == ChangeStatus.held
    assert f.store.get_change(third.id).current_station == "deploy"
    _assert_kept(f, ex, third.id, healthy, upgrades, "validated in place")


@pytest.mark.parametrize(
    "error",
    [
        'Error: Kubernetes cluster unreachable: context "kind-ingress" not found',  # review of #150
        'Error: namespaces "app-bookmarks-service" not found',
        "Error: UPGRADE FAILED: chart not found",
        "Error: could not reach the API server",
    ],
)
async def test_production_that_cant_be_read_is_never_released_in_place(make_factory, cfg, monkeypatch, error: str):
    """Only Helm's own "release: not found" means nothing is installed; any other failure, even one
    that says "not found", holds without touching production."""
    _helm_target(cfg)
    needle = "get values bookmarks-service"
    ex = FakeExecutor(fail_with={needle: error})
    f = make_factory(ex)
    product, _ = await _two_colours(f, ex)
    upgrades = len(_prod_upgrades(ex))
    monkeypatch.setattr(HelmProvider, "validates_separately", False)
    ex.fail_on.extend([needle] * 10)
    f.manager.cfg.budgets.infra_backoff_seconds = [0.0]
    third = f.manager.feedback(product.id, "Export notes as CSV.")
    assert await wait_run(f, third.id) == ChangeStatus.held
    held = f.store.get_change(third.id)
    assert held.hold_class == "infra" and "can't tell whether production runs two colours" in held.summary
    assert error in held.summary, "the hold names what Helm said"
    assert len(_prod_upgrades(ex)) == upgrades, "no production upgrade"


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


# ---------------------------------------- schema guard (2026-11-01, tightened 2026-11-02) --
from agent_factory import schema_compat as sc  # noqa: E402


def _verdict(changed: dict[str, tuple[str | None, str | None]]) -> sc.Verdict:
    return sc.compare(changed)


def _ts(sql: str) -> str:
    """SQL in a template literal, as the Next.js pack writes it (a backtick escaped, as JS needs)."""
    body = sql.replace("`", "\\`")
    return f"export const SCHEMA = `\n{body}\n`;\n"


USERS = 'create table if not exists "users" (id text primary key, email text);'


def test_new_tables_and_indexes_are_additive():
    v = _verdict(
        {
            "src/lib/schema.ts": (
                _ts(USERS),
                _ts(USERS + "\ncreate table teams (id text);\ncreate index t_i on teams(id);"),
            )
        }
    )
    assert (v.status, v.new_objects) == (sc.ADDITIVE, ["index t_i", "table teams"])
    plain = USERS.replace('"users"', "users")
    assert (
        _verdict({"src/lib/schema.ts": (_ts(plain), _ts(plain.upper().replace(" (", "  (")))}).status == sc.ADDITIVE
    ), "case and spacing of keywords don't change a definition"


@pytest.mark.parametrize(
    "after",
    [
        'create table if not exists "users" (id text primary key, email text not null);',  # reported 2026-11-02
        "create table if not exists `users` (id text primary key, email text, name text);",
        "create table if not exists [users] (id text primary key);",
        "CREATE TABLE IF NOT EXISTS users (id text primary key, email varchar(200));",
        "create table if not exists \"users\" (id text primary key, email text default 'a;b');",  # `;` in a literal
        'create table if not exists "users" (id text primary key, "e;mail" text);',  # `;` in an identifier
    ],
)
def test_a_changed_table_is_never_missed_whatever_the_quoting(after: str):
    v = _verdict({"src/lib/schema.ts": (_ts(USERS), _ts(after))})
    assert v.status == sc.NOT_ADDITIVE and v.problems == ["table users's definition changed"]


@pytest.mark.parametrize(
    ("before", "after", "problem"),
    [
        (_ts(USERS), _ts(""), "table users is no longer created (dropped or renamed)"),
        (_ts(USERS), _ts(USERS + '\nalter table "users" drop column email;'), 'alter table "users" drop column email'),
        (
            _ts(USERS + '\nalter table "users" add column a text;'),
            _ts(USERS + '\nalter table "users" drop column a;'),
            'alter table "users" drop column a',
        ),  # the reported case: both versions start the same up to the quote
        (_ts(USERS), _ts(USERS + "\n/* tidy */ ALTER TABLE users RENAME COLUMN email TO mail;"), "rename column email"),
        (_ts(USERS), _ts(USERS + "\ntruncate users;"), "truncate users"),
        (_ts(USERS), _ts(USERS + "\ncomment on table users is 'x';"), "comment on table users"),
    ],
)
def test_alter_drop_rename_truncate_are_not_additive(before: str, after: str, problem: str):
    v = _verdict({"src/lib/schema.ts": (before, after)})
    assert v.status == sc.NOT_ADDITIVE and problem in " ".join(v.problems)


def test_python_strings_models_and_migrations():
    old = "SQL = 'create table notes (id integer primary key)'\n"
    assert _verdict({"app/db.py": (old, old.replace(")'", ", body text)'"))}).status == sc.NOT_ADDITIVE
    model = "class Note(Base):\n    __tablename__ = 'notes'\n    id = mapped_column(Integer, primary_key=True)  # pk\n"
    assert _verdict({"app/models.py": (model, model + "    body = mapped_column(String)\n")}).problems == [
        "table notes's definition changed"
    ]
    assert _verdict({"app/models.py": (model, model.replace("# pk", "# the key"))}).status == sc.ADDITIVE, "comments"
    teams = model + "\n\nclass Team(Base):\n    __tablename__ = 'teams'\n    id = mapped_column(Integer)\n"
    assert _verdict({"app/models.py": (model, teams)}).new_objects == ["table teams"]
    mig = "from alembic import op\n\ndef upgrade():\n    op.drop_column('notes', 'body')\n"
    assert _verdict({"migrations/versions/2.py": (None, mig)}).status == sc.NOT_ADDITIVE
    not_alembic = "import os.path as op\n\nPATH = op.join('a', 'b')\n"
    assert _verdict({"app/paths.py": (None, not_alembic)}).status == sc.ADDITIVE, "op is Alembic's only when imported"
    raw = 'def upgrade():\n    op.execute("ALTER TABLE \\"notes\\" ADD COLUMN x text")\n'
    assert 'alter table "notes" add column x text' in " ".join(_verdict({"m/3.py": (None, raw)}).problems)


@pytest.mark.parametrize(
    ("path", "before", "after", "why"),
    [
        # not readable exactly: never additive
        ("prisma/schema.prisma", "model U {\n id Int\n}", "model U {\n id Int\n name String\n}", ".prisma file"),
        (
            "src/app/page.tsx",
            "const s = `create table a (id text)`;",
            "const s = `create table a (id int)`;",
            ".tsx file",
        ),
        (
            "src/db.ts",
            'import { pgTable, text } from "drizzle-orm/pg-core";\nexport const u = pgTable("u", { id: text("id") });',
            'import { pgTable, text } from "drizzle-orm/pg-core";\n'
            'export const u = pgTable("u", { id: text("id"), n: text("n") });',
            "schema library",
        ),
        (
            "src/a.ts",
            "q(`create table ${T} (id text)`)",
            "q(`create table ${T} (id text, b text)`)",
            "built from expressions",
        ),
        ("src/a.ts", 'q("create table x (" + cols + ")")', 'q("create table x (" + more + ")")', "unbalanced"),
        (
            "src/a.ts",
            "const s = 'please create table rows';",
            "const s = 'please create table columns';",
            "isn't a SQL",
        ),
        ("src/a.ts", "const s = `create table a (id text)`;", "const s = `create table a (id text)", "unterminated"),
        ("app/m.py", "from sqlmodel import SQLModel\n", "from sqlmodel import SQLModel, Field\n", "sqlmodel"),
        ("app/m.py", "t = 1\n", "x = Column(Integer)\n", "outside a model"),
        ("app/m.py", "sql = f'create table {name} (id int)'\n", "sql = f'create table {name} (id text)'\n", "f-string"),
        ("app/m.py", "def f(:\n  'alter table x'\n", "def f(:\n  'alter table y'\n", "not valid Python"),
        (
            "db/init.sql",
            "create table a (id text);",
            "create table a (id text); create view v as select 1 /* x",
            "can't be read",
        ),
        ("deploy/init.yaml", "sql: create table a (id text)", "sql: create table a (id int)", ".yaml file"),
    ],
)
def test_what_it_cannot_read_is_unsure_never_additive(path: str, before: str, after: str, why: str):
    v = _verdict({path: (before, after)})
    assert v.status == sc.UNSURE and why in " ".join(v.unsure), v


def test_a_form_it_cannot_read_is_unsure_even_when_both_versions_look_alike():
    """The reported gap: an unrecognised definition must not pass as unchanged."""
    old = 'import { pgTable } from "drizzle-orm/pg-core";\nexport const t = pgTable("t", {});\n'
    assert _verdict({"src/schema.ts": (old, old + "// renamed a column\n")}).status == sc.UNSURE


def test_adding_a_schema_library_is_unsure():
    old = json.dumps({"dependencies": {"next": "15"}})
    new = json.dumps({"dependencies": {"next": "15", "drizzle-orm": "0.40"}})
    assert "adds drizzle-orm" in " ".join(_verdict({"package.json": (old, new)}).unsure)
    assert "adds alembic" in " ".join(
        _verdict({"pyproject.toml": ('dependencies = ["fastapi"]', 'dependencies = ["fastapi", "alembic>=1"]')}).unsure
    )


def test_tests_docs_and_ordinary_queries_are_not_schema():
    q = "const rows = db.query(`select * from users where email = $1`);"
    v = _verdict({
        "e2e/users.spec.ts": (None, "await db.exec(`drop table users`)"),
        "tests/test_db.py": (None, "op.drop_table('x')"),
        "README.md": ("create table users", "drop table users"),
        "src/q.ts": (q, q.replace("*", "id")),
        "src/msg.ts": ("const t = 'Delete this item?';", "const t = 'Delete it? It\\'s gone';"),
    })  # fmt: skip
    assert (v.status, v.files) == (sc.ADDITIVE, [])


def test_the_pack_templates_read_cleanly():
    root = Path(__file__).parents[2] / "packs"
    files = {
        str(f.relative_to(root)): f.read_text()
        for f in [*root.glob("nextjs-web/template/src/**/*.ts"), *root.glob("fastapi-service/template/app/*.py")]
    }
    parsed = [sc.parse_file(path, text) for path, text in files.items()]
    assert [u for p in parsed for u in p.unsure] == [], "the templates' own schema is read exactly"
    tables = {name for p in parsed for (kind, name) in p.objects if kind == "table"}
    assert {"app_info", "app_users", "app_sessions"} <= tables


def test_a_large_file_is_read_whole():
    pad = "// padding\n" * 4000  # far past the executor's 6000-character output tail
    assert (
        _verdict({"src/lib/schema.ts": (_ts(USERS) + pad, _ts(USERS.replace("email text", "email int")) + pad)}).status
        == sc.NOT_ADDITIVE
    )


# --------------------------------------------------------------- in the pipeline --
def _commit_to_main(f, product: Product, path: str, content: str) -> None:
    """What the next change's developer would have written: on main, so its worktree starts from it."""
    import os
    import subprocess

    repo = f.manager.ws.product_dir(product.slug)
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "PATH": os.environ["PATH"], "HOME": str(repo)}  # fmt: skip
    for args in (["add", "-A"], ["commit", "-q", "-m", f"schema: {path}"]):
        subprocess.run(["git", "-c", f"safe.directory={repo}", *args], cwd=repo, env=env, check=True)


MODEL = (
    "from app.db import Base\nfrom sqlalchemy import Integer, String\nfrom sqlalchemy.orm import mapped_column\n\n\n"
    "class Note(Base):\n    __tablename__ = 'notes'\n    id = mapped_column(Integer, primary_key=True)\n"
)


async def _second_iteration(f, product: Product, path: str, content: str) -> str:
    _commit_to_main(f, product, path, content)
    change = f.manager.feedback(product.id, "Keep notes.")
    await wait_run(f, change.id)
    return change.id


async def test_a_new_table_is_released_blue_green(make_factory, cfg):
    _helm_target(cfg)
    f = make_factory(FakeExecutor())
    product, _ = await _blue_green_product(f)
    second = await _second_iteration(f, product, "app/models.py", MODEL)
    assert f.store.get_change(second).status == ChangeStatus.awaiting_feedback, _messages(f, second)[-5:]
    assert "schema is additive against iteration 1: new table notes" in _messages(f, second)
    assert f.store.get_product(product.id).release.color == "green"


async def test_a_changed_table_holds_until_the_product_is_set_in_place(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f)
    await _second_iteration(f, product, "app/models.py", MODEL)
    healthy = f.store.get_product(product.id).release
    upgrades = len(_prod_upgrades(ex))
    third = await _second_iteration(f, product, "app/models.py", MODEL + "    body = mapped_column(String)\n")

    held = f.store.get_change(third)
    assert held.status == ChangeStatus.held and held.hold_class == "decision"
    assert "table notes's definition changed" in held.summary and held.required_action.kind == "schema_in_place"
    assert len(_prod_upgrades(ex)) == upgrades, "production untouched: users stay on the healthy colour"
    with pytest.raises(Exception, match="not yet"):
        f.manager.resume(third)  # nothing changed: resuming would only hold again
    with pytest.raises(Exception, match="isn't waiting for a schema approval"):
        f.manager.approve_schema(third, "I am sure it is fine", "priya")  # not additive: no approval, only in place

    product = f.store.get_product(product.id)
    product.deploy_strategy = "in-place"
    f.store.save_product(product)
    f.manager.resume(third)
    assert await wait_run(f, third) == ChangeStatus.awaiting_feedback, _messages(f, third)[-5:]
    now = f.store.get_product(product.id).release
    assert now.change_id == third and now.color is None and now.revision > healthy.revision


async def test_what_it_cannot_read_holds_for_an_approval_of_these_exact_commits(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f)
    drizzle = 'import { pgTable } from "drizzle-orm/pg-core";\nexport const notes = pgTable("notes", {});\n'
    second = await _second_iteration(f, product, "src/schema.ts", drizzle)

    held = f.store.get_change(second)
    need = held.required_action
    assert held.status == ChangeStatus.held and need.kind == "approve_schema"
    assert "can't tell whether this change is compatible" in held.summary and "drizzle-orm" in " ".join(need.unsure)
    assert [a.kind for a in pending_actions(product, held, NOW)] == ["approve_schema"]
    with pytest.raises(Exception, match="not yet"):
        f.manager.resume(second)

    f.manager.approve_schema(second, "adds a table nothing old reads", "priya")
    f.manager.resume(second)
    assert await wait_run(f, second) == ChangeStatus.awaiting_feedback, _messages(f, second)[-5:]
    approved = next(m for m in _messages(f, second) if m.startswith("schema compatibility approved by priya"))
    assert need.commits[0] in approved and need.commits[1] in approved and "adds a table nothing old reads" in approved
    assert f.store.get_product(product.id).release.color == "green"


async def test_an_approval_does_not_carry_over_to_other_commits(make_factory, cfg):
    _helm_target(cfg)
    f = make_factory(FakeExecutor())
    product, _ = await _blue_green_product(f)
    drizzle = 'import { pgTable } from "drizzle-orm/pg-core";\nexport const notes = pgTable("notes", {});\n'
    second = await _second_iteration(f, product, "src/schema.ts", drizzle)
    change = f.store.get_change(second)
    change.schema_approval = SchemaApproval(
        by="priya", at=NOW, from_commit="0000000", to_commit=change.required_action.commits[1], note="an older pair"
    )
    f.store.save_change(change)
    with pytest.raises(Exception, match="not yet"):
        f.manager.resume(second)


async def test_a_person_approves_through_the_api(make_factory, cfg):
    _helm_target(cfg)
    f = make_factory(FakeExecutor())
    product, _ = await _blue_green_product(f)
    drizzle = 'import { pgTable } from "drizzle-orm/pg-core";\nexport const notes = pgTable("notes", {});\n'
    second = await _second_iteration(f, product, "src/schema.ts", drizzle)
    app, ctx, c = await _client(f)
    async with c:
        url = f"/api/changes/{second}/schema-approval"
        assert (await c.post(url, json={"note": "ok"})).status_code == 422, "a reason is required"
        r = await c.post(url, json={"note": "adds a table nothing old reads"})
        assert r.status_code == 200 and r.json()["schema_approval"]["note"] == "adds a table nothing old reads"
    await ctx.__aexit__(None, None, None)


async def test_a_cause_fixed_elsewhere_resumes_blue_green(make_factory, cfg):
    """Decided by Saravana 2026-11-04 (review of #151): after a person fixes the cause outside the
    factory, Resume runs the blue/green checks again and, when they pass, carries on blue/green: the
    tested image goes to the idle colour, users switch, the colour they left is kept. No In place."""
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, healthy = await _two_colours(f, ex)
    upgrades = len(_prod_upgrades(ex))
    ex.fail_on.append("blueGreen.blue.repository=probe/app")  # the render check misses the colours
    third = f.manager.feedback(product.id, "Export notes as CSV.")
    assert await wait_run(f, third.id) == ChangeStatus.held
    _assert_kept(f, ex, third.id, healthy, upgrades, "predates blue/green")

    f.manager.resume(third.id)  # fixed elsewhere: the render now shows both colours
    assert await wait_run(f, third.id) == ChangeStatus.awaiting_feedback, _messages(f, third.id)[-5:]
    assert "strategy=blue-green" in _prod_upgrades(ex)[-1]
    product = f.store.get_product(product.id)
    assert product.deploy_strategy != "in-place"
    assert product.release.change_id == third.id and product.release.color == "blue"
    values = ex.helm.releases["bookmarks-service"][-1]["values"]
    assert FakeHelm.images(values)["green"] == healthy.image, "the colour users left is kept"


async def test_a_blue_green_product_on_a_lane_without_promote_holds_before_production(make_factory, cfg, monkeypatch):
    """Plant watering iteration 2 (2026-11-05): blue/green chosen, but the change ran on Product change
    v2 (no promote), went straight over production in place, failed its live test and was rolled back.
    Decided by Saravana: hold before deploy instead, production untouched. In place, by a person's
    choice, still releases in place; a product that chose in place is unaffected."""
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f, strategy="in-place")  # iteration 1, in place
    upgrades = len(_prod_upgrades(ex))
    product.deploy_strategy = "blue-green"  # then the person chose blue/green
    f.store.save_product(product)
    monkeypatch.setattr(validation, "in_place_reason", lambda *_: validation.NO_PROMOTE)  # an old lane
    second = f.manager.feedback(product.id, "Export notes as CSV.")
    assert await wait_run(f, second.id) == ChangeStatus.held, _messages(f, second.id)[-6:]
    held = f.store.get_change(second.id)
    assert held.current_station == "deploy", "\n".join(
        m[:160] for m in _messages(f, second.id) if "deploy" in m or "blue" in m or "in place" in m or "held" in m
    )
    assert held.current_station == "deploy" and len(_prod_upgrades(ex)) == upgrades, "production untouched"
    stop = next(e for e in f.store.list_events(second.id) if e.data.get("rule") == "blue_green_lane_without_promote")
    assert "releases blue/green (product's choice), but its lane (" in stop.message
    assert "Nothing was changed in production" in stop.message
    assert held.required_action is not None and held.required_action.kind == "lane_without_promote"
    assert "close this change, restore the" in held.required_action.text, "a resume keeps the old lane version"
    with pytest.raises(FactoryError, match="not yet"):
        f.manager.resume(second.id)  # still blue/green: it would only hold again

    product = f.store.get_product(product.id)
    product.deploy_strategy = "in-place"  # a person's choice: release this one in place
    f.store.save_product(product)
    f.manager.resume(second.id)
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback, _messages(f, second.id)[-5:]
    assert len(_prod_upgrades(ex)) == upgrades + 1


async def test_an_in_place_product_on_a_lane_without_promote_still_goes_in_place(make_factory, cfg, monkeypatch):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product, _ = await _blue_green_product(f, strategy="in-place")
    monkeypatch.setattr(validation, "in_place_reason", lambda *_: validation.NO_PROMOTE)
    second = f.manager.feedback(product.id, "Export notes as CSV.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback
    assert not any(e.data.get("rule") == "blue_green_lane_without_promote" for e in f.store.list_events(second.id))
