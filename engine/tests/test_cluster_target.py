"""Phase 5 (ADR-0026): any OCI registry + Helm to any cluster, one ingress host per app."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from conftest import ORDER, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.config import IntegrationAuth
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import RunStatus
from agent_factory.providers import HelmProvider, OciRegistryProvider, ProviderError
from agent_factory.providers.base import CheckContext
from agent_factory.sandbox.egress import Allowlist, handle


@dataclass
class EnvExecutor(FakeExecutor):
    """Records each command's environment; `helm template` renders an Ingress."""

    envs: list[dict[str, str]] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)

    async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN201
        self.envs.append(dict(env or {}))
        res = await super().run(command, cwd, timeout, env)
        for needle, out in self.outputs.items():
            if needle in command and res.ok:
                return CommandResult(command, 0, out)
        return res


def _use_cluster_target(cfg, **helm):  # noqa: ANN001, ANN003, ANN202
    cfg.integrations["kind-ingress"].settings.update(helm)
    cfg.product_lines["fastapi-service"].environment = "local-ingress"
    return cfg


# -------------------------------------------------------------- the config --
def test_the_local_trial_is_configured_and_ports_are_not_needed(cfg):
    from agent_factory.providers import Providers

    ps = Providers(_use_cluster_target(cfg)).for_product_line("fastapi-service")
    assert (ps.registry.kind, ps.deploy.kind, ps.scan.kind) == ("oci", "helm", "local")
    assert ps.deploy.uses_node_ports is False
    assert "*.localtest.me:8180" in cfg.sandbox.egress
    assert Allowlist(cfg.sandbox.egress, cfg.sandbox.routes).upstream("x.localtest.me", 8180) == ("dind", 8180)


def test_settings_are_validated():
    with pytest.raises(ProviderError, match="repository is required"):
        OciRegistryProvider("r", {})
    with pytest.raises(ProviderError, match="ingress_domain is required"):
        HelmProvider("h", {})
    with pytest.raises(ProviderError, match="public_scheme"):
        HelmProvider("h", {"ingress_domain": "a.b", "public_scheme": "ftp"})
    h = HelmProvider("h", {"ingress_domain": "Apps.Example.com."})
    assert (h.domain, h.port, h.tls, h.egress_rule()) == ("apps.example.com", 443, True, "*.apps.example.com:443")


# ------------------------------------------------------------- a whole run --
async def test_a_run_pushes_to_the_registry_and_deploys_behind_an_ingress(make_factory, cfg):
    _use_cluster_target(cfg)
    ex = EnvExecutor(
        outputs={"helm template": "kind: Ingress", "docker --config": "latest: digest: sha256:abc size: 1"}
    )
    runner = FakeAgentRunner()
    f = make_factory(ex, runner)
    order = f.manager.create_order(ORDER)
    assert order.node_port is None and order.host_port is None, "no port: an ingress host instead"
    run = f.manager.start_run(order)
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback

    push = next(c for c in ex.calls if "docker --config" in c and " push " in c)
    assert "docker tag bookmarks-service:" in push and "localhost:5001/factory/bookmarks-service:" in push
    assert not any(c.startswith("kind load") for c in ex.calls), "no side-loading"
    helm = next(c for c in ex.calls if "helm upgrade --install bookmarks-service" in c)
    for want in ("service.type=ClusterIP", "ingress.enabled=true", "ingress.host=bookmarks-service.localtest.me",
                 "ingress.className=nginx", "ingress.tls=false", "--namespace app-bookmarks-service"):  # fmt: skip
        assert want in helm, want
    assert "nodePort" not in helm
    smoke = next(c for c in ex.calls if "/healthz" in c)
    assert "--connect-to bookmarks-service.localtest.me:8180:dind:8180" in smoke
    assert "http://bookmarks-service.localtest.me:8180/healthz" in smoke

    got = f.store.get_order(order.id)
    assert got.app_url == "http://bookmarks-service.localtest.me:8180"
    verifier = next(c for c in runner.calls if c.role == "verifier")
    assert "http://bookmarks-service.localtest.me:8180" in verifier.prompt, "acceptance tests the ingress URL"

    await f.manager.archive(order.id, "saravana")
    assert any("helm uninstall bookmarks-service --namespace app-bookmarks-service" in c for c in ex.calls)
    archived = [e.message for e in f.store.list_events(run.id) if e.message.startswith("order archived")]
    assert archived and "app port" not in archived[0]


async def test_a_chart_without_an_ingress_goes_to_the_devops_agent(make_factory, cfg):
    _use_cluster_target(cfg)
    ex = EnvExecutor(outputs={"helm template": "kind: Service"})  # an old product chart
    f = make_factory(ex)
    f.settings.factory_mode = "live"

    run = f.manager.start_run(f.manager.create_order(ORDER))
    await wait_run(f, run.id)
    ev = [e for e in f.store.list_events(run.id) if e.station == "deploy" and "no Ingress" in e.message]
    assert ev, "deploy failed with the reason"
    devops = [c for c in f.manager.agents.calls if c.role == "devops"]
    assert devops and "add templates/ingress.yaml" in devops[0].prompt


async def test_credentials_go_through_the_environment_never_the_command_line(make_factory, cfg, monkeypatch):
    monkeypatch.setenv("REG_TOKEN", "s3cret-registry-token")
    monkeypatch.setenv("KUBECONFIG_CONTENT", "apiVersion: v1\nkind: Config\n# s3cret-kube")
    monkeypatch.setenv("PULL_JSON", '{"auths": {"ghcr.io": {"auth": "s3cret-pull"}}}')
    _use_cluster_target(cfg, pull_secret_ref="env://PULL_JSON")
    cfg.integrations["kind-registry"].settings.update({"username": "bot"})
    cfg.integrations["kind-registry"].auth = IntegrationAuth(secret_ref="env://REG_TOKEN")
    cfg.integrations["kind-ingress"].auth = IntegrationAuth(secret_ref="env://KUBECONFIG_CONTENT")
    ex = EnvExecutor(outputs={"helm template": "kind: Ingress"})
    f = make_factory(ex)
    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback

    assert not any("s3cret" in c for c in ex.calls), "no credential on a command line"
    for e in f.store.list_events(run.id):
        assert "s3cret" not in e.message and "s3cret" not in str(e.data), "no credential in the event log"
    login = next(i for i, c in enumerate(ex.calls) if "--password-stdin" in c)
    assert ex.envs[login] == {"AF_REGISTRY_PASSWORD": "s3cret-registry-token"}
    assert 'docker --config "$D" login localhost:5001 -u bot' in ex.calls[login] and "rm -rf" in ex.calls[login]
    helm = next(i for i, c in enumerate(ex.calls) if "helm upgrade" in c)
    assert ex.envs[helm]["AF_KUBECONFIG"].endswith("# s3cret-kube") and "rm -f" in ex.calls[helm]
    assert "factory-pull" in ex.calls[helm] and ex.envs[helm]["AF_PULL_SECRET"].startswith('{"auths"')
    used = [e.message for e in f.store.list_events(run.id) if e.message.startswith("used secret")]
    assert {"used secret env://REG_TOKEN for push to localhost:5001", "used secret env://KUBECONFIG_CONTENT for deploy",
            "used secret env://PULL_JSON for image pull secret"} <= set(used)  # fmt: skip


# --------------------------------------------------------------- readiness --
async def test_readiness_checks_api_ingress_class_and_the_egress_allowlist(cfg, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")
    h = HelmProvider("cluster", {"ingress_domain": "apps.example.com"})
    ex = FakeExecutor()
    r = await h.check(CheckContext(cfg, None, ex))
    assert r.state == "degraded" and "add *.apps.example.com:443 to sandbox.egress" in r.reasons[-1]
    cfg.sandbox.egress.append("*.apps.example.com:443")
    assert (await h.check(CheckContext(cfg, None, FakeExecutor()))).state == "ready"
    r = await h.check(CheckContext(cfg, None, FakeExecutor(fail_on=["get ingressclass"])))
    assert r.state == "failed" and "IngressClass 'nginx' not found" in r.reasons[-1]


async def test_registry_readiness(cfg):
    reg = OciRegistryProvider(
        "r", {"repository": "localhost:5001/factory", "insecure": True, "check_host": "dind:5001"}
    )
    ex = EnvExecutor(outputs={"curl": "200"})
    r = await reg.check(CheckContext(cfg, None, ex))
    assert r.state == "ready" and ex.calls[0].endswith("http://dind:5001/v2/")
    assert (await reg.check(CheckContext(cfg, None, EnvExecutor(outputs={"curl": "401"})))).state == "failed"
    assert (await reg.check(CheckContext(cfg, None, EnvExecutor(outputs={"curl": "000"})))).state == "failed"


# ------------------------------------------------------------ egress routes --
async def test_the_egress_proxy_routes_keeping_the_host_name():
    seen: list[bytes] = []

    async def app(reader, writer):  # noqa: ANN001, ANN202
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    upstream = await asyncio.start_server(app, "127.0.0.1", 0)
    port = upstream.sockets[0].getsockname()[1]
    allow = Allowlist(["*.localtest.me:8180"], [f"*.localtest.me:8180=127.0.0.1:{port}"])
    proxy = await asyncio.start_server(lambda r, w: handle(r, w, allow), "127.0.0.1", 0)
    pport = proxy.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", pport)
    writer.write(b"GET http://shop.localtest.me:8180/healthz HTTP/1.1\r\nHost: shop.localtest.me:8180\r\n\r\n")
    await writer.drain()
    body = await reader.read()
    assert body.endswith(b"ok")
    assert seen and seen[0].startswith(b"GET /healthz HTTP/1.1") and b"Host: shop.localtest.me:8180" in seen[0]
    with pytest.raises(ValueError, match="pattern:port=host:port"):
        Allowlist([], ["*.x:80"])
    proxy.close()
    upstream.close()
