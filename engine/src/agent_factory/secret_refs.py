"""Secret references: credentials are named, never stored.

Config holds a *reference* such as ``env://GITHUB_TOKEN`` or
``aws-sm://factory/github#token``; the engine resolves it just in time, for
one step, in memory. Every resolved value is registered with the redactor, so
it never reaches the event log, the database or the UI, even if a command
prints it. See docs/design/integrations.md (phase 2).

Backends (``scheme://path[#key]``):

    env://NAME                  environment variable, or the file NAME_FILE names (Docker's convention,
                                S-06: .env then holds a path, never the value)
    file:///run/secrets/name    a mounted file (Docker/K8s secrets); #key reads a JSON field
    github-app://APP/INST[#r,r] a GitHub App installation token, minted when asked and valid for an hour (S-06):
                                no long-lived token; the app's private key is a mounted file
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
import urllib.error
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
        if not value and (path := os.environ.get(f"{ref.path}_FILE")):
            # Docker's NAME_FILE convention (S-06): the value is a mounted file, read now, never in the environment
            file = Path(path)
            if not file.is_file():
                raise SecretError(f"{ref}: {ref.path}_FILE names {path}, which is not a file")
            value = file.read_text().strip()
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None  # a token request is never carried to another address


_NO_REDIRECTS = urllib.request.build_opener(_NoRedirect)


class GitHubAppBackend:
    """Short-lived GitHub App installation tokens (S-06), in place of long-lived personal tokens.

    ``github-app://<app id>/<installation id>`` mints a token for the installation (GitHub gives it an hour);
    ``#repo-a,repo-b`` narrows it to those repositories of the installation's owner. The app's private key is a
    mounted file: ``GITHUB_APP_PRIVATE_KEY_FILE`` (default ``/run/secrets/github-app-<app id>.pem``). A token is
    reused, in memory only, until ten minutes before it expires; nothing is written anywhere. The API is
    ``GITHUB_API_URL`` (https only; default api.github.com), and the request never follows a redirect."""

    REUSE_UNTIL_S = 600  # a token with less left than this is replaced, so no step starts on a dying one

    def __init__(
        self,
        opener: Callable[[urllib.request.Request], Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time  # noqa: PLC0415

        self._open = opener or (lambda req: _NO_REDIRECTS.open(req, timeout=15))
        self._clock = clock or time.time
        self._cache: dict[tuple[str, str, str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _parse(ref: SecretRef) -> tuple[str, str, list[str]]:
        app, _, inst = ref.path.strip("/").partition("/")
        if not (app.isdigit() and inst.isdigit()):
            raise SecretError(f"{ref}: expected github-app://<app id>/<installation id>")
        repos = [r.strip() for r in (ref.key or "").split(",") if r.strip()]
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", r) for r in repos):
            raise SecretError(f"{ref}: #… lists repository names of the installation's owner, comma-separated")
        return app, inst, repos

    def _jwt(self, app: str, now: float) -> str:
        import base64  # noqa: PLC0415

        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: PLC0415

        path = Path(os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE") or f"/run/secrets/github-app-{app}.pem")
        if not path.is_file():
            raise SecretError(f"github-app://{app}: the app's private key {path} is not a file")
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except ValueError as exc:
            raise SecretError(f"github-app://{app}: {path} is not a PEM private key") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SecretError(f"github-app://{app}: {path} is not an RSA key (GitHub App keys are RSA)")

        def b64(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).decode().rstrip("=")

        # iat a minute back for clock drift; GitHub refuses an exp more than ten minutes ahead
        claims = {"iat": int(now) - 60, "exp": int(now) + 540, "iss": app}
        head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = b64(json.dumps(claims, separators=(",", ":")).encode())
        sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{b64(sig)}"

    def get(self, ref: SecretRef) -> str:
        from datetime import datetime  # noqa: PLC0415

        app, inst, repos = self._parse(ref)
        api = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        if not api.startswith("https://"):
            raise SecretError(f"{ref}: GITHUB_API_URL must be https")
        cache_key = (api, app, inst, ",".join(sorted(repos)))
        now = self._clock()
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit and hit[1] - now > self.REUSE_UNTIL_S:
                return hit[0]
            req = urllib.request.Request(  # noqa: S310 - https, checked above
                f"{api}/app/installations/{inst}/access_tokens",
                data=json.dumps({"repositories": repos} if repos else {}).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._jwt(app, now)}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                },
            )
            try:
                with self._open(req) as resp:
                    data = json.loads(resp.read())
            except urllib.error.HTTPError as exc:  # the status only: a response body could echo a request
                why = {401: "the app id or its private key was refused", 404: "no such installation for this app"}
                raise SecretError(f"{ref}: GitHub answered {exc.code} ({why.get(exc.code, 'refused')})") from None
            except Exception as exc:  # noqa: BLE001 - network errors vary; never include a value
                raise SecretError(f"{ref}: {type(exc).__name__}") from None
            token, expires = data.get("token"), data.get("expires_at")
            if not isinstance(token, str) or not token or not isinstance(expires, str):
                raise SecretError(f"{ref}: GitHub's answer had no token")
            try:
                until = datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
            except ValueError:
                until = now + 3600 - 60
            self._cache[cache_key] = (token, until)
            return token


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

    def knows(self, value: str) -> bool:
        """True for a value the engine resolved or read as a secret (masked wherever it is stored)."""
        with self._lock:
            return value in self._values

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
_GITHUB_APP = GitHubAppBackend()  # process-wide, so a token is reused across steps until near its expiry


# ------------------------------------------------------------------ resolver --
BackendFactory = Callable[[Any], SecretBackend]
_BACKENDS: dict[str, BackendFactory] = {
    "env": lambda settings: EnvBackend(settings),
    "file": lambda settings: FileBackend(),
    "aws-sm": lambda settings: AwsSecretsManagerBackend(),
    "vault": lambda settings: VaultBackend(),
    "github-app": lambda settings: _GITHUB_APP,
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
