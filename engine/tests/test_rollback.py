"""Rollback to the last healthy release (ADR-0046 slice 1).

The pure rules, then the pipeline in dry-run (fake agents, real git, and the executor's simulated
Helm: revisions, history, values and pod digests). Every failure scenario fails closed:

- a record missing its revision, or naming a revision, image or place Helm doesn't have → refused,
  no `helm rollback` runs, the change holds for a person;
- a rollback whose restored release fails verification (history, digest, readiness) → held;
- a deploy that can't identify what it installed → held, nothing recorded;
- a change interrupted after deploy (a crash, a hold, a cancel) → production restored on stop;
- a failure after acceptance (handover) → the accepted release is already recorded, not rolled back.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from conftest import PRODUCT, wait_run

from agent_factory import rollback
from agent_factory.agents import FakeAgentRunner
from agent_factory.engine import stations as stations_mod
from agent_factory.engine.stations import StationOutcome, StationResult
from agent_factory.executor import CLEAN_SCANS, FakeExecutor, FakeHelm
from agent_factory.models import Change, ChangeStatus, EventKind, Product, Release

NOW = datetime(2026, 11, 1, tzinfo=UTC)
D1 = "sha256:" + "a" * 64
WHERE = {
    "environment": "local",
    "target": "local",
    "cluster": "kind:af",
    "namespace": "app-app",
    "helm_release": "app",
}


def _release(change_id: str = "c1", iteration: int = 1, **kw) -> Release:
    fields = {**WHERE, "revision": 3, "digest": D1, "image": f"app:{iteration}", **kw}
    return Release(change_id=change_id, iteration=iteration, at=NOW, **fields)


def _product(release: Release | None) -> Product:
    return Product(id="p1", title="T", requirements="r", blueprint="b", slug="app", created_at=NOW, release=release)


def _change(deployed: Release | None = None, change_id: str = "c2") -> Change:
    return Change(
        id=change_id, product_id="p1", iteration=2, status=ChangeStatus.running, created_at=NOW, updated_at=NOW,
        deployed=deployed,
    )  # fmt: skip


# ---------------------------------------------------------------- pure --
def test_a_failed_deploy_over_production_restores_the_last_healthy_release():
    last = _release()
    over_production = _change(deployed=_release("c2", 2, revision=None, digest=None))  # the intent, saved first
    assert rollback.target("deploy", over_production, _product(last), "local") == last
    assert rollback.target("promote", over_production, _product(last), "local") == last
    # a deploy to the change's temporary environment records no intent on production (ADR-0046 slice 2)
    assert rollback.target("deploy", _change(), _product(last), "local") is None, "production never touched"


def test_acceptance_failure_restores_only_while_this_change_is_serving():
    last = _release()
    serving = _release("c2", 2, revision=4)
    assert rollback.target("acceptance", _change(deployed=serving), _product(last), "local") == last
    assert rollback.target("acceptance", _change(), _product(last), "local") is None, "already rolled back"


def test_nothing_to_restore_without_a_healthy_release_or_elsewhere():
    serving = _release("c2", 2)
    assert rollback.target("deploy", _change(serving), _product(None), "local") is None, "first delivery"
    assert rollback.target("deploy", _change(serving), _product(_release(environment="prod")), "local") is None
    assert rollback.target("deploy", _change(change_id="c1"), _product(_release()), "local") is None


def test_other_stations_never_roll_back():
    for handler in ("test", "build", "implement", "handover", "deploy_repair"):
        assert rollback.target(handler, _change(), _product(_release()), "local") is None


def test_an_incomplete_record_is_refused_never_guessed():
    assert rollback.record_problems(_release()) == []
    assert rollback.record_problems(_release(revision=None)) == ["no Helm revision recorded"]
    assert rollback.record_problems(_release(revision=0)) == ["no Helm revision recorded"]
    assert rollback.record_problems(_release(digest=None)) == ["no image digest recorded"]
    assert rollback.record_problems(_release(digest="sha256:abc")) == ["no image digest recorded"]
    assert rollback.record_problems(_release(namespace="", cluster="")) == ["no cluster, namespace recorded"]


def test_a_record_for_another_place_is_refused():
    assert rollback.identity_problems(_release(), WHERE) == []
    moved = {**WHERE, "cluster": "kind:other", "namespace": "app-x"}
    problems = rollback.identity_problems(_release(), moved)
    assert problems == [
        "recorded cluster 'kind:af' is not the current 'kind:other'",
        "recorded namespace 'app-app' is not the current 'app-x'",
    ]


def test_the_revision_must_be_in_the_history_with_the_recorded_image():
    rows = [
        {"revision": 2, "status": "superseded", "description": "Install complete"},
        {"revision": 3, "status": "superseded", "description": "Upgrade complete"},
        {"revision": 4, "status": "failed", "description": "Upgrade failed"},
    ]
    assert rollback.history_problems(_release(), rows, "app:1") == []
    assert rollback.history_problems(_release(revision=9), rows, None) == [
        "revision 9 is not in the release history of app"
    ]
    assert rollback.history_problems(_release(revision=4), rows, "app:1") == [
        "revision 4 is 'failed', not a release that ran"
    ]
    assert rollback.history_problems(_release(), rows, "app:2") == ["revision 3 runs app:2, not app:1"]


SIDECAR = f"docker.io/istio/proxyv2:1.24.0=docker.io/istio/proxyv2@sha256:{'f' * 64}"


def _pod(digest: str, image: str = "docker.io/library/app:1", deleting: str = "", sidecar: bool = True) -> str:
    return f"{deleting}|{image}=docker.io/library/app@{digest} " + (f"{SIDECAR} " if sidecar else "") + "\n"


def test_a_restored_release_must_be_the_rollback_running_the_recorded_digest():
    latest = {"revision": 5, "status": "deployed", "description": "Rollback to 3"}
    pods = _pod(D1) + _pod(f"sha256:{'b' * 64}", deleting="2026-11-01T09:00:00Z")
    assert rollback.restored_problems(_release(), latest, "app:1", pods) == [], "a terminating pod doesn't count"
    wrong = {"revision": 5, "status": "deployed", "description": "Upgrade complete"}
    assert rollback.restored_problems(_release(), wrong, "app:1", pods) == [
        "the newest revision 5 is 'Upgrade complete', not 'Rollback to 3'"
    ]
    other = _pod(f"sha256:{'c' * 64}")
    assert rollback.restored_problems(_release(), latest, "app:1", other) == [
        f"running pods use sha256:{'c' * 64}, not the recorded {D1}"
    ]
    assert rollback.restored_problems(_release(), latest, "app:2", "") == [
        "the release now runs app:2, not app:1",
        "no running container of app",
    ]


def test_sidecars_never_cause_a_digest_mismatch():
    """Only the release's own containers are compared: a mesh proxy or log shipper on its own image
    is ignored, whatever its digest; a pod with only a sidecar running is not the release."""
    assert rollback.one_digest(_pod(D1), "app:1") == (D1, "")
    assert rollback.one_digest(_pod(D1, sidecar=False), "app:1") == (D1, "")
    two_sidecars = (
        f"|docker.io/library/app:1=docker.io/library/app@{D1} {SIDECAR} fluent/fluent-bit:3=x@sha256:{'9' * 64} \n"
    )
    assert rollback.one_digest(two_sidecars, "app:1") == (D1, "")
    assert rollback.one_digest(f"|{SIDECAR} \n", "app:1") == (None, "no running container of app")
    split = _pod(D1) + _pod(f"sha256:{'c' * 64}")
    assert rollback.one_digest(split, "app:1")[1].startswith("running pods disagree on the image")
    # the image a runtime reports may carry a registry, a tag or a digest: the repository decides
    for ran in ("app:1", "docker.io/library/app:1", "app@" + D1, "index.docker.io/library/app:2"):
        assert rollback.repository(ran) == "app", ran
    assert rollback.repository("registry.local:5000/team/app:1") == "registry.local:5000/team/app"
    assert rollback.one_digest(
        f"|123.dkr.ecr.eu-west-2.amazonaws.com/f/app:1=x@{D1} \n", "123.dkr.ecr.eu-west-2.amazonaws.com/f/app:1"
    ) == (D1, "")


def test_parsers_read_helm_and_kubectl_output():
    assert rollback.helm_revision('WARNING: kubeconfig is group-readable\n{"version": 7}') == 7
    for text in ("", "[dry-run] ok: helm status", '{"version": "3"}', '{"version": 0}', "[1, 2]"):
        assert rollback.helm_revision(text) is None, text
    assert rollback.values_image('{"image": {"repository": "app", "tag": "abc"}}') == "app:abc"
    assert rollback.values_image('{"image": {"repository": "app"}}') is None
    assert rollback.normalize_digest(f"docker.io/library/app@{D1}") == D1
    assert rollback.one_digest("|app:1=app:1 \n", "app:1") == (None, "a running container reports no image digest")


def test_the_note_is_stable_for_the_no_progress_stop():
    assert rollback.note(_release(), True, "a") == rollback.note(_release(), True, "b")
    assert "iteration 1" in rollback.note(_release(), True, "") and "revision 3" in rollback.note(_release(), True, "")
    assert "NOT rolled back" in rollback.note(_release(), False, "refused")


# ------------------------------------------------------------ pipeline --
async def _delivered(f) -> Product:
    product = f.manager.create_product(PRODUCT)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    return f.store.get_product(product.id)


def _rollbacks(f, change_id: str) -> list[dict]:
    file = f.manager.ws.data_dir / "artifacts" / change_id / "rollbacks.json"
    return json.loads(file.read_text()) if file.is_file() else []


def _rollback_cmds(ex: FakeExecutor) -> list[str]:
    return [c for c in ex.calls if " rollback " in c]


def _set_release(f, product: Product, **update) -> Release:
    product.release = product.release.model_copy(update=update)
    f.store.save_product(product)
    return product.release


async def test_a_delivery_records_a_complete_release(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    rel = product.release
    assert rel is not None and rollback.record_problems(rel) == []
    assert (rel.iteration, rel.revision, rel.helm_release, rel.namespace) == (1, 1, product.slug, f"app-{product.slug}")
    assert rel.digest == FakeHelm.digest(rel.image) and rel.cluster.startswith("kind:")
    assert not _rollback_cmds(ex), "the first delivery has nothing to restore"


async def test_a_failed_deploy_rolls_back_to_the_recorded_revision_after_diagnostics(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    healthy = product.release
    ex.fail_on.append("helm upgrade")
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback

    [cmd] = _rollback_cmds(ex)
    assert f"rollback {product.slug} {healthy.revision} " in cmd, "the recorded revision, never 'the previous'"
    rb = ex.calls.index(cmd)
    upgrade = max(i for i, c in enumerate(ex.calls[:rb]) if "helm upgrade" in c)
    diag = max(i for i, c in enumerate(ex.calls[:rb]) if "describe pods" in c)
    assert upgrade < diag < rb, "diagnostics are collected after the failed upgrade, before the rollback"
    after = ex.calls[rb:]
    assert any("rollout status" in c for c in after), "the rollout is verified"
    assert any(healthy and "/readyz" in c for c in after) and any("/healthz" in c for c in after)

    [entry] = _rollbacks(f, second.id)
    assert entry["ok"] and entry["station"] == "deploy" and entry["to"]["revision"] == healthy.revision
    repair = next(e for e in f.store.list_events(second.id) if e.data.get("routed"))
    assert "Production was rolled back to iteration 1" in repair.data["routed"]["evidence"]
    assert f.store.get_product(product.id).release.change_id == second.id, "the repaired iteration is healthy now"


async def test_a_failed_live_acceptance_rolls_back_with_diagnostics(make_factory):
    ex = FakeExecutor()
    agents = FakeAgentRunner()
    f = make_factory(ex, agents)
    product = await _delivered(f)
    agents.fail_once.append("verifier")
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback

    [entry] = _rollbacks(f, second.id)
    assert entry["ok"] and entry["station"] == "acceptance"
    assert entry["replaced"]["change_id"] == second.id, "it replaced this change's own release"
    rb = ex.calls.index(_rollback_cmds(ex)[0])
    assert any("describe pods" in c for c in ex.calls[rb - 3 : rb]), "acceptance collects diagnostics first"
    levels = [e.level for e in f.store.list_events(second.id) if e.data.get("rollback")]
    assert levels and set(levels) == {"warn"}


async def _held_without_rollback(f, ex, product) -> Change:
    ex.fail_on.append("helm upgrade")
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held
    held = f.store.get_change(second.id)
    assert held.hold_class == "infra" and "production needs a person" in held.summary
    assert not _rollback_cmds(ex), "nothing is rolled back on a record that can't be trusted"
    started = [e.station for e in f.store.list_events(second.id) if e.kind == EventKind.station_started]
    assert "deploy-repair" not in started
    return held


async def test_a_missing_revision_fails_closed(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    _set_release(f, product, revision=None)
    held = await _held_without_rollback(f, ex, product)
    [entry] = _rollbacks(f, held.id)
    assert entry["refused"] == ["no Helm revision recorded"]


async def test_a_revision_not_in_the_history_fails_closed(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    _set_release(f, product, revision=42)
    held = await _held_without_rollback(f, ex, product)
    assert _rollbacks(f, held.id)[0]["refused"] == [f"revision 42 is not in the release history of {product.slug}"]


async def test_a_revision_running_another_image_fails_closed(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    rel = _set_release(f, product, image=f"{product.slug}:not-what-ran")
    held = await _held_without_rollback(f, ex, product)
    [why] = _rollbacks(f, held.id)[0]["refused"]
    assert why.startswith(f"revision {rel.revision} runs {product.slug}:") and why.endswith(f"not {rel.image}")


async def test_a_record_for_another_namespace_fails_closed(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    _set_release(f, product, namespace="app-somewhere-else")
    held = await _held_without_rollback(f, ex, product)
    assert "recorded namespace 'app-somewhere-else'" in _rollbacks(f, held.id)[0]["refused"][0]


async def test_a_restored_release_that_fails_verification_holds(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    ex.fail_on.append("helm upgrade")
    image = product.release.image  # the app's own containers run another build after the rollback
    ex.scan_outputs["jsonpath"] = f"|docker.io/library/{image}=x@sha256:{'e' * 64} {SIDECAR} \n"
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held

    assert len(_rollback_cmds(ex)) == 1, "rolled back once, then a person is needed"
    [entry] = _rollbacks(f, second.id)
    assert not entry["ok"] and entry["verify"] == [
        f"running pods use sha256:{'e' * 64}, not the recorded {product.release.digest}"
    ]
    held = f.store.get_change(second.id)
    assert held.hold_class == "infra" and "production needs a person" in held.summary
    errors = [e for e in f.store.list_events(second.id) if e.data.get("rollback", {}).get("ok") is False]
    assert errors and errors[0].level == "error"


async def test_a_restored_release_that_is_not_ready_holds(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    ex.fail_on += ["helm upgrade", "/readyz"]
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held
    [entry] = _rollbacks(f, second.id)
    assert not entry["ok"] and entry["verify"][0].startswith("/readyz did not answer")


async def test_a_deploy_that_cannot_identify_its_release_records_nothing(make_factory):
    ex = FakeExecutor(scan_outputs={**CLEAN_SCANS, "helm status": "Error: release: not found"})
    f = make_factory(ex)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert held.hold_class == "infra" and "can't be recorded" in held.summary
    assert f.store.get_product(product.id).release is None
    assert any("no healthy release on record" in e.message for e in f.store.list_events(change.id))


async def test_an_interrupted_handover_keeps_the_accepted_release_recorded(make_factory, monkeypatch):
    """The release is recorded and saved when acceptance passes, so a handover that fails (a merge, a
    publish) can't leave an untracked release serving, and it isn't rolled back: it was accepted."""
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)

    async def handover_held(ctx):
        return StationResult(StationOutcome.held, "push to GitHub failed")

    monkeypatch.setitem(stations_mod.STATIONS, "handover", handover_held)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held
    rel = f.store.get_product(product.id).release
    assert rel.change_id == second.id and rel.iteration == 2 and rollback.record_problems(rel) == []
    assert not _rollback_cmds(ex) and not _rollbacks(f, second.id)


async def test_a_hold_after_deploy_restores_production_then_resume_redeploys(make_factory, monkeypatch):
    """Held at acceptance by an infra stop (no repair route runs): the stop restores production, and a
    resume starts again from deploy, since production no longer runs this change's release."""
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    healthy = product.release
    real = stations_mod.STATIONS["acceptance"]

    async def unreachable(ctx):
        ctx.stop = stations_mod.Stop(stations_mod.FailureClass.infra, "target unreachable", rule="target_probe")
        return StationResult(StationOutcome.failed, "target probe failed")

    monkeypatch.setitem(stations_mod.STATIONS, "acceptance", unreachable)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held
    [cmd] = _rollback_cmds(ex)
    assert f"rollback {product.slug} {healthy.revision} " in cmd
    held = f.store.get_change(second.id)
    assert held.deployed is None and held.current_station == "deploy"
    assert f.store.get_product(product.id).release == healthy

    monkeypatch.setitem(stations_mod.STATIONS, "acceptance", real)
    f.manager.resume(second.id)
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback
    assert f.store.get_product(product.id).release.change_id == second.id


async def test_a_crash_after_deploy_restores_production_on_restart(make_factory, monkeypatch):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    healthy = product.release
    gate = asyncio.Event()

    async def stuck(ctx):
        gate.set()
        await asyncio.sleep(3600)

    monkeypatch.setitem(stations_mod.STATIONS, "acceptance", stuck)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    await asyncio.wait_for(gate.wait(), 30)
    await f.manager.shutdown()  # the engine stops mid-acceptance: this change's release is serving
    assert f.store.get_change(second.id).deployed.change_id == second.id, "the intent survived the crash"
    assert not _rollback_cmds(ex)

    interrupted = f.manager.recover_on_startup()
    assert second.id in interrupted
    await f.manager.settle_interrupted(interrupted)
    [cmd] = _rollback_cmds(ex)
    assert f"rollback {product.slug} {healthy.revision} " in cmd
    after = f.store.get_change(second.id)
    assert after.status == ChangeStatus.interrupted and after.deployed is None and after.current_station == "deploy"
    assert _rollbacks(f, second.id)[0]["ok"]


async def test_a_cancelled_change_puts_production_back(make_factory, monkeypatch):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    gate = asyncio.Event()

    async def stuck(ctx):
        gate.set()
        await asyncio.sleep(3600)

    monkeypatch.setitem(stations_mod.STATIONS, "acceptance", stuck)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    await asyncio.wait_for(gate.wait(), 30)
    f.manager.cancel(second.id)
    await asyncio.wait_for(f.manager._tasks[second.id], 30)
    assert _rollbacks(f, second.id)[0]["ok"]
    assert f.store.get_product(product.id).release == product.release


async def test_an_engine_error_after_deploy_still_restores_production(make_factory, monkeypatch):
    """`_drive` raises after this change's release went live (here: recording it after acceptance):
    the change fails with its own evidence, and production is still put back (ADR-0046)."""
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)
    healthy = product.release

    def boom(*_a, **_k):
        raise RuntimeError("store unavailable while recording the release")

    monkeypatch.setattr(f.manager, "_record_release", boom)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.failed

    failed = f.store.get_change(second.id)
    assert failed.summary.startswith("engine error: store unavailable"), "the failure is still what it was"
    assert "RuntimeError: store unavailable" in failed.last_failure, "with its traceback as evidence"
    assert any("traceback" in e.data for e in f.store.list_events(second.id))
    [cmd] = _rollback_cmds(ex)
    assert f"rollback {product.slug} {healthy.revision} " in cmd
    [entry] = _rollbacks(f, second.id)
    assert entry["ok"] and entry["replaced"]["change_id"] == second.id
    assert f.store.get_product(product.id).release == healthy
    assert failed.deployed is None


async def test_a_fault_in_the_restore_itself_is_reported_not_raised(make_factory, monkeypatch):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = await _delivered(f)

    def boom(*_a, **_k):
        raise RuntimeError("store unavailable while recording the release")

    async def broken(*_a, **_k):
        raise OSError("artifacts disk full")

    monkeypatch.setattr(f.manager, "_record_release", boom)
    monkeypatch.setattr(stations_mod, "restore_last_healthy", broken)
    monkeypatch.setattr("agent_factory.engine.pipeline.restore_last_healthy", broken)
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.failed
    failed = f.store.get_change(second.id)
    assert failed.summary.startswith("engine error: store unavailable")
    assert "production state unknown, needs a person" in failed.summary and failed.hold_class == "infra"
