"""Rollback to the last healthy release (ADR-0046 slice 1): the pure rules, and the pipeline in
dry-run (fake commands and agents, real git): a delivery records the release; a later change that
fails at deploy or live acceptance restores it, diagnostics first, before repair; a rollback that
fails holds the change for a person."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from conftest import PRODUCT, wait_run

from agent_factory import rollback
from agent_factory.agents import FakeAgentRunner
from agent_factory.executor import CLEAN_SCANS, FakeExecutor
from agent_factory.models import Change, ChangeStatus, EventKind, Product, Release

NOW = datetime(2026, 11, 1, tzinfo=UTC)
STATUS = {"helm status": '{"name": "bookmarks-service", "version": 3, "info": {"status": "deployed"}}'}


def _release(change_id: str = "c1", iteration: int = 1, env: str = "local", revision: int | None = 3) -> Release:
    return Release(
        change_id=change_id, iteration=iteration, image=f"app:{iteration}", environment=env, revision=revision, at=NOW
    )


def _product(release: Release | None) -> Product:
    return Product(id="p1", title="T", requirements="r", blueprint="b", slug="app", created_at=NOW, release=release)


def _change(deployed: Release | None = None, change_id: str = "c2") -> Change:
    return Change(
        id=change_id, product_id="p1", iteration=2, status=ChangeStatus.running, created_at=NOW, updated_at=NOW,
        deployed=deployed,
    )  # fmt: skip


# ---------------------------------------------------------------- pure --
def test_deploy_failure_restores_the_last_healthy_release():
    last = _release()
    assert rollback.target("deploy", _change(), _product(last), "local") == last


def test_acceptance_failure_restores_only_when_this_change_is_serving():
    last = _release()
    serving = _release("c2", 2, revision=4)
    assert rollback.target("acceptance", _change(deployed=serving), _product(last), "local") == last
    assert rollback.target("acceptance", _change(), _product(last), "local") is None, "already rolled back"


def test_nothing_to_restore_without_a_healthy_release_or_elsewhere():
    assert rollback.target("deploy", _change(), _product(None), "local") is None, "first delivery"
    assert rollback.target("deploy", _change(), _product(_release(env="prod")), "local") is None
    assert rollback.target("deploy", _change(change_id="c1"), _product(_release()), "local") is None


def test_other_stations_never_roll_back():
    for handler in ("test", "build", "implement", "handover", "deploy_repair"):
        assert rollback.target(handler, _change(), _product(_release()), "local") is None


def test_helm_revision_reads_the_release_version():
    assert rollback.helm_revision(STATUS["helm status"]) == 3
    assert rollback.helm_revision('WARNING: kubeconfig is group-readable\n{"version": 7}') == 7
    for text in ("", "[dry-run] ok: helm status", '{"version": "3"}', '{"version": 0}', "[1, 2]"):
        assert rollback.helm_revision(text) is None, text


def test_the_note_is_stable_for_the_no_progress_stop():
    assert rollback.note(_release(), True, "a") == rollback.note(_release(), True, "b")
    assert "iteration 1" in rollback.note(_release(), True, "") and "revision 3" in rollback.note(_release(), True, "")
    assert "failed: helm rollback failed" in rollback.note(_release(), False, "helm rollback failed")


# ------------------------------------------------------------ pipeline --
def _ex(**kw) -> FakeExecutor:
    return FakeExecutor(scan_outputs={**CLEAN_SCANS, **STATUS}, **kw)


async def _delivered(f) -> tuple[Product, Change]:
    product = f.manager.create_product(PRODUCT)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    return f.store.get_product(product.id), f.store.get_change(first.id)


def _rollbacks(f, change_id: str) -> list[dict]:
    file = f.manager.ws.data_dir / "artifacts" / change_id / "rollbacks.json"
    return json.loads(file.read_text()) if file.is_file() else []


async def test_a_delivery_records_the_healthy_release(make_factory):
    ex = _ex()
    f = make_factory(ex)
    product, first = await _delivered(f)
    assert product.release is not None
    assert (product.release.change_id, product.release.iteration, product.release.revision) == (first.id, 1, 3)
    assert product.release.image.startswith("bookmarks-service:")
    assert not any("helm rollback" in c for c in ex.calls), "the first delivery has nothing to restore"


async def test_a_failed_deploy_rolls_back_after_diagnostics_then_repairs(make_factory):
    ex = _ex()
    f = make_factory(ex)
    product, _ = await _delivered(f)
    healthy = product.release
    ex.fail_on.append("helm upgrade")
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback

    rb = next(i for i, c in enumerate(ex.calls) if "helm rollback" in c)
    assert f"helm rollback bookmarks-service {healthy.revision} " in ex.calls[rb]
    diag = max(i for i, c in enumerate(ex.calls[:rb]) if "describe pods" in c)
    upgrade = max(i for i, c in enumerate(ex.calls[:rb]) if "helm upgrade" in c)
    assert upgrade < diag < rb, "diagnostics are collected after the failed upgrade, before the rollback"

    [entry] = _rollbacks(f, second.id)
    assert entry["ok"] and entry["station"] == "deploy" and entry["to"]["change_id"] == healthy.change_id
    repair = next(e for e in f.store.list_events(second.id) if e.data.get("routed"))
    assert "Production was rolled back to iteration 1" in repair.data["routed"]["evidence"]
    assert f.store.get_product(product.id).release.change_id == second.id, "the repaired iteration is healthy now"


async def test_a_failed_live_acceptance_rolls_back_with_diagnostics(make_factory):
    ex = _ex()
    agents = FakeAgentRunner()
    f = make_factory(ex, agents)
    product, _ = await _delivered(f)
    agents.fail_once.append("verifier")
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback

    [entry] = _rollbacks(f, second.id)
    assert entry["ok"] and entry["station"] == "acceptance"
    assert entry["replaced"]["change_id"] == second.id, "it replaced this change's own release"
    rb = next(i for i, c in enumerate(ex.calls) if "helm rollback" in c)
    assert any("describe pods" in c for c in ex.calls[rb - 3 : rb]), "acceptance collects diagnostics first"
    levels = [e.level for e in f.store.list_events(second.id) if e.data.get("rollback")]
    assert levels and set(levels) == {"warn"}


async def test_a_failed_rollback_holds_for_a_person(make_factory):
    ex = _ex()
    f = make_factory(ex)
    product, _ = await _delivered(f)
    ex.fail_on += ["helm upgrade", "helm rollback"]
    second = f.manager.feedback(product.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id) == ChangeStatus.held

    held = f.store.get_change(second.id)
    assert held.hold_class == "infra" and "production needs a person" in held.summary
    started = [e.station for e in f.store.list_events(second.id) if e.kind == EventKind.station_started]
    assert "deploy-repair" not in started
    [entry] = _rollbacks(f, second.id)
    assert not entry["ok"]
    assert f.store.get_product(product.id).release == product.release, "the record still names the healthy release"
