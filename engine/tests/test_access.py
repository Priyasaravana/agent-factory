"""Who may change what (ADR-0012, ADR-0022). Every mutating action is classified:
either admin-only (it changes how the factory behaves for everyone) or allowed to
any signed-in member. A new action that is neither fails this test, so access is
always a deliberate decision."""

from __future__ import annotations

import asyncio
import re

import pytest
from conftest import PRODUCT, wait_run
from test_api import _client

from agent_factory import actions
from agent_factory.identity import requires_admin
from agent_factory.models import ChangeStatus

ADMIN = {"X-Auth-User": "saravana", "X-Auth-Role": "admin"}
MEMBER = {"X-Auth-User": "priya", "X-Auth-Role": "member"}
OTHER = {"X-Auth-User": "bob", "X-Auth-Role": "member"}

# mutating actions any signed-in member may call; those that steer an existing
# order are further limited to its creator or an admin (STEERING)
MEMBER_ACTIONS = {
    "create_product",
    "onboard_repo",
    "submit_feedback",
    "answer_questions",
    "resume_change",
    "cancel_change",
    "approve_spec",
    "request_spec_changes",
    "edit_spec",
    "send_back_risk",
    "archive_product",
    "run_preflight",
    "log_decision",
}
STEERING = {
    "submit_feedback",
    "answer_questions",
    "resume_change",
    "cancel_change",
    "approve_spec",
    "request_spec_changes",
    "edit_spec",
    "send_back_risk",
    "archive_product",
}
MUTATING = [s for s in actions.REGISTRY.values() if s.method not in {"GET", "HEAD", "OPTIONS"}]


def test_every_mutating_action_is_classified():
    admin_only = {s.name for s in MUTATING if requires_admin(s.method, s.path)}
    member = {s.name for s in MUTATING} - admin_only
    assert member == MEMBER_ACTIONS, (
        f"classify new actions: admin-only paths live under /api/workflows or /api/skills; "
        f"unexpected member actions: {sorted(member - MEMBER_ACTIONS)}; gone: {sorted(MEMBER_ACTIONS - member)}"
    )
    assert {
        "publish_draft",
        "activate_workflow_version",
        "accept_learning",
        "install_skill",
        "approve_risk",
    } <= admin_only


@pytest.mark.parametrize("spec", [s for s in MUTATING if requires_admin(s.method, s.path)], ids=lambda s: s.name)
async def test_members_cannot_change_workflows_or_skills(make_factory, spec):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    path = re.sub(r"\{[^}]+\}", "x", spec.path)
    app, ctx, c = await _client(f)
    async with c:
        r = await c.request(spec.method, path, json={}, headers=MEMBER)
        assert r.status_code == 403 and r.json()["detail"] == "admin role required"
    await ctx.__aexit__(None, None, None)


async def test_transcripts_are_for_the_orders_creator_or_an_admin(make_factory):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        product = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()["product"]
        change_id = product["latest_change_id"]
        assert await wait_run(f, change_id) == ChangeStatus.awaiting_feedback
        await asyncio.sleep(0.1)
        calls = (await c.get(f"/api/changes/{change_id}/calls", headers=OTHER)).json()["calls"]
        assert calls, "the call summaries are visible to every member"
        url = f"/api/changes/{change_id}/calls/{calls[0]['transcript']}/transcript"
        assert (await c.get(url, headers=OTHER)).status_code == 403
        assert (await c.get(url, headers=MEMBER)).status_code == 200, "the product's creator"
        assert (await c.get(url, headers=ADMIN)).status_code == 200
    await ctx.__aexit__(None, None, None)


async def test_only_the_creator_or_an_admin_steers_an_order(make_factory):
    """Feedback, answers, resume and cancel on someone else's order are refused (403)."""
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        product = (await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)).json()["product"]
        change_id, oid = product["latest_change_id"], product["id"]
        assert await wait_run(f, change_id) == ChangeStatus.awaiting_feedback
        attempts = [
            ("POST", f"/api/products/{oid}/feedback", {"text": "add a reset endpoint"}),
            ("POST", f"/api/changes/{change_id}/answers", {"answers": ["x"]}),
            ("POST", f"/api/changes/{change_id}/resume", None),
            ("POST", f"/api/changes/{change_id}/cancel", None),
            ("POST", f"/api/products/{oid}/archive", None),
        ]
        for method, url, body in attempts:
            r = await c.request(method, url, json=body, headers=OTHER)
            assert r.status_code == 403, f"{url}: {r.status_code}"
            assert r.json()["detail"] == "only the product's creator or an admin can do this"
        r = await c.post(f"/api/products/{oid}/feedback", json={"text": "add a reset endpoint"}, headers=MEMBER)
        assert r.status_code == 201, "the creator can"
        change2 = r.json()["id"]
        assert (await c.post(f"/api/changes/{change2}/cancel", headers=ADMIN)).status_code == 200, "an admin can"
    await ctx.__aexit__(None, None, None)


def test_every_steering_action_checks_the_order():
    import inspect

    for name in STEERING:
        src = inspect.getsource(actions.REGISTRY[name].fn)
        assert "_may_steer" in src, f"{name} must check the product's creator or an admin"
