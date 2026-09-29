"""Secret references: credentials are named, never stored.

Config holds a *reference* such as ``env://GITHUB_TOKEN`` or
``aws-sm://factory/github#token``; the engine resolves it just in time, for
one step, in memory. Every resolved value is registered with the redactor, so
it never reaches the event log, the database or the UI, even if a command
prints it. See docs/design/integrations.md (phase 2).

Backends (``scheme://path[#key]``):

    env://NAME                  environment variable (dev / docker-compose .env)
    file:///run/secrets/name    a mounted file (Docker/K8s secrets); #key reads a JSON field
    aws-sm://secret-id#key      AWS Secrets Manager (IRSA / default credential chain)
    vault://mount/path#key      HashiCorp Vault KV v2 (VAULT_ADDR + VAULT_TOKEN[_FILE])

More backends plug in via register_backend or the ``agent_factory.secret_backends``
entry point group.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

_REF = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*)://(?P<path>[^#]*)(?:#(?P<key>.+))?$")
MASK = "***"
MIN_REDACT_LEN = 6  # shorter values would mask ordinary words


class SecretError(Exception):
    """A reference could not be parsed or resolved. Never contains the value."""


@dataclass(frozen=True)
class SecretRef:
    scheme: str
    path: str
    key: str | None = None

    @classmethod
    def parse(cls, ref: str) -> SecretRef:
        m = _REF.match(ref.strip())
        if not m or not m.group("path"):
            raise SecretError(f"invalid secret reference '{ref}' (expected scheme://path[#key])")
        return cls(m.group("scheme"), m.group("path"), m.group("key"))

    def __str__(self) -> str:
        return f"{self.scheme}://{self.path}" + (f"#{self.key}" if self.key else "")


class SecretBackend(Protocol):
    def get(self, ref: SecretRef) -> str: ...


def _pick_key(raw: str, ref: SecretRef) -> str:
    if not ref.key:
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretError(f"{ref}: value is not JSON, cannot read key '{ref.key}'") from exc
    if not isinstance(data, dict) or ref.key not in data:
        raise SecretError(f"{ref}: key '{ref.key}' not found")
    return str(data[ref.key])


class EnvBackend:
    """Environment variables, then same-named Settings fields (.env loaded by pydantic)."""

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    def get(self, ref: SecretRef) -> str:
        value = os.environ.get(ref.path)
        if not value and self.settings is not None:
            value = getattr(self.settings, ref.path.lower(), None)
        if not value:
            raise SecretError(f"{ref} is not set")
        return _pick_key(str(value), ref)


class FileBackend:
    def get(self, ref: SecretRef) -> str:
        path = Path("/" + ref.path.lstrip("/"))
        if not path.is_file():
            raise SecretError(f"{ref}: file not found")
        return _pick_key(path.read_text().strip(), ref)


class AwsSecretsManagerBackend:
    """Uses the default AWS credential chain (IRSA on EKS, SSO/profile locally).
    Needs the `aws` extra (boto3)."""

    def __init__(self, client_factory: Callable[[], Any] | None = None, region: str | None = None) -> None:
        self._client_factory = client_factory
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

    def _client(self) -> Any:
        if self._client_factory:
            return self._client_factory()
        try:
            import boto3  # noqa: PLC0415 - optional dependency
        except ImportError as exc:
            raise SecretError("aws-sm:// needs boto3: install the engine with the `aws` extra") from exc
        return boto3.client("secretsmanager", region_name=self.region)

    def get(self, ref: SecretRef) -> str:
        try:
            resp = self._client().get_secret_value(SecretId=ref.path)
        except SecretError:
            raise
        except Exception as exc:  # noqa: BLE001 - boto errors vary; never include the value
            raise SecretError(f"{ref}: {type(exc).__name__}") from exc
        return _pick_key(resp.get("SecretString") or "", ref)


class VaultBackend:
    """KV v2: vault://<mount>/<path>#<key>. Token from VAULT_TOKEN or VAULT_TOKEN_FILE."""

    def __init__(self, opener: Callable[[urllib.request.Request], Any] | None = None) -> None:
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=10))  # noqa: S310 - https URL from config

    def get(self, ref: SecretRef) -> str:
        addr = os.environ.get("VAULT_ADDR")
        token = os.environ.get("VAULT_TOKEN")
        token_file = os.environ.get("VAULT_TOKEN_FILE")
        if not token and token_file and Path(token_file).is_file():
            token = Path(token_file).read_text().strip()
        if not addr or not token:
            raise SecretError(f"{ref}: VAULT_ADDR and VAULT_TOKEN (or VAULT_TOKEN_FILE) are required")
        mount, _, path = ref.path.partition("/")
        if not path:
            raise SecretError(f"{ref}: expected vault://<mount>/<path>")
        req = urllib.request.Request(  # noqa: S310
            f"{addr.rstrip('/')}/v1/{mount}/data/{path}", headers={"X-Vault-Token": token}
        )
        try:
            with self._open(req) as resp:
                data = json.loads(resp.read())["data"]["data"]
        except Exception as exc:  # noqa: BLE001
            raise SecretError(f"{ref}: {type(exc).__name__}") from exc
        if ref.key:
            if ref.key not in data:
                raise SecretError(f"{ref}: key '{ref.key}' not found")
            return str(data[ref.key])
        if len(data) != 1:
            raise SecretError(f"{ref}: secret has {len(data)} keys; name one with #key")
        return str(next(iter(data.values())))


# ------------------------------------------------------------------ redactor --
class Redactor:
    """Masks every secret value the engine has resolved, wherever text is stored."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str) -> None:
        if len(value) >= MIN_REDACT_LEN:
            with self._lock:
                self._values.add(value)

    def text(self, s: str) -> str:
        for v in sorted(self._values, key=len, reverse=True):  # longest first
            if v in s:
                s = s.replace(v, MASK)
        return s

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [self.obj(v) for v in o]
        return o


REDACTOR = Redactor()  # process-wide: every store write goes through it


# ------------------------------------------------------------------ resolver --
BackendFactory = Callable[[Any], SecretBackend]
_BACKENDS: dict[str, BackendFactory] = {
    "env": lambda settings: EnvBackend(settings),
    "file": lambda settings: FileBackend(),
    "aws-sm": lambda settings: AwsSecretsManagerBackend(),
    "vault": lambda settings: VaultBackend(),
}


def register_backend(scheme: str, factory: BackendFactory) -> None:
    _BACKENDS[scheme] = factory


def _load_entry_points() -> None:
    for ep in entry_points(group="agent_factory.secret_backends"):
        _BACKENDS.setdefault(ep.name, ep.load())


class SecretResolver:
    """Resolves references just in time. Values are returned to the caller and
    registered for redaction; nothing is cached or written anywhere."""

    def __init__(self, settings: Any | None = None, redactor: Redactor = REDACTOR) -> None:
        _load_entry_points()
        self.settings = settings
        self.redactor = redactor
        self._instances: dict[str, SecretBackend] = {}

    def backend(self, scheme: str) -> SecretBackend:
        if scheme not in self._instances:
            factory = _BACKENDS.get(scheme)
            if factory is None:
                raise SecretError(f"unknown secret backend '{scheme}://' (known: {sorted(_BACKENDS)})")
            self._instances[scheme] = factory(self.settings)
        return self._instances[scheme]

    def resolve(self, ref: str) -> str:
        parsed = SecretRef.parse(ref)
        value = self.backend(parsed.scheme).get(parsed)
        if not value:
            raise SecretError(f"{parsed} is empty")
        self.redactor.add(value)
        return value

    def resolve_optional(self, ref: str | None) -> str | None:
        if not ref:
            return None
        try:
            return self.resolve(ref)
        except SecretError:
            return None

    def available(self, ref: str | None) -> tuple[bool, str]:
        """Whether a reference resolves, and why not (never the value)."""
        if not ref:
            return False, "no secret reference configured"
        try:
            self.resolve(ref)
            return True, ""
        except SecretError as exc:
            return False, str(exc)
