"""Single sign-on in the auth core (ADR-0052): a provider only proves who someone is; the core checks the state,
links the account, refuses takeovers and disabled accounts, and makes the session."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from factory_auth import extensions
from factory_auth.app import COOKIE, SSO_STATE_COOKIE, Settings, create_app
from factory_auth.extensions import Identity, SignInFailed
from factory_auth.store import AuthError, Store

ADMIN_PW = "correct-horse-battery"
ISSUER = "https://idp.corp.example"


def _id(username, role=None, sub=None, iss=ISSUER, email=None):
    return Identity(issuer=iss, subject=sub or f"sub-{username}", username=username, role=role, email=email)


LOCAL = "http://localhost:8080"


class FakeProvider:
    id = "corp"
    label = "Sign in with Corp"

    def __init__(self):
        self.identity = _id("ana@corp.example", "member", sub="s-ana")
        self.fail: Exception | None = None
        self.started: list[tuple[str, str]] = []
        self.up = True

    def available(self):
        return self.up

    def start(self, redirect_uri, state):
        self.started.append((redirect_uri, state))
        return f"https://idp.example/authorize?state={state}", {"nonce": "n-" + state}

    def finish(self, params, pending, redirect_uri):
        assert pending == {"nonce": "n-" + params["state"]}
        if self.fail:
            raise self.fail
        return self.identity


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ADMIN_PASSWORD", ADMIN_PW)
    store = Store(tmp_path / "auth.db")
    reg = extensions.Registry()
    provider = FakeProvider()
    reg.provide("test", "sso", sign_in=provider)
    app = create_app(store, Settings(data_dir=tmp_path, public_url="http://localhost:8080"), reg)
    return TestClient(app, base_url=LOCAL, follow_redirects=False), store, provider


def _start(c):
    r = c.get("/auth/sso/corp/start")
    assert r.status_code == 303
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def test_the_sign_in_page_lists_providers_only_when_set_up(env, tmp_path):
    c, _, _ = env
    assert c.get("/auth/sso").json() == [{"id": "corp", "label": "Sign in with Corp"}]
    bare = create_app(Store(":memory:"), Settings(data_dir=tmp_path / "b"), extensions.Registry())
    assert TestClient(bare, base_url=LOCAL).get("/auth/sso").json() == []


def test_a_proved_identity_gets_an_account_and_a_session(env):
    c, store, provider = env
    state = _start(c)
    assert provider.started[0][0] == "http://localhost:8080/auth/sso/corp/callback", "from AUTH_PUBLIC_URL"
    r = c.get(f"/auth/sso/corp/callback?code=x&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert COOKIE in r.cookies
    check = c.get("/auth/check")
    assert check.status_code == 200 and check.headers["X-Auth-User"] == "ana@corp.example"
    user = store.get_user("ana@corp.example")
    assert user and user.source == "sso:corp" and user.role == "member"
    assert any(a["action"] == "signed in with sso:corp" for a in store.audit_log())


def test_the_callback_must_carry_this_browsers_state_once(env):
    c, _, _ = env
    state = _start(c)
    other = TestClient(c.app, base_url=LOCAL, follow_redirects=False)  # another browser: no state cookie
    r = other.get(f"/auth/sso/corp/callback?code=x&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=state"
    state = _start(c)
    c.cookies.set(SSO_STATE_COOKIE, "forged", path="/auth/sso")
    assert c.get(f"/auth/sso/corp/callback?code=x&state={state}").headers["location"].endswith("sso_error=state")
    c.cookies.clear()
    state = _start(c)
    assert c.get(f"/auth/sso/corp/callback?code=x&state={state}").headers["location"] == "/"
    c.cookies.clear()
    c.cookies.set(SSO_STATE_COOKIE, state)
    replay = c.get(f"/auth/sso/corp/callback?code=x&state={state}")
    assert replay.headers["location"].endswith("sso_error=state"), "a state is used once"


def test_an_identity_provider_can_never_take_over_a_local_account(env):
    c, store, provider = env
    provider.identity = _id("admin", "admin")
    state = _start(c)
    r = c.get(f"/auth/sso/corp/callback?code=x&state={state}")
    assert r.headers["location"] == "/login?sso_error=account"
    assert c.get("/auth/check").status_code == 401
    assert store.get_user("admin").source == "local"


def test_a_disabled_account_stays_out_and_the_providers_text_is_never_shown(env):
    c, store, provider = env
    state = _start(c)
    c.get(f"/auth/sso/corp/callback?code=x&state={state}")
    c.post("/auth/logout")
    store.update_user("ana@corp.example", disabled=True)
    state = _start(c)
    assert c.get(f"/auth/sso/corp/callback?code=x&state={state}").headers["location"].endswith("=disabled")
    provider.fail = SignInFailed("invalid", "<script>idp said this</script>")
    state = _start(c)
    loc = c.get(f"/auth/sso/corp/callback?code=x&state={state}").headers["location"]
    assert loc == "/login?sso_error=invalid"
    provider.fail = RuntimeError("bug with secrets in it")
    state = _start(c)
    assert c.get(f"/auth/sso/corp/callback?code=x&state={state}").headers["location"].endswith("=provider")


def test_the_providers_error_is_a_denied_sign_in(env):
    c, _, _ = env
    state = _start(c)
    r = c.get(f"/auth/sso/corp/callback?error=access_denied&state={state}")
    assert r.headers["location"] == "/login?sso_error=denied"


def test_roles_follow_the_providers_groups_but_never_strand_the_admins(env):
    c, store, provider = env
    provider.identity = _id("lead@corp.example", "admin")
    c.get(f"/auth/sso/corp/callback?code=x&state={_start(c)}")
    assert store.get_user("lead@corp.example").role == "admin"
    provider.identity = _id("lead@corp.example", "member")
    c.get(f"/auth/sso/corp/callback?code=x&state={_start(c)}")
    assert store.get_user("lead@corp.example").role == "member"
    provider.identity = _id("lead@corp.example", None)  # no mapping: the role an admin set stays
    store.update_user("lead@corp.example", role="admin")
    c.get(f"/auth/sso/corp/callback?code=x&state={_start(c)}")
    assert store.get_user("lead@corp.example").role == "admin"


def test_an_sso_account_has_no_password_and_local_sign_in_still_works(env):
    c, store, _ = env
    c.get(f"/auth/sso/corp/callback?code=x&state={_start(c)}")
    assert c.post("/auth/login", json={"username": "ana@corp.example", "password": "sso"}).status_code == 401
    with pytest.raises(AuthError, match="no password here"):
        store.set_password("ana@corp.example", "a-long-enough-password")
    assert c.post("/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 200


def test_start_is_refused_over_plain_http_off_this_machine(env):
    c, _, _ = env
    far = TestClient(c.app, base_url="http://factory.example.com", follow_redirects=False)
    assert far.get("/auth/sso/corp/start").status_code == 403


def test_a_registry_refuses_bad_or_duplicate_providers():
    reg = extensions.Registry()
    p = FakeProvider()
    assert reg.provide("a", "sso", sign_in=p).status == "loaded"
    assert reg.provide("b", "sso", sign_in=p).status == "refused"
    bad = FakeProvider()
    bad.id = "Bad/Id"
    assert reg.provide("c", "sso", sign_in=bad).status == "refused"
    assert reg.provide("d", "sso", turn_off_local=True).status == "refused"


def test_admins_see_the_edition(env):
    c, _, _ = env
    c.post("/auth/login", json={"username": "admin", "password": ADMIN_PW})
    e = c.get("/auth/edition").json()
    assert e["edition"] == "core" and e["providers"] == [{"id": "corp", "label": "Sign in with Corp"}]


# ------------------------------------------------- the PR #221 review: identity binding --
def _sign_in(c, provider, ident):
    provider.identity = ident
    return c.get(f"/auth/sso/corp/callback?code=x&state={_start(c)}").headers["location"]


def test_the_account_is_bound_to_issuer_and_subject_not_the_name(env):
    c, store, provider = env
    assert _sign_in(c, provider, _id("ana", sub="s-1", email="ana@corp.example")) == "/"
    ana = store.get_user("ana")
    assert ana.uid and store.sso_identities("ana")[0]["subject"] == "s-1"
    store.update_user("ana", role="admin")  # a role an admin gave locally (no group mapping)
    c.cookies.clear()

    # the same person, renamed at the identity provider: the same account, with its role
    assert _sign_in(c, provider, _id("ana.smith", sub="s-1", email="ana.smith@corp.example")) == "/"
    assert c.get("/auth/check").headers["X-Auth-User"] == "ana"
    assert store.sso_identities("ana")[0]["username_claim"] == "ana.smith"
    assert store.get_user("ana.smith") is None and store.get_user("ana").uid == ana.uid
    c.cookies.clear()

    # a different person who is given the old name: never Ana's account, nor her admin role
    assert _sign_in(c, provider, _id("ana", sub="s-2")).endswith("sso_error=account")
    assert c.get("/auth/check").status_code == 401
    c.cookies.clear()

    # the issuer is replaced (a new tenant or provider): a new identity, refused on the taken name
    assert _sign_in(c, provider, _id("ana", sub="s-1", iss="https://other-idp.example")).endswith("sso_error=account")
    assert [i["issuer"] for i in store.sso_identities("ana")] == [ISSUER]


def test_an_identity_without_issuer_or_subject_is_refused(env):
    c, store, provider = env
    assert _sign_in(c, provider, Identity(issuer=ISSUER, subject="", username="bob", role=None)).endswith("=account")
    assert store.get_user("bob") is None


def test_a_deleted_account_gets_a_new_one_on_its_next_sign_in(env):
    c, store, provider = env
    _sign_in(c, provider, _id("cara", sub="s-c"))
    old = store.get_user("cara").uid
    c.post("/auth/logout")
    store.delete_user("cara")
    assert _sign_in(c, provider, _id("cara", sub="s-c")) == "/"
    assert store.get_user("cara").uid != old


def test_accounts_from_before_sso_get_an_internal_id(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE users (username TEXT PRIMARY KEY, role TEXT NOT NULL, pw TEXT NOT NULL,"
        " disabled INTEGER NOT NULL DEFAULT 0, must_change INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
    )
    con.execute("INSERT INTO users VALUES ('old', 'admin', 'x$y$z', 0, 0, '2026-01-01')")
    con.commit()
    con.close()
    user = Store(db).get_user("old")
    assert user.source == "local" and len(user.uid) == 32


# ------------------------------------------------- the PR #221 review: tokens and offboarding --
def test_an_sso_account_cannot_make_an_api_token_and_an_old_one_counts_for_nothing(env):
    c, store, provider = env
    _sign_in(c, provider, _id("dev", sub="s-d"))
    r = c.post("/auth/me/tokens", json={"name": "ci"})
    assert r.status_code == 403 and "single sign-on" in r.json()["detail"]
    _, raw = store.create_token("dev", "made before the rule")
    assert store.token_user(raw) is None
    c.cookies.clear()
    c.post("/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert c.post("/auth/me/tokens", json={"name": "ci"}).status_code == 200, "local accounts keep tokens"


# ------------------------------------------------- the PR #221 review: sign-in capacity --
def test_one_address_cannot_use_up_single_sign_on_for_everyone_else(env):
    from factory_auth.app import SSO_PENDING_PER_SOURCE

    c, _, _ = env
    noisy = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(SSO_PENDING_PER_SOURCE):
        assert c.get("/auth/sso/corp/start", headers=noisy).status_code == 303
    assert c.get("/auth/sso/corp/start", headers=noisy).status_code == 429
    assert c.get("/auth/sso/corp/start", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 303


def test_a_provider_whose_licence_lapses_while_running_stops_offering_sign_in(env):
    c, _, provider = env
    state = _start(c)
    provider.up = False
    assert c.get("/auth/sso").json() == []
    assert c.get("/auth/sso/corp/start").status_code == 404
    assert c.get(f"/auth/sso/corp/callback?code=x&state={state}").headers["location"].endswith("=provider")


def test_what_reaches_the_log_and_audit_is_one_printable_line(env):
    c, store, provider = env
    provider.fail = SignInFailed("invalid", "bad\nFAKE LOG LINE\r\x1b[31m")
    c.get(f"/auth/sso/corp/callback?code=x&state={_start(c)}")
    target = [a for a in store.audit_log() if a["action"] == "single sign-on failed: invalid"][-1]["target"]
    assert "\n" not in target and "\r" not in target and "\x1b" not in target
