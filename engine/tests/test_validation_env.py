"""Validate in a temporary environment, then promote the tested image (ADR-0046 slices 2 and 3).

The pure rules, then the pipeline in dry-run on the `helm` target (kind-ingress: one host per
release), with the executor's simulated Helm:

- a change deploys as its own release, namespace and host; live e2e and the verifier test it there;
  production is not touched until `promote`, which deploys the same image and checks its digest;
- the healthy release is recorded at promote, with production's identity, never the temporary one's;
- a failure in the temporary environment never rolls production back; a failure at promote does,
  then holds for a person;
- the temporary environment is removed whatever the outcome, on archive and by the start-up sweep,
  and a resumed change deploys a new one before acceptance instead of testing production;
- the `local` NodePort target validates in place and says so.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from conftest import PRODUCT, wait_run

from agent_factory import validation
from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.runner import AgentRequest, AgentResult
from agent_factory.executor import FakeExecutor
from agent_factory.levels import EventLevel
from agent_factory.models import Change, ChangeStatus, EventKind, Product, Validation
from agent_factory.providers import HelmProvider, LocalProvider

NOW = datetime(2026, 11, 1, tzinfo=UTC)
D1, D2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64
PROD = "http://bookmarks-service.localtest.me:8180"


def _helm_target(cfg):  # noqa: ANN001, ANN202
    cfg.blueprints["fastapi-service"].environment = "local-ingress"
    return cfg


def _v(**kw) -> Validation:
    fields = {"environment": "local-ingress", "target": "kind-ingress", "namespace": "app-a-c1",
              "helm_release": "a-c1", "image": "a:1", "digest": D1, "at": NOW, **kw}  # fmt: skip
    return Validation(**fields)


def _change(v: Validation | None) -> Change:
    return Change(
        id="c1", product_id="p1", iteration=2, status=ChangeStatus.running, created_at=NOW, updated_at=NOW,
        validation=v,
    )  # fmt: skip


# ---------------------------------------------------------------- pure --
def test_the_temporary_release_is_named_after_the_change_and_fits_helm():
    assert validation.release_name("bookmarks", "75fd61391452") == "bookmarks-c75fd61391452"
    long = validation.release_name("a-very-long-product-name-that-keeps-going-and-going", "75fd61391452")
    assert len(long) <= 53 and long.endswith("-c75fd61391452"), "the slug is shortened, never the change id"
    assert not long.removesuffix("-c75fd61391452").endswith("-"), "still a DNS label"


def test_which_changes_validate_in_place():
    helm, local = HelmProvider("kind-ingress", {"ingress_domain": "localtest.me"}), LocalProvider()
    lane = ["build", "deploy", "acceptance", "promote", "handover"]
    assert validation.in_place_reason(helm, lane) is None
    assert "can't give a change a second address" in (validation.in_place_reason(local, lane) or "")
    assert "no promote station" in (validation.in_place_reason(helm, [h for h in lane if h != "promote"]) or "")
    assert validation.in_place_reason(helm, ["implement", "handover"]) == "the lane has no deploy station"


def test_only_validating_stations_act_on_the_temporary_release_while_it_exists():
    change = _change(_v())
    for handler in ("deploy", "deploy-repair", "acceptance"):
        assert validation.slot(handler, change) == "a-c1", handler
    for handler in ("promote", "handover", "build"):
        assert validation.slot(handler, change) is None, f"{handler} acts on production"
    gone = _change(_v(removed_at=NOW))
    assert validation.slot("acceptance", gone) is None
    assert validation.needs_redeploy("acceptance", gone), "never test production instead"
    assert not validation.needs_redeploy("acceptance", change) and not validation.needs_redeploy("deploy", gone)


def test_only_an_identified_tested_image_is_promoted():
    assert validation.promote_problems(_v()) == []
    assert validation.promote_problems(None) == ["nothing was validated in a temporary environment"]
    assert validation.promote_problems(_v(image=None, digest=None)) == [
        "no tested image recorded",
        "no digest of the tested image recorded",
    ]
    assert validation.same_image(_v(), D1) == []
    assert validation.same_image(_v(), D2) == [f"production runs {D2}, not the tested {D1}"]


def test_a_temporary_environment_left_behind_is_a_warning():
    from agent_factory.levels import level_of

    left = {"validation_removed": {"ok": False, "release": "a-c1"}}
    assert level_of("decision", "temporary environment a-c1 NOT removed", left) == EventLevel.warn
    assert level_of("decision", "removed", {"validation_removed": {"ok": True}}) == EventLevel.info


# ------------------------------------------------------------ pipeline --
def _messages(f, change_id: str) -> list[str]:
    return [e.message for e in f.store.list_events(change_id)]


def _upgrades(ex: FakeExecutor, release: str) -> list[str]:
    return [c for c in ex.calls if f"upgrade --install {release} " in c]


async def _delivered(f) -> tuple[Product, Change]:
    product = f.manager.create_product(PRODUCT)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback, _messages(f, first.id)[-5:]
    return f.store.get_product(product.id), f.store.get_change(first.id)


async def test_a_change_is_validated_in_its_own_environment_then_the_tested_image_promoted(make_factory, cfg):
    _helm_target(cfg)
    ex, agents = FakeExecutor(), FakeAgentRunner()
    f = make_factory(ex, agents)
    product, change = await _delivered(f)
    temp = f"bookmarks-service-c{change.id}"

    [tested] = _upgrades(ex, temp)
    [promoted] = _upgrades(ex, "bookmarks-service")
    assert ex.calls.index(tested) < ex.calls.index(promoted), "validated before production is touched"
    assert f"--namespace app-{temp} " in tested and "--namespace app-bookmarks-service " in promoted
    assert tested.split("image.tag=")[1].split()[0] == promoted.split("image.tag=")[1].split()[0]
    e2e = [c for c in ex.calls if "E2E_BASE_URL=" in c]
    assert f"http://{temp}.localtest.me:8180" in e2e[0] and PROD in e2e[-1], "e2e in the temporary env, then production"

    got = f.store.get_change(change.id)
    v = got.validation
    assert v is not None and v.helm_release == temp and v.removed_at is not None, "removed once delivered"
    assert any(f"helm uninstall {temp} --namespace app-{temp}" in c for c in ex.calls)
    assert not any("uninstall bookmarks-service " in c for c in ex.calls), "production stays"
    rel = product.release
    assert rel is not None and rel.helm_release == "bookmarks-service" and rel.namespace == "app-bookmarks-service"
    assert rel.digest == v.digest, "production runs exactly the digest that was tested"
    assert product.app_url == PROD

    messages = _messages(f, change.id)
    order = [
        next(i for i, m in enumerate(messages) if m.startswith("validating in a temporary environment")),
        next(i for i, m in enumerate(messages) if m.startswith("promoting ")),
        next(i for i, m in enumerate(messages) if m.startswith("healthy release recorded")),
        next(i for i, m in enumerate(messages) if m.startswith(f"temporary environment {temp} removed")),
    ]
    assert order == sorted(order)
    recorded = next(e for e in f.store.list_events(change.id) if e.message.startswith("healthy release recorded"))
    assert recorded.station == "promote", "recorded only once production passed its checks"


async def test_a_failure_in_the_temporary_environment_never_touches_production(make_factory, cfg):
    _helm_target(cfg)
    ex, agents = FakeExecutor(), FakeAgentRunner()
    f = make_factory(ex, agents)
    product, _ = await _delivered(f)
    healthy = product.release
    agents.fail_once.append("verifier")
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback

    assert not [c for c in ex.calls if " rollback " in c], "production was never touched, so nothing to roll back"
    temp = f"bookmarks-service-c{second.id}"
    assert len(_upgrades(ex, temp)) == 2, "repaired and redeployed in the temporary environment"
    assert len(_upgrades(ex, "bookmarks-service")) == 2, "production: the first delivery, then one promotion"
    now = f.store.get_product(product.id).release
    assert now.change_id == second.id and now.revision == healthy.revision + 1


async def test_a_failure_at_promotion_rolls_production_back_and_holds(make_factory, cfg):
    _helm_target(cfg)
    ex, agents = FakeExecutor(), FakeAgentRunner()
    f = make_factory(ex, agents)
    product, _ = await _delivered(f)
    healthy = product.release
    ex.fail_on.append(f"E2E_BASE_URL={PROD} ")  # passes in the temporary environment, fails in production
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held

    held = f.store.get_change(second.id)
    assert "held at promote" in held.summary and "after promotion to production" in held.summary
    [cmd] = [c for c in ex.calls if " rollback " in c]
    assert f"rollback bookmarks-service {healthy.revision} " in cmd, "back to the recorded healthy revision"
    assert "Production was rolled back to iteration 1" in (held.last_failure or "")
    assert f.store.get_product(product.id).release == healthy, "the failed promotion is never recorded"
    assert held.validation is not None and held.validation.removed_at is not None


async def test_production_must_run_the_tested_digest(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    pod = "|localhost:5001/factory/bookmarks-service:x=x@{} \n"
    ex.scan_outputs["jsonpath"] = [pod.format(D1), pod.format(D2)]  # the temporary env, then production
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.held

    held = f.store.get_change(change.id)
    assert held.hold_class == "infra" and f"production runs {D2}, not the tested {D1}" in held.summary
    assert f.store.get_product(product.id).release is None, "nothing recorded as healthy"
    started = [e.station for e in f.store.list_events(change.id) if e.kind == EventKind.station_started]
    assert "deploy-repair" not in started and started.count("promote") == 1


@dataclass
class VerifierLimited(FakeAgentRunner):
    """The verifier's first call meets a usage limit: the change pauses at acceptance."""

    limited: bool = True

    async def run(self, req: AgentRequest, sink):  # noqa: ANN001, ANN201
        if req.role == "verifier" and self.limited:
            self.limited = False
            self.calls.append(req)
            return AgentResult(ok=False, rate_limited=True, limit_utilization=1.0, error="rate limited (simulated)")
        return await super().run(req, sink)


async def test_a_stopped_change_removes_its_environment_and_deploys_a_new_one_on_resume(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex, VerifierLimited())
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.paused_limits
    temp = f"bookmarks-service-c{change.id}"
    paused = f.store.get_change(change.id)
    assert paused.current_station == "acceptance" and paused.validation.removed_at is not None
    assert any(f"helm uninstall {temp} " in c for c in ex.calls), "removed while the change waits"

    f.manager.resume(change.id)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert len(_upgrades(ex, temp)) == 2, "a new temporary environment before acceptance"
    assert any(
        m.startswith("the temporary environment was removed when the change stopped") for m in _messages(f, change.id)
    )
    e2e = [c for c in ex.calls if "E2E_BASE_URL=" in c]
    assert all(PROD not in c for c in e2e[:-1]), "acceptance never ran against production"


async def test_a_cancelled_change_removes_its_environment(make_factory, cfg):
    _helm_target(cfg)
    ex, agents = FakeExecutor(), FakeAgentRunner(hang={"verifier": 30})
    f = make_factory(ex, agents)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))

    async def at_acceptance() -> None:
        while not any(c.role == "verifier" for c in agents.calls):  # noqa: ASYNC110 - polls the fake's calls
            await asyncio.sleep(0.05)

    await asyncio.wait_for(at_acceptance(), 30)
    f.manager.cancel(change.id)
    assert await wait_run(f, change.id) == ChangeStatus.cancelled

    async def removed() -> None:
        while (c := f.store.get_change(change.id)).validation is None or c.validation.removed_at is None:  # noqa: ASYNC110
            await asyncio.sleep(0.05)

    await asyncio.wait_for(removed(), 10)
    assert any(f"helm uninstall bookmarks-service-c{change.id} " in c for c in ex.calls)


async def test_a_failed_removal_is_swept_at_start_up_and_archive_cleans_up(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    temp = f"bookmarks-service-c{change.id}"
    ex.fail_on.append(f"uninstall {temp} ")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback, "a removal never fails the change"
    left = f.store.get_change(change.id)
    assert left.validation.removed_at is None
    warn = next(e for e in f.store.list_events(change.id) if e.data.get("validation_removed"))
    assert warn.level == EventLevel.warn and "NOT removed" in warn.message

    assert await f.manager.sweep_validation() == [change.id]
    assert f.store.get_change(change.id).validation.removed_at is not None
    assert await f.manager.sweep_validation() == [], "nothing left"

    await f.manager.archive(product.id, "saravana")
    assert sum(f"uninstall {temp} " in c for c in ex.calls) == 2, "archive doesn't remove it twice"
    assert any("uninstall bookmarks-service --namespace app-bookmarks-service" in c for c in ex.calls)


async def test_archive_removes_a_temporary_environment_left_behind(make_factory, cfg):
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    temp = f"bookmarks-service-c{change.id}"
    ex.fail_on.append(f"uninstall {temp} ")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    await f.manager.archive(product.id, "saravana")
    assert f.store.get_change(change.id).validation.removed_at is not None
    removed = [i for i, c in enumerate(ex.calls) if "uninstall" in c]
    assert f"uninstall {temp} " in ex.calls[removed[-2]] and "uninstall bookmarks-service " in ex.calls[removed[-1]]


async def test_the_local_target_validates_in_place_and_says_so(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product, change = await _delivered(f)
    assert change.validation is None
    [upgrade] = [c for c in ex.calls if "helm upgrade --install" in c]
    assert f"upgrade --install {product.slug} " in upgrade, "one deploy, in place"
    events = f.store.list_events(change.id)
    why = next(e for e in events if e.data.get("validated_in_place"))
    assert "can't give a change a second address" in why.message
    assert any(e.station == "promote" and "validated in place" in e.message for e in events)
    recorded = next(e for e in events if e.message.startswith("healthy release recorded"))
    assert recorded.station == "acceptance", "in place, the release is recorded when acceptance passes"


async def test_e2e_and_the_verifier_sign_in_with_each_releases_own_admin(make_factory, cfg):
    """A temporary environment starts with empty data and its own first-admin Secret (the chart
    names it after the release); production's admin is read again after promotion."""
    from agent_factory.models import CreateProductInput

    cfg.blueprints["nextjs-web"].environment = "local-ingress"
    ex = FakeExecutor()
    f = make_factory(ex)
    product = f.manager.create_product(
        CreateProductInput(title="Kudos board", requirements=PRODUCT.requirements, blueprint="nextjs-web")
    )
    change = f.manager.start_change(product)
    await wait_run(f, change.id)
    reads = [c for c in ex.calls if " get secret " in c]
    assert reads, "the pack declares an admin Secret"
    temp = f"kudos-board-c{change.id}"
    assert f"-n app-{temp} get secret {temp}-admin" in reads[0]
    assert all("get secret kudos-board-admin" not in c for c in reads[:1]), "never production's while validating"


def test_removal_is_refused_unless_the_target_still_points_where_it_was_recorded():
    v = _v(cluster="kind:af")
    here = {"environment": "local-ingress", "target": "kind-ingress", "cluster": "kind:af",
            "namespace": "app-a-c1", "helm_release": "a-c1"}  # fmt: skip
    assert validation.removal_problems(v, "a", here) == []
    assert validation.removal_problems(v, "a", {**here, "cluster": "ctx:prod"}) == [
        "recorded cluster 'kind:af' is not the current 'ctx:prod'"
    ]
    assert validation.removal_problems(v, "a", {**here, "environment": "eu"})[0].startswith("recorded environment")
    assert validation.removal_problems(v, "a", None) == ["deploy target kind-ingress can't say where its releases run"]
    assert validation.removal_problems(_v(cluster=""), "a", here) == ["no cluster recorded"]
    assert validation.removal_problems(_v(helm_release="a"), "a", here) == ["its name is the product's own release"]


async def test_a_moved_deploy_target_never_removes_a_same_named_release_elsewhere(make_factory, cfg):
    """The sweep and archive act on the recorded environment or not at all: if the target now points
    at another cluster, the release there with the same name is left alone, and the recorded one stays
    on record as not removed (a person cleans it up), never marked removed."""
    _helm_target(cfg)
    ex = FakeExecutor()
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    temp = f"bookmarks-service-c{change.id}"
    ex.fail_on.append(f"uninstall {temp} ")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert f.store.get_change(change.id).validation.removed_at is None

    f.manager.providers.integrations["kind-ingress"].kube_context = "prod-cluster"  # the config moved
    tries = len(ex.calls)
    assert await f.manager.sweep_validation() == []
    assert not any("uninstall" in c for c in ex.calls[tries:]), "nothing deleted in the other cluster"
    kept = f.store.get_change(change.id).validation
    assert kept.removed_at is None and kept.cluster != "prod-cluster", "still on record where it really runs"
    refused = [e for e in f.store.list_events(change.id) if e.data.get("validation_removed")][-1]
    assert (
        refused.level == EventLevel.warn
        and "recorded cluster" in refused.message
        and "'prod-cluster'" in refused.message
    )

    import pytest

    from agent_factory.engine.pipeline import FactoryError

    with pytest.raises(FactoryError, match="could not be removed"):
        await f.manager.archive(product.id, "saravana")
    assert not any("uninstall" in c for c in ex.calls[tries:]), "archive stops before touching anything"
