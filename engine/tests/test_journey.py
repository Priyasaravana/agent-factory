"""The golden journey, scripted (roadmap: "a journey acceptance test grows with each milestone").

One product goes through the journey the way a person drives it: through the HTTP API, on the
current Next.js pack, with the agents and commands faked and git real. Each milestone adds its
steps here when it lands, so a step that works never silently breaks. Today: J1, launch done well
(request → align with Design decisions set to ask → modules and sign-in → e2e before deploy →
readiness → publish).

When this fails, the assertion message names the journey step that broke. The detailed rules of
each step have their own tests (test_decisions, test_app_sign_in, test_preview_e2e, test_no_progress,
test_secret_refs); this one checks they still fit together, in order, on one product."""

from __future__ import annotations

import json

from conftest import PRODUCT, wait_run
from test_api import _client
from test_app_sign_in import PASSWORD, RecordingExecutor, _secret_json

from agent_factory.agents import FakeAgentRunner
from agent_factory.engine.stations import ADMIN_PASSWORD_ENV
from agent_factory.models import ChangeStatus, EventKind

WF = "new-product"
ORG = "acme-apps"  # stands for the dedicated GitHub org in `policies.publish.owner`
TOKEN = "ghp_" + "j" * 36  # low entropy: no secret-scan noise
OPEN_QUESTION = "Should bookmarks be private to each user?"
STATIONS = [
    "requirements", "design", "implement", "test", "preview-e2e", "quality-gate", "code-scan", "dependency-scan",
    "config-scan", "code-review", "change-risk", "build", "deploy", "acceptance", "handover",
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
