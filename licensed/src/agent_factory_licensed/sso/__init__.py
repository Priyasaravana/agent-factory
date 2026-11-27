# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""Single sign-on (feature `sso`, ADR-0052): the licensed edition's entry point into the auth service.

Found by the auth service under the `factory_auth.extensions` entry point group (the auth image's enterprise
target installs this package). It registers the OpenID Connect provider only when the licence key covers `sso`
and `AUTH_OIDC_ISSUER` is set; otherwise it records why not. Local sign-in is core and stays on either way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_factory_licensed import licence as _licence
from agent_factory_licensed.sso.oidc import OidcConfig, OidcProvider

FEATURE = "sso"


class AuthEnterprise:
    name = "licensed"

    def __init__(
        self,
        check: Callable[[], _licence.Licence] = _licence.current,
        config: Callable[[], OidcConfig | None] = OidcConfig.from_env,
        provider: Callable[..., Any] = OidcProvider,
        ttl: float = 600.0,
    ) -> None:
        self._check, self._config, self._provider, self._ttl = check, config, provider, ttl

    def register(self, registry: Any) -> None:
        live = _licence.Live(self._check, self._ttl)
        lic = live.now()
        registry.set_edition("enterprise", lic.summary())
        if not lic.valid:
            registry.refuse("oidc", FEATURE, f"licence: {lic.problem}")
            return
        if not lic.grants(FEATURE):
            registry.refuse("oidc", FEATURE, "the licence does not include this feature")
            return
        try:
            cfg = self._config()
        except ValueError as exc:
            registry.refuse("oidc", FEATURE, f"set up wrongly: {exc}")
            return
        if cfg is None:
            registry.refuse("oidc", FEATURE, "not set up: AUTH_OIDC_ISSUER is empty")
            return
        # checked again before every listing, start and callback (PR #220/#221 review: expiry while running)
        registry.provide("oidc", FEATURE, sign_in=self._provider(cfg, licensed=lambda: live.grants(FEATURE)))


auth_extension = AuthEnterprise()
