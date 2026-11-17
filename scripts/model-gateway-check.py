#!/usr/bin/env python3
"""Per-call model keys on a real LiteLLM gateway (ADR-0036; #182 security review).

Run against a running gateway with its database (`docker compose --profile gateway up`, a published
port and a test model list; CI job `model-gateway`), from the engine's environment:

    cd engine && LITELLM_MASTER_KEY=... uv run python ../scripts/model-gateway-check.py http://127.0.0.1:4000

It uses the engine's own `LiteLLMGateway` (the code that mints and revokes keys for every agent call)
against the real gateway, with test models that need no provider key: `claude-mock` answers at once,
`claude-slow` points at an address that never answers, `claude-other` exists but no key may use it.

For each way an agent call can end (it succeeds, it fails, it times out) it opens a session, uses the
key, closes the session the way the runner does in its `finally`, and asks the gateway to serve the
key again: it must refuse. It also checks what a key may do while it lives (only the allowed models,
a budget, an expiry). Exit code 0 only when every check passes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from agent_factory.config import ModelGatewayConfig
from agent_factory.gateway import CallScope, LiteLLMGateway
from agent_factory.secret_refs import SecretResolver

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail and not ok else ''}", flush=True)
    if not ok:
        FAILED.append(name)


def chat(url: str, key: str, model: str, timeout: float = 20, body: dict | None = None) -> int | str:
    """The gateway's answer to one model request with `key`: an HTTP status, or the error's name."""
    payload = body if body is not None else {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    req = urllib.request.Request(  # noqa: S310 - the gateway under test
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - reported as the answer
        return type(exc).__name__


def refused_later(url: str, key: str) -> int | str:
    """The key once revoked: the gateway may serve it from a short cache, so ask for up to 30 s."""
    answer: int | str = "never asked"
    for _ in range(15):
        answer = chat(url, key, "claude-mock")
        if answer in (401, 403):
            return answer
        time.sleep(2)
    return answer


async def main(url: str) -> int:
    cfg = ModelGatewayConfig(
        provider="litellm",
        url=url,
        credential="api_key",
        admin_key_ref="env://LITELLM_MASTER_KEY",
        key_minutes=5,
        call_budget_usd=0.5,
        allowed_models=["claude-mock", "claude-slow"],
    )
    gw = LiteLLMGateway(cfg, SecretResolver())
    for _ in range(60):
        state = await gw.check()
        if state.state == "ready":
            break
        await asyncio.sleep(2)
    check("the engine sees the gateway ready (its database connected)", state.state == "ready", str(state.reasons))
    if state.state != "ready":
        return 1

    def scope(station: str) -> CallScope:
        return CallScope("chg-live", station, "developer", "claude-mock")

    print("a call that succeeds")
    s = await gw.open(scope("develop"))
    check("a key is minted for the call", bool(s.key))
    check("the key serves an allowed model", chat(url, s.key, "claude-mock") == 200)
    other = chat(url, s.key, "claude-other")
    check("the key may not use a model outside its list", other in (401, 403), str(other))
    info_req = urllib.request.Request(  # noqa: S310
        url.rstrip("/") + "/key/info?key=" + urllib.parse.quote(s.key), headers=gw._admin()
    )

    def key_info() -> dict:
        with urllib.request.urlopen(info_req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read()).get("info") or {}

    info = await asyncio.to_thread(key_info)
    check("the key has a budget", info.get("max_budget") == 0.5, str(info.get("max_budget")))
    check("the key has an expiry", bool(info.get("expires")), str(info.get("expires")))
    check(
        "the key is limited to the allowed models", sorted(info.get("models") or []) == ["claude-mock", "claude-slow"]
    )
    closed = await gw.close(s)
    check("closing the session revokes the key", closed.get("revoked") is True, str(closed))
    answer = refused_later(url, s.key)
    check("after the call: the gateway refuses the key", answer in (401, 403), str(answer))

    print("a call that fails")
    s = await gw.open(scope("test"))
    bad = chat(url, s.key, "claude-mock", body={"model": "claude-mock"})  # no messages: the request fails
    check("the call fails at the gateway", isinstance(bad, int) and bad >= 400, str(bad))
    closed = await gw.close(s)
    check("closing the session revokes the key", closed.get("revoked") is True, str(closed))
    answer = refused_later(url, s.key)
    check("after the failed call: the gateway refuses the key", answer in (401, 403), str(answer))

    print("a call that times out (the agent is stopped while the model hasn't answered)")
    s = await gw.open(scope("review"))
    slow = await asyncio.to_thread(chat, url, s.key, "claude-slow", 3)
    # the client gives up first (TimeoutError), or the gateway answers with its own timeout or upstream error
    timed_out = slow in ("TimeoutError", "URLError") or (isinstance(slow, int) and (slow == 408 or slow >= 500))
    check("the call doesn't complete", timed_out, str(slow))
    closed = await gw.close(s)
    check("closing the session revokes the key", closed.get("revoked") is True, str(closed))
    answer = refused_later(url, s.key)
    check("after the timed-out call: the gateway refuses the key", answer in (401, 403), str(answer))

    print(f"\n{'all checks passed' if not FAILED else f'{len(FAILED)} FAILED: ' + '; '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4000")))
