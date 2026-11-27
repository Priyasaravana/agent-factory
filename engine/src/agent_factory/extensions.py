"""Extension points (ADR-0051, phase C): where the licensed edition plugs into the Apache-2.0 core.

The core never imports `licensed/` (a CI guard, `tests/test_licence_boundary.py`, fails if anything
outside it does). It only looks for installed Python entry points in the group `GROUP` and lets each one
register into a `Registry`. Nothing is found in the core image, so the registry stays empty and every
point falls back to core behaviour. The enterprise image installs `licensed/`, whose one entry point
checks the licence key (in `licensed/`, never here) and registers only the features the key grants;
the rest it records as refused, with the reason.

Points:
- `evidence_signer`: how sealed evidence is signed (a customer KMS, phase D). Used only when the install names
  it (`AGENT_FACTORY_EVIDENCE_SIGNER`); a named signer that didn't load makes sealing fail visibly, never fall
  back to the local key (`signing.signer_for`). Verifying stays in core: whatever signs must produce an SSHSIG
  that `signing.check` accepts, and its keys are the trusted set.
- `audit_sink`: receives the factory's audit events (SIEM export, phase D). Core emits; a sink that
  fails is logged and never stops the factory.
- `report_pack`: renders a report from a change's sealed evidence (compliance pack, phase D).
- `ui_slot`: a named place in the factory UI an extension fills (shown on Settings → Edition).

One factory serves one customer (ADR-0051 §9): there is no tenancy point. Tenancy, when it comes, is enforced
by the core (principal, membership, active tenant, resource ownership), never by an extension.

Registration is all or nothing: each extension registers into a staged copy of the registry, which is published
only when `register` returns, so one that raises half-way leaves nothing of itself behind.

Safety is never an extension: the policy floor, sandbox, evidence sealing, signing and the verifier,
MFA and local sign-in stay in core, and no point here can switch one off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from agent_factory.signing import Signer

log = logging.getLogger(__name__)

GROUP = "agent_factory.extensions"
POINTS = ("evidence_signer", "audit_sink", "report_pack", "ui_slot")
SINGLE = {"evidence_signer"}  # one provider at most; a second one is refused


class Refused(Exception):
    """Raised by an extension that declines to load (no licence, wrong feature): recorded, never fatal."""


class AuditSink(Protocol):
    def emit(self, event: dict[str, Any]) -> None: ...


class ReportPack(Protocol):
    id: str
    title: str

    def render(self, evidence_folder: Path) -> dict[str, bytes]:
        """File name -> content, built only from the sealed evidence in the folder."""
        ...


class Extension(Protocol):
    """What an entry point in `GROUP` resolves to."""

    name: str

    def register(self, registry: Registry) -> None: ...


@dataclass
class Loaded:
    """One extension (or one feature an extension offered) and what became of it."""

    name: str
    feature: str
    status: str  # loaded | refused | failed
    reason: str = ""
    points: list[str] = field(default_factory=list)


@dataclass
class Registry:
    edition: str = "core"
    licence: dict[str, Any] | None = None  # what the licensed edition says about its key (no key material)
    licence_live: Callable[[], dict[str, Any]] | None = None  # the same, checked now (keys expire while running)
    signer_factory: Callable[[Path], Signer] | None = None
    audit_sinks: list[tuple[str, AuditSink]] = field(default_factory=list)
    report_packs: list[ReportPack] = field(default_factory=list)
    ui_slots: dict[str, str] = field(default_factory=dict)  # slot -> extension name
    loaded: list[Loaded] = field(default_factory=list)
    _providers: dict[str, str] = field(default_factory=dict)

    # -- what an extension calls
    def set_edition(
        self,
        edition: str,
        licence: dict[str, Any] | None,
        live: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.edition = edition
        self.licence = licence
        self.licence_live = live

    def licence_now(self) -> dict[str, Any] | None:
        """The licence as it stands now, when the edition can say; else what it said at start."""
        if self.licence_live is None:
            return self.licence
        try:
            return self.licence_live()
        except Exception as exc:  # noqa: BLE001 — a failing check reads as an invalid licence, never a 500
            return {"valid": False, "problem": f"licence check failed: {type(exc).__name__}"}

    def _stage(self) -> Registry:
        """A copy to register into; published by `load` only if registration completes."""
        return replace(
            self,
            audit_sinks=list(self.audit_sinks),
            report_packs=list(self.report_packs),
            ui_slots=dict(self.ui_slots),
            loaded=list(self.loaded),
            _providers=dict(self._providers),
        )

    def provide(self, name: str, feature: str, **points: Any) -> Loaded:
        """Register `name` (granting `feature`) at the given points. All or nothing: an unknown point, or a
        single point already taken, refuses the whole feature."""
        unknown = [p for p in points if p not in POINTS]
        taken = [p for p in points if p in SINGLE and p in self._providers]
        if unknown or taken:
            why = f"unknown point(s): {', '.join(unknown)}" if unknown else ""
            why = why or f"already provided: {', '.join(f'{p} by {self._providers[p]}' for p in taken)}"
            return self.refuse(name, feature, why)
        for point, value in points.items():
            if point == "evidence_signer":
                self.signer_factory = value
            elif point == "audit_sink":
                self.audit_sinks.append((name, value))
            elif point == "report_pack":
                self.report_packs.append(value)
            elif point == "ui_slot":
                for slot in [value] if isinstance(value, str) else value:
                    self.ui_slots[slot] = name
            self._providers.setdefault(point, name)
        entry = Loaded(name, feature, "loaded", points=sorted(points))
        self.loaded.append(entry)
        log.info("extension %s loaded (%s): %s", name, feature, ", ".join(entry.points) or "no points")
        return entry

    def refuse(self, name: str, feature: str, reason: str) -> Loaded:
        entry = Loaded(name, feature, "refused", reason)
        self.loaded.append(entry)
        log.warning("extension %s refused (%s): %s", name, feature, reason)
        return entry

    # -- what core calls
    def signer(self, data_dir: Path | str, name: str) -> Signer | None:
        """The signer extension `name` provides, or None when it isn't loaded."""
        if self.signer_factory is None or self._providers.get("evidence_signer") != name:
            return None
        return self.signer_factory(Path(data_dir))

    def audit(self, kind: str, **data: Any) -> None:
        """Hand an audit event to every sink. Never raises: a broken sink is logged, the factory goes on."""
        if not self.audit_sinks:
            return
        event = {"kind": kind, "at": datetime.now(UTC).isoformat(), **data}
        for name, sink in self.audit_sinks:
            try:
                sink.emit(event)
            except Exception:  # noqa: BLE001 — an export failing must never stop a change
                log.exception("audit sink %s failed on %s", name, kind)


def _entry_points() -> Iterable[Any]:
    return metadata.entry_points(group=GROUP)


def load(entry_points: Iterable[Any] | None = None) -> Registry:
    """Load every installed extension into a fresh registry. One that raises is recorded as failed (or
    refused, for `Refused`) and the factory starts on core behaviour for it."""
    reg = Registry()
    for ep in _entry_points() if entry_points is None else entry_points:
        name = getattr(ep, "name", str(ep))
        staged = reg._stage()
        try:
            ext = ep.load()
            ext.register(staged)
            reg = staged  # published whole, only now
        except Refused as exc:
            reg.refuse(name, "*", str(exc))
        except Exception as exc:  # noqa: BLE001 — a broken extension never stops the core
            log.exception("extension %s failed to load", name)
            reg.loaded.append(Loaded(name, "*", "failed", f"{type(exc).__name__}: {exc}"))
    return reg


_active = Registry()


def active() -> Registry:
    return _active


def activate(registry: Registry) -> Registry:
    global _active
    _active = registry
    return registry
