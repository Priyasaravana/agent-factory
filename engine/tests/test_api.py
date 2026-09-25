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
            "/api/orders", json={"title": "Bookmarks service", "requirements": "Save and tag bookmarks, filter by tag."}
        )
        assert r.status_code == 201
        detail = r.json()
        run_id = detail["runs"][0]["id"]
        for _ in range(200):
            run = (await c.get(f"/api/runs/{run_id}")).json()
            if run["run"]["status"] == "awaiting_feedback":
                break
            await asyncio.sleep(0.05)
        assert run["run"]["status"] == "awaiting_feedback"
        assert all(s["state"] in ("passed", "pending") for s in run["stations"])
        events = (await c.get(f"/api/runs/{run_id}/events")).json()
        assert any(e["kind"] == "decision" for e in events)

        d = await c.post(
            "/api/decisions", json={"run_id": run_id, "station": "build", "decision": "use SQLAlchemy 2 typed models"}
        )
        assert d.status_code == 201

        fb = await c.post(f"/api/orders/{detail['order']['id']}/feedback", json={"text": "Add search"})
        assert fb.status_code == 201 and fb.json()["iteration"] == 2
        again = await c.post(f"/api/orders/{detail['order']['id']}/feedback", json={"text": "more"})
        assert again.status_code == 409
        for _ in range(200):
            if (await c.get(f"/api/runs/{fb.json()['id']}")).json()["run"]["status"] == "awaiting_feedback":
                break
            await asyncio.sleep(0.05)
    await ctx.__aexit__(None, None, None)


async def test_errors_and_contract(make_factory):
    app, ctx, c = await _client(make_factory())
    async with c:
        assert (await c.get("/api/runs/nope")).status_code == 404
        bad = await c.post("/api/orders", json={"title": "x", "requirements": "short"})
        assert bad.status_code == 422
        spec = app.openapi()
        ops = {op["operationId"] for p in spec["paths"].values() for op in p.values()}
        assert {"create_order", "get_run", "submit_feedback", "log_decision", "stream_run"} <= ops
    await ctx.__aexit__(None, None, None)
