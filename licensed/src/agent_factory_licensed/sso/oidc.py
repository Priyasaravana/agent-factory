# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""OpenID Connect sign-in (feature `sso`, ADR-0052): authorization code flow with PKCE, state and nonce.

Configured by the auth container's environment:

    AUTH_OIDC_ISSUER              https://login.example.com (discovery at /.well-known/openid-configuration)
    AUTH_OIDC_CLIENT_ID
    AUTH_OIDC_CLIENT_SECRET_FILE  a mounted file (default /run/secrets/auth_oidc_client_secret); or
                                  AUTH_OIDC_CLIENT_SECRET. None for a public client (PKCE only)
    AUTH_OIDC_LABEL               the button (default "Sign in with single sign-on")
    AUTH_OIDC_SCOPES              default "openid email profile"
    AUTH_OIDC_USERNAME_CLAIM      default preferred_username, then email
    AUTH_OIDC_GROUPS_CLAIM        default groups
    AUTH_OIDC_ADMIN_GROUPS        comma-separated: members of any are admins
    AUTH_OIDC_MEMBER_GROUPS       comma-separated: when set, only these (and admin groups) may sign in
    AUTH_OIDC_ALLOWED_DOMAINS     comma-separated email domains; when set, the email must be verified and in one

Every call to the provider is https, with a timeout and no redirects followed; the discovery document must name
the same issuer, and every endpoint in it must be https. Nothing the provider says is shown to the person.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_factory_licensed.sso import jwt

TIMEOUT = 10
CACHE_SECONDS = 3600
Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https call to {url}")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310 — https only
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(1_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


def _csv(v: str | None) -> tuple[str, ...]:
    return tuple(x.strip() for x in (v or "").split(",") if x.strip())


@dataclass
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str | None = None
    label: str = "Sign in with single sign-on"
    scopes: str = "openid email profile"
    username_claims: tuple[str, ...] = ("preferred_username", "email")
    groups_claim: str = "groups"
    admin_groups: tuple[str, ...] = ()
    member_groups: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> OidcConfig | None:
        """None when no issuer is set (sso not set up); raises ValueError when it is set up wrongly."""
        env = dict(os.environ) if env is None else env
        issuer = env.get("AUTH_OIDC_ISSUER", "").strip().rstrip("/")
        if not issuer:
            return None
        if not issuer.startswith("https://"):
            raise ValueError("AUTH_OIDC_ISSUER must be https")
        client_id = env.get("AUTH_OIDC_CLIENT_ID", "").strip()
        if not client_id:
            raise ValueError("AUTH_OIDC_CLIENT_ID is not set")
        secret = env.get("AUTH_OIDC_CLIENT_SECRET", "").strip() or None
        if secret is None:
            p = Path(env.get("AUTH_OIDC_CLIENT_SECRET_FILE") or "/run/secrets/auth_oidc_client_secret")
            try:
                secret = p.read_text().strip() or None
            except (FileNotFoundError, PermissionError, IsADirectoryError):
                secret = None
        claim = env.get("AUTH_OIDC_USERNAME_CLAIM", "").strip()
        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=secret,
            label=env.get("AUTH_OIDC_LABEL", "").strip() or cls.label,
            scopes=env.get("AUTH_OIDC_SCOPES", "").strip() or cls.scopes,
            username_claims=(claim,) if claim else cls.username_claims,
            groups_claim=env.get("AUTH_OIDC_GROUPS_CLAIM", "").strip() or cls.groups_claim,
            admin_groups=_csv(env.get("AUTH_OIDC_ADMIN_GROUPS")),
            member_groups=_csv(env.get("AUTH_OIDC_MEMBER_GROUPS")),
            allowed_domains=tuple(d.lower() for d in _csv(env.get("AUTH_OIDC_ALLOWED_DOMAINS"))),
        )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@dataclass
class OidcProvider:
    """A `factory_auth.extensions.SignInProvider` for one OpenID Connect issuer."""

    config: OidcConfig
    http: Http = _http
    id: str = "oidc"
    clock: Callable[[], float] = time.time
    licensed: Callable[[], bool] = lambda: True  # the live licence check (agent_factory_licensed.sso)
    _meta: dict[str, Any] | None = field(default=None, repr=False)
    _jwks: dict[str, Any] | None = field(default=None, repr=False)
    _fetched: float = 0.0

    @property
    def label(self) -> str:
        return self.config.label

    def available(self) -> bool:
        """Asked by the auth core before listing and starting: false once the licence lapses while running."""
        return self.licensed()

    # -- provider documents (cached; refreshed once when a token names a key we don't have)
    def _get_json(self, url: str) -> dict[str, Any]:
        status, body = self.http("GET", url, {"Accept": "application/json"}, None)
        if status != 200:
            raise jwt.TokenInvalid(f"GET {url} answered {status}")
        doc = json.loads(body)
        if not isinstance(doc, dict):
            raise jwt.TokenInvalid(f"{url} is not a JSON object")
        return doc

    def metadata(self, refresh: bool = False) -> dict[str, Any]:
        if self._meta is None or refresh or self.clock() - self._fetched > CACHE_SECONDS:
            meta = self._get_json(f"{self.config.issuer}/.well-known/openid-configuration")
            if meta.get("issuer", "").rstrip("/") != self.config.issuer:
                raise jwt.TokenInvalid("the discovery document names another issuer")
            for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not str(meta.get(k, "")).startswith("https://"):
                    raise jwt.TokenInvalid(f"discovery: {k} missing or not https")
            self._meta, self._jwks, self._fetched = meta, None, self.clock()
        return self._meta

    def jwks(self, refresh: bool = False) -> dict[str, Any]:
        if self._jwks is None or refresh:
            self._jwks = self._get_json(self.metadata()["jwks_uri"])
        return self._jwks

    # -- SignInProvider
    def start(self, redirect_uri: str, state: str) -> tuple[str, dict[str, Any]]:
        meta = self.metadata()
        verifier = secrets.token_urlsafe(48)
        nonce = secrets.token_urlsafe(24)
        q = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.config.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
        sep = "&" if "?" in meta["authorization_endpoint"] else "?"
        return meta["authorization_endpoint"] + sep + urllib.parse.urlencode(q), {"verifier": verifier, "nonce": nonce}

    def finish(self, params: dict[str, str], pending: dict[str, Any], redirect_uri: str) -> Any:
        from factory_auth.extensions import Identity, SignInFailed  # the auth service is there when this runs

        code = params.get("code", "")
        if not code:
            raise SignInFailed("invalid", "no code in the callback")
        try:
            claims = self._exchange(code, pending, redirect_uri)
        except (jwt.TokenInvalid, ValueError, KeyError, OSError) as exc:
            raise SignInFailed("invalid", str(exc)) from exc
        username, role = self.decide(claims)
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        # the account is bound to (iss, sub), both verified by jwt.verify (OIDC Core §5.7); the name is a label
        return Identity(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            username=username,
            role=role,
            email=email,
            groups=tuple(_groups(claims, self.config)),
        )

    def _exchange(self, code: str, pending: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": pending["verifier"],
            "client_id": self.config.client_id,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if self.config.client_secret:
            basic = f"{urllib.parse.quote(self.config.client_id)}:{urllib.parse.quote(self.config.client_secret)}"
            headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
        status, body = self.http(
            "POST", self.metadata()["token_endpoint"], headers, urllib.parse.urlencode(form).encode()
        )
        if status != 200:
            raise jwt.TokenInvalid(f"the token endpoint answered {status}")
        token = json.loads(body).get("id_token")
        if not isinstance(token, str):
            raise jwt.TokenInvalid("no id_token in the token response")
        check = {"issuer": self.config.issuer, "client_id": self.config.client_id, "nonce": pending["nonce"]}
        try:
            return jwt.verify(token, self.jwks(), now=self.clock(), **check)
        except jwt.TokenInvalid as exc:
            if "no single" not in str(exc):
                raise
            return jwt.verify(token, self.jwks(refresh=True), now=self.clock(), **check)  # the provider rotated

    def decide(self, claims: dict[str, Any]) -> tuple[str, Any]:
        """Pure: the account name and role from verified claims, or SignInFailed with why."""
        from factory_auth.extensions import SignInFailed

        cfg = self.config
        email = claims.get("email") if isinstance(claims.get("email"), str) else ""
        if cfg.allowed_domains:
            domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
            if claims.get("email_verified") is not True or domain not in cfg.allowed_domains:
                raise SignInFailed("denied", f"email {email!r} not verified or not in an allowed domain")
        username = next(
            (str(claims[c]).strip().lower() for c in cfg.username_claims if isinstance(claims.get(c), str)), ""
        )
        if not username:
            raise SignInFailed("invalid", f"none of {cfg.username_claims} in the token")
        groups = set(_groups(claims, cfg))
        if cfg.admin_groups and groups & set(cfg.admin_groups):
            return username, "admin"
        if cfg.member_groups and not groups & set(cfg.member_groups):
            raise SignInFailed("denied", "not in a group allowed to use the factory")
        # with a group mapping the role follows the provider; without one the account keeps the role it has
        return username, ("member" if cfg.admin_groups or cfg.member_groups else None)


def _groups(claims: dict[str, Any], cfg: OidcConfig) -> list[str]:
    g = claims.get(cfg.groups_claim)
    if isinstance(g, str):
        return [g]
    return [str(x) for x in g] if isinstance(g, list) else []
