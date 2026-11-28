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

import logging
import os
from collections.abc import Callable
from typing import Any

from agent_factory_licensed import licence as _licence
from agent_factory_licensed import siem
from agent_factory_licensed.siem import delivery

EDITION = "enterprise"
log = logging.getLogger(__name__)


class GatedSink:
    """An audit sink that works only while the licence covers its feature; checked before every event."""

    def __init__(self, sink: Any, feature: str, live: _licence.Live) -> None:
        self.sink, self.feature, self.live = sink, feature, live
        self._stopped = False

    def emit(self, event: dict[str, Any]) -> None:
        if not self.live.grants(self.feature):
            if not self._stopped:  # once per lapse, not once per event
                log.warning("audit export stopped: %s", self.live.why_not(self.feature))
            self._stopped = True
            return
        self._stopped = False
        self.sink.emit(event)

    def status(self) -> list[dict[str, object]]:
        """What the sink reports (`Registry.audit_status`); when the licence has lapsed, says so first."""
        rows = list(self.sink.status()) if hasattr(self.sink, "status") else []
        if not self.live.grants(self.feature):
            rows = [{**r, "state": "paused", "detail": self.live.why_not(self.feature)} for r in rows]
        return rows


def _siem(reg: Any, live: _licence.Live, env: dict[str, str] | None = None) -> None:
    env = dict(os.environ) if env is None else env
    folder = siem.default_dir(env)
    forwarders: list[delivery.Forwarder] = []
    problem = ""
    try:
        start = delivery.start_from_env(env)
        forwarders = [
            delivery.Forwarder(folder, t, lambda: live.grants("siem"), lambda: live.why_not("siem"), start)
            for t in delivery.transports_from_env(env)
        ]
    except (ValueError, OSError) as exc:  # a mistyped destination: the record is still written, delivery refused
        problem = str(exc)
    export = siem.Export(siem.AuditFile(folder), forwarders)
    reg.provide("audit-file", "siem", audit_sink=GatedSink(export, "siem", live))
    if problem:
        reg.refuse("siem-delivery", "siem", f"delivery settings: {problem}")
    elif forwarders:
        reg.provide("siem-delivery", "siem")
        export.start()  # delivery threads, daemon: they read the record and never block an event


def _sso(reg: Any, live: _licence.Live) -> None:
    # sign-in itself runs in the auth service (agent_factory_licensed.sso); the engine shows the licence's state
    reg.provide("sso", "sso")


# feature -> how it registers. Phase D adds kms and compliance here.
FEATURES: dict[str, Callable[[Any, _licence.Live], None]] = {"siem": _siem, "sso": _sso}


class Enterprise:
    name = "licensed"

    def __init__(self, check: Callable[[], _licence.Licence] = _licence.current, ttl: float = 600.0) -> None:
        self._check, self._ttl = check, ttl

    def register(self, registry: Any) -> None:
        live = _licence.Live(self._check, self._ttl)
        lic = live.now()
        registry.set_edition(EDITION, lic.summary(), live=lambda: live.now().summary())
        # A feature the key doesn't cover at start is refused (restart to add it). One it covers is registered,
        # and each use asks `live` again, so expiry while running stops it without a restart.
        for feature, provide in FEATURES.items():
            if lic.grants(feature):
                provide(registry, live)
            else:
                registry.refuse(feature, feature, live.why_not(feature))


extension = Enterprise()
