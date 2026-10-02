"""Pluggable delivery providers. See providers/base.py and docs/design/integrations.md."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from agent_factory.providers.base import (
    CAPABILITIES,
    CheckContext,
    ImageRef,
    Provider,
    ProviderError,
    ProviderSet,
    Readiness,
    StepResult,
)
from agent_factory.providers.helm import HelmProvider
from agent_factory.providers.local import LocalProvider
from agent_factory.providers.oci import OciRegistryProvider

if TYPE_CHECKING:
    from agent_factory.config import FactoryConfig

__all__ = [
    "CAPABILITIES",
    "CheckContext",
    "ImageRef",
    "HelmProvider",
    "LocalProvider",
    "OciRegistryProvider",
    "Provider",
    "ProviderError",
    "ProviderSet",
    "Providers",
    "Readiness",
    "StepResult",
    "register_kind",
]

ProviderFactory = Callable[[str, dict[str, Any]], Provider]
_KINDS: dict[str, ProviderFactory] = {"local": LocalProvider, "oci": OciRegistryProvider, "helm": HelmProvider}
ENTRY_POINT_GROUP = "agent_factory.providers"


def register_kind(kind: str, factory: ProviderFactory) -> None:
    """Add a provider type (companies can also ship one as a Python entry point
    in the `agent_factory.providers` group, without forking the engine)."""
    _KINDS[kind] = factory


def _load_entry_points() -> None:
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name not in _KINDS:
            _KINDS[ep.name] = ep.load()


class Providers:
    """All configured integrations, instantiated once; resolves an environment
    to one provider per capability."""

    def __init__(self, cfg: FactoryConfig) -> None:
        _load_entry_points()
        self.cfg = cfg
        self.integrations: dict[str, Provider] = {}
        for name, integ in cfg.integrations.items():
            factory = _KINDS.get(integ.provider)
            if factory is None:
                raise ProviderError(
                    f"integration '{name}': unknown provider '{integ.provider}' (known: {sorted(_KINDS)})"
                )
            provider = factory(name, dict(integ.settings))
            provider.auth = integ.auth  # a reference, resolved by the provider when it needs it
            self.integrations[name] = provider
        for env_name, env in cfg.environments.items():
            for cap in CAPABILITIES:
                self._bind(env_name, cap, getattr(env, cap))  # fail at startup, not mid-run
        for pl_name, pl in cfg.product_lines.items():
            if pl.environment not in cfg.environments:
                raise ProviderError(f"product line '{pl_name}': unknown environment '{pl.environment}'")

    def _bind(self, env_name: str, cap: str, integration: str) -> Provider:
        p = self.integrations.get(integration)
        if p is None:
            raise ProviderError(f"environment '{env_name}': {cap} uses unknown integration '{integration}'")
        if cap not in p.capabilities:
            raise ProviderError(f"environment '{env_name}': integration '{integration}' ({p.kind}) cannot do {cap}")
        return p

    def for_environment(self, env_name: str) -> ProviderSet:
        env = self.cfg.environments.get(env_name)
        if env is None:
            raise ProviderError(f"unknown environment '{env_name}'")
        bound = {cap: self._bind(env_name, cap, getattr(env, cap)) for cap in CAPABILITIES}
        return ProviderSet(environment=env_name, **bound)  # type: ignore[arg-type]

    def for_product_line(self, product_line: str) -> ProviderSet:
        return self.for_environment(self.cfg.product_lines[product_line].environment)
