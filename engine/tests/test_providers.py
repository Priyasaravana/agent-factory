"""Provider seam (phase 1 of docs/design/integrations.md): delivery steps go
through the providers bound to the blueprint's environment; `local` keeps
today's behaviour exactly."""

from __future__ import annotations

import pytest
from conftest import PRODUCT, REPO, wait_run

from agent_factory.agents import FakeAgentRunner
from agent_factory.config import Integration, load_config
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus, EventKind
from agent_factory.providers import ImageRef, ProviderError, Providers, Readiness, StepResult, register_kind

CFG_PATH = REPO / ".agent-factory" / "config.yaml"


def _cfg(**update):
    cfg = load_config(CFG_PATH)
    return cfg.model_copy(update=update) if update else cfg


def test_local_is_the_default_without_any_config():
    cfg = _cfg()
    assert cfg.integrations["local"].provider == "local"
    assert cfg.blueprints["fastapi-service"].environment == "local"
    ps = Providers(cfg).for_blueprint("fastapi-service")
    assert ps.environment == "local" and ps.deploy.kind == "local" and ps.registry is ps.deploy


async def test_local_run_issues_the_same_commands_as_before(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    def idx(prefix: str) -> int:
        return next(i for i, c in enumerate(ex.calls) if c.startswith(prefix))

    trivy = next(i for i, c in enumerate(ex.calls) if "aquasec/trivy" in c)
    assert idx("docker build") < trivy < idx("kind load") < idx("helm upgrade --install")
    helm = ex.calls[idx("helm upgrade")]
    assert "--namespace app-bookmarks-service" in helm and "--set image.repository=bookmarks-service" in helm
    env_event = next(e for e in f.store.list_events(change.id) if e.message.startswith("delivery environment"))
    assert env_event.kind == EventKind.decision and "deploy: local (local)" in env_event.message


def test_misconfiguration_fails_at_startup():
    cfg = load_config(CFG_PATH)
    cfg.integrations["x"] = Integration(provider="nope")
    with pytest.raises(ProviderError, match="unknown provider 'nope'"):
        Providers(cfg)
    cfg = load_config(CFG_PATH)
    cfg.environments["dev"] = cfg.environments["local"].model_copy(update={"deploy": "missing"})
    with pytest.raises(ProviderError, match="deploy uses unknown integration 'missing'"):
        Providers(cfg)
    cfg = load_config(CFG_PATH)
    cfg.blueprints["fastapi-service"].environment = "staging"
    with pytest.raises(ProviderError, match="unknown environment 'staging'"):
        Providers(cfg)


class RecordingDeploy:
    """A stand-in for a remote target (e.g. Argo CD on EKS): records what the
    stations ask for and reports its own URLs."""

    kind = "recording"
    capabilities = frozenset({"registry", "deploy"})
    calls: list[str] = []

    def __init__(self, name, settings):
        self.name, self.settings = name, settings

    async def check(self, ctx):
        return Readiness("ready")

    def image_ref(self, ctx, tag):
        return ImageRef(f"{self.settings['registry']}/{ctx.product.slug}", tag)

    async def push(self, ctx, local_image, ref):
        RecordingDeploy.calls.append(f"push {local_image} -> {ref}")
        return StepResult(True, "pushed")

    def target(self, ctx):
        return "the dev cluster"

    def internal_url(self, ctx):
        return f"https://{ctx.product.slug}.dev.internal"

    def public_url(self, ctx):
        return f"https://{ctx.product.slug}.dev.example.com"

    async def deploy(self, ctx, image):
        RecordingDeploy.calls.append(f"deploy {image}")
        return StepResult(True, f"synced {image}")

    async def diagnostics(self, ctx):
        return ""


async def test_an_environment_can_route_steps_to_another_provider(make_factory, cfg):
    register_kind("recording", RecordingDeploy)
    RecordingDeploy.calls = []
    cfg.integrations["dev-cluster"] = cfg.integrations["local"].model_copy(
        update={"provider": "recording", "settings": {"registry": "123.dkr.ecr.eu-west-2.amazonaws.com/factory"}}
    )
    cfg.environments["dev"] = cfg.environments["local"].model_copy(
        update={"registry": "dev-cluster", "deploy": "dev-cluster"}
    )
    cfg.blueprints["fastapi-service"].environment = "dev"
    runner, ex = FakeAgentRunner(), FakeExecutor()
    f = make_factory(ex, runner)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    assert (
        RecordingDeploy.calls[0].startswith("push bookmarks-service:")
        and "amazonaws.com/factory" in RecordingDeploy.calls[0]
    )
    assert RecordingDeploy.calls[1].startswith("deploy 123.dkr.ecr.eu-west-2.amazonaws.com/factory/bookmarks-service:")
    assert not any(c.startswith(("kind load", "helm upgrade")) for c in ex.calls), "local steps are not used"
    assert any(c.startswith("docker run") for c in ex.calls), "scan still local"
    verifier = next(c for c in runner.calls if c.role == "verifier")
    assert "https://bookmarks-service.dev.internal" in verifier.prompt, "acceptance tests the deployed URL"
    product = f.store.get_product(change.product_id)
    assert product.app_url == "https://bookmarks-service.dev.example.com"


async def test_delivery_is_visible_over_the_api(make_factory):
    from test_api import _client

    app, ctx, c = await _client(make_factory())
    async with c:
        d = (await c.get("/api/integrations")).json()
        local = next(i for i in d["integrations"] if i["id"] == "local")
        assert local["provider"] == "local" and set(local["capabilities"]) == {"registry", "scan", "deploy", "publish"}
        assert local["readiness"]["state"] in {"ready", "failed"} and local["used_by"] == ["local", "local-ingress"]
        env = next(e for e in d["environments"] if e["name"] == "local")
        assert env["bindings"]["deploy"] == "local" and env["blueprints"] == ["fastapi-service"]
        wf = (await c.get("/api/workflows/fastapi-service")).json()
        assert wf["environment"] == "local"
        assert {b["capability"]: b["integration"] for b in wf["delivery"]}["registry"] == "local"
        assert (await c.get("/api/workflows")).json()[0]["environment"] == "local"
    await ctx.__aexit__(None, None, None)
