"""With AUTH_MODE=gateway the engine trusts only the identity the auth gateway
forwards, and enforces roles; without it, nothing reaches the API."""

from __future__ import annotations

from conftest import PRODUCT
from test_api import _client

ADMIN = {"X-Auth-User": "saravana", "X-Auth-Role": "admin"}
MEMBER = {"X-Auth-User": "priya", "X-Auth-Role": "member"}
WF = "/api/workflows/fastapi-service"


async def test_gateway_mode_requires_identity_and_enforces_roles(make_factory):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.get("/api/health")).status_code == 200, "health stays open for container checks"
        assert (await c.get("/api/products")).status_code == 401
        assert (await c.get("/api/products", headers={"X-Auth-User": "x", "X-Auth-Role": "root"})).status_code == 401

        r = await c.post("/api/products", json=PRODUCT.model_dump(), headers=MEMBER)
        assert r.status_code == 201 and r.json()["product"]["created_by"] == "priya"
        assert (await c.get(WF, headers=MEMBER)).status_code == 200, "members can read workflows"
        assert (await c.delete(f"{WF}/draft", headers=MEMBER)).status_code == 403
        assert (await c.post("/api/skills/preview", json={"repo": "a/b"}, headers=MEMBER)).status_code == 403

        body = {"id": "strict", "prompt": "check", "tools": "reviewer"}
        r = await c.put(f"{WF}/draft/agents/strict", json=body, headers=ADMIN)
        assert r.status_code == 200
        r = await c.post(f"{WF}/draft/publish", json={"note": "add strict"}, headers=ADMIN)
        assert r.status_code == 201 and r.json()["note"] == "add strict (by saravana)"
    await ctx.__aexit__(None, None, None)


async def test_off_mode_is_single_user(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        r = await c.post("/api/products", json=PRODUCT.model_dump())
        assert r.status_code == 201 and r.json()["product"]["created_by"] == "local"
    await ctx.__aexit__(None, None, None)
