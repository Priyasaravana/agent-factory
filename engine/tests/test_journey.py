"""The golden journey, scripted (roadmap: "a journey acceptance test grows with each milestone").

One product goes through the journey the way a person drives it: through the HTTP API, on the
current Next.js pack, with the agents and commands faked and git real. Each milestone adds its
steps here when it lands, so a step that works never silently breaks. Today: J1, launch done well
(request → align with Design decisions set to ask → modules and sign-in → e2e before deploy →
readiness → publish), J3, team repo (an assessment item → a pull request → red CI fixed on the
same pull request → the merge closes the change), on a team repo with a local origin and the repo
host's answers faked, and J4 + J5, run and maintain, then end of life (an owner and a target → the
watch → backup and a proven restore → an injected fault becomes an incident, a fix change that a
factory restart interrupts and resumes, and a redeploy → upkeep → retire → the history from the
first request to the decommission record), with the live app and its database faked.

When this fails, the assertion message names the journey step that broke. The detailed rules of
each step have their own tests (test_decisions, test_app_sign_in, test_preview_e2e, test_no_progress,
test_secret_refs); this one checks they still fit together, in order, on one product."""

from __future__ import annotations

import asyncio
import json

from conftest import PRODUCT, wait_run
from test_access import ADMIN
from test_api import _client
from test_app_sign_in import PASSWORD, RecordingExecutor, _secret_json
from test_ci_fix import SLUG, _git, _red_pr
from test_operate import PIP_OUTDATED
from test_operations import OpsExecutor

from agent_factory.agents import FakeAgentRunner
from agent_factory.engine import stations as stations_mod
from agent_factory.engine.stations import ADMIN_PASSWORD_ENV
from agent_factory.models import ChangeSource, ChangeStatus, EventKind

WF = "new-product"
ORG = "acme-apps"  # stands for the dedicated GitHub org in `policies.publish.owner`
TOKEN = "ghp_" + "j" * 36  # low entropy: no secret-scan noise
OPEN_QUESTION = "Should bookmarks be private to each user?"
STATIONS = [
    "requirements", "design", "implement", "test", "preview-e2e", "quality-gate", "code-scan", "dependency-scan",
    "config-scan", "code-review", "change-risk", "build", "deploy", "acceptance", "promote", "handover",
]  # fmt: skip


def _finished(f, change_id: str) -> list[str]:
    return [e.station for e in f.store.list_events(change_id) if e.kind == EventKind.station_finished]


def _messages(f, change_id: str) -> list[str]:
    return [e.message for e in f.store.list_events(change_id)]


async def test_j1_launch_done_well(make_factory, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    ex = RecordingExecutor(scan_outputs={"get secret": _secret_json()})  # the chart made the first admin
    ex.scan_outputs["gh repo view"] = f"https://github.com/{ORG}/bookmarks-service\n"
    agents = FakeAgentRunner(open_questions=[OPEN_QUESTION])
    f = make_factory(ex, agents)
    f.manager.cfg.policies.publish.owner = ORG
    app, ctx, c = await _client(f)
    async with c:
        # J1.0 The lane asks a person: "Design decisions → Ask a person" in the workflow editor.
        r = await c.patch(f"/api/workflows/{WF}/draft/settings", json={"decisions": "ask"})
        assert r.status_code == 200, f"J1.0 set Design decisions to ask: {r.text}"
        r = await c.post(f"/api/workflows/{WF}/draft/publish", json={"note": "ask a person"})
        assert r.status_code == 201 and not r.json().get("evaluation"), f"J1.0 publish the lane: {r.text}"

        # J1.1 Launch: a person submits the request on the Next.js pack; the first change starts.
        r = await c.post(
            "/api/products",
            json={"title": PRODUCT.title, "requirements": PRODUCT.requirements, "blueprint": "nextjs-web"},
        )
        assert r.status_code == 201, f"J1.1 create the product: {r.text}"
        pid, cid = r.json()["product"]["id"], r.json()["changes"][0]["id"]

        # J1.2 Design: intake doesn't stop for the open question; the architect proposes an answer, and
        # the change waits for a person on the review page, listed under "Needs you".
        assert await wait_run(f, cid, 60) == ChangeStatus.awaiting_approval, "J1.2 wait at the review gate"
        assert _finished(f, cid) == ["requirements", "design"], "J1.2 nothing is built before the review"
        view = (await c.get(f"/api/changes/{cid}/decisions")).json()
        assert (view["mode"], view["pending"], view["problems"]) == ("ask", True, []), "J1.2 the sheet is asked"
        assert [q["id"] for q in view["questions"]] == [
            "authentication",
            "sign-up",
            "authorisation",
            "data-store",
            "look",
        ]
        [oq] = view["open_questions"]
        assert (oq["question"], oq["decided_by"]) == (OPEN_QUESTION, "architect (proposed)"), "J1.2 open question"
        needs = (await c.get("/api/attention", params={"product_id": pid})).json()
        assert [(n["kind"], n["blocking"]) for n in needs] == [("align", True)], "J1.2 'Needs you' shows the review"

        # J1.3 Align: choices, an answer in the person's own words and a comment go to the architect at once.
        r = await c.post(
            f"/api/changes/{cid}/align",
            json={
                "answers": [{"question": "look", "choice": "warm", "reason": "a friendly reading app"}],
                "open_answers": [{"id": "Q1", "answer": "Yes, each user sees only their own"}],
                "message": "Keep sign-up closed for v1.",
            },
        )
        assert r.status_code == 200, f"J1.3 align: {r.text}"
        assert await wait_run(f, cid, 60) == ChangeStatus.awaiting_approval, "J1.3 the architect replied"
        view = (await c.get(f"/api/changes/{cid}/decisions")).json()
        assert [t["role"] for t in view["discussion"]] == ["person", "architect"], "J1.3 one architect turn"
        assert next(a for a in view["answers"] if a["question"] == "look")["choice"] == "warm"
        assert view["open_questions"][0]["answer"] == "Yes, each user sees only their own"
        wt = f.manager.ws.change_dir(cid)
        assert (wt / "docs/adr/0005-look.md").is_file(), "J1.3 the choices become ADRs in the product repo"

        # J1.4 Approve, then build to delivery. The person's steps above went through the API's preflight,
        # which a test can't pass (no model credential, no cluster tools); from here the factory's own
        # steps run as live, so publish takes its real path. Agents and commands stay fakes.
        r = await c.post(f"/api/changes/{cid}/spec/approve", json={})
        assert r.status_code == 200, f"J1.4 approve: {r.text}"
        f.settings.factory_mode = "live"
        assert await wait_run(f, cid, 90) == ChangeStatus.awaiting_feedback, "J1.4 delivered"
        assert _finished(f, cid)[-len(STATIONS) + 2 :] == STATIONS[2:], "J1.4 every station, in order, once"
        messages = _messages(f, cid)

        # J1.5 Modules and sign-in, measured: Level 3 with the module signals passing.
        readiness = json.loads((f.manager.ws.data_dir / "artifacts" / cid / "readiness.json").read_text())
        signals = {s["id"]: s["ok"] for s in readiness["signals"]}
        assert readiness["level"] == 3, f"J1.5 Level 3: missing {[k for k, ok in signals.items() if not ok]}"
        assert signals["modules_isolated"] and signals["module_contracts"], "J1.5 modules behind ports"

        # J1.6 E2E before deploy (against the preview), then live, signed in as the chart's first admin.
        seq = _finished(f, cid)
        assert seq.index("preview-e2e") < seq.index("build") < seq.index("deploy") < seq.index("acceptance")
        live = [env for cmd, env in ex.envs if cmd.startswith("make e2e BASE_URL=http://dind")]
        assert live and live[-1].get(ADMIN_PASSWORD_ENV) == PASSWORD, "J1.6 live e2e signs in"
        assert any(m.startswith("the sandbox reaches the app at http://dind") for m in messages), "J1.6 probe"
        assert "traceability: 2/2 requirements verified live by hidden scenarios" in messages, "J1.6 verified"
        assert PASSWORD not in json.dumps([[e.message, e.data] for e in f.store.list_events(cid)]), "redacted"

        # J1.7 Publish to the org, then the product is live and its feedback gate is open.
        assert any(f"gh repo create {ORG}/bookmarks-service --private" in cmd for cmd in ex.calls), "J1.7 org"
        product = (await c.get(f"/api/products/{pid}")).json()["product"]
        assert product["repo_url"] == f"https://github.com/{ORG}/bookmarks-service", "J1.7 published"
        assert product["app_url"] and product["latest_status"] == ChangeStatus.awaiting_feedback.value
        assert "used secret env://GITHUB_TOKEN for publish" in messages and TOKEN not in json.dumps(messages)
        needs = (await c.get("/api/attention", params={"product_id": pid})).json()
        assert needs == [], "J1.7 nothing blocks a person once delivered"

        # Sealed evidence: the demo record of the run.
        evidence = f.manager.ws.data_dir / "artifacts" / cid
        for name in ("decisions.json", "discussion.json", "readiness.json", "traceability.json"):
            assert (evidence / name).is_file(), f"evidence: {name}"
        assert messages[-1].startswith("evidence sealed:"), "the run ends sealed"
    await ctx.__aexit__(None, None, None)


async def test_j3_team_repo(make_factory, tmp_path, monkeypatch):
    """J3 "Done when": an assessment item becomes a PR, CI fails once and is fixed on the same PR, and
    the merge closes the change. The rules of each step: test_repo_changes, test_pr_loop, test_ci_fix."""
    # J3.0 A team repo is onboarded and assessed; a person turns one open item into a change, which
    # leaves the factory only as a pull request. Its CI then fails on the host.
    f, ex, host, change, item_id = await _red_pr(make_factory, tmp_path, monkeypatch)
    red = host.head()
    assert change.pr_url == f"https://github.com/{SLUG}/pull/7", "J3.0 the change is a pull request"
    assert sum(x.startswith("gh pr create") for x in ex.calls) == 1, "J3.0 one pull request"
    assert not any(x.startswith("gh pr merge") for x in ex.calls), "J3.0 the factory never merges"
    f.settings.auth_mode = "gateway"  # as shipped: people reach the engine only through the gateway
    app, ctx, c = await _client(f)
    async with c:
        # J3.1 The factory follows the pull request: it reads the red CI and says what a person can do.
        r = await c.post(f"/api/changes/{change.id}/pull-request/refresh", headers=ADMIN)
        assert r.status_code == 200 and r.json()["pull_request"]["checks"] == "failing", f"J3.1 red CI read: {r.text}"
        detail = (await c.get(f"/api/changes/{change.id}", headers=ADMIN)).json()
        assert [a["kind"] for a in detail["actions"]] == ["fix_ci"], "J3.1 'Needs you' offers Fix CI"
        assert detail["change"]["pull_request"]["failures"], "J3.1 what the failing check reported is shown"

        # J3.2 Fix CI: a change that continues the same pull request from the failing commit.
        r = await c.post(f"/api/changes/{change.id}/ci-fix", headers=ADMIN)
        assert r.status_code == 200, f"J3.2 start the fix: {r.text}"
        fix_id = r.json()["id"]
        assert await wait_run(f, fix_id, 60) == ChangeStatus.awaiting_feedback, "J3.2 the fix is delivered"
        fix = f.store.get_change(fix_id)
        assert fix.source == ChangeSource.ci and fix.continues, "J3.2 the fix continues the change"
        assert (fix.continues.change_id, fix.continues.from_sha) == (change.id, red), "J3.2 from the failing commit"
        wt = f.manager.ws.change_dir(fix_id)
        assert _git(wt, "rev-parse", "HEAD~1") == red, "J3.2 built on the pull request's head"
        pushes = [x for x in ex.calls if "git push" in x and f"HEAD:refs/heads/{host.branch}" in x]
        assert pushes and all("--force" not in x and ":+" not in x for x in pushes), "J3.2 pushed fast-forward"
        assert sum(x.startswith("gh pr create") for x in ex.calls) == 1, "J3.2 the same pull request, no second"

        # J3.3 CI passes on the fix's commit: the factory reads it and has nothing more to offer.
        _git(host.origin, "fetch", "-q", str(wt), f"run/{fix_id}:{host.branch}")  # the push, on the origin
        host.ci[host.head()] = "success"
        r = await c.post(f"/api/changes/{change.id}/pull-request/refresh", headers=ADMIN)
        pr = r.json()["pull_request"]
        assert (pr["head_sha"], pr["checks"]) == (host.head(), "passing"), f"J3.3 green CI read: {r.text}"
        detail = (await c.get(f"/api/changes/{change.id}", headers=ADMIN)).json()
        assert not detail["ci_fix"]["offer"], "J3.3 no further fix is offered"

        # J3.4 The team merges on the host: the merge closes the change and its fix; the item awaits
        # verification (it is fixed only when its check really runs again).
        host.merged = True
        r = await c.post(f"/api/changes/{change.id}/pull-request/refresh", headers=ADMIN)
        assert r.status_code == 200 and r.json()["outcome"]["state"] == "merged", f"J3.4 merged: {r.text}"
        fix = f.store.get_change(fix_id)
        assert fix.outcome and fix.outcome.state == "merged", "J3.4 the fix takes the pull request's outcome"
        item = next(i for i in f.store.list_work_items(change.product_id) if i.id == item_id)
        assert item.status == "in_progress", "J3.4 the item awaits verification"
        assert change.id not in [x.id for x in f.manager.pr_watcher.followed()], "J3.4 the following stops"
    await ctx.__aexit__(None, None, None)


async def _read_health(c, pid: str, times: int = 5) -> dict:  # noqa: ANN001
    """The Operate watch's reading, `times` in a row (the watch takes one a minute)."""
    view: dict = {}
    for _ in range(times):
        r = await c.post(f"/api/products/{pid}/health-check")
        assert r.status_code == 200, f"read the product's health: {r.text}"
        view = r.json()
    return view


async def test_j4_j5_run_maintain_and_retire(make_factory, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    ex = OpsExecutor()  # stands for the live app and its database
    f = make_factory(ex)
    f.manager.cfg.policies.recover.mode = "auto"  # J4: a restart resumes changes at their station
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post("/api/products", json={"title": PRODUCT.title, "requirements": PRODUCT.requirements})
        assert r.status_code == 201, f"J4.0 create the product: {r.text}"
        pid, cid = r.json()["product"]["id"], r.json()["changes"][0]["id"]
        assert await wait_run(f, cid, 90) == ChangeStatus.awaiting_feedback, "J4.0 delivered and live"
        # dry-run simulates publish (J1.7 proves the real path): the product's repository is where J1 put it
        published = f.store.get_product(pid)
        published.repo_url = f"https://github.com/{ORG}/bookmarks-service"
        f.store.save_product(published)

        # J4.1 Owner: an admin names who is accountable; from then on product items go to them.
        r = await c.post(f"/api/products/{pid}/owner", json={"owner": "priya"})
        assert r.status_code == 200, f"J4.1 name the owner: {r.text}"
        ops = (await c.get(f"/api/products/{pid}/operations")).json()
        assert (ops["owner"], ops["owner_named"], ops["live"]) == ("priya", True, True), "J4.1 the owner runs it"

        # J4.2 Target: the stack's default availability target, which the product may only tighten.
        assert ops["availability"]["target"].startswith("99% of health checks over 30 minutes"), "J4.2 pack default"
        r = await c.post(f"/api/products/{pid}/availability", json={"success": 0.9})
        assert r.status_code == 422, "J4.2 a product can't loosen its stack's target"
        r = await c.post(f"/api/products/{pid}/availability", json={"success": 0.995})
        assert r.status_code == 200 and r.json()["availability"]["chosen_by"] == "product", "J4.2 tightened"

        # J4.3 The watch: readings of the live app, judged against the target; healthy files nothing.
        ops = await _read_health(c, pid)
        assert ops["availability"]["state"] == "ok" and ops["availability"]["watching"], "J4.3 within target"
        assert ops["incidents"] == [], "J4.3 no incident while healthy"

        # J4.4 Backup and a tested restore: a backup counts only once a drill restored it and the data matched.
        r = await c.post(f"/api/products/{pid}/backup")
        assert r.status_code == 200 and r.json()["ok"], f"J4.4 backup: {r.text}"
        r = await c.post(f"/api/products/{pid}/restore-drill")
        assert r.status_code == 200 and r.json()["ok"], f"J4.4 restore drill: {r.text}"
        assert "restored data matches the backup" in r.json()["summary"], "J4.4 the engine compared the data"
        ops = (await c.get(f"/api/products/{pid}/operations")).json()
        assert ops["restore_proven"] and not ops["restore_overdue"], "J4.4 the restore is proven"

        # J4.5 An injected fault: the app stops answering; the breach is the alert that opens an incident
        # for the owner, on "Needs you" and on the Next iteration card.
        ex.down = True
        ops = await _read_health(c, pid)
        assert ops["availability"]["state"] == "breached", "J4.5 the breach is seen"
        [incident] = ops["incidents"]
        assert incident["status"] == "open" and incident["severity"] == "high", "J4.5 one incident"
        needs = (await c.get("/api/attention", params={"product_id": pid})).json()
        assert [(n["kind"], n["owner"], n["blocking"]) for n in needs] == [("incident", "priya", True)], (
            "J4.5 'Needs you' routes the incident to the owner"
        )
        card = (await c.get(f"/api/products/{pid}/next-iteration")).json()
        assert [i["id"] for i in card["incidents"]] == [incident["id"]] and card["can_start"], "J4.5 on the card"

        # J4.6 "Make this change": the incident becomes a bug-fix change, no terminal. The factory restarts
        # in the middle of it, and the change goes on at its station instead of being lost.
        reached, real = asyncio.Event(), stations_mod.STATIONS["test"]

        async def cut_off(ctx):  # noqa: ANN001, ANN202
            reached.set()
            await asyncio.sleep(3600)

        monkeypatch.setitem(stations_mod.STATIONS, "test", cut_off)
        r = await c.post(f"/api/products/{pid}/feedback", json={"text": "Fix the incident.", "items": [incident["id"]]})
        assert r.status_code == 201 and r.json()["kind"] == "bug", f"J4.6 the fix change starts: {r.text}"
        fix = r.json()["id"]
        await asyncio.wait_for(reached.wait(), 60)
        await f.manager.shutdown()  # the factory stops mid-station
        monkeypatch.setitem(stations_mod.STATIONS, "test", real)
        assert f.manager.recover_on_startup() == [fix], "J4.6 the restart finds the change"
        assert await wait_run(f, fix, 90) == ChangeStatus.awaiting_feedback, "J4.6 resumed and delivered"
        assert any(m.startswith("resumed after the factory restarted, at test") for m in _messages(f, fix)), "J4.6"
        assert _finished(f, fix).count("implement") == 1, "J4.6 finished stations are not run again"

        # J4.7 The redeploy is healthy: the watch closes the incident, because the check really ran again.
        ex.down = False
        ops = await _read_health(c, pid)
        assert ops["availability"]["state"] == "ok" and ops["incidents"] == [], "J4.7 within target again"
        ledger_items = (await c.get(f"/api/products/{pid}/work-items")).json()
        closed = next(i for i in ledger_items if i["id"] == incident["id"])
        assert (closed["status"], closed["resolved_by"]) == ("fixed", fix), (
            "J4.7 fixed by the change, verified by the watch"
        )
        needs = (await c.get("/api/attention", params={"product_id": pid})).json()
        assert needs == [], "J4.7 nothing waits on the owner"

        # J4.8 Upkeep: the scheduled check finds dependencies that fell behind; they wait on the card.
        ex.scan_outputs["uv pip list --outdated"] = PIP_OUTDATED
        r = await c.post(f"/api/products/{pid}/upkeep")
        assert r.status_code == 200 and r.json()["data"]["dependencies"]["behind"] == 2, f"J4.8 upkeep: {r.text}"
        card = (await c.get(f"/api/products/{pid}/next-iteration")).json()
        assert [i["title"] for i in card["upkeep"]] == ["2 dependencies are behind (1 by a major version)"], "J4.8"

        # J5.1 Retire: the owner retires the product by name; data exported, app removed, repository
        # archived, record sealed, in that order.
        before = len(ex.calls)
        r = await c.post(f"/api/products/{pid}/retire", json={"reason": "replaced", "confirm": "wrong"})
        assert r.status_code == 422, "J5.1 retiring needs the product's name"
        r = await c.post(f"/api/products/{pid}/retire", json={"reason": "replaced by v2", "confirm": PRODUCT.title})
        assert r.status_code == 200, f"J5.1 retire: {r.text}"
        retirement = r.json()["retirement"]
        assert [(s["id"], s["status"]) for s in retirement["steps"]] == [
            ("freeze", "done"), ("export", "done"), ("undeploy", "done"), ("archive-repo", "done"), ("record", "done"),
        ], "J5.1 every step done"  # fmt: skip
        assert retirement["owner"] == "priya" and r.json()["archived_at"], "J5.1 names the owner; the app is gone"
        calls = ex.calls[before:]
        at = [next(i for i, cmd in enumerate(calls) if n in cmd) for n in ("pg_dump", "uninstall", "gh repo archive")]
        assert at == sorted(at), "J5.1 export, then undeploy, then archive"
        assert f"gh repo archive {ORG}/bookmarks-service --yes" in calls and TOKEN not in json.dumps(calls), "J5.1"

        # J5.2 Frozen: nothing new starts on it, and nobody is asked to act on it.
        r = await c.post(f"/api/products/{pid}/feedback", json={"text": "one more thing"})
        assert r.status_code == 409 and "retired" in r.json()["detail"], "J5.2 frozen"
        assert (await c.get("/api/attention", params={"product_id": pid})).json() == [], "J5.2 nothing waits"
        assert (await c.post(f"/api/products/{pid}/health-check")).status_code == 422, "J5.2 no longer watched"

        # J5.3 The full history, from the first request to the decommission, shown from the evidence.
        history = (await c.get(f"/api/products/{pid}/history")).json()
        kinds = [e["kind"] for e in history["entries"]]
        assert kinds[0] == "requested" and kinds[-1] == "decommissioned" and history["retired"], "J5.3 start to end"
        for kind in ("change", "delivered", "owner", "backup", "restore", "incident", "upkeep", "retire"):
            assert kind in kinds, f"J5.3 the history tells of: {kind}"
        delivered = [e for e in history["entries"] if e["kind"] == "delivered"]
        assert len(delivered) == 2 and all(e["evidence"] for e in delivered), "J5.3 each delivery's sealed evidence"
        record = (await c.get(f"/api/products/{pid}/decommission")).json()
        assert record["intact"] and record["manifest_sha256"] == retirement["record_sha256"], "J5.3 sealed, intact"
        assert record["record"]["totals"] == {"changes": 2, "deliveries": 2}, "J5.3 the record counts the changes"
        assert all(ch["evidence"]["intact"] for ch in record["record"]["changes"]), "J5.3 every change's evidence"
        retired = (await c.get("/api/outcomes")).json()["retired"]
        assert [(x["product_id"], x["record_sha256"]) for x in retired] == [(pid, retirement["record_sha256"])], (
            "J5.3 the decommission record is on Outcomes"
        )
    await ctx.__aexit__(None, None, None)
