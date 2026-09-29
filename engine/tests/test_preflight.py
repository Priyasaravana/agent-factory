"""Phase 4 (ADR-0015): readiness checks, order preflight, archive, app ports."""

from __future__ import annotations

import pytest
from conftest import ORDER, wait_run
from test_api import _client

from agent_factory.config import ProductLine, SandboxConfig
from agent_factory.engine.pipeline import FactoryError
from agent_factory.engine.preflight import PreflightFailed
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.identity import Identity, _current
from agent_factory.models import CreateOrderInput, EventKind, RunStatus
from agent_factory.providers import CheckContext, LocalProvider
from agent_factory.providers.base import Readiness
from agent_factory.sandbox import SandboxManager


def _order(n: int) -> CreateOrderInput:
    return CreateOrderInput(title=f"App number {n}", requirements="A tiny API with GET /hello returning hi.")


def test_node_ports_accept_a_range_and_the_default_config_has_twenty(cfg):
    assert ProductLine(template="t", node_ports="30080-30082").node_ports == [30080, 30081, 30082]
    ports = cfg.product_lines["fastapi-service"].node_ports
    assert ports[0] == 30080 and len(ports) == 20
    assert "dind:8081-8100" in cfg.sandbox.egress


async def test_dry_run_preflight_is_ready_and_lists_every_check(make_factory):
    f = make_factory()
    view = await f.manager.preflight.product_line("fastapi-service")
    ids = {c.id for c in view.checks}
    assert {"model", "sandbox", "integration:local", "ports", "scanner", "workflow"} <= ids
    assert view.state == "ready" and view.environment == "local"
    assert next(c for c in view.checks if c.id == "ports").reasons == ["20 free app port(s)"]


async def test_full_port_pool_refuses_the_order_before_any_work_then_archive_frees_it(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    f.cfg.product_lines["fastapi-service"].node_ports = [30080, 30081]
    app, ctx, c = await _client(f)
    async with c:
        ids = []
        for n in (1, 2):
            r = await c.post("/api/orders", json=_order(n).model_dump())
            assert r.status_code == 201
            ids.append(r.json()["order"]["id"])
            await wait_run(f, r.json()["runs"][0]["id"])
        calls_before = len(ex.calls)
        refused = await c.post("/api/orders", json=_order(3).model_dump())
        assert refused.status_code == 409
        body = refused.json()
        assert "preflight failed" in body["detail"] and any("archive an order" in p for p in body["problems"])
        assert len(ex.calls) == calls_before and len(f.store.list_orders()) == 2, "nothing was started"

        archived = await c.post(f"/api/orders/{ids[0]}/archive")
        assert archived.status_code == 200 and archived.json()["archived_at"] and archived.json()["app_url"] is None
        assert any(x.startswith("helm uninstall app-number-1") for x in ex.calls)
        assert [o["id"] for o in (await c.get("/api/orders")).json()] == [ids[1]]
        assert len((await c.get("/api/orders", params={"include_archived": True})).json()) == 2
        again = await c.post("/api/orders", json=_order(3).model_dump())
        assert again.status_code == 201 and again.json()["order"]["node_port"] == 30080, "freed port reused"
        await wait_run(f, again.json()["runs"][0]["id"])
        feedback = await c.post(f"/api/orders/{ids[0]}/feedback", json={"text": "more please"})
        assert feedback.status_code == 409 and "archived" in feedback.json()["detail"]
    await ctx.__aexit__(None, None, None)
    order = f.store.get_order(ids[0])
    ev = [
        e for e in f.store.list_events(order.latest_run_id) if e.station == "archive" and e.kind == EventKind.decision
    ]
    assert ev and "order archived by local" in ev[0].message and ev[0].kind == EventKind.decision


async def test_archive_waits_for_a_running_iteration_and_checks_permissions(make_factory):
    f = make_factory()
    order = f.manager.create_order(ORDER)
    order.created_by = "alice"
    f.store.save_order(order)
    run = f.manager.start_run(order)
    with pytest.raises(FactoryError, match="cancel it first"):
        await f.manager.archive(order.id, "alice")
    await wait_run(f, run.id)
    from agent_factory.actions import archive_order

    token = _current.set(Identity("bob", "member"))
    try:
        with pytest.raises(PermissionError):
            await archive_order(f, order.id)
    finally:
        _current.reset(token)
    token = _current.set(Identity("carol", "admin"))
    try:
        assert (await archive_order(f, order.id)).archived_by == "carol"
    finally:
        _current.reset(token)


async def test_failed_undeploy_keeps_the_order_and_its_port(make_factory):
    f = make_factory(FakeExecutor(fail_on=["helm uninstall"]))
    order = f.manager.create_order(ORDER)
    await wait_run(f, f.manager.start_run(order).id)
    with pytest.raises(FactoryError, match="archive stopped"):
        await f.manager.archive(order.id, "local")
    assert not f.store.get_order(order.id).archived_at


async def test_live_checks_block_what_they_guard(make_factory, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda t: f"/usr/bin/{t}")
    f = make_factory()
    f.settings.factory_mode = "live"
    f.settings.claude_code_oauth_token = None
    f.settings.anthropic_api_key = None
    pf = f.manager.preflight
    with pytest.raises(PreflightFailed) as err:
        await pf.gate("fastapi-service", "iteration")
    assert any("no model credential" in p for p in err.value.problems)

    f.settings.claude_code_oauth_token = "x"
    mgr = SandboxManager(SandboxConfig(), f.manager.ws.factory_home, f.manager.ws.data_dir)
    f.manager.sandbox = mgr
    mgr.state = Readiness("unknown", ["preparing"])
    view = await pf.product_line("fastapi-service", max_age=0)
    assert next(c for c in view.checks if c.id == "sandbox").state == "degraded", "preparing never blocks"
    await pf.gate("fastapi-service", "order")
    mgr.state = Readiness("failed", ["image build failed"])
    with pytest.raises(PreflightFailed) as err:
        await pf.gate("fastapi-service", "order")
    assert err.value.problems == ["Agent sandbox: image build failed"]


async def test_only_ports_the_cluster_actually_maps_are_handed_out(make_factory):
    f = make_factory()
    pf = f.manager.preflight
    pf.mapped_node_ports = {30080, 30081, 30082, 30083, 30084}  # a cluster created before ADR-0015
    view = await pf.product_line("fastapi-service", max_age=0)
    ports = next(c for c in view.checks if c.id == "ports")
    assert ports.state == "degraded" and "maps 5 of 20" in ports.reasons[1]
    for n in range(5):
        f.manager.create_order(_order(n))
    with pytest.raises(FactoryError, match="archive an order"):
        f.manager.create_order(_order(6))
    view = await pf.product_line("fastapi-service", max_age=0)
    assert next(c for c in view.checks if c.id == "ports").state == "failed"


class _ClusterEx:
    async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001
        out = {
            "docker info": "27.5.1",
            "kubectl get --raw=/readyz": "ok",
            "docker port factory-control-plane": "6443/tcp -> 0.0.0.0:6443\n30080/tcp -> 0.0.0.0:8081\n"
            "30081/tcp -> 0.0.0.0:8082\n",
        }
        key = next(k for k in out if command.startswith(k))
        return CommandResult(command, 0, out[key])


async def test_local_provider_check_reports_dind_cluster_and_mapped_ports(make_factory, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda t: f"/usr/bin/{t}")
    f = make_factory()
    r = await LocalProvider().check(CheckContext(f.cfg, f.settings, _ClusterEx()))
    assert r.state == "ready" and r.data["mapped_node_ports"] == [30080, 30081]
    assert r.reasons == ["Docker-in-Docker 27.5.1", "kind cluster API ready", "2 app ports mapped to the host"]

    class Down(_ClusterEx):
        async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001
            return CommandResult(command, 1, "Cannot connect to the Docker daemon")

    down = await LocalProvider().check(CheckContext(f.cfg, f.settings, Down()))
    assert down.state == "failed" and "Docker-in-Docker not reachable" in down.reasons[0]


async def test_workflow_without_readiness_is_flagged_not_blocked(make_factory):
    from agent_factory.workflow import workflow_warnings

    f = make_factory()
    wf = f.workflows["fastapi-service"]
    doc = wf.get(wf.active_version())
    assert not [w for w in workflow_warnings(doc) if "readiness" in w]
    doc.stations = [s for s in doc.stations if s.id != "readiness"]
    assert any(w.startswith("no readiness station") for w in workflow_warnings(doc))


async def test_preflight_api(make_factory):
    f = make_factory()
    app, ctx, c = await _client(f)
    async with c:
        cached = (await c.get("/api/preflight")).json()
        fresh = (await c.post("/api/preflight")).json()
        assert cached[0]["product_line"] == fresh[0]["product_line"] == "fastapi-service"
        integ = (await c.get("/api/integrations")).json()["integrations"]
        assert integ[0]["readiness"]["state"] == "ready"
    await ctx.__aexit__(None, None, None)
    assert RunStatus.queued  # keep import used
