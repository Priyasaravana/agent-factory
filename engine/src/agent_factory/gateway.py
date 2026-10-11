"""Model gateway seam (ADR-0036): where agents reach a model, separate from the agent.

    sandbox (agent CLI) ──► gateway (holds the real credential) ──► model provider

`direct` keeps today's behaviour: the agent gets the model credential and calls the
provider itself. A gateway provider (LiteLLM first) instead mints a short-lived
**virtual key per agent call**, scoped by budget and lifetime, and the sandbox
only ever sees that key:

    api_key       the provider key lives only in the gateway; the sandbox gets the
                  virtual key and no model credential at all (for anything others run)
    subscription  the gateway forwards the agent's own Claude subscription token per
                  request (never stored); the token still reaches the sandbox, but
                  routing, budgets and metering apply (own, local use only)

Adding another gateway (Portkey, Kong, our own proxy) = one class in PROVIDERS that
implements ModelGateway. Nothing else in the engine changes.

The pure helpers (sandbox_env, withheld_env, gateway_egress, key_request) carry the
rules and are unit-tested; the HTTP calls stay thin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets as pysecrets
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

from agent_factory.providers.base import Readiness
from agent_factory.secret_refs import REDACTOR

if TYPE_CHECKING:
    from agent_factory.config import ModelGatewayConfig
    from agent_factory.secret_refs import SecretResolver

log = logging.getLogger("agent_factory.gateway")

DIRECT_MODEL_EGRESS = "api.anthropic.com:443"


class GatewayRefused(Exception):
    """The gateway will not serve this call (budget reached, gateway down, no key)."""


@dataclass(frozen=True)
class CallScope:
    """Who is asking: one agent call in one change."""

    change_id: str
    station: str
    role: str
    model: str


@dataclass
class GatewaySession:
    """What one agent call gets from the gateway."""

    provider: str
    env: dict[str, str] = field(default_factory=dict)  # added to the agent process (values may be secret)
    withhold: list[str] = field(default_factory=list)  # engine variables the agent must NOT inherit
    key_alias: str | None = None  # safe to log; the key itself never is
    key: str | None = None  # the virtual key (secret)
    change_id: str = ""

    def info(self) -> dict[str, Any]:
        return {"provider": self.provider, "key_alias": self.key_alias}


class ModelGateway(Protocol):
    name: str
    state: Readiness

    async def check(self) -> Readiness: ...

    async def open(self, scope: CallScope) -> GatewaySession: ...

    async def close(self, session: GatewaySession, cost_usd: float = 0.0) -> dict[str, Any]: ...


# ------------------------------------------------------------------- pure --
def sandbox_env(credential: str, url: str, key: str) -> dict[str, str]:
    """Environment that sends the agent CLI to the gateway with its virtual key."""
    env = {"ANTHROPIC_BASE_URL": url.rstrip("/")}
    if credential == "subscription":
        # the CLI keeps sending its own subscription token; the virtual key authenticates to the gateway
        env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-litellm-api-key: Bearer {key}"
    else:
        env["ANTHROPIC_AUTH_TOKEN"] = key  # sent as Authorization: Bearer; the only credential it has
    return env


def withheld_env(credential: str) -> list[str]:
    """Engine variables an agent behind the gateway must not inherit."""
    if credential == "subscription":
        # the subscription token travels per request; an API key would replace it (and bypass seat billing)
        return ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]
    # ANTHROPIC_AUTH_TOKEN is not withheld: the session sets it to the virtual key (sandbox_env)
    return ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]


def gateway_target(url: str) -> tuple[str, int]:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"model gateway url '{url}' must be http(s)://host[:port]")
    return u.hostname, u.port or (443 if u.scheme == "https" else 80)


def gateway_egress(egress: list[str], routes: list[str], url: str, ip: str | None) -> tuple[list[str], list[str]]:
    """Sandbox egress behind a gateway: the gateway replaces direct model access.
    The egress proxy runs inside dind and can't resolve compose service names, so
    the gateway is reached through a route to its address (ADR-0036)."""
    host, port = gateway_target(url)
    rule = f"{host}:{port}"
    out = [r for r in egress if r.strip() != DIRECT_MODEL_EGRESS]
    if rule not in out:
        out.insert(0, rule)
    new_routes = [r for r in routes if not r.startswith(f"{rule}=")]
    if ip and ip != host:
        new_routes.append(f"{rule}={ip}:{port}")
    return out, new_routes


def key_request(scope: CallScope, cfg: ModelGatewayConfig, nonce: str) -> dict[str, Any]:
    """LiteLLM /key/generate body: one key per agent call, capped and short-lived."""
    alias = f"af-{scope.change_id}-{scope.station}-{nonce}"[:120]
    body: dict[str, Any] = {
        "key_alias": alias,
        "duration": f"{cfg.key_minutes}m",
        "max_budget": cfg.call_budget_usd,
        "metadata": {"change_id": scope.change_id, "station": scope.station, "role": scope.role},
    }
    if cfg.allowed_models:
        body["models"] = list(cfg.allowed_models)
    return body


def change_budget_left(spent: float, budget: float | None) -> bool:
    return budget is None or spent < budget


# ----------------------------------------------------------------- direct --
class DirectGateway:
    """No gateway: the agent calls the model provider with the engine's credential."""

    name = "direct"

    def __init__(self) -> None:
        self.state = Readiness("ready", ["direct: agents call the model provider themselves"])

    async def check(self) -> Readiness:
        return self.state

    async def open(self, scope: CallScope) -> GatewaySession:
        return GatewaySession(provider=self.name)

    async def close(self, session: GatewaySession, cost_usd: float = 0.0) -> dict[str, Any]:
        return session.info()


# ---------------------------------------------------------------- litellm --
Http = Callable[[str, str, dict[str, str], dict[str, Any] | None], Awaitable[tuple[int, Any]]]


async def urllib_http(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None) -> tuple[int, Any]:
    def call() -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - configured http(s) URL
        for k, v in headers.items():
            req.add_header(k, v)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                raw = resp.read().decode()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read().decode(errors="replace"), exc.code
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw

    return await asyncio.to_thread(call)


class LiteLLMGateway:
    """LiteLLM proxy: virtual keys per agent call, budgets, spend, provider routing."""

    name = "litellm"

    def __init__(self, cfg: ModelGatewayConfig, secrets: SecretResolver, http: Http | None = None) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.http = http or urllib_http
        self.state = Readiness("unknown", ["not checked yet"])
        self.change_spend: dict[str, float] = {}  # reported cost per change, for the change budget

    def _admin(self) -> dict[str, str]:
        key = self.secrets.resolve_optional(self.cfg.admin_key_ref)
        if not key:
            raise GatewayRefused(f"model gateway admin key not set ({self.cfg.admin_key_ref})")
        return {"Authorization": f"Bearer {key}"}

    def _url(self, path: str) -> str:
        return self.cfg.url.rstrip("/") + path

    async def check(self) -> Readiness:
        try:
            self._admin()
            status, body = await self.http("GET", self._url("/health/readiness"), {}, None)
        except GatewayRefused as exc:
            self.state = Readiness("failed", [str(exc)])
            return self.state
        except Exception as exc:  # noqa: BLE001 - reported as readiness
            self.state = Readiness("failed", [f"model gateway {self.cfg.url} unreachable: {type(exc).__name__}: {exc}"])
            return self.state
        db = body.get("db") if isinstance(body, dict) else None
        if status != 200:
            self.state = Readiness("failed", [f"model gateway {self.cfg.url} answered {status}"])
        elif db not in (None, "connected"):
            self.state = Readiness("failed", [f"model gateway database: {db} (virtual keys need it)"])
        else:
            self.state = Readiness(
                "ready", [f"litellm at {self.cfg.url}", f"credential: {self.cfg.credential}", "virtual key per call"]
            )
        return self.state

    async def open(self, scope: CallScope) -> GatewaySession:
        spent = self.change_spend.get(scope.change_id, 0.0)
        if not change_budget_left(spent, self.cfg.change_budget_usd):
            raise GatewayRefused(
                f"model budget for this change reached (${spent:.2f} of ${self.cfg.change_budget_usd:.2f})"
            )
        body = key_request(scope, self.cfg, pysecrets.token_hex(3))
        try:
            status, resp = await self.http("POST", self._url("/key/generate"), self._admin(), body)
        except GatewayRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - the call is held, not run without a key
            raise GatewayRefused(f"model gateway unreachable: {type(exc).__name__}: {exc}") from exc
        key = resp.get("key") if isinstance(resp, dict) else None
        if status != 200 or not key:
            raise GatewayRefused(f"model gateway refused a key ({status})")
        REDACTOR.add(key)  # never in events, transcripts or evidence
        return GatewaySession(
            provider=self.name,
            env=sandbox_env(self.cfg.credential, self.cfg.url, key),
            withhold=withheld_env(self.cfg.credential),
            key_alias=body["key_alias"],
            key=key,
            change_id=scope.change_id,
        )

    async def close(self, session: GatewaySession, cost_usd: float = 0.0) -> dict[str, Any]:
        """Read the key's spend, then delete it. Spend is written by the gateway in batches,
        so it may lag the call by seconds; the CLI's reported cost counts for the change budget."""
        info = session.info()
        change = session.change_id
        if not session.key:
            return info
        try:
            status, resp = await self.http("GET", self._url(f"/key/info?key={session.key}"), self._admin(), None)
            if status == 200 and isinstance(resp, dict):
                spend = (resp.get("info") or {}).get("spend")
                if isinstance(spend, int | float):
                    info["spend_usd"] = round(float(spend), 4)
            status, _ = await self.http("POST", self._url("/key/delete"), self._admin(), {"keys": [session.key]})
            info["revoked"] = status == 200
        except Exception as exc:  # noqa: BLE001 - the key still expires after key_minutes
            info["revoked"] = False
            log.warning("could not revoke model gateway key %s: %s", session.key_alias, exc)
        if change:
            self.change_spend[change] = self.change_spend.get(change, 0.0) + max(
                cost_usd, float(info.get("spend_usd") or 0.0)
            )
        return info


PROVIDERS: dict[str, Callable[..., ModelGateway]] = {
    "direct": lambda cfg, secrets: DirectGateway(),
    "litellm": lambda cfg, secrets: LiteLLMGateway(cfg, secrets),
}


def build_gateway(cfg: ModelGatewayConfig, secrets: SecretResolver) -> ModelGateway:
    return PROVIDERS[cfg.provider](cfg, secrets)


def resolve_address(url: str, attempts: int = 15, delay: float = 2.0) -> str | None:
    """The gateway's IP as the engine sees it (compose DNS), for the sandbox egress route.
    Retried briefly: compose may start the factory before the gateway container exists."""
    try:
        host = gateway_target(url)[0]
    except ValueError:
        return None
    for attempt in range(attempts):
        try:
            return socket.gethostbyname(host)
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(delay)
    return None
