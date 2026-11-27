# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""OpenID Connect sign-in (ADR-0052) against a fake identity provider: the whole flow through the auth service,
and every way an ID token or a provider is refused."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from factory_auth import extensions
from factory_auth.app import Settings, create_app
from factory_auth.extensions import SignInFailed
from factory_auth.store import Store
from fastapi.testclient import TestClient

from agent_factory_licensed import licence
from agent_factory_licensed.sso import AuthEnterprise, jwt
from agent_factory_licensed.sso.oidc import OidcConfig, OidcProvider

ISSUER = "https://idp.example"
CLIENT = "factory"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _n(i: int, size: int) -> str:
    return _b64(i.to_bytes(size, "big"))


class FakeIdP:
    """Discovery, JWKS and a token endpoint, as an Http function."""

    def __init__(self, alg: str = "RS256"):
        self.alg = alg
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.claims: dict = {}
        self.last_form: dict = {}
        self.issuer_in_discovery = ISSUER
        self.token_status = 200
        self.kid = "k1"

    def jwks(self) -> dict:
        pn = self.rsa.public_key().public_numbers()
        en = self.ec.public_key().public_numbers()
        return {
            "keys": [
                {"kty": "RSA", "kid": self.kid, "use": "sig", "n": _n(pn.n, 256), "e": _n(pn.e, 3)},
                {"kty": "EC", "kid": self.kid, "crv": "P-256", "x": _n(en.x, 32), "y": _n(en.y, 32)},
            ]
        }

    def sign(self, claims: dict, alg: str | None = None, kid: str | None = None) -> str:
        alg = alg or self.alg
        h = _b64(json.dumps({"alg": alg, "kid": kid or self.kid, "typ": "JWT"}).encode())
        p = _b64(json.dumps(claims).encode())
        data = f"{h}.{p}".encode()
        if alg == "RS256":
            sig = self.rsa.sign(data, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "PS256":
            sig = self.rsa.sign(data, padding.PSS(padding.MGF1(hashes.SHA256()), 32), hashes.SHA256())
        elif alg == "ES256":
            r, s = decode_dss_signature(self.ec.sign(data, ec.ECDSA(hashes.SHA256())))
            sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        else:
            sig = b""
        return f"{h}.{p}.{_b64(sig)}"

    def http(self, method, url, headers, body):
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return 200, json.dumps(
                {
                    "issuer": self.issuer_in_discovery,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                }
            ).encode()
        if url == f"{ISSUER}/jwks":
            return 200, json.dumps(self.jwks()).encode()
        if url == f"{ISSUER}/token" and method == "POST":
            self.last_form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            self.last_form["_auth"] = headers.get("Authorization", "")
            return self.token_status, json.dumps({"id_token": self.sign(self.claims)}).encode()
        return 404, b"{}"


def _claims(nonce: str, **kw) -> dict:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": CLIENT, "sub": "u1", "iat": now, "exp": now + 300, "nonce": nonce}
    return base | {"preferred_username": "Ana", "email": "ana@corp.example", "email_verified": True} | kw


def _provider(idp: FakeIdP, **cfg) -> OidcProvider:
    return OidcProvider(OidcConfig(issuer=ISSUER, client_id=CLIENT, client_secret="s3cret", **cfg), http=idp.http)


# ------------------------------------------------------------- the whole flow --
@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ADMIN_PASSWORD", "correct-horse-battery")
    idp = FakeIdP()
    reg = extensions.Registry()
    reg.provide("licensed", "sso", sign_in=_provider(idp, admin_groups=("factory-admins",)))
    store = Store(tmp_path / "auth.db")
    app = create_app(store, Settings(data_dir=tmp_path, public_url="http://localhost:8080"), reg)
    return TestClient(app, base_url="http://localhost:8080", follow_redirects=False), store, idp


def _round_trip(c: TestClient, idp: FakeIdP, **claims):
    r = c.get("/auth/sso/oidc/start")
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"].endswith("/auth/sso/oidc/callback")
    idp.claims = _claims(q["nonce"], **claims)
    return c.get(f"/auth/sso/oidc/callback?code=abc&state={q['state']}"), q


def test_an_admin_group_member_signs_in_as_admin_with_pkce_and_the_client_secret(site):
    c, store, idp = site
    r, q = _round_trip(c, idp, groups=["factory-admins"])
    assert r.headers["location"] == "/"
    assert c.get("/auth/check").headers["X-Auth-Role"] == "admin"
    assert store.get_user("ana").source == "sso:oidc"
    assert idp.last_form["code_verifier"] and idp.last_form["redirect_uri"] == q["redirect_uri"]
    assert idp.last_form["_auth"].startswith("Basic ")


def test_a_token_for_another_sign_in_fails_at_the_nonce(site):
    c, store, idp = site
    r = c.get("/auth/sso/oidc/start")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    idp.claims = _claims("someone-elses-nonce")
    assert c.get(f"/auth/sso/oidc/callback?code=abc&state={state}").headers["location"].endswith("=invalid")
    assert store.get_user("ana") is None


def test_a_refused_token_request_is_a_failed_sign_in(site):
    c, _, idp = site
    idp.token_status = 400
    r, _ = _round_trip(c, idp)
    assert r.headers["location"] == "/login?sso_error=invalid"


# ---------------------------------------------------------------- ID tokens --
@pytest.mark.parametrize("alg", ["RS256", "PS256", "ES256"])
def test_each_accepted_algorithm_verifies(alg):
    idp = FakeIdP(alg)
    claims = jwt.verify(idp.sign(_claims("n")), idp.jwks(), issuer=ISSUER, client_id=CLIENT, nonce="n")
    assert claims["sub"] == "u1"


@pytest.mark.parametrize(
    "change, why",
    [
        ({"iss": "https://evil.example"}, "issuer"),
        ({"aud": "other-client"}, "not for this client"),
        ({"aud": [CLIENT, "x"]}, "authorised party"),
        ({"azp": "other"}, "authorised party is another client"),
        ({"exp": int(time.time()) - 3600}, "expired"),
        ({"iat": int(time.time()) + 3600}, "not valid yet"),
        ({"nonce": "other"}, "nonce"),
        ({"sub": ""}, "subject"),
    ],
)
def test_a_token_whose_claims_dont_hold_is_refused(change, why):
    idp = FakeIdP()
    token = idp.sign(_claims("n") | change)
    with pytest.raises(jwt.TokenInvalid, match=why):
        jwt.verify(token, idp.jwks(), issuer=ISSUER, client_id=CLIENT, nonce="n")


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_unsigned_and_shared_secret_tokens_are_refused(alg):
    idp = FakeIdP()
    with pytest.raises(jwt.TokenInvalid, match="not accepted"):
        jwt.check_signature(idp.sign(_claims("n"), alg=alg), idp.jwks())


def test_a_token_signed_by_another_key_or_edited_is_refused():
    idp, other = FakeIdP(), FakeIdP()
    with pytest.raises(jwt.TokenInvalid, match="does not match"):
        jwt.check_signature(other.sign(_claims("n")), idp.jwks())
    h, p, s = idp.sign(_claims("n")).split(".")
    edited = _b64(json.dumps(_claims("n") | {"preferred_username": "admin"}).encode())
    with pytest.raises(jwt.TokenInvalid, match="does not match"):
        jwt.check_signature(f"{h}.{edited}.{s}", idp.jwks())


def test_a_rotated_key_is_fetched_once_more():
    idp = FakeIdP()
    p = _provider(idp)
    p.jwks()  # cached with kid k1
    idp.kid = "k2"  # the provider rotates
    url, pending = p.start("http://localhost:8080/cb", "st")
    idp.claims = _claims(pending["nonce"])
    assert p._exchange("abc", pending, "http://localhost:8080/cb")["sub"] == "u1"


# ------------------------------------------------------------ the provider --
def test_discovery_must_name_the_same_issuer_and_https_endpoints():
    idp = FakeIdP()
    idp.issuer_in_discovery = "https://other.example"
    with pytest.raises(jwt.TokenInvalid, match="another issuer"):
        _provider(idp).metadata()


def test_decide_maps_groups_and_domains():
    p = _provider(FakeIdP(), admin_groups=("adm",), member_groups=("eng",), allowed_domains=("corp.example",))
    assert p.decide(_claims("n", groups=["adm"])) == ("ana", "admin")
    assert p.decide(_claims("n", groups=["eng"])) == ("ana", "member")
    with pytest.raises(SignInFailed, match="denied"):
        p.decide(_claims("n", groups=["sales"]))
    with pytest.raises(SignInFailed, match="denied"):
        p.decide(_claims("n", groups=["eng"], email_verified=False))
    with pytest.raises(SignInFailed, match="denied"):
        p.decide(_claims("n", groups=["eng"], email="x@gmail.com"))
    plain = _provider(FakeIdP())
    assert plain.decide(_claims("n")) == ("ana", None), "no mapping: the account keeps its role"


def test_config_from_the_environment(tmp_path):
    assert OidcConfig.from_env({}) is None
    with pytest.raises(ValueError, match="https"):
        OidcConfig.from_env({"AUTH_OIDC_ISSUER": "http://idp", "AUTH_OIDC_CLIENT_ID": "x"})
    with pytest.raises(ValueError, match="CLIENT_ID"):
        OidcConfig.from_env({"AUTH_OIDC_ISSUER": ISSUER})
    secret = tmp_path / "secret"
    secret.write_text("from-file\n")
    cfg = OidcConfig.from_env(
        {
            "AUTH_OIDC_ISSUER": ISSUER + "/",
            "AUTH_OIDC_CLIENT_ID": CLIENT,
            "AUTH_OIDC_CLIENT_SECRET_FILE": str(secret),
            "AUTH_OIDC_ADMIN_GROUPS": "a, b",
        }
    )
    assert cfg and cfg.issuer == ISSUER and cfg.client_secret == "from-file" and cfg.admin_groups == ("a", "b")


def test_the_http_function_refuses_plain_http():
    from agent_factory_licensed.sso.oidc import _http

    with pytest.raises(ValueError, match="non-https"):
        _http("GET", "http://idp.example/x", {}, None)


# ------------------------------------------------------------------ licence --
def _lic(features):
    issuer = Ed25519PrivateKey.generate()
    key = licence.issue(issuer, "Acme", features, datetime.now(UTC).date() + timedelta(days=30))
    return lambda: licence.check(key, [licence._raw_public(issuer)])


def _cfg():
    return OidcConfig(issuer=ISSUER, client_id=CLIENT)


def test_sso_registers_only_with_a_licence_that_covers_it_and_a_set_up_provider():
    reg = extensions.Registry()
    AuthEnterprise(_lic(["sso"]), _cfg).register(reg)
    assert reg.edition == "enterprise" and list(reg.providers) == ["oidc"]
    for check, config, why in [
        (_lic(["siem"]), _cfg, "does not include"),
        (lambda: licence.check(None, []), _cfg, "licence:"),
        (_lic(["sso"]), lambda: None, "not set up"),
    ]:
        reg = extensions.Registry()
        AuthEnterprise(check, config).register(reg)
        assert reg.providers == {} and why in reg.loaded[0].reason


def test_the_package_is_found_by_the_auth_services_entry_point_group():
    from importlib import metadata

    import agent_factory_licensed.sso as sso

    eps = [ep for ep in metadata.entry_points(group=extensions.GROUP) if ep.name == "licensed"]
    assert eps and eps[0].load() is sso.auth_extension


# ----------------------------------------------------------- PR #221 review --
def test_the_account_follows_the_verified_subject_through_a_rename(site):
    c, store, idp = site
    r, _ = _round_trip(c, idp, sub="person-1", preferred_username="ana")
    assert r.headers["location"] == "/"
    assert store.sso_identities("ana")[0] | {"last_seen_at": ""} == {
        "issuer": ISSUER,
        "subject": "person-1",
        "provider": "oidc",
        "username_claim": "ana",
        "email": "ana@corp.example",
        "last_seen_at": "",
    }
    c.cookies.clear()
    r, _ = _round_trip(c, idp, sub="person-1", preferred_username="ana.renamed")
    assert r.headers["location"] == "/" and c.get("/auth/check").headers["X-Auth-User"] == "ana"
    c.cookies.clear()
    r, _ = _round_trip(c, idp, sub="person-2", preferred_username="ana")  # the name, reused for someone else
    assert r.headers["location"] == "/login?sso_error=account"


def test_sign_in_stops_when_the_licence_lapses_while_running(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ADMIN_PASSWORD", "correct-horse-battery")
    issuer = Ed25519PrivateKey.generate()
    keys = {"k": licence.issue(issuer, "Acme", ["sso"], datetime.now(UTC).date() + timedelta(days=30))}
    idp = FakeIdP()
    reg = extensions.Registry()
    AuthEnterprise(
        lambda: licence.check(keys["k"], [licence._raw_public(issuer)]),
        lambda: OidcConfig(issuer=ISSUER, client_id=CLIENT),
        lambda cfg, licensed: OidcProvider(cfg, http=idp.http, licensed=licensed),
        ttl=0,
    ).register(reg)
    app = create_app(Store(tmp_path / "a.db"), Settings(data_dir=tmp_path, public_url="http://localhost:8080"), reg)
    c = TestClient(app, base_url="http://localhost:8080", follow_redirects=False)
    assert c.get("/auth/sso").json() == [{"id": "oidc", "label": "Sign in with single sign-on"}]
    keys["k"] = licence.issue(issuer, "Acme", ["sso"], date(2020, 1, 1), issued=date(2019, 1, 1))  # expired
    assert c.get("/auth/sso").json() == [] and c.get("/auth/sso/oidc/start").status_code == 404
    assert c.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery"}).status_code == 200
