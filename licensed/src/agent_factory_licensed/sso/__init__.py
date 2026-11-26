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
        provider: Callable[[OidcConfig], Any] = OidcProvider,
    ) -> None:
        self._check, self._config, self._provider = check, config, provider

    def register(self, registry: Any) -> None:
        lic = self._check()
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
        registry.provide("oidc", FEATURE, sign_in=self._provider(cfg))


auth_extension = AuthEnterprise()
