# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""The licensed edition's one entry point into the core (ADR-0051).

Installed in the enterprise image, it is found by the core under the `agent_factory.extensions` entry
point group. It checks the licence key once, says which edition is running and what the key grants, then
registers each feature the key covers. A feature this build has but the key doesn't cover is recorded as
refused, with the reason, so Settings → Edition shows what a person would get with a key.

It never imports the core (the registry is passed in), so the core and this package meet only at the
registry's methods: `set_edition`, `provide`, `refuse`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_factory_licensed import licence as _licence
from agent_factory_licensed import siem

EDITION = "enterprise"


def _siem(reg: Any) -> None:
    reg.provide("audit-file", "siem", audit_sink=siem.AuditFile(siem.default_dir()))


# feature -> how it registers. Phase D adds sso, kms and compliance here.
FEATURES: dict[str, Callable[[Any], None]] = {"siem": _siem}


class Enterprise:
    name = "licensed"

    def __init__(self, check: Callable[[], _licence.Licence] = _licence.current) -> None:
        self._check = check

    def register(self, registry: Any) -> None:
        lic = self._check()
        registry.set_edition(EDITION, lic.summary())
        for feature, provide in FEATURES.items():
            if not lic.valid:
                registry.refuse(feature, feature, f"licence: {lic.problem}")
            elif not lic.grants(feature):
                registry.refuse(feature, feature, "the licence does not include this feature")
            else:
                provide(registry)


extension = Enterprise()
