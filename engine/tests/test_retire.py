"""End of life (roadmap J5): the retire flow, a retirement that stops and continues, the frozen
product, the sealed decommission record, the product's history and Outcomes."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import PRODUCT, wait_run
from test_access import ADMIN, MEMBER, OTHER
from test_api import _client
from test_operations import OpsExecutor, delivered, readings

from agent_factory import evidence, retire
from agent_factory.engine.pipeline import FactoryError, InvalidRequestError
from agent_factory.models import ChangeStatus, Product
from agent_factory.outcomes import outcomes

REPO = "https://github.com/acme-apps/bookmarks-service"
TOKEN = "ghp_" + "r" * 36  # low entropy: no secret-scan noise
REASON = "replaced by the new catalogue"


def _steps(product: Product) -> dict[str, str]:
    assert product.retirement is not None
    return {s.id: s.status for s in product.retirement.steps}


async def _published(make_factory, monkeypatch):  # noqa: ANN001, ANN202
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    f, ex, p = await delivered(make_factory)
    p.repo_url = REPO
    f.store.save_product(p)
    return f, ex, p


# -------------------------------------------------------------------- pure --
def test_retiring_needs_the_products_name_typed():
    p = Product(id="p1", title="Bookmarks service", slug="bookmarks-service", requirements="x", blueprint="b",
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC))  # fmt: skip
    assert (
        retire.confirm_problem(p, " bookmarks SERVICE ") is None
        and retire.confirm_problem(p, "bookmarks-service") is None
    )
    assert "type the product's name ('Bookmarks service')" in (retire.confirm_problem(p, "yes") or "")


def test_a_retirement_continues_at_the_step_that_failed():
    now = datetime.now(UTC)
    r = retire.begin("priya", " no longer used ", "priya", now)
    assert [s.id for s in r.steps] == ["freeze", "export", "undeploy", "archive-repo", "record"]
    assert r.reason == "no longer used" and retire.next_step(r).id == "freeze"
    retire.mark(r, "freeze", "done", "", now)
    retire.mark(r, "export", "failed", "the cluster didn't answer", now)
    assert retire.next_step(r).id == "export" and not r.done
    retire.mark(r, "export", "skipped", "retired without a final data export", now)
    assert retire.next_step(r).id == "undeploy"


# ---------------------------------------------------------------- the flow --
async def test_retiring_exports_undeploys_archives_and_seals(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    await f.manager.operations.backup(p)  # an earlier, scheduled backup
    before = len(ex.calls)
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    r = done.retirement
    assert r is not None and r.done and r.requested_by == "priya" and r.reason == REASON
    assert _steps(done) == dict.fromkeys(("freeze", "export", "undeploy", "archive-repo", "record"), "done")
    calls = ex.calls[before:]
    order = [
        next(i for i, c in enumerate(calls) if needle in c) for needle in ("pg_dump", "uninstall", "gh repo archive")
    ]
    assert order == sorted(order), "the data is exported before the app is removed, the repository archived after"
    assert any(c == "gh repo archive acme-apps/bookmarks-service --yes" for c in calls)
    assert TOKEN not in json.dumps(calls), "the token goes through the environment"
    assert done.archived_at and done.app_url is None, "the app is gone and its port is free"
    exports = sorted(x.name for x in f.manager.operations.backup_dir(p).glob("*.backup"))
    assert len(exports) == 1 and exports[0] in r.steps[1].detail, "only the final export is kept"
    messages = [e.message for e in f.store.list_events(p.latest_change_id)]
    assert messages[-1] == f"product retired by priya: {REASON}"


async def test_the_decommission_record_is_sealed_and_names_every_changes_evidence(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    second = f.manager.feedback(p.id, "Add full-text search over notes.")
    assert await wait_run(f, second.id, 90) == ChangeStatus.awaiting_feedback
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "bookmarks-service")
    folder = f.manager.retirer.folder(done)
    assert sorted(x.name for x in folder.iterdir()) == [
        "SHA256SUMS", "history.json", "manifest.json", "operations.json", "record.json", "work-ledger.json",
    ]  # fmt: skip
    assert evidence.verify(folder, done.retirement.record_sha256).intact
    record = json.loads((folder / "record.json").read_text())
    assert record["decommission"] == retire.SCHEMA and record["retired_by"] == "priya" and record["reason"] == REASON
    assert record["totals"] == {"changes": 2, "deliveries": 2}
    assert [c["iteration"] for c in record["changes"]] == [1, 2]
    for c in record["changes"]:
        seal = evidence.latest_seal(f.store, c["id"])
        assert c["evidence"] == {"sealed": True, "manifest_sha256": seal["sha256"], "intact": True}
    assert all(s["status"] == "done" for s in record["steps"])
    export = record["data_export"]
    assert export["sha256"] and export["size"] > 0 and export["fingerprint"] == "bookmarks=12\nusers=3"
    text = (folder / "record.json").read_text() + (folder / "history.json").read_text()
    assert TOKEN not in text
    view = f.manager.retirer.verify(done)
    assert view["intact"] and view["record"]["product"]["slug"] == "bookmarks-service"
    (folder / "record.json").write_text("{}")
    tampered = f.manager.retirer.verify(done)
    assert not tampered["intact"] and tampered["changed"] == ["record.json"], "a changed record is seen"


async def test_nothing_starts_on_a_retired_product_and_it_is_no_longer_run(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    with pytest.raises(FactoryError, match="this product is retired: nothing new starts on it"):
        f.manager.feedback(p.id, "one more thing")
    ops = f.manager.operations
    assert ops.why_not(done) == "the product is retired"
    assert await ops.watch(done) is None
    view = ops.view(done)
    assert not view.live and view.retirement.done
    again = await f.manager.retirer.retire(p.id, "bob", "again", "wrong name")
    assert again.retirement.requested_by == "priya", "a retired product stays as it was retired"


async def test_retiring_is_refused_without_the_name_or_while_a_change_runs(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    with pytest.raises(InvalidRequestError, match="type the product's name"):
        await f.manager.retirer.retire(p.id, "priya", REASON, "yes please")
    running = f.manager.feedback(p.id, "Add full-text search over notes.")
    with pytest.raises(FactoryError, match="a change to this product is in progress"):
        await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    assert f.store.get_product(p.id).retirement is None, "nothing was frozen"
    assert await wait_run(f, running.id, 90) == ChangeStatus.awaiting_feedback


async def test_a_step_that_fails_holds_the_retirement_and_the_next_call_continues_it(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    ex.fail_on.append("pg_dump")
    stopped = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    assert _steps(stopped) == {
        "freeze": "done", "export": "failed", "undeploy": "pending", "archive-repo": "pending", "record": "pending",
    }  # fmt: skip
    assert not stopped.retirement.done and not stopped.archived_at, "the app stays up: its data isn't safe yet"
    assert "retire without an export" in stopped.retirement.steps[1].detail
    assert not any("uninstall" in c or "gh repo archive" in c for c in ex.calls)
    with pytest.raises(FactoryError, match="being retired"):
        f.manager.feedback(p.id, "one more thing")
    app, ctx, c = await _client(f)
    async with c:
        [need] = (await c.get("/api/attention", params={"product_id": p.id})).json()
        assert (need["kind"], need["panel"], need["blocking"]) == ("retire", "operations", True)
    await ctx.__aexit__(None, None, None)
    done = await f.manager.retirer.retire(p.id, "sam", "ignored: the first reason stands", "Bookmarks service")
    assert done.retirement.done and done.retirement.requested_by == "priya" and done.retirement.reason == REASON
    assert _steps(done)["export"] == "done", "continued at the step that failed"
    assert len([c for c in ex.calls if "uninstall" in c]) == 1


async def test_the_final_export_never_runs_beside_another_backup(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    f.manager.operations._busy.add(p.id)  # a scheduled backup is running
    stopped = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    assert _steps(stopped)["export"] == "failed" and "continue in a minute" in stopped.retirement.steps[1].detail
    assert not any("pg_dump" in c for c in ex.calls)
    f.manager.operations._busy.discard(p.id)
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    assert done.retirement.done and p.id not in f.manager.operations._busy


async def test_a_product_can_be_retired_without_an_export_when_its_data_is_out_of_reach(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    ex.fail_on += ["pg_dump", "pg_dump"]
    await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service", without_export=True)
    assert done.retirement.done and _steps(done)["export"] == "skipped"
    record = json.loads((f.manager.retirer.folder(done) / "record.json").read_text())
    assert record["data_export"] is None
    assert "skipped by priya: retired without a final data export" in json.dumps(record["steps"])


async def test_the_repository_step_says_why_it_did_nothing(make_factory, monkeypatch):
    f, ex, p = await delivered(make_factory)  # never published: no repo_url, no token
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    step = done.retirement.steps[3]
    assert (step.status, step.detail) == (
        "skipped",
        "the product was never published: there is no repository to archive",
    )
    f2, ex2, p2 = await _published(make_factory, monkeypatch)
    f2.manager.cfg.retire.archive_repo = False
    done2 = await f2.manager.retirer.retire(p2.id, "priya", REASON, "Bookmarks service")
    assert done2.retirement.steps[3].status == "skipped" and not any("gh repo archive" in c for c in ex2.calls)


async def test_archiving_the_repository_needs_the_token_and_fails_closed(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    monkeypatch.delenv("GITHUB_TOKEN")
    f.settings.github_token = None  # the settings read it at start, too
    stopped = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    assert _steps(stopped)["archive-repo"] == "failed" and "no GitHub token" in stopped.retirement.steps[3].detail
    assert not stopped.retirement.done and stopped.archived_at, "the app is already gone; the record waits"
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert (await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")).retirement.done


async def test_a_never_delivered_product_retires_with_nothing_to_export(make_factory):
    f = make_factory(OpsExecutor())
    p = f.manager.create_product(PRODUCT)
    done = await f.manager.retirer.retire(p.id, "priya", "never started", "Bookmarks service")
    assert done.retirement.done and _steps(done)["export"] == "skipped"
    assert evidence.verify(f.manager.retirer.folder(done), done.retirement.record_sha256).intact


async def test_overlapping_retire_requests_run_one_after_the_other(make_factory, monkeypatch):
    """Review of #185: B read A's state while A was exporting, then saved stale progress over A's."""
    f, ex, p = await _published(make_factory, monkeypatch)
    gate, release = asyncio.Event(), asyncio.Event()
    archive = f.manager.archive

    async def slow_undeploy(pid, by):  # noqa: ANN001, ANN202
        gate.set()
        await release.wait()
        return await archive(pid, by)

    monkeypatch.setattr(f.manager, "archive", slow_undeploy)
    a = asyncio.create_task(f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service"))
    await asyncio.wait_for(gate.wait(), 30)  # A exported and is removing the app
    b = asyncio.create_task(f.manager.retirer.retire(p.id, "sam", "a second click", "Bookmarks service"))
    await asyncio.sleep(0.2)
    assert not b.done(), "B waits for A instead of acting on what A is still doing"
    release.set()
    done_a, done_b = await asyncio.wait_for(asyncio.gather(a, b), 60)
    assert done_a.retirement.done and done_b.retirement.done
    stored = f.store.get_product(p.id)
    assert stored.retirement.requested_by == "priya" and stored.retirement.reason == REASON
    assert all(s.status == "done" for s in stored.retirement.steps), "B didn't save stale steps over A's"
    assert len([c for c in ex.calls if "pg_dump" in c]) == 1, "exported once"
    assert (
        len([c for c in ex.calls if "uninstall" in c]) == 1
        and len([c for c in ex.calls if "gh repo archive" in c]) == 1
    )
    assert evidence.verify(f.manager.retirer.folder(stored), stored.retirement.record_sha256).intact


async def test_a_retry_continues_from_the_stored_state_not_the_one_it_read_first(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    ex.fail_on.append("gh repo archive")
    stopped = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    assert _steps(stopped)["archive-repo"] == "failed"
    stale = stopped.model_copy(deep=True)  # what a slow second request may still hold
    done = await f.manager.retirer.retire(stale.id, "priya", REASON, "Bookmarks service")
    assert done.retirement.done and len([c for c in ex.calls if "pg_dump" in c]) == 1, "no second export"


# ----------------------------------------------------------------- history --
async def test_the_history_runs_from_the_first_request_to_the_decommission(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    p.created_at = datetime.now(UTC) - timedelta(hours=2)  # asked for two hours ago
    f.store.save_product(p)
    ops = f.manager.operations
    await ops.backup(p)
    await ops.restore_drill(p)
    ex.down = True
    await readings(f, p)
    [incident] = [i for i in f.store.list_work_items(p.id) if i.category == "incident"]
    fix = f.manager.feedback(p.id, "", kind=None, items=[incident.id])
    assert await wait_run(f, fix.id, 90) == ChangeStatus.awaiting_feedback
    ex.down = False
    fixed = f.store.get_product(p.id)
    fixed.release.at = datetime.now(UTC) - timedelta(minutes=12)  # the fix has been live for a while
    f.store.save_product(fixed)
    await readings(f, fixed, start=datetime.now(UTC) - timedelta(minutes=10))
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    h = f.manager.retirer.history(done)
    kinds = [e.kind for e in h.entries]
    assert kinds[0] == "requested" and kinds[-1] == "decommissioned" and h.retired
    for kind in ("change", "delivered", "backup", "restore", "incident", "retire"):
        assert kind in kinds, kind
    assert [e.at for e in h.entries] == sorted(e.at for e in h.entries), "oldest first"
    delivered_entries = [e for e in h.entries if e.kind == "delivered"]
    assert len(delivered_entries) == 2 and all(e.evidence for e in delivered_entries), "each with its sealed evidence"
    assert h.entries[-1].evidence == done.retirement.record_sha256 == h.record_sha256
    told = [e.title for e in h.entries if e.kind == "incident"]
    assert told[0] == "Incident found: the product is outside its availability target"
    assert any(t.startswith("Incident fixed:") for t in told), "found, addressed by a change, and verified fixed"
    sealed = json.loads((f.manager.retirer.folder(done) / "history.json").read_text())
    assert [e["kind"] for e in sealed][0] == "requested" and len(sealed) == len(h.entries) - 1, (
        "the sealed history ends where the record begins"
    )


# ---------------------------------------------------------------- outcomes --
async def test_outcomes_list_retired_products_with_their_record(make_factory, monkeypatch):
    f, ex, p = await _published(make_factory, monkeypatch)
    assert outcomes(f.store, 30).retired == []
    done = await f.manager.retirer.retire(p.id, "priya", REASON, "Bookmarks service")
    [r] = outcomes(f.store, 30).retired
    assert (r.product_id, r.title, r.retired_by, r.reason, r.changes, r.deliveries, r.record_sha256) == (
        p.id, "Bookmarks service", "priya", REASON, 1, 1, done.retirement.record_sha256,
    )  # fmt: skip
    later = datetime.now(UTC) + timedelta(days=40)
    assert outcomes(f.store, 30, later).retired == [], "outside the window"


# --------------------------------------------------------------------- API --
async def test_retiring_over_the_api_is_for_the_owner_the_creator_or_an_admin(make_factory, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    f = make_factory(OpsExecutor())
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        created = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()
        pid = created["product"]["id"]
        assert await wait_run(f, created["product"]["latest_change_id"], 90) == ChangeStatus.awaiting_feedback
        body = {"reason": REASON, "confirm": "Bookmarks service"}
        assert (await c.post(f"/api/products/{pid}/retire", json=body, headers=OTHER)).status_code == 403
        r = await c.get(f"/api/products/{pid}/decommission", headers=MEMBER)
        assert r.status_code == 422 and "not retired" in r.json()["detail"]
        r = await c.post(f"/api/products/{pid}/retire", json={**body, "confirm": "yes"}, headers=MEMBER)
        assert r.status_code == 422 and "type the product's name" in r.json()["detail"]
        r = await c.post(f"/api/products/{pid}/retire", json=body, headers=MEMBER)
        assert r.status_code == 200 and r.json()["retirement"]["finished_at"] and r.json()["archived_at"]
        record = (await c.get(f"/api/products/{pid}/decommission", headers=OTHER)).json()
        assert record["intact"] and record["record"]["retired_by"] == "priya"
        history = (await c.get(f"/api/products/{pid}/history", headers=OTHER)).json()
        assert history["retired"] and history["entries"][-1]["kind"] == "decommissioned"
        retired = (await c.get("/api/outcomes", headers=ADMIN)).json()["retired"]
        assert [x["product_id"] for x in retired] == [pid]
        r = await c.post(f"/api/products/{pid}/feedback", json={"text": "one more"}, headers=MEMBER)
        assert r.status_code == 409 and "retired" in r.json()["detail"]
        assert (await c.get("/api/attention", params={"product_id": pid}, headers=MEMBER)).json() == []
    await ctx.__aexit__(None, None, None)
