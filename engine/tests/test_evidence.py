"""Evidence manifest (ADR-0023): every stopped run is sealed; the seal is re-verified on read."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime

import pytest
from conftest import PRODUCT, wait_run
from test_access import ADMIN, MEMBER, OTHER
from test_api import _client

from agent_factory import evidence
from agent_factory.models import ChangeStatus


# ------------------------------------------------------------ pure parts --
def _sealed(tmp_path):
    d = tmp_path / "artifacts" / "r1"
    (d / "sessions").mkdir(parents=True)
    (d / "review.json").write_text('{"ok": true}')
    (d / "sessions" / "01-build-builder.jsonl").write_text('{"type": "text"}\n')
    m = evidence.build_manifest(d, {"run": {"id": "r1"}}, datetime(2026, 10, 1, tzinfo=UTC))
    return d, evidence.write_manifest(d, m)


def test_a_sealed_folder_verifies(tmp_path):
    d, digest = _sealed(tmp_path)
    m = json.loads((d / "manifest.json").read_text())
    assert [f["path"] for f in m["files"]] == ["review.json", "sessions/01-build-builder.jsonl"]
    assert m["totals"]["files"] == 2 and m["_type"] == evidence.SCHEMA
    v = evidence.verify(d, digest)
    assert v.intact and v.manifest_ok and not (v.changed or v.missing or v.added)


def test_every_change_after_the_seal_is_reported(tmp_path):
    d, digest = _sealed(tmp_path)
    (d / "review.json").write_text('{"ok": false}')
    (d / "sessions" / "01-build-builder.jsonl").unlink()
    (d / "late.txt").write_text("written after the seal")
    v = evidence.verify(d, digest)
    assert (v.changed, v.missing, v.added) == (["review.json"], ["sessions/01-build-builder.jsonl"], ["late.txt"])
    assert v.manifest_ok and not v.intact


def test_an_edited_manifest_does_not_match_the_recorded_digest(tmp_path):
    d, digest = _sealed(tmp_path)
    m = json.loads((d / "manifest.json").read_text())
    m["run"]["id"] = "someone-else"
    (d / "manifest.json").write_text(json.dumps(m))
    v = evidence.verify(d, digest)
    assert v.sealed and not v.manifest_ok and not v.intact


def test_unsealed(tmp_path):
    assert not evidence.verify(tmp_path, None).sealed
    assert not evidence.verify(tmp_path, "abc").sealed, "no manifest on disk"


@pytest.mark.skipif(not shutil.which("sha256sum"), reason="coreutils sha256sum not installed")
def test_sha256sums_checks_with_standard_tools(tmp_path):
    d, _ = _sealed(tmp_path)
    res = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=d, capture_output=True, text=True)  # noqa: S607
    assert res.returncode == 0, res.stdout + res.stderr
    assert "manifest.json: OK" in res.stdout


# ------------------------------------------------------------ in the engine --
async def test_a_delivered_run_is_sealed_and_downloadable(make_factory):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        product = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()["product"]
        change_id = product["latest_change_id"]
        assert await wait_run(f, change_id) == ChangeStatus.awaiting_feedback

        ev = (await c.get(f"/api/changes/{change_id}/evidence", headers=OTHER)).json()
        assert ev["sealed"] and ev["intact"] and ev["status_at_seal"] == "awaiting_feedback"
        paths = {x["path"] for x in ev["files"]}
        assert {"events.jsonl", "spec/requirements.yaml", "spec/spec.md"} <= paths
        assert any(p.startswith("sessions/") for p in paths), "agent transcripts are evidence"
        assert ev["commit"], "the run branch's commit is recorded"
        seal = [e for e in f.store.list_events(change_id) if "evidence_seal" in e.data]
        assert seal and seal[-1].data["evidence_seal"]["sha256"] == ev["sha256"]

        folder = f.manager.ws.data_dir / "artifacts" / change_id
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["workflow"]["id"] and manifest["order"]["created_by"] == "priya"
        assert manifest["events"] == len((folder / "events.jsonl").read_text().splitlines())

        # the bundle holds transcripts: the product's creator or an admin only
        assert (await c.get(f"/api/changes/{change_id}/evidence/bundle", headers=OTHER)).status_code == 403
        r = await c.get(f"/api/changes/{change_id}/evidence/bundle", headers=MEMBER)
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        assert {f"{change_id}/manifest.json", f"{change_id}/SHA256SUMS", f"{change_id}/events.jsonl"} <= names
        assert len(names) == len(ev["files"]) + 2
        assert (await c.get(f"/api/changes/{change_id}/evidence/bundle", headers=ADMIN)).status_code == 200

        # tampering is reported, not hidden
        session = next(p for p in paths if p.startswith("sessions/"))
        (folder / session).write_text("rewritten\n")
        ev = (await c.get(f"/api/changes/{change_id}/evidence", headers=OTHER)).json()
        assert not ev["intact"] and ev["changed"] == [session]
    await ctx.__aexit__(None, None, None)


async def test_a_cancelled_run_is_sealed_too(make_factory):
    f = make_factory()
    w = f.workflows["fastapi-service"]
    w.draft.set_spec_review("first")
    w.draft.publish("gate on")
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_approval
    f.manager.cancel(change.id)
    assert await wait_run(f, change.id) == ChangeStatus.cancelled
    seals = evidence.latest_seal(f.store, change.id)
    assert seals and seals["status"] == "cancelled"
    folder = f.manager.ws.data_dir / "artifacts" / change.id
    assert evidence.verify(folder, seals["sha256"]).intact


async def test_no_evidence_before_the_run_stops(make_factory):
    f = make_factory()
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.get("/api/changes/nope/evidence")).status_code == 404
        change = f.manager.start_change(f.manager.create_product(PRODUCT))
        await f.manager.shutdown()  # stopped mid-flight: interrupted, never sealed
        assert (await c.get(f"/api/changes/{change.id}/evidence")).json()["sealed"] is False
        assert (await c.get(f"/api/changes/{change.id}/evidence/bundle")).status_code == 409
    await ctx.__aexit__(None, None, None)


async def test_post_run_work_never_blocks_the_next_iteration(make_factory):
    """Feedback right after delivery starts iteration 2 even while the seal of iteration 1 is still running."""
    import asyncio

    f = make_factory()
    gate = asyncio.Event()
    real_seal = f.manager._seal

    async def slow_seal(change, product):  # noqa: ANN001, ANN202
        await gate.wait()
        await real_seal(change, product)

    f.manager._seal = slow_seal  # type: ignore[method-assign]
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    for _ in range(600):
        if f.store.get_change(change.id).status == ChangeStatus.awaiting_feedback:
            break
        await asyncio.sleep(0.05)
    assert f.manager.is_active(change.id), "still sealing"
    second = f.manager.feedback(product.id, "add a reset endpoint")
    assert second.iteration == 2
    gate.set()
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert await wait_run(f, second.id) == ChangeStatus.awaiting_feedback
    assert evidence.latest_seal(f.store, change.id) and evidence.latest_seal(f.store, second.id)
