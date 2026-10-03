from __future__ import annotations

import asyncio

import httpx

from agent_factory.app_factory import create_app


async def _client(f):
    app = create_app(f)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    return app, ctx, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_order_lifecycle_over_http(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        assert (await c.get("/api/health")).json()["mode"] == "dry-run"
        r = await c.post(
            "/api/products",
            json={"title": "Bookmarks service", "requirements": "Save and tag bookmarks, filter by tag."},
        )
        assert r.status_code == 201
        detail = r.json()
        change_id = detail["changes"][0]["id"]
        for _ in range(200):
            change = (await c.get(f"/api/changes/{change_id}")).json()
            if change["change"]["status"] == "awaiting_feedback":
                break
            await asyncio.sleep(0.05)
        assert change["change"]["status"] == "awaiting_feedback"
        assert all(s["state"] in ("passed", "pending") for s in change["stations"])
        events = (await c.get(f"/api/changes/{change_id}/events")).json()
        assert any(e["kind"] == "decision" for e in events)

        d = await c.post(
            "/api/decisions",
            json={"change_id": change_id, "station": "build", "decision": "use SQLAlchemy 2 typed models"},
        )
        assert d.status_code == 201

        fb = await c.post(f"/api/products/{detail['product']['id']}/feedback", json={"text": "Add search"})
        assert fb.status_code == 201, fb.text
        assert fb.json()["iteration"] == 2
        again = await c.post(f"/api/products/{detail['product']['id']}/feedback", json={"text": "more"})
        assert again.status_code == 409
        for _ in range(200):
            if (await c.get(f"/api/changes/{fb.json()['id']}")).json()["change"]["status"] == "awaiting_feedback":
                break
            await asyncio.sleep(0.05)
    await ctx.__aexit__(None, None, None)


async def test_errors_and_contract(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        assert (await c.get("/api/changes/nope")).status_code == 404
        bad = await c.post("/api/products", json={"title": "x", "requirements": "short"})
        assert bad.status_code == 422
        spec = app.openapi()
        ops = {op["operationId"] for p in spec["paths"].values() for op in p.values()}
        assert {"create_product", "get_change", "submit_feedback", "log_decision", "stream_change"} <= ops
    await ctx.__aexit__(None, None, None)


async def test_workflow_endpoints(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        line = (await c.get("/api/workflows/fastapi-service")).json()
        assert line["version"] == 1 and line["active"] and line["template"] == "default"
        agents = {a["spec"]["id"] for a in line["agents"]}
        assert agents == {"intake", "architect", "developer", "reviewer", "devops", "verifier"}
        verifier = next(a for a in line["agents"] if a["spec"]["id"] == "verifier")
        assert verifier["observe_only"] and verifier["model_resolved"] == "opus"
        assert (await c.get("/api/workflows/fastapi-service/versions")).json()[0]["version"] == 1
        assert (await c.get("/api/workflows/fastapi-service/versions/9")).status_code == 404
        cfg = (await c.get("/api/config")).json()
        assert cfg["workflows"] == {"fastapi-service": 1}
    await ctx.__aexit__(None, None, None)
