"""Model gateway (ADR-0036): virtual key per agent call, credential kept out of sandboxes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from agent_factory.agents.hooks import evaluate
from agent_factory.agents.runner import AgentRequest, AgentResult, ClaudeAgentRunner
from agent_factory.config import ModelGatewayConfig
from agent_factory.engine.preflight import Preflight
from agent_factory.gateway import (
    DIRECT_MODEL_EGRESS,
    CallScope,
    DirectGateway,
    GatewayRefused,
    GatewaySession,
    LiteLLMGateway,
    gateway_egress,
    key_request,
    sandbox_env,
    withheld_env,
)
from agent_factory.providers.base import Readiness
from agent_factory.sandbox import wrapper
from agent_factory.sandbox.spec import SandboxSpec, forwarded_env_names
from agent_factory.secret_refs import REDACTOR, SecretResolver

SCOPE = CallScope(change_id="c-1", station="implement", role="developer", model="sonnet")
URL = "http://model-gateway:4000"


# ------------------------------------------------------------------- pure --
def test_api_key_mode_gives_the_sandbox_only_the_virtual_key():
    env = sandbox_env("api_key", URL + "/", "sk-virtual")
    assert env == {"ANTHROPIC_BASE_URL": URL, "ANTHROPIC_AUTH_TOKEN": "sk-virtual"}
    # the engine's own credentials are withheld; the virtual key travels under ANTHROPIC_AUTH_TOKEN
    assert set(withheld_env("api_key")) == {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
    assert not set(withheld_env("api_key")) & set(env)


def test_subscription_mode_keeps_the_login_and_sends_the_key_as_a_header():
    env = sandbox_env("subscription", URL, "sk-virtual")
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "x-litellm-api-key: Bearer sk-virtual"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    # an API key would replace the subscription login, so it is withheld; the login is not
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in withheld_env("subscription")
    assert "ANTHROPIC_API_KEY" in withheld_env("subscription")


def test_gateway_replaces_direct_model_egress_and_routes_to_its_address():
    egress = [DIRECT_MODEL_EGRESS, "pypi.org:443"]
    out, routes = gateway_egress(egress, ["*.localtest.me:8180=dind:8180"], URL, "172.18.0.9")
    assert out == ["model-gateway:4000", "pypi.org:443"]
    assert routes == ["*.localtest.me:8180=dind:8180", "model-gateway:4000=172.18.0.9:4000"]
    # idempotent: applying it again changes nothing
    assert gateway_egress(out, routes, URL, "172.18.0.9") == (out, routes)


def test_unresolved_gateway_gets_no_route():
    out, routes = gateway_egress([DIRECT_MODEL_EGRESS], [], URL, None)
    assert out == ["model-gateway:4000"] and routes == []


def test_key_request_is_capped_short_lived_and_labelled():
    cfg = ModelGatewayConfig(provider="litellm", call_budget_usd=2.5, key_minutes=30, allowed_models=["claude-x"])
    body = key_request(SCOPE, cfg, "abc123")
    assert body["key_alias"] == "af-c-1-implement-abc123"
    assert body["duration"] == "30m" and body["max_budget"] == 2.5
    assert body["metadata"] == {"change_id": "c-1", "station": "implement", "role": "developer"}
    assert body["models"] == ["claude-x"]
    assert "models" not in key_request(SCOPE, ModelGatewayConfig(), "n")


# ----------------------------------------------------------------- config --
def test_config_defaults_to_direct_and_refuses_values_for_references():
    assert ModelGatewayConfig().provider == "direct"
    with pytest.raises(ValidationError):
        ModelGatewayConfig(admin_key_ref="sk-literal-master-key")
    with pytest.raises(ValidationError):
        ModelGatewayConfig(url="model-gateway:4000")


# ---------------------------------------------------------------- sandbox --
def test_wrapper_withholds_the_engine_credential_and_never_puts_the_key_on_argv():
    engine_env = {
        "CLAUDE_CODE_OAUTH_TOKEN": "engine-login",
        "ANTHROPIC_API_KEY": "engine-api-key",
        "LITELLM_MASTER_KEY": "master",
        "ANTHROPIC_BASE_URL": URL,
        "ANTHROPIC_AUTH_TOKEN": "sk-virtual",
    }
    # the per-call key reaches the CLI process through the SDK's env (ANTHROPIC_AUTH_TOKEN above)
    spec = SandboxSpec(name="n", image="img:1", network="net", workdir="/w", withhold_env=withheld_env("api_key"))
    argv = wrapper.build_argv(spec.to_json(), ["-p"], engine_env)
    joined = " ".join(argv)
    assert "-e ANTHROPIC_AUTH_TOKEN" in joined and "-e ANTHROPIC_BASE_URL" in joined
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in joined and "ANTHROPIC_API_KEY" not in joined
    assert "LITELLM" not in joined
    assert not any(v in joined for v in ("sk-virtual", "engine-login", "master"))


def test_gateway_admin_key_never_reaches_a_sandbox():
    assert "LITELLM_MASTER_KEY" not in forwarded_env_names({"LITELLM_MASTER_KEY": "m", "CLAUDE_CODE_X": "1"})


@pytest.mark.parametrize("cmd", ["echo $ANTHROPIC_AUTH_TOKEN", "echo $ANTHROPIC_CUSTOM_HEADERS"])
def test_agents_may_not_read_the_virtual_key(cmd: str) -> None:
    assert evaluate("developer", "Bash", {"command": cmd}, Path("/data/runs/r1"), [])


def test_an_ordinary_command_is_not_mistaken_for_reading_the_key() -> None:
    assert evaluate("developer", "Bash", {"command": "uv run pytest -q"}, Path("/data/runs/r1"), []) is None


# ---------------------------------------------------------------- litellm --
class FakeLiteLLM:
    def __init__(self, generate_status: int = 200, spend: float = 0.42) -> None:
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.generate_status, self.spend = generate_status, spend

    async def __call__(self, method: str, url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
        self.calls.append((method, url, headers, body))
        if url.endswith("/health/readiness"):
            return 200, {"status": "healthy", "db": "connected"}
        if url.endswith("/key/generate"):
            ok = self.generate_status == 200
            return self.generate_status, ({"key": "sk-virtual-0123456789"} if ok else {"error": "no"})
        if "/key/info" in url:
            return 200, {"info": {"spend": self.spend}}
        if url.endswith("/key/delete"):
            return 200, {"deleted_keys": body["keys"]}
        return 404, None


def gateway(monkeypatch, http: FakeLiteLLM, **cfg: Any) -> LiteLLMGateway:
    monkeypatch.setenv("LITELLM_MASTER_KEY", "master-key-for-tests")
    return LiteLLMGateway(ModelGatewayConfig(provider="litellm", **cfg), SecretResolver(), http=http)


async def test_a_key_is_minted_per_call_and_revoked_with_its_spend(monkeypatch):
    http = FakeLiteLLM(spend=0.42)
    gw = gateway(monkeypatch, http)
    s = await gw.open(SCOPE)
    method, url, headers, body = http.calls[0]
    assert url == URL + "/key/generate" and headers == {"Authorization": "Bearer master-key-for-tests"}
    assert body["metadata"]["change_id"] == "c-1"
    assert s.env["ANTHROPIC_AUTH_TOKEN"] == "sk-virtual-0123456789"
    assert "sk-virtual-0123456789" not in REDACTOR.text("key sk-virtual-0123456789")  # masked everywhere
    info = await gw.close(s, cost_usd=0.40)
    assert info == {"provider": "litellm", "key_alias": s.key_alias, "spend_usd": 0.42, "revoked": True}
    assert http.calls[-1][1] == URL + "/key/delete" and http.calls[-1][3] == {"keys": ["sk-virtual-0123456789"]}
    assert gw.change_spend["c-1"] == pytest.approx(0.42)  # the larger of reported and gateway spend


async def test_the_change_budget_stops_new_calls(monkeypatch):
    gw = gateway(monkeypatch, FakeLiteLLM(spend=0.0), change_budget_usd=1.0)
    s = await gw.open(SCOPE)
    await gw.close(s, cost_usd=1.2)
    with pytest.raises(GatewayRefused, match="budget"):
        await gw.open(SCOPE)
    other = CallScope("c-2", "implement", "developer", "sonnet")
    assert (await gw.open(other)).key  # another change is unaffected


async def test_no_key_means_the_call_is_refused_not_run_direct(monkeypatch):
    with pytest.raises(GatewayRefused):
        await gateway(monkeypatch, FakeLiteLLM(generate_status=500)).open(SCOPE)
    monkeypatch.delenv("LITELLM_MASTER_KEY")
    gw = LiteLLMGateway(ModelGatewayConfig(provider="litellm"), SecretResolver(), http=FakeLiteLLM())
    with pytest.raises(GatewayRefused, match="admin key"):
        await gw.open(SCOPE)


async def test_readiness(monkeypatch):
    gw = gateway(monkeypatch, FakeLiteLLM())
    assert (await gw.check()).state == "ready"

    async def down(*_: Any) -> tuple[int, Any]:
        raise OSError("connection refused")

    gw.http = down
    assert (await gw.check()).state == "failed"


# ----------------------------------------------------------------- runner --
def request(tmp_path: Path) -> AgentRequest:
    return AgentRequest(change_id="c-1", station="implement", role="developer", prompt="p", cwd=tmp_path, model="m")


class RecordingGateway(DirectGateway):
    name = "litellm"

    def __init__(self, refuse: bool = False) -> None:
        super().__init__()
        self.refuse, self.closed = refuse, 0

    async def open(self, scope: CallScope) -> GatewaySession:
        if self.refuse:
            raise GatewayRefused("model budget for this change reached")
        return GatewaySession(
            provider="litellm", env={"ANTHROPIC_AUTH_TOKEN": "sk-zz-secret"}, withhold=["X"], key_alias="a"
        )

    async def close(self, session: GatewaySession, cost_usd: float = 0.0) -> dict[str, Any]:
        self.closed += 1
        return {**session.info(), "revoked": True}


async def test_a_refused_call_is_held_without_starting_an_agent(tmp_path):
    runner = ClaudeAgentRunner(tmp_path, gateway=RecordingGateway(refuse=True))

    async def never(*_: Any) -> AgentResult:
        raise AssertionError("agent must not start without a key")

    runner._sandboxed = never  # type: ignore[method-assign]
    res = await runner.run(request(tmp_path), lambda *_: _noop())
    assert not res.ok and "model gateway: model budget" in (res.error or "")


async def test_the_key_is_revoked_even_when_the_call_fails(tmp_path):
    gw = RecordingGateway()
    runner = ClaudeAgentRunner(tmp_path, gateway=gw)

    async def boom(*_: Any) -> AgentResult:
        raise RuntimeError("cancelled")

    runner._sandboxed = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await runner.run(request(tmp_path), lambda *_: _noop())
    assert gw.closed == 1


async def test_the_call_record_carries_the_gateway(tmp_path):
    runner = ClaudeAgentRunner(tmp_path, gateway=RecordingGateway())
    seen: list[dict[str, Any]] = []

    async def ok(req: AgentRequest, sink: Any, session: GatewaySession) -> AgentResult:
        seen.append(session.env)
        return AgentResult(ok=True, cost_usd=0.1)

    async def sink(kind: str, data: dict[str, Any]) -> None:
        if kind == "gateway":
            seen.append(data)

    runner._sandboxed = ok  # type: ignore[method-assign]
    res = await runner.run(request(tmp_path), sink)
    assert res.gateway == {"provider": "litellm", "key_alias": "a", "revoked": True}
    assert seen[0]["key_alias"] == "a" and "sk-zz-secret" not in str(seen[0])  # named, never shown
    assert seen[1] == {"ANTHROPIC_AUTH_TOKEN": "sk-zz-secret"}


async def test_direct_calls_have_no_gateway_record(tmp_path):
    runner = ClaudeAgentRunner(tmp_path)

    async def ok(*_: Any) -> AgentResult:
        return AgentResult(ok=True)

    runner._sandboxed = ok  # type: ignore[method-assign]
    assert (await runner.run(request(tmp_path), lambda *_: _noop())).gateway is None


async def _noop() -> None:
    return None


# -------------------------------------------------------------- preflight --
def manager(state: str, credential: str = "api_key", login: str | None = None) -> Any:
    gw = DirectGateway()
    gw.name = "litellm"  # type: ignore[misc]
    gw.state = Readiness(state, [f"gateway {state}"])  # type: ignore[arg-type]
    return SimpleNamespace(
        gateway=gw,
        cfg=SimpleNamespace(model_gateway=ModelGatewayConfig(provider="litellm", credential=credential)),
        settings=SimpleNamespace(claude_code_oauth_token=login, model_auth_configured=lambda: False),
    )


def test_preflight_model_access_is_the_gateway_readiness():
    assert Preflight(manager("ready"))._model(True).state == "ready"  # no engine credential needed
    assert Preflight(manager("failed"))._model(True).state == "failed"
    assert Preflight(manager("unknown"))._model(True).state == "degraded"


def test_preflight_subscription_mode_needs_the_login():
    assert Preflight(manager("ready", "subscription"))._model(True).state == "failed"
    assert Preflight(manager("ready", "subscription", login="tok"))._model(True).state == "ready"


# ------------------------------------------------------------------ wiring --
def test_sandboxes_reach_the_model_only_through_the_gateway(monkeypatch, tmp_path):
    from agent_factory import app_factory
    from agent_factory.config import load_config

    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root / ".agent-factory" / "config.yaml")
    cfg.model_gateway = ModelGatewayConfig(provider="litellm")
    monkeypatch.setattr(app_factory, "resolve_address", lambda url: "10.0.0.5")
    sb = app_factory.sandbox_for(cfg, SimpleNamespace(agent_sandbox=None), root, tmp_path)
    assert sb is not None
    assert DIRECT_MODEL_EGRESS not in sb.cfg.egress and sb.cfg.egress[0] == "model-gateway:4000"
    assert "model-gateway:4000=10.0.0.5:4000" in sb.cfg.routes
    assert sb.cfg.model_endpoint == "model-gateway:4000"  # what `make sandbox-check` probes


def test_direct_mode_leaves_the_sandbox_egress_alone(tmp_path):
    from agent_factory import app_factory
    from agent_factory.config import load_config

    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root / ".agent-factory" / "config.yaml")
    sb = app_factory.sandbox_for(cfg, SimpleNamespace(agent_sandbox=None), root, tmp_path)
    assert sb is not None and DIRECT_MODEL_EGRESS in sb.cfg.egress
    assert sb.cfg.model_endpoint == DIRECT_MODEL_EGRESS
