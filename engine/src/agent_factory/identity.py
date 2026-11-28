"""Who is calling. The engine does not authenticate anyone itself: the auth
gateway (a separate container) does, and nginx forwards its answer as
`X-Auth-User` / `X-Auth-Role`. With `AUTH_MODE=gateway` the engine refuses API
calls without that identity and enforces roles; `off` keeps local dev and tests
simple. Swapping the gateway (e.g. for an SSO proxy) only has to keep the headers.

The headers alone prove nothing: anything that can open a connection to the engine could send
them (assessment 2026-11-15, S-01). So in gateway mode every request also carries the **gateway
proof**, a secret only nginx and the engine hold (`X-Gateway-Proof`, made by `factory-init`, read
from `GATEWAY_PROOF_FILE`). Without it nothing but the container's own health check is answered,
whatever identity the request claims, and the engine refuses to start in gateway mode without one.
"""

from __future__ import annotations

import hmac
import json
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# container health checks, and the repo host's webhook (J3), which proves itself by its signature
PUBLIC_PATHS = {"/api/health", "/api/hooks/github"}
# answered without the gateway proof: only the container's health check, which calls the engine directly
UNPROVED_PATHS = {"/api/health"}
PROOF_HEADER = "x-gateway-proof"
PROOF_MIN_LENGTH = 32
# the account's immutable id from the auth service (`X-Auth-Uid`, ADR-0052): opaque, short, no spaces
UID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


class GatewayProofError(RuntimeError):
    """Gateway mode without a usable proof: the engine must not start."""


def read_proof(path: str | None) -> str:
    """The secret the gateway adds to every request. Missing, unreadable or short refuses (never a default)."""
    if not path:
        raise GatewayProofError(
            "AUTH_MODE=gateway needs GATEWAY_PROOF_FILE: the secret the gateway adds to every request, "
            "so identity headers from anyone else are refused (compose: written by factory-init)"
        )
    try:
        proof = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise GatewayProofError(f"the gateway proof at {path} can't be read: {exc.strerror or exc}") from exc
    if len(proof) < PROOF_MIN_LENGTH or not proof.isascii() or any(ch.isspace() for ch in proof):
        raise GatewayProofError(
            f"the gateway proof at {path} is not usable: at least {PROOF_MIN_LENGTH} characters, no spaces"
        )
    return proof


def proved(sent: str | None, proof: str) -> bool:
    """Whether a request carries the gateway's proof (constant-time; pure)."""
    return bool(sent) and hmac.compare_digest((sent or "").encode(), proof.encode())


@dataclass(frozen=True)
class Identity:
    user: str
    role: str  # admin | member
    # the account's immutable id (ADR-0052, `X-Auth-Uid`): "" when the gateway doesn't send one (AUTH_MODE=off,
    # an older auth service). Recorded beside the name on what a person owns (`Product.created_by_uid`).
    uid: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def is_person(who: Identity, name: str | None, uid: str | None) -> bool:
    """Whether `who` is the person a record names (pure). When the record and the caller both carry the account's
    immutable id, the id decides: a reused or renamed sign-in name never matches someone else's account. A record
    from before ids were recorded, or a caller without one, is matched by name, as before."""
    if not name:
        return False
    if uid and who.uid:
        return hmac.compare_digest(uid.encode(), who.uid.encode())
    return who.user == name


ANONYMOUS = Identity("local", "admin")  # AUTH_MODE=off: single-user laptop
NOBODY = Identity("", "none")  # AUTH_MODE=gateway, a public path with no signed-in caller
_current: ContextVar[Identity] = ContextVar("identity", default=ANONYMOUS)


def current_identity() -> Identity:
    return _current.get()


def requires_admin(method: str, path: str) -> bool:
    """Changing workflows, the skills library or the repository sources (ADR-0047: which credentials
    reach which repos), approving a risky change (ADR-0027), naming a product's owner (principle 7) and
    moving a team repo to another source is admin work; everyone signed in can order, give feedback,
    operate runs and read everything."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    return (
        path.startswith(("/api/workflows", "/api/skills", "/api/repo-sources"))
        or (path.startswith("/api/changes/") and path.endswith("/risk/approve"))
        or (path.startswith("/api/products/") and path.endswith(("/owner", "/repo-source")))
    )


class IdentityMiddleware:
    def __init__(self, app: Any, mode: str, proof: str | None = None) -> None:
        if mode == "gateway" and not proof:
            raise GatewayProofError("gateway mode needs the gateway proof (identity.read_proof)")
        self.app, self.mode, self.proof = app, mode, proof or ""

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return
        if self.mode != "gateway":
            token = _current.set(ANONYMOUS)
            try:
                await self.app(scope, receive, send)
            finally:
                _current.reset(token)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        user, role = headers.get("x-auth-user"), headers.get("x-auth-role")
        uid = headers.get("x-auth-uid") or ""
        path, method = scope["path"], scope["method"]
        if path not in UNPROVED_PATHS and not proved(headers.get(PROOF_HEADER), self.proof):
            # not from the gateway: no identity it claims counts, and the webhook isn't read either
            await _deny(send, 401, "sign in required")
            return
        if path in UNPROVED_PATHS:
            user = role = None  # the health check has no caller: never an identity from headers
            uid = ""
        if uid and not UID_PATTERN.fullmatch(uid):
            # only the gateway sends it; a malformed one is a fault, never read as "no id" (which matches by name)
            await _deny(send, 401, "sign in required")
            return
        if path not in PUBLIC_PATHS:
            if not user or role not in {"admin", "member"}:
                await _deny(send, 401, "sign in required")
                return
            if requires_admin(method, path) and role != "admin":
                await _deny(send, 403, "admin role required")
                return
        # a public path without a signed-in caller is nobody, never the single-user dev admin
        token = _current.set(Identity(user, role, uid) if user and role else NOBODY)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)


async def _deny(send: Any, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})
