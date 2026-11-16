"""With AUTH_MODE=gateway the engine trusts only the identity the auth gateway
forwards, and enforces roles; without it, nothing reaches the API."""

from __future__ import annotations

import httpx
import pytest
from conftest import GATEWAY_PROOF, PRODUCT
from test_api import _client

from agent_factory.app_factory import create_app
from agent_factory.identity import GatewayProofError, proved, read_proof

ADMIN = {"X-Auth-User": "saravana", "X-Auth-Role": "admin"}
MEMBER = {"X-Auth-User": "priya", "X-Auth-Role": "member"}
WF = "/api/workflows/new-product"


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


async def test_identity_headers_count_only_with_the_gateways_proof(make_factory):
    """S-01 (assessment 2026-11-15): anything on the engine's network could send X-Auth-Role: admin."""
    f = make_factory()
    f.settings.auth_mode = "gateway"
    app = create_app(f)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    # a caller that reached the engine without going through the gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as direct:
        assert (await direct.get("/api/health")).status_code == 200, "the container's health check needs no proof"
        assert (await direct.get("/api/products", headers=ADMIN)).status_code == 401, "admin headers, no proof"
        for wrong in ("", "x", GATEWAY_PROOF[:-1], GATEWAY_PROOF + "t", GATEWAY_PROOF.upper()):
            headers = {**ADMIN, "X-Gateway-Proof": wrong}
            assert (await direct.get("/api/products", headers=headers)).status_code == 401, f"wrong proof {wrong!r}"
        body = {"id": "x", "prompt": "p", "tools": "reviewer"}
        r = await direct.put(f"{WF}/draft/agents/x", json=body, headers=ADMIN)
        assert r.status_code == 401, "admin work is refused the same way"
        r = await direct.post("/api/hooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"})
        assert r.status_code == 401, "the webhook is read only when the gateway passed it on"
        assert f.store.list_products() == []
        # the same request with the proof is the gateway's
        ok = await direct.get("/api/products", headers={**ADMIN, "X-Gateway-Proof": GATEWAY_PROOF})
        assert ok.status_code == 200
        # the proof alone is not an identity
        assert (await direct.get("/api/products", headers={"X-Gateway-Proof": GATEWAY_PROOF})).status_code == 401
    await ctx.__aexit__(None, None, None)


def test_gateway_mode_refuses_to_start_without_a_usable_proof(make_factory, tmp_path):
    f = make_factory()
    f.settings.auth_mode = "gateway"
    for path, why in ((None, "GATEWAY_PROOF_FILE"), (str(tmp_path / "missing"), "can't be read")):
        f.settings.gateway_proof_file = path
        with pytest.raises(GatewayProofError, match=why):
            create_app(f)
    for bad in ("", "short", "a" * 20 + " " + "b" * 20, "é" * 40):
        (tmp_path / "bad").write_text(bad)
        f.settings.gateway_proof_file = str(tmp_path / "bad")
        with pytest.raises(GatewayProofError, match="not usable"):
            create_app(f)
    # off mode needs none: local development and tests
    f.settings.auth_mode, f.settings.gateway_proof_file = "off", None
    create_app(f)


def test_the_proof_is_read_trimmed_and_compared_whole(tmp_path):
    (tmp_path / "p").write_text("a" * 64 + "\n")
    proof = read_proof(str(tmp_path / "p"))
    assert proof == "a" * 64
    assert proved(proof, proof)
    assert not proved(None, proof) and not proved("", proof) and not proved("a" * 63, proof)
