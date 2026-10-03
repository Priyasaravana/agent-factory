"""Who is calling. The engine does not authenticate anyone itself: the auth
gateway (a separate container) does, and nginx forwards its answer as
`X-Auth-User` / `X-Auth-Role`. With `AUTH_MODE=gateway` the engine refuses API
calls without that identity and enforces roles; `off` keeps local dev and tests
simple. Swapping the gateway (e.g. for an SSO proxy) only has to keep the headers.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

PUBLIC_PATHS = {"/api/health"}  # container health checks


@dataclass(frozen=True)
class Identity:
    user: str
    role: str  # admin | member

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


ANONYMOUS = Identity("local", "admin")  # AUTH_MODE=off: single-user laptop
_current: ContextVar[Identity] = ContextVar("identity", default=ANONYMOUS)


def current_identity() -> Identity:
    return _current.get()


def requires_admin(method: str, path: str) -> bool:
    """Changing workflows or the skills library, and approving a risky change
    (ADR-0027), is admin work; everyone signed in can order, give feedback,
    operate runs and read everything."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    return path.startswith(("/api/workflows", "/api/skills")) or (
        path.startswith("/api/changes/") and path.endswith("/risk/approve")
    )


class IdentityMiddleware:
    def __init__(self, app: Any, mode: str) -> None:
        self.app, self.mode = app, mode

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
        path, method = scope["path"], scope["method"]
        if path not in PUBLIC_PATHS:
            if not user or role not in {"admin", "member"}:
                await _deny(send, 401, "sign in required")
                return
            if requires_admin(method, path) and role != "admin":
                await _deny(send, 403, "admin role required")
                return
        token = _current.set(Identity(user, role) if user and role else ANONYMOUS)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)


async def _deny(send: Any, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})
