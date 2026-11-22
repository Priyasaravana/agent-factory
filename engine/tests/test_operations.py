"""Run and maintain (roadmap J4), the part that acts: the watch, incidents, backup, the restore
drill, upkeep and resuming after a restart, on a dry-run factory."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import PRODUCT, wait_run
from test_access import ADMIN, MEMBER, OTHER
from test_api import _client
from test_operate import NPM_OUTDATED, PIP_OUTDATED

from agent_factory import ledger, packs
from agent_factory.engine import stations as stations_mod
from agent_factory.engine.operations import OperateError
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import ChangeSource, ChangeStatus, EventKind
from agent_factory.secret_refs import REDACTOR

_OUT = re.compile(r"> '?([^' ]+\.backup)'?$")
_IN = re.compile(r"< '?([^' ]+\.backup)'?$")
_DB = re.compile(r"-d (\w+)")
_MAKE = re.compile(r"(?:createdb|dropdb) .*?(\w+)'")
_FILE = re.compile(r"-o '?([^' ]+)'? ")


@dataclass
class OpsExecutor(FakeExecutor):
    """A fake executor that also stands for a live app and its database: health reads answer or
    not (`down`), a metrics page is served, and the pack's data commands back up, restore and
    fingerprint `data` (database name -> what its data looks like)."""

    down: bool = False
    requests: float = 100.0
    errors: float = 0.0
    data: dict[str, str] = field(default_factory=lambda: {"app": "bookmarks=12\nusers=3"})
    corrupt_restore: bool = False  # a restore that silently loses rows
    heal_on: tuple[str, ...] = ()  # a command that brings the app back (incident response: rollback, restart)
    refuse: dict[str, str] = field(default_factory=dict)  # a command (needle) that fails, with its output
    moving: bool = False  # the data changes with every read (a backup can't be fingerprinted)

    async def run(self, command, cwd=None, timeout=600, env=None) -> CommandResult:  # noqa: ANN001
        for needle, output in self.refuse.items():
            if needle in command:
                self.calls.append(command)
                return CommandResult(command, 1, output)
        if any(needle in command for needle in self.heal_on):
            self.down = False
        if "curl -fsS -m 8" in command:
            self.calls.append(command)
            if self.down:
                return CommandResult(command, 7, "curl: (7) Failed to connect to the app: Connection refused")
            if (m := _FILE.search(command)) and m.group(1) != "/dev/null":
                self.requests += 50
                page = (
                    f'http_requests_total{{route="/x",status="200"}} {self.requests - self.errors}\n'
                    f'http_requests_total{{route="/x",status="500"}} {self.errors}\n'
                )
                Path(m.group(1)).write_text(page)  # noqa: ASYNC240
            return CommandResult(command, 0, "")
        if " exec " in command and "kubectl" in command:
            self.calls.append(command)
            for needle in list(self.fail_on):
                if needle in command:
                    self.fail_on.remove(needle)
                    return CommandResult(command, 1, f"[dry-run] simulated failure for '{needle}'")
            db = m.group(1) if (m := _DB.search(command)) else "app"
            if made := _MAKE.search(command):
                db = made.group(1)
            if "-cdrill" in command:  # a restore drill's scratch release has a database of its own
                db = "drill:" + db
            if "pg_dump" in command and (out := _OUT.search(command)):
                Path(out.group(1)).write_text(self.data.get(db, ""))  # noqa: ASYNC240
                return CommandResult(command, 0, "")
            if "pg_restore" in command and (src := _IN.search(command)):
                got = Path(src.group(1)).read_text()  # noqa: ASYNC240
                self.data[db] = got.rsplit("\n", 1)[0] if self.corrupt_restore else got
                return CommandResult(command, 0, "")
            if "psql" in command:
                if self.moving:
                    self.data[db] = self.data.get(db, "") + "\nmore=1"
                return CommandResult(command, 0 if db in self.data else 1, self.data.get(db, "no such database"))
            if "createdb" in command:
                self.data[db] = ""
                return CommandResult(command, 0, "")
            if "dropdb" in command:
                self.data.pop(db, None)
                return CommandResult(command, 0, "")
        return await super().run(command, cwd, timeout, env)


async def delivered(make_factory, ex: OpsExecutor | None = None, **product):
    """A factory with one delivered product, and the executor that stands for its live app."""
    ex = ex or OpsExecutor()
    f = make_factory(ex)
    p = f.manager.create_product(PRODUCT.model_copy(update=product))
    change = f.manager.start_change(p)
    assert await wait_run(f, change.id, 90) == ChangeStatus.awaiting_feedback
    p = f.store.get_product(p.id)
    p.release.at = datetime.now(UTC) - timedelta(hours=1)  # it has been live for an hour
    f.store.save_product(p)
    return f, ex, p


async def readings(f, product, n: int = 6, start: datetime | None = None, every: timedelta = timedelta(minutes=1)):
    """`n` readings `every` apart (a minute; by default starting 20 minutes ago); returns the last verdict."""
    start = start or datetime.now(UTC) - timedelta(minutes=20)
    a = None
    for i in range(n):
        a = await f.manager.operations.watch(f.store.get_product(product.id), start + every * (i + 1))
    return a


FAST = timedelta(seconds=10)  # how often a product is read while a reading fails or an incident is open


def _incidents(f, product) -> list:
    return [i for i in f.store.list_work_items(product.id) if i.category == ledger.INCIDENT]


def _records(f, product, kind: str) -> list:
    return f.store.list_operations(product.id, kind)


# ------------------------------------------------------------------ watch --
async def test_a_healthy_product_is_watched_and_nothing_is_filed(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    assert ops.why_not(p) is None
    a = await readings(f, p)
    assert (a.state, a.samples, a.success, a.error_rate) == ("ok", 6, 1.0, 0.0)
    assert _incidents(f, p) == [] and _records(f, p, "alert") == []
    health = [c for c in ex.calls if "curl -fsS -m 8" in c and "/healthz" in c]
    assert len(health) == 6 and all("-o /dev/null" in c for c in health), "read-only, one request per reading"
    events = [e.message for e in f.store.list_events(p.latest_change_id)]
    assert not any("curl -fsS -m 8" in m for m in events), "readings never fill the change's event log"


async def test_too_few_readings_alert_nobody(make_factory):
    f, ex, p = await delivered(make_factory)
    ex.down = True
    a = await readings(f, p, n=4)
    assert a.state == "unknown" and _incidents(f, p) == []


async def test_a_breach_is_the_alert_that_opens_an_incident_for_the_owner(make_factory):
    f, ex, p = await delivered(make_factory)
    p.owner = "priya"
    f.store.save_product(p)
    ex.down = True
    a = await readings(f, p)
    assert a.state == "breached"
    [incident] = _incidents(f, p)
    assert (incident.status, incident.severity, incident.change_kind) == ("open", "high", "bug")
    assert "0.0% of 6 health checks answered" in incident.detail and "Connection refused" in incident.detail
    [alert] = _records(f, p, "alert")
    assert not alert.ok and alert.data["incident"] == incident.id and alert.data["owner"] == "priya"
    assert any(f"incident {incident.id} opened for priya" in e.message for e in f.store.list_events(p.latest_change_id))
    await readings(f, p, start=datetime.now(UTC) - timedelta(minutes=14))
    assert len(_incidents(f, p)) == 1 and len(_records(f, p, "alert")) == 1, "one incident, one alert, while it lasts"
    app, ctx, c = await _client(f)
    async with c:
        [need] = (await c.get("/api/attention", params={"product_id": p.id})).json()
        # no factory restore drill has passed on this install: the response is proposed to the owner
        assert (need["kind"], need["owner"], need["blocking"], need["panel"], need["title"]) == (
            "incident",
            "priya",
            True,
            "operations",
            "Incident: restore service (restart the app)",
        )
        card = (await c.get(f"/api/products/{p.id}/next-iteration")).json()
        assert [i["id"] for i in card["incidents"]] == [incident.id]
    await ctx.__aexit__(None, None, None)


async def test_an_incident_becomes_a_fix_change_and_closes_when_the_fix_is_live_and_healthy(make_factory):
    f, ex, p = await delivered(make_factory)
    ex.down = True
    await readings(f, p)
    [incident] = _incidents(f, p)
    fix = f.manager.feedback(p.id, "", kind=None, items=[incident.id])
    assert fix.kind == "bug", "the engine picks the kind from the incident"
    assert "Incident: the product is outside its availability target" in fix.change_request
    assert _incidents(f, p)[0].status == "in_progress"
    assert await wait_run(f, fix.id, 90) == ChangeStatus.awaiting_feedback
    ex.down = False
    after = f.store.get_product(p.id)
    assert after.release.change_id == fix.id
    a = await readings(f, after, start=datetime.now(UTC))
    assert a.state == "ok" and a.samples == 6, "only readings of the fixed release are judged"
    [closed] = _incidents(f, p)
    assert (closed.status, closed.resolved_by) == ("fixed", fix.id)
    assert [r.kind for r in f.store.list_operations(p.id)][:1] == ["recovered"]
    assert f.manager.operations.view(after).incidents == []


async def test_a_fix_that_does_not_hold_alerts_again(make_factory):
    f, ex, p = await delivered(make_factory)
    ex.down = True
    await readings(f, p)
    fix = f.manager.feedback(p.id, "", kind=None, items=[_incidents(f, p)[0].id])
    assert await wait_run(f, fix.id, 90) == ChangeStatus.awaiting_feedback
    await readings(f, f.store.get_product(p.id), start=datetime.now(UTC))  # still down on the new release
    [incident] = _incidents(f, p)
    assert incident.status == "open" and incident.history[-1].event == "still-present"
    assert len(_records(f, p, "alert")) == 2


async def test_a_product_that_recovers_by_itself_closes_its_incident(make_factory):
    f, ex, p = await delivered(make_factory)
    ex.down = True
    await readings(f, p)
    ex.down = False
    start = datetime.now(UTC) + timedelta(minutes=40)  # the failed readings have left the window
    a = await readings(f, p, start=start)
    assert a.state == "ok" and _incidents(f, p)[0].status == "fixed"


async def test_server_errors_breach_the_target_even_when_the_app_answers(make_factory):
    f, ex, p = await delivered(make_factory)
    await readings(f, p, n=1)
    ex.errors = 60.0  # 60 of the next 250 requests fail
    a = await readings(f, p, n=5, start=datetime.now(UTC) - timedelta(minutes=19))
    assert a.state == "breached" and a.success == 1.0 and a.error_rate == pytest.approx(60 / 250)
    assert "requests failed with a server error" in _incidents(f, p)[0].detail


async def test_nothing_is_read_while_a_change_deploys(make_factory, monkeypatch):
    f, ex, p = await delivered(make_factory)
    gate, release = asyncio.Event(), asyncio.Event()
    real = stations_mod.STATIONS["deploy"]

    async def slow(ctx):  # noqa: ANN001, ANN202
        gate.set()
        await release.wait()
        return await real(ctx)

    monkeypatch.setitem(stations_mod.STATIONS, "deploy", slow)
    second = f.manager.feedback(p.id, "Add full-text search over notes.")
    await asyncio.wait_for(gate.wait(), 60)
    ex.down = True  # the old pods are going away: a reading now would say nothing about the product
    assert await f.manager.operations.watch(f.store.get_product(p.id)) is None
    assert f.store.list_health_samples(p.id) == []
    ex.down = False
    release.set()
    assert await wait_run(f, second.id, 90) == ChangeStatus.awaiting_feedback


async def test_the_factory_runs_only_what_it_built_and_deployed(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    undelivered = f.manager.create_product(PRODUCT.model_copy(update={"title": "Never built"}))
    assert ops.why_not(undelivered) == "nothing is live yet: the product has no delivered release"
    await f.manager.archive(p.id, "priya")
    assert ops.why_not(f.store.get_product(p.id)) == "the product is archived"
    assert await ops.watch(f.store.get_product(p.id)) is None
    with pytest.raises(OperateError, match="archived"):
        await ops.backup(f.store.get_product(p.id))


# ------------------------------------------------- incident response (slice 0) --
def _drill_passed(f) -> None:
    from agent_factory.engine.operations import DRILL_PASSED

    f.store.set_meta(DRILL_PASSED, json.dumps({"passed": True, "by": "saravana", "note": "restored to /tmp/x"}))


def _respond_records(f, product) -> list:
    return list(reversed(f.store.list_operations(product.id, "respond")))


def _ran(f, product) -> list[str]:
    return [r.data["step"] for r in _respond_records(f, product) if r.data.get("ran")]


async def test_drift_is_snapshotted_then_reapplied_and_restored_without_a_change(make_factory):
    f, ex, p = await delivered(make_factory, OpsExecutor(heal_on=("helm rollback ",)))
    _drill_passed(f)
    ex.helm.drift[p.slug] = {"digest": "sha256:" + "a" * 64}  # someone set another image behind Helm's back
    ex.down = True
    start = datetime.now(UTC) - timedelta(minutes=20)
    await readings(f, p, n=5, start=start, every=FAST)
    [incident] = _incidents(f, p)
    assert _ran(f, p) == ["reapply"], "the live state differs from the recorded release: re-apply it"
    [act] = [r for r in _respond_records(f, p) if r.data.get("ran")]
    assert act.ok and "rolled back to revision 1" in act.summary and "not the recorded" in act.data["reason"]
    snap = f.manager.operations.incidents_dir(p) / act.data["snapshot"]["file"]
    assert snap.is_file() and snap.stat().st_mode & 0o777 == 0o600, "the snapshot comes first, private"
    assert "recorded release: revision 1" in snap.read_text() and "differs: running containers" in snap.read_text()
    assert ex.helm.drift[p.slug] == {}, "Helm put the recorded release back"
    changes = f.store.list_changes(p.id)
    assert len(changes) == 1, "no development cycle: restoring service is not a change"
    # three healthy readings in a row after the action: restored, with the DORA timings
    await readings(f, p, n=3, start=start + 5 * FAST, every=FAST)
    [restored] = _records(f, p, "restored")
    assert restored.data["by_step"] == "reapply" and restored.data["time_to_restore_s"] > 0
    assert restored.data["caused_by"] == p.latest_change_id, "it began within a day of that change's release"
    assert restored.data["classification"].startswith("operational: the live state had drifted")
    view = f.manager.operations.view(f.store.get_product(p.id))
    [r] = view.responses
    assert (r.state, r.tried, r.restored_by, r.held) == ("restored", ["reapply"], "reapply", "")
    assert r.time_to_restore_s is not None and r.time_to_restore_s < 5 * 60, "under five minutes"
    # while the window still holds the outage the incident stays open, restored
    await readings(f, p, n=3, start=start + timedelta(minutes=10))
    assert _incidents(f, p)[0].status == "open"
    # once the window is back within its target it is resolved at once, with its timings (live check
    # 2026-11-22: kept open after the window recovered, the card showed a stale failure next to 100 %)
    await readings(f, p, n=6, start=start + timedelta(minutes=50))
    [closed] = _incidents(f, p)
    assert closed.status == "fixed" and incident.id == closed.id
    [resolved] = _records(f, p, "resolved")
    assert "window is back within its target" in resolved.summary and resolved.data["time_to_restore_s"] > 0
    assert resolved.data["classification"].startswith("operational: the live state had drifted")
    assert _records(f, p, "recovered") == [], "one record says how it ended"
    assert f.manager.operations.view(f.store.get_product(p.id)).responses == []


async def test_the_same_breach_soon_after_it_was_resolved_goes_on_up_the_ladder(make_factory):
    f, ex, p = await delivered(make_factory, OpsExecutor(heal_on=("helm rollback ",)))
    _drill_passed(f)
    ex.helm.drift[p.slug] = {"digest": "sha256:" + "a" * 64}
    ex.down = True
    start = datetime.now(UTC) - timedelta(hours=3)
    p.release.at = start - timedelta(minutes=5)
    f.store.save_product(p)
    await readings(f, p, n=5, start=start, every=FAST)
    await readings(f, p, n=3, start=start + 5 * FAST, every=FAST)
    await readings(f, p, n=6, start=start + timedelta(minutes=40))
    [item] = _incidents(f, p)
    assert item.status == "fixed"
    ex.down = True  # back an hour later: within a day of the resolution
    await readings(f, p, n=5, start=start + timedelta(hours=1), every=FAST)
    [item] = _incidents(f, p)
    assert item.status == "open" and _ran(f, p) == ["reapply", "restart"], "re-apply was tried: the next step"
    [r] = f.manager.operations.view(f.store.get_product(p.id)).responses
    assert (r.state, r.reopened) in {("mitigating", 1), ("reopened", 1)}


async def test_a_crash_with_the_recorded_release_is_restarted_once_then_held_for_the_owner(make_factory):
    f, ex, p = await delivered(make_factory)
    _drill_passed(f)
    ex.helm.drift[p.slug] = {"crash": "CrashLoopBackOff", "crash_persists": True}
    ex.down = True
    start = datetime.now(UTC) - timedelta(minutes=20)
    await readings(f, p, n=5, start=start)
    assert _ran(f, p) == ["restart"], "nothing drifted: restart once"
    assert any("rollout restart deployment" in c for c in ex.calls)
    assert not any("helm rollback" in c for c in ex.calls), "re-apply only when the live state differs"
    # the crash survives the restart; after the settle time the ladder has nothing left in slice 0
    await readings(f, p, n=4, start=start + timedelta(minutes=5))
    assert _ran(f, p) == ["restart"], "each step once per incident"
    [r] = f.manager.operations.view(f.store.get_product(p.id)).responses
    assert r.state == "mitigating" and "rolling back to an earlier release" in r.held
    holds = [x for x in _respond_records(f, p) if x.data.get("hold")]
    assert len(holds) == 1, "a hold is said once, not on every reading"
    app, ctx, c = await _client(f)
    async with c:
        [need] = (await c.get("/api/attention", params={"product_id": p.id})).json()
        assert (need["title"], need["panel"], need["blocking"]) == (
            "Incident: the factory's response is held for you",
            "operations",
            True,
        )
    await ctx.__aexit__(None, None, None)


async def test_a_newer_live_release_is_never_undone(make_factory):
    f, ex, p = await delivered(make_factory)
    _drill_passed(f)
    revs = ex.helm.releases[p.slug]
    newer = json.loads(json.dumps(revs[-1]["values"]))
    newer["image"]["tag"] = "hotfix"
    ex.helm._add(p.slug, newer, "Upgrade complete")  # released by someone (or a newer factory) since the record
    ex.down = True
    await readings(f, p, n=5)
    assert _ran(f, p) == [] and not any("helm rollback" in c or "rollout restart" in c for c in ex.calls)
    [hold] = _respond_records(f, p)
    assert hold.data["hold"] == "stale_record" and "revision 2" in hold.summary


async def test_ask_mode_proposes_and_the_owner_runs_it(make_factory):
    f, ex, p = await delivered(make_factory, OpsExecutor(heal_on=("rollout restart",)))
    ex.down = True  # no factory restore drill on this install: ask
    await readings(f, p, n=5)
    assert _ran(f, p) == []
    [proposal] = _respond_records(f, p)
    assert proposal.data == {**proposal.data, "ask": True, "step": "restart"}
    view = f.manager.operations.view(f.store.get_product(p.id))
    assert (view.respond.configured, view.respond.mode) == ("auto", "ask") and "restore drill" in view.respond.why
    ops = f.manager.operations
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post(f"/api/products/{p.id}/respond")
        assert r.status_code == 200, r.text
        assert r.json()["responding"], "the step runs in the background: a dropped request can't cut it off"
        again = await c.post(f"/api/products/{p.id}/respond")
        assert again.status_code == 422 and "already running" in again.text
        await asyncio.gather(*list(ops._jobs))
        view = (await c.get(f"/api/products/{p.id}/operations")).json()
        [resp] = view["responses"]
        assert (view["responding"], resp["state"], resp["tried"]) == (False, "mitigating", ["restart"])
    await ctx.__aexit__(None, None, None)
    [act] = [r for r in _respond_records(f, p) if r.data.get("ran")]
    assert act.ok and act.by != "factory"


async def test_a_failed_step_says_why_and_is_not_credited_when_service_comes_back(make_factory):
    """Live check 2026-11-22: a re-apply that didn't restore the release, then a person rolled back by hand.
    The record must carry Helm's own words, and the restore must not be credited to the failed step."""
    helm_says = "Error: timed out waiting for the condition (token=s3cr3t-value)"
    ex = OpsExecutor(refuse={"helm rollback ": helm_says})
    f, ex, p = await delivered(make_factory, ex)
    _drill_passed(f)
    ex.helm.drift[p.slug] = {"digest": "sha256:" + "a" * 64}
    ex.down = True
    start = datetime.now(UTC) - timedelta(minutes=20)
    with patch.object(REDACTOR, "text", lambda t: t.replace("s3cr3t-value", "***")):
        await readings(f, p, n=5, start=start, every=FAST)
    [act] = [r for r in _respond_records(f, p) if r.data.get("ran")]
    assert not act.ok and act.summary.startswith("re-apply the recorded release failed: helm rollback failed")
    assert "timed out waiting for the condition" in act.data["detail"] and "s3cr3t" not in act.data["detail"]
    [r] = f.manager.operations.view(f.store.get_product(p.id)).responses
    assert (r.failed, r.last_ok) == (["reapply"], False) and "timed out" in r.last_detail
    ex.down = False  # someone ran `helm rollback` by hand
    await readings(f, p, n=3, start=start + 5 * FAST, every=FAST)
    [restored] = _records(f, p, "restored")
    assert restored.data["by_step"] == "" and "not by the factory" in restored.summary
    [r] = f.manager.operations.view(f.store.get_product(p.id)).responses
    assert r.state == "restored" and r.classification.startswith(
        "service came back after the factory's last step failed"
    )


async def test_the_page_says_the_app_answers_while_the_window_still_holds_the_outage(make_factory):
    f, ex, p = await delivered(make_factory)
    ex.down = True
    start = datetime.now(UTC) - timedelta(minutes=10)
    await readings(f, p, n=6, start=start)
    ex.down = False
    await readings(f, p, n=2, start=start + timedelta(minutes=6))
    now = start + timedelta(minutes=8, seconds=30)
    av = f.manager.operations.view(f.store.get_product(p.id), now).availability
    assert av.state == "breached" and av.last_ok is True and av.last_detail == ""
    assert av.clears_at == start + timedelta(minutes=6) + timedelta(minutes=30), "the last failed reading ages out"
    ex.down = True
    await readings(f, p, n=1, start=start + timedelta(minutes=8))
    av = f.manager.operations.view(f.store.get_product(p.id), now).availability
    assert av.last_ok is False and "Connection refused" in av.last_detail and av.clears_at is None


async def test_a_recurrence_reopens_the_incident_and_goes_up_the_ladder(make_factory):
    f, ex, p = await delivered(make_factory, OpsExecutor(heal_on=("helm rollback ",)))
    _drill_passed(f)
    ex.helm.drift[p.slug] = {"digest": "sha256:" + "a" * 64}
    ex.down = True
    start = datetime.now(UTC) - timedelta(minutes=50)
    await readings(f, p, n=5, start=start, every=FAST)
    await readings(f, p, n=3, start=start + 5 * FAST, every=FAST)
    assert [r.kind for r in f.store.list_operations(p.id)][0] == "restored"
    ex.down = True  # the same breach, twenty minutes later, before the quiet period ended
    await readings(f, p, n=5, start=start + timedelta(minutes=20), every=FAST)
    assert _ran(f, p) == ["reapply", "restart"], "re-apply was tried for this incident: the next step"
    [r] = f.manager.operations.view(f.store.get_product(p.id)).responses
    assert r.reopened == 1 and len(_incidents(f, p)) == 1


async def test_at_most_three_actions_an_hour_on_a_product(make_factory):
    f, ex, p = await delivered(make_factory)
    _drill_passed(f)
    now = datetime.now(UTC)
    for i in range(3):  # three steps on this product within the hour, for earlier incidents
        f.manager.operations._record(p, "respond", True, "earlier", "factory",
                                     {"incident": f"old{i}", "step": "restart", "ran": True},
                                     at=now - timedelta(minutes=30))  # fmt: skip
    ex.down = True
    await readings(f, p, n=5, start=now - timedelta(minutes=20))
    assert not any("rollout restart" in c or "helm rollback" in c for c in ex.calls)
    [hold] = [r for r in _respond_records(f, p) if r.data.get("hold")]
    assert hold.data["hold"] == "rate_limited" and "at most 3" in hold.summary


async def test_after_a_factory_restore_it_asks_until_the_record_matches_the_live_release(make_factory):
    f, ex, p = await delivered(make_factory)
    _drill_passed(f)
    ops = f.manager.operations
    (Path(f.manager.ws.data_dir) / "factory-restored").write_text("2026-11-22T12:00:00Z\n")
    assert ops.note_restore() and not (Path(f.manager.ws.data_dir) / "factory-restored").exists()
    assert ops.response_mode(p)[0] == "ask"
    await readings(f, p, n=1)  # one reading: the recorded release is the live one
    assert ops.response_mode(f.store.get_product(p.id)) == ("auto", "")
    assert any("matches the live one" in e.message for e in f.store.list_events(p.latest_change_id))


async def test_respond_off_leaves_incidents_to_a_fix_change(make_factory):
    f, ex, p = await delivered(make_factory)
    _drill_passed(f)
    f.cfg.operate.respond = "off"
    ex.down = True
    await readings(f, p)
    assert len(_incidents(f, p)) == 1 and _respond_records(f, p) == []


# ------------------------------------------------------- backup and restore --
async def test_a_backup_is_taken_with_the_packs_command_and_kept_private(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    rec = await ops.backup(p, "priya")
    assert rec.ok and rec.by == "priya" and rec.data["fingerprint"] == "bookmarks=12\nusers=3"
    path = ops.backup_dir(p) / rec.data["file"]
    assert path.read_text() == "bookmarks=12\nusers=3" and path.stat().st_mode & 0o777 == 0o600
    assert ops.backup_dir(p).stat().st_mode & 0o777 == 0o700
    dump = next(c for c in ex.calls if "pg_dump" in c)
    assert "kubectl -n app-bookmarks-service exec statefulset/bookmarks-service-postgres -- sh -c" in dump
    assert "pg_dump -U app -d app --format=custom --no-owner" in dump
    events = [(e.message, e.data) for e in f.store.list_events(p.latest_change_id)]
    assert "bookmarks=12" not in str(events), "the product's data is never logged"
    assert not any("pg_dump" in m for m, _ in events)


async def test_a_backup_of_moving_data_or_a_failed_command_does_not_count(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ex.moving = True
    rec = await ops.backup(p)
    assert not rec.ok and "kept changing while it was backed up" in rec.summary
    assert list(ops.backup_dir(p).glob("*.backup")) == [], "a backup that doesn't count is not kept"
    ex.moving = False
    ex.fail_on.append("pg_dump")
    rec = await ops.backup(p)
    assert not rec.ok and rec.summary.startswith("backup failed: the backup command failed")
    assert ops.backups(p) == []


async def test_backups_beyond_the_newest_are_pruned(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    f.manager.cfg.operate.keep_backups = 2
    made = [(await ops.backup(p)).data["file"] for _ in range(3)]
    assert sorted(x.name for x in ops.backup_dir(p).glob("*.backup")) == sorted(made[1:])
    assert [r.data["file"] for r in ops.backups(p)] == [made[2], made[1]]


async def test_a_restore_drill_proves_the_newest_backup_away_from_production(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    with pytest.raises(OperateError, match="no backup to restore yet"):
        await ops.restore_drill(p)
    await ops.backup(p)
    rec = await ops.restore_drill(p, "priya")
    assert rec.ok and rec.data == {
        "backup": ops.backups(p)[0].data["backup"],
        "where": "database",
        "app_ran_on_it": False,
        "target": "restore_drill",
    }
    assert "scratch database restore_drill beside production's" in rec.summary
    restore = next(c for c in ex.calls if "pg_restore" in c)
    assert "exec -i statefulset/bookmarks-service-postgres" in restore and "-d restore_drill " in restore
    assert not any("pg_restore" in c and "-d app " in c for c in ex.calls), (
        "production's database is never restored into"
    )
    assert ex.data == {"app": "bookmarks=12\nusers=3"}, "the scratch database is dropped again"
    view = ops.view(p)
    assert view.restore_proven and not view.restore_overdue and view.backups == 1


async def test_a_restore_that_loses_data_fails_the_drill(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    await ops.backup(p)
    ex.corrupt_restore = True
    rec = await ops.restore_drill(p)
    assert not rec.ok and "the restored data differs from the backup: users=3" in rec.summary
    view = ops.view(p)
    assert not view.restore_proven and view.restore_overdue
    assert "restore_drill" not in ex.data


async def test_a_backup_file_that_changed_is_never_restored(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    rec = await ops.backup(p)
    (ops.backup_dir(p) / rec.data["file"]).write_text("tampered")
    drill = await ops.restore_drill(p)
    assert not drill.ok and "its hash differs" in drill.summary
    assert not any("pg_restore" in c for c in ex.calls)


async def test_on_a_target_with_releases_the_drill_runs_the_app_on_the_restored_data(make_factory, cfg):
    cfg.blueprints["fastapi-service"].environment = "local-ingress"
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    await ops.backup(p)
    before = list(ex.calls)
    rec = await ops.restore_drill(p)
    assert rec.ok and rec.data["where"] == "release" and rec.data["app_ran_on_it"] is True
    assert rec.data["target"] == "bookmarks-service-cdrill"
    calls = ex.calls[len(before) :]
    install = next(c for c in calls if "upgrade --install bookmarks-service-cdrill " in c)
    assert f"image.tag={p.release.image.rsplit(':', 1)[1]}" in install, "the live image, not a new build"
    restore = next(c for c in calls if "pg_restore" in c)
    assert "-n app-bookmarks-service-cdrill exec -i statefulset/bookmarks-service-cdrill-postgres" in restore
    ready = [c for c in calls if "curl -fsS -m 8" in c and "/readyz" in c]
    assert ready and "bookmarks-service-cdrill." in ready[-1]
    assert any("uninstall bookmarks-service-cdrill" in c for c in calls), "the scratch release is removed"
    assert ex.helm.latest_image("bookmarks-service") == p.release.image, "production is untouched"
    assert f.store.get_meta("restore_drills_open") == "{}"


async def test_a_scratch_release_left_by_a_cut_off_drill_is_swept_at_start(make_factory, cfg):
    cfg.blueprints["fastapi-service"].environment = "local-ingress"
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ops._mark_drill(p, "bookmarks-service-cdrill")
    await ops.sweep_drills()
    assert any("uninstall bookmarks-service-cdrill" in c for c in ex.calls)
    assert f.store.get_meta("restore_drills_open") == "{}"


async def test_a_stack_without_data_has_nothing_to_back_up(make_factory):
    f, ex, p = await delivered(make_factory)
    f.manager.cfg.blueprints["fastapi-service"].operate.data = None
    with pytest.raises(OperateError, match="declares no data"):
        await f.manager.operations.backup(p)
    assert not f.manager.operations.view(p).data_declared


# ------------------------------------------------------------------ upkeep --
IMAGE_SCAN = " image --quiet --format json"  # the fake executor's key for `trivy image`
TRIVY = """{"SchemaVersion": 2, "Results": [{"Target": "app/uv.lock", "Class": "lang-pkgs", "Type": "uv",
 "Vulnerabilities": [{"VulnerabilityID": "CVE-2026-0001", "PkgName": "jinja2", "InstalledVersion": "3.1.4",
 "FixedVersion": "3.1.6", "Severity": "HIGH", "Title": "sandbox escape"}]}]}"""


async def test_upkeep_finds_a_new_cve_and_dependencies_behind(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ex.scan_outputs[IMAGE_SCAN] = TRIVY
    ex.scan_outputs["uv pip list --outdated"] = PIP_OUTDATED
    rec = await ops.upkeep(p, "priya")
    assert rec.ok and rec.data["gaps"] == []
    assert rec.data["image"]["findings"] == 1 and rec.data["image"]["new"] == 1
    assert rec.data["dependencies"] == {"behind": 2, "major": 1, "ledger": "new"}
    items = f.store.list_work_items(p.id)
    [cve] = [i for i in items if i.category == ledger.IMAGE]
    assert "CVE-2026-0001" in cve.title + cve.detail + str(cve.sources)
    [behind] = [i for i in items if i.category == ledger.UPKEEP]
    assert behind.title == "2 dependencies are behind (1 by a major version)" and behind.at == ["pydantic", "fastapi"]
    assert any(p.release.image in c for c in ex.calls if IMAGE_SCAN in c), "the live image is what is scanned"
    view = ops.view(p)
    assert [i.id for i in view.upkeep_items] == [behind.id] and view.last_upkeep.id == rec.id
    # the next check, with everything up to date, closes both: the checks really ran again
    ex.scan_outputs[IMAGE_SCAN] = '{"SchemaVersion": 2, "Results": []}'
    ex.scan_outputs["uv pip list --outdated"] = "[]"
    await ops.upkeep(p)
    after = {i.id: i.status for i in f.store.list_work_items(p.id)}
    assert (after[cve.id], after[behind.id]) == ("fixed", "fixed")


async def test_upkeep_says_what_it_could_not_check(make_factory):
    f, ex, p = await delivered(make_factory)
    ex.scan_outputs["uv pip list --outdated"] = "error: no virtual environment"
    rec = await f.manager.operations.upkeep(p)
    gap = "the dependency check didn't answer (it failed, or printed no list)"
    assert rec.ok and rec.data["gaps"] == [gap]
    assert f"not checked: {gap}" in rec.summary
    assert [i for i in f.store.list_work_items(p.id) if i.category == ledger.UPKEEP] == [], (
        "a gap is not a clean result"
    )


async def _open_upkeep(f, ex, p):  # noqa: ANN001, ANN202
    ex.scan_outputs["uv pip list --outdated"] = PIP_OUTDATED
    await f.manager.operations.upkeep(p)
    [item] = [i for i in f.store.list_work_items(p.id) if i.category == ledger.UPKEEP]
    assert item.status == "open"
    return item


@pytest.mark.parametrize(
    ("why", "answer"),
    [
        ("the install failed before the list ran", CommandResult("uv sync", 1, "error: Failed to download `fastapi`")),
        ("it answered nothing", CommandResult("uv pip list", 0, "")),
        ("it failed after printing a list", CommandResult("uv pip list", 2, "[]")),
    ],
)
async def test_a_dependency_check_that_did_not_answer_never_closes_an_open_item(make_factory, why, answer):
    """Review of #185: a failed or empty check read as 'nothing is behind' closed real findings."""
    f, ex, p = await delivered(make_factory)
    item = await _open_upkeep(f, ex, p)
    ex.scan_outputs.pop("uv pip list --outdated")
    real = ex.run

    async def answering(command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN202
        if "uv pip list --outdated" in command:
            ex.calls.append(command)
            return answer
        return await real(command, cwd, timeout, env)

    ex.run = answering  # type: ignore[method-assign]
    rec = await f.manager.operations.upkeep(p)
    assert "the dependency check" in " ".join(rec.data["gaps"]), why
    still = next(i for i in f.store.list_work_items(p.id) if i.id == item.id)
    assert still.status == "open", f"{why}: a check that didn't answer proves nothing"


async def test_npm_says_outdated_with_exit_status_1_and_that_is_an_answer(make_factory):
    f, ex, p = await delivered(make_factory, blueprint="nextjs-web")
    assert f.manager.cfg.blueprints["nextjs-web"].operate.upkeep.outdated == "npm outdated --json"
    real = ex.run

    async def npm(command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN202
        if command.startswith("npm outdated"):
            return CommandResult(command, 1, NPM_OUTDATED)  # npm's documented status when something is behind
        return await real(command, cwd, timeout, env)

    ex.run = npm  # type: ignore[method-assign]
    rec = await f.manager.operations.upkeep(p)
    assert rec.data["dependencies"]["behind"] == 2 and rec.data["gaps"] == []


async def test_upkeep_waits_for_a_person_unless_the_factory_is_told_to_start_it(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ex.scan_outputs["uv pip list --outdated"] = PIP_OUTDATED
    await ops.upkeep(p)
    assert await ops.start_upkeep(p) is None, "upkeep_start: ask is the default"
    f.manager.cfg.operate.upkeep_start = "auto"
    change = await ops.start_upkeep(p)
    assert change is not None and (change.kind, change.source, change.requested_by) == (
        "upkeep", ChangeSource.schedule, "factory",
    )  # fmt: skip
    assert "2 dependencies are behind" in change.change_request
    assert await ops.start_upkeep(f.store.get_product(p.id)) is None, "not while a change is running on it"
    assert await wait_run(f, change.id, 90) == ChangeStatus.awaiting_feedback


async def test_upkeep_never_starts_during_an_incident(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    f.manager.cfg.operate.upkeep_start = "auto"
    ex.scan_outputs["uv pip list --outdated"] = PIP_OUTDATED
    await ops.upkeep(p)
    ex.down = True
    await readings(f, p)
    assert await ops.start_upkeep(p) is None, "the fix comes first"


async def test_auto_upkeep_tries_a_fix_once_and_then_leaves_it_for_the_owner(make_factory):
    """E2E audit 2026-11-17 (cost): with `upkeep_start: auto`, an item a scheduled change didn't fix
    reopened and started the same change again on every upkeep cycle, spending model budget and
    redeploying production without end."""
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    f.manager.cfg.operate.upkeep_start = "auto"
    ex.scan_outputs["uv pip list --outdated"] = PIP_OUTDATED
    await ops.upkeep(p)
    first = await ops.start_upkeep(p)
    assert first is not None and await wait_run(f, first.id, 90) == ChangeStatus.awaiting_feedback
    await asyncio.sleep(0.05)
    await ops.upkeep(f.store.get_product(p.id))  # the dependencies are still behind: the fix didn't hold
    [item] = [i for i in f.store.list_work_items(p.id) if i.category == ledger.UPKEEP]
    assert item.status == "open" and first.id in item.claimed_by
    assert await ops.start_upkeep(f.store.get_product(p.id)) is None, "the same fix is not started again"
    assert await ops.start_upkeep(f.store.get_product(p.id)) is None
    said = [e for e in f.store.list_events(first.id) if "left for the owner" in e.message]
    assert len(said) == 1 and item.id in said[0].message, "said once, not on every cycle"
    changes = f.store.list_changes(p.id)
    assert [c.source for c in changes].count(ChangeSource.schedule) == 1


async def test_a_backup_never_grows_past_its_limit(make_factory):
    """E2E audit 2026-11-17 (reliability): the dump comes from the app's container, which agent-written
    code controls; an unbounded one could fill the factory's own data volume."""
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ok = await ops.backup(p)
    assert ok.ok
    dump = next(c for c in ex.calls if "pg_dump" in c)
    cap = f.manager.cfg.operate.max_backup_mb * 1024 * 1024
    assert dump.startswith(f"ulimit -f {cap // 512}; "), "the redirect's file is capped by the shell"
    f.manager.cfg.operate.max_backup_mb = 1
    ex.data["app"] = "x" * (2 * 1024 * 1024)
    rec = await ops.backup(f.store.get_product(p.id))
    assert not rec.ok and "operate.max_backup_mb (1 MB)" in rec.summary
    assert [x.name for x in ops.backup_dir(p).glob("*.backup")] == [ok.data["file"]], "the oversized file is removed"


async def test_one_app_that_doesnt_answer_doesnt_delay_the_others_readings(make_factory, monkeypatch):
    """E2E audit 2026-11-17 (reliability): readings were taken one product after another, so a few apps
    waiting out their timeouts pushed every other product's reading (and alert) past the interval."""
    import time

    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    many = [p.model_copy(update={"id": f"p{i}"}) for i in range(6)]
    monkeypatch.setattr(f.store, "list_products", lambda *a, **k: many)
    monkeypatch.setattr(ops, "why_not", lambda product: None)
    monkeypatch.setattr(ops, "_deploying", lambda product: True)  # readings only, no maintenance

    async def slow(product, now=None):  # noqa: ANN001, ANN202
        await asyncio.sleep(0.4)  # an app waiting out its timeout

    monkeypatch.setattr(ops, "watch", slow)
    start = time.monotonic()
    await ops.tick(wait=True)
    assert time.monotonic() - start < 1.2, "the six readings were taken side by side"


# ---------------------------------------------------------------- schedule --
async def test_one_pass_does_what_is_due_and_no_more(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ex.scan_outputs["uv pip list --outdated"] = "[]"
    now = datetime.now(UTC) + timedelta(minutes=1)
    await ops.tick(now, wait=True)
    kinds = [r.kind for r in reversed(f.store.list_operations(p.id))]
    assert kinds == ["backup", "restore", "upkeep"], "backup, then the drill that proves it, then upkeep"
    assert len(f.store.list_health_samples(p.id)) == 1
    await ops.tick(now + timedelta(seconds=20), wait=True)
    assert len(f.store.list_health_samples(p.id)) == 1, "a reading is taken once per probe interval"
    await ops.tick(now + timedelta(minutes=2), wait=True)
    assert len(f.store.list_health_samples(p.id)) == 2
    assert len(f.store.list_operations(p.id)) == 3, "nothing is due again yet"


async def test_a_failed_step_is_tried_again_after_an_hour_not_a_day(make_factory):
    f, ex, p = await delivered(make_factory)
    ops = f.manager.operations
    ex.fail_on.append("pg_dump")
    assert not (await ops.backup(p)).ok
    now = datetime.now(UTC)
    assert not ops._due(p, "backup", 24, now + timedelta(minutes=30))
    assert ops._due(p, "backup", 24, now + timedelta(minutes=61))


async def test_the_watch_runs_in_a_live_factory_only(make_factory):
    f = make_factory()
    f.manager.operations.start()
    assert f.manager.operations._task is None, "dry-run has nothing to read"


# ------------------------------------------------------ resume after restart --
async def _interrupted_at(f, monkeypatch, handler: str):  # noqa: ANN001, ANN202
    """A change cut off by a factory restart while it was at `handler`."""
    reached = asyncio.Event()
    real = stations_mod.STATIONS[handler]

    async def stuck(ctx):  # noqa: ANN001, ANN202
        reached.set()
        await asyncio.sleep(3600)

    monkeypatch.setitem(stations_mod.STATIONS, handler, stuck)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    await asyncio.wait_for(reached.wait(), 60)
    await f.manager.shutdown()  # the engine stops in the middle of the station
    monkeypatch.setitem(stations_mod.STATIONS, handler, real)
    return change


async def test_with_recover_auto_a_restart_resumes_the_change_at_its_station(make_factory, monkeypatch):
    f = make_factory()
    f.manager.cfg.policies.recover.mode = "auto"
    change = await _interrupted_at(f, monkeypatch, "test")
    assert f.manager.recover_on_startup() == [change.id]
    assert await wait_run(f, change.id, 90) == ChangeStatus.awaiting_feedback
    events = f.store.list_events(change.id)
    finished = [e.station for e in events if e.kind == "station_finished"]
    assert finished.count("requirements") == 1 and finished.count("implement") == 1, "finished stations are not redone"
    assert any(e.message.startswith("resumed after the factory restarted, at test (1 of 2") for e in events)
    assert f.store.get_change(change.id).auto_resumed == {}, "the station finished: its restarts no longer count"


async def test_a_station_that_keeps_being_cut_off_waits_for_a_person(make_factory, monkeypatch):
    f = make_factory()
    f.manager.cfg.policies.recover.mode = "auto"
    change = await _interrupted_at(f, monkeypatch, "test")
    stored = f.store.get_change(change.id)
    stored.auto_resumed = {"test": 2}
    f.store.save_change(stored)
    f.manager.recover_on_startup()
    after = f.store.get_change(change.id)
    assert after.status == ChangeStatus.interrupted and not f.manager.is_active(change.id)
    assert (
        "not resumed automatically: the factory restarted 3 times in a row"
        in f.store.list_events(change.id)[-1].message
    )
    f.manager.resume(change.id)  # a person looked
    assert await wait_run(f, change.id, 90) == ChangeStatus.awaiting_feedback


async def test_recover_manual_still_waits_for_a_person(make_factory, monkeypatch):
    f = make_factory()
    change = await _interrupted_at(f, monkeypatch, "test")
    f.manager.recover_on_startup()
    assert f.store.get_change(change.id).status == ChangeStatus.interrupted
    assert not f.manager.resume_after_restart(change.id)


# -------------------------------------------------------------------- pack --
def _operate(**data) -> dict:  # noqa: ANN003
    base = {
        "workload": "statefulset/{release}-postgres",
        "backup": "pg_dump -d {database}",
        "restore": "pg_restore -d {database}",
        "fingerprint": "psql -d {database} -c x",
    }
    return {"data": {**base, **data}}


def test_data_commands_must_name_the_database_so_a_drill_never_acts_on_production():
    assert packs.PackOperate.model_validate(_operate()).data.database == "app"
    with pytest.raises(ValueError, match="data.restore must name {database}"):
        packs.PackOperate.model_validate(_operate(restore="pg_restore -d app"))
    with pytest.raises(ValueError, match="come together"):
        packs.PackOperate.model_validate(_operate(scratch_create="createdb {database}"))
    with pytest.raises(ValueError, match="a restore drill drops a database of that name"):
        packs.PackOperate.model_validate(_operate(database="restore_drill"))  # production would be the scratch
    with pytest.raises(ValueError):
        packs.PackOperate.model_validate(_operate(workload="statefulset/x; rm -rf /"))
    with pytest.raises(ValueError):
        packs.PackOperate.model_validate({"availability": {"success": 0.2}})


MASKED = ["npm outdated --json || true", "npm outdated --json ||:", "npm outdated --json; true"]


@pytest.mark.parametrize("masked", MASKED)
def test_a_dependency_check_may_not_hide_its_exit_status(masked):
    with pytest.raises(ValueError, match="must not mask its exit status"):
        packs.PackUpkeep.model_validate({"outdated": masked, "format": "npm"})
    ok = packs.PackUpkeep.model_validate({"outdated": "npm outdated --json", "format": "npm", "answers": [0, 1]})
    assert ok.answers == [0, 1]
    assert packs.PackUpkeep.model_validate({"outdated": "uv pip list --outdated", "format": "pip"}).answers == [0]


def test_both_stack_packs_declare_how_their_products_are_run(make_factory):
    cfg = make_factory().manager.cfg
    for name in ("fastapi-service", "nextjs-web"):
        op = cfg.blueprints[name].operate
        assert op is not None and op.data is not None and op.upkeep is not None and op.metrics is not None, name
        assert op.availability.success == 0.99 and op.data.scratch_create and op.data.scratch_drop
        assert "information_schema.tables" in op.data.fingerprint and "{database}" in op.data.fingerprint
    assert cfg.blueprints["nextjs-web"].operate.upkeep.format == "npm"
    assert cfg.blueprints["nextjs-web"].operate.metrics.path == "/api/metrics"


async def test_npm_products_are_checked_with_npm(make_factory):
    f, ex, p = await delivered(make_factory, blueprint="nextjs-web")
    ex.scan_outputs["npm outdated --json"] = NPM_OUTDATED
    rec = await f.manager.operations.upkeep(p)
    assert rec.data["dependencies"] == {"behind": 2, "major": 1, "ledger": "new"}


# --------------------------------------------------------------------- API --
async def test_operations_over_the_api_for_the_owner_the_creator_or_an_admin(make_factory):
    f = make_factory(OpsExecutor())
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        created = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()
        pid, cid = created["product"]["id"], created["product"]["latest_change_id"]
        assert await wait_run(f, cid, 90) == ChangeStatus.awaiting_feedback
        for url, body in (
            ("backup", None), ("restore-drill", None), ("upkeep", None), ("health-check", None),
            ("availability", {"success": 0.999}),
        ):  # fmt: skip
            r = await c.post(f"/api/products/{pid}/{url}", json=body, headers=OTHER)
            assert r.status_code == 403, f"{url}: {r.status_code}"
        assert (await c.post(f"/api/products/{pid}/owner", json={"owner": "bob"}, headers=MEMBER)).status_code == 403
        assert (await c.post(f"/api/products/{pid}/owner", json={"owner": "bob"}, headers=ADMIN)).status_code == 200
        r = await c.post(f"/api/products/{pid}/backup", headers=OTHER)  # bob is the named owner now
        assert r.status_code == 200 and r.json()["ok"] and r.json()["by"] == "bob"
        assert (await c.post(f"/api/products/{pid}/restore-drill", headers=MEMBER)).json()["ok"], (
            "the creator still can"
        )
        r = await c.post(f"/api/products/{pid}/availability", json={"success": 0.9}, headers=OTHER)
        assert r.status_code == 422 and "not loosen it" in r.json()["detail"]
        r = await c.post(f"/api/products/{pid}/availability", json={"success": 0.999}, headers=OTHER)
        assert r.status_code == 200 and r.json()["availability"]["chosen_by"] == "product"
        view = (await c.get(f"/api/products/{pid}/operations", headers=OTHER)).json()
        assert (view["owner"], view["owner_named"], view["live"], view["restore_proven"]) == ("bob", True, True, True)
        assert view["availability"]["target"].startswith("99.9% of health checks")
        assert [r["kind"] for r in view["records"]] == ["restore", "backup", "owner"]
        r = await c.post(f"/api/products/{pid}/health-check", headers=OTHER)
        assert r.status_code == 200 and r.json()["availability"]["last_at"]
    await ctx.__aexit__(None, None, None)


async def test_ready_for_production_once_it_is_watched_and_a_restore_is_proven(make_factory):
    f, ex, p = await delivered(make_factory)
    # pilot first: a named owner and a rollback proven on this product (ADR-0046, tested in test_rollback)
    f.store.add_event(p.latest_change_id, EventKind.decision, "production restored", data={"rollback": {"ok": True}})
    app, ctx, c = await _client(f)
    async with c:
        await c.post(f"/api/products/{p.id}/owner", json={"owner": "priya"})
        r = (await c.get(f"/api/products/{p.id}/ready-for")).json()
        assert (r["tier"], r["next_tier"]) == ("pilot", "production")
        assert r["needs"] == [
            "an alert when it is missed (the factory isn't watching this product)",
            "a tested restore of its data",
        ], "the target is the stack's; nothing else is claimed before it is measured"
        await c.post(f"/api/products/{p.id}/health-check")
        await c.post(f"/api/products/{p.id}/backup")
        r = (await c.get(f"/api/products/{p.id}/ready-for")).json()
        assert r["needs"] == ["a tested restore of its data"], "a backup that was never restored proves nothing"
        assert (await c.post(f"/api/products/{p.id}/restore-drill")).json()["ok"]
        r = (await c.get(f"/api/products/{p.id}/ready-for")).json()
        assert (r["tier"], r["next_tier"], r["needs"]) == ("production", None, [])
        assert any(x.startswith("availability target: 99% of health checks") for x in r["proven"])
        assert any(x.startswith("restore proven (drill on ") for x in r["proven"])
    await ctx.__aexit__(None, None, None)
