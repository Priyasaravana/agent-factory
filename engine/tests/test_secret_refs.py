"""Phase 2: credentials are references resolved just in time, and resolved
values never reach the event log, the database or the API."""

from __future__ import annotations

import io
import json

import pytest
from conftest import PRODUCT, wait_run
from pydantic import ValidationError

from agent_factory.config import IntegrationAuth
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.secret_refs import (
    MASK,
    AwsSecretsManagerBackend,
    Redactor,
    SecretError,
    SecretRef,
    SecretResolver,
    VaultBackend,
    register_backend,
)

TOKEN = "ghp_TESTSECRETVALUE_1234567890"


def test_parse_references():
    assert SecretRef.parse("aws-sm://factory/github#token") == SecretRef("aws-sm", "factory/github", "token")
    assert str(SecretRef.parse("env://GITHUB_TOKEN")) == "env://GITHUB_TOKEN"
    for bad in ("GITHUB_TOKEN", "ghp_abc", "env://", "://x"):
        with pytest.raises(SecretError):
            SecretRef.parse(bad)


def test_config_refuses_values_where_references_belong():
    with pytest.raises(ValidationError, match="never a value"):
        IntegrationAuth(secret_ref=TOKEN)
    assert IntegrationAuth.model_validate({"secretRef": "vault://kv/github#token"}).secret_ref


def test_env_and_file_backends(monkeypatch, tmp_path):
    r = SecretResolver(redactor=Redactor())
    monkeypatch.setenv("FACTORY_TEST_TOKEN", TOKEN)
    assert r.resolve("env://FACTORY_TEST_TOKEN") == TOKEN
    f = tmp_path / "gh.json"
    f.write_text(json.dumps({"token": TOKEN, "user": "bot"}))
    assert r.resolve(f"file://{f}#token") == TOKEN
    with pytest.raises(SecretError, match="not set"):
        r.resolve("env://FACTORY_DOES_NOT_EXIST")
    with pytest.raises(SecretError, match="key 'nope' not found"):
        r.resolve(f"file://{f}#nope")
    ok, why = r.available("env://FACTORY_DOES_NOT_EXIST")
    assert not ok and "not set" in why and TOKEN not in why


def test_aws_secrets_manager_backend_with_stub_client():
    calls = []

    class Stub:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3 signature
            calls.append(SecretId)
            return {"SecretString": json.dumps({"token": TOKEN})}

    backend = AwsSecretsManagerBackend(client_factory=Stub)
    assert backend.get(SecretRef.parse("aws-sm://factory/github#token")) == TOKEN
    assert calls == ["factory/github"]


def test_vault_backend_kv2(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.internal")
    monkeypatch.setenv("VAULT_TOKEN", "s.vaulttoken")
    seen = {}

    def opener(req):
        seen["url"], seen["token"] = req.full_url, req.get_header("X-vault-token")
        return io.BytesIO(json.dumps({"data": {"data": {"token": TOKEN}}}).encode())

    assert VaultBackend(opener).get(SecretRef.parse("vault://kv/factory/github#token")) == TOKEN
    assert seen == {"url": "https://vault.internal/v1/kv/data/factory/github", "token": "s.vaulttoken"}


def test_custom_backend_and_redaction():
    register_backend("test", lambda settings: type("B", (), {"get": lambda self, ref: TOKEN})())
    red = Redactor()
    r = SecretResolver(redactor=red)
    assert r.resolve("test://anything") == TOKEN
    assert red.text(f"push https://x:{TOKEN}@github.com") == f"push https://x:{MASK}@github.com"
    assert red.obj({"out": [TOKEN, 1]}) == {"out": [MASK, 1]}
    red.add("abc")  # too short to mask safely
    assert red.text("abc") == "abc"


async def test_a_resolved_secret_never_reaches_events_or_the_database(make_factory, monkeypatch, tmp_path):
    """The publish step resolves the token; the (fake) command even echoes it.
    Nothing stored or served may contain it."""
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    ex = FakeExecutor()
    f = make_factory(ex)
    f.settings.factory_mode = "live"  # publish runs only outside dry-run; agents/executor stay fakes
    real_run = ex.run

    async def echoing(command, cwd=None, timeout=600, env=None):
        res = await real_run(command, cwd, timeout, env)
        if env and "GH_TOKEN" in env:  # a leaky tool printing its credential
            res.output += f"\nusing token {env['GH_TOKEN']}"
        return res

    monkeypatch.setattr(ex, "run", echoing)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    events = f.store.list_events(change.id)
    blob = json.dumps([e.model_dump(mode="json") for e in events])
    leaks = [e.model_dump(mode="json") for e in events if TOKEN in json.dumps(e.model_dump(mode="json"))]
    assert not leaks, leaks
    assert MASK in blob, "the echoed token was masked, not dropped silently"
    audit = [e.message for e in events if e.message.startswith("used secret")]
    assert audit == ["used secret env://GITHUB_TOKEN for publish"]
    assert any("gh repo create" in c or "git push" in c for c in ex.calls)
    assert TOKEN not in json.dumps(f.store.list_products()[0].model_dump(mode="json"))


async def test_missing_token_is_reported_not_fatal(make_factory, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    f = make_factory()
    f.settings.factory_mode = "live"
    f.settings.github_token = None
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    handover = next(e.message for e in f.store.list_events(change.id) if e.message.startswith("handover: passed"))
    assert "NOT pushed (env://GITHUB_TOKEN not available)" in handover


async def test_integrations_api_shows_the_reference_not_the_value(make_factory, monkeypatch):
    from test_api import _client

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    f = make_factory()
    f.settings.factory_mode = "live"
    f.settings.github_token = None
    app, ctx, c = await _client(f)
    async with c:
        local = next(i for i in (await c.get("/api/integrations")).json()["integrations"] if i["id"] == "local")
        assert local["auth"] == "env://GITHUB_TOKEN"
        assert any("credential env://GITHUB_TOKEN not available" in r for r in local["readiness"]["reasons"])
        assert (await c.get("/api/health")).json()["github"] is False
    await ctx.__aexit__(None, None, None)
