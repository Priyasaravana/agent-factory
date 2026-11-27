"""Extension points of the auth service (ADR-0051, ADR-0052): where the licensed edition adds sign-in providers.

The auth service is its own package and never imports the engine or `licensed/`. Like the engine, it looks for
Python entry points, here in the group `GROUP`, and lets each register into a `Registry`. The core image finds
none, so only local sign-in exists. The enterprise image's licensed package checks the licence key and registers
a provider only when the key covers `sso` and the provider is configured.

The one point is `sign_in`: a provider that proves who someone is at an identity provider. It never creates
sessions, users or cookies: the core does, after checking the request's state, the account it maps to and that
the account is not disabled (`app.py`, `/auth/sso/...`). Local sign-in is never an extension and can't be
switched off, so an identity provider being down or a licence ending never locks the admins out.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Protocol

from factory_auth.store import Role

log = logging.getLogger("factory_auth")
GROUP = "factory_auth.extensions"
POINTS = ("sign_in",)


class Refused(Exception):
    pass


class SignInFailed(Exception):
    """A provider could not prove the identity. `code` is one of a fixed set, shown to the person; the detail is
    for the audit log and the server log only (never the browser: it may hold the identity provider's text)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Identity:
    """Who a provider proved someone is. The account is found by `(issuer, subject)` alone (OIDC Core §5.7);
    `username` names the account on its first sign-in and is otherwise a profile attribute, like `email`."""

    issuer: str
    subject: str
    username: str
    role: Role | None  # None: keep the account's role (no group mapping configured)
    email: str | None = None
    groups: tuple[str, ...] = ()


class SignInProvider(Protocol):
    id: str  # [a-z0-9-], part of the URL
    label: str  # the button: "Sign in with Okta"

    def start(self, redirect_uri: str, state: str) -> tuple[str, dict[str, Any]]:
        """The identity provider's URL to send the browser to, and what `finish` will need (kept server-side)."""
        ...

    def finish(self, params: dict[str, str], pending: dict[str, Any], redirect_uri: str) -> Identity:
        """Prove the identity from the callback's query; raise SignInFailed when it can't be proved."""
        ...

    # Optional: `available() -> bool`, asked before listing and starting (a licence that lapsed while running).


@dataclass
class Loaded:
    name: str
    feature: str
    status: str  # loaded | refused | failed
    reason: str = ""
    points: list[str] = field(default_factory=list)


@dataclass
class Registry:
    edition: str = "core"
    licence: dict[str, Any] | None = None
    providers: dict[str, SignInProvider] = field(default_factory=dict)
    loaded: list[Loaded] = field(default_factory=list)

    def set_edition(self, edition: str, licence: dict[str, Any] | None) -> None:
        self.edition, self.licence = edition, licence

    def provide(self, name: str, feature: str, **points: Any) -> Loaded:
        unknown = [p for p in points if p not in POINTS]
        provider = points.get("sign_in")
        if unknown:
            return self.refuse(name, feature, f"unknown point(s): {', '.join(unknown)}")
        if provider is not None:
            pid = getattr(provider, "id", "")
            if not pid or not pid.replace("-", "").isalnum() or pid != pid.lower():
                return self.refuse(name, feature, f"provider id {pid!r} must be lowercase letters, digits and '-'")
            if pid in self.providers:
                return self.refuse(name, feature, f"a sign-in provider {pid!r} is already registered")
            self.providers[pid] = provider
        entry = Loaded(name, feature, "loaded", points=sorted(points))
        self.loaded.append(entry)
        log.info("auth extension %s loaded (%s)", name, feature)
        return entry

    def refuse(self, name: str, feature: str, reason: str) -> Loaded:
        entry = Loaded(name, feature, "refused", reason)
        self.loaded.append(entry)
        log.warning("auth extension %s refused (%s): %s", name, feature, reason)
        return entry


def load(entry_points: Iterable[Any] | None = None) -> Registry:
    reg = Registry()
    for ep in metadata.entry_points(group=GROUP) if entry_points is None else entry_points:
        name = getattr(ep, "name", str(ep))
        try:
            ep.load().register(reg)
        except Refused as exc:
            reg.refuse(name, "*", str(exc))
        except Exception as exc:
            log.exception("auth extension %s failed to load", name)
            reg.loaded.append(Loaded(name, "*", "failed", f"{type(exc).__name__}: {exc}"))
    return reg
