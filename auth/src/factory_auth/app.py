"""The auth gateway service.

Sits beside the factory as its own container. nginx asks it about every API
request (`auth_request` -> GET /auth/check) and forwards the answer to the
engine as `X-Auth-User` / `X-Auth-Role`. The engine never sees passwords,
sessions or tokens, and this service can be replaced (e.g. by an SSO proxy
that sets the same headers) without touching the factory.
"""

import logging
import os
import secrets
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from factory_auth.store import AuthError, Role, Store, User

log = logging.getLogger("factory_auth")
COOKIE = "factory_session"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("AUTH_DATA_DIR", "/data")))
    session_hours: int = field(default_factory=lambda: int(os.environ.get("AUTH_SESSION_HOURS", "12")))
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("AUTH_COOKIE_SECURE", "false") == "true")
    admin_user: str = field(default_factory=lambda: os.environ.get("FACTORY_ADMIN_USER", "admin"))
    admin_password_ref: str = field(
        default_factory=lambda: os.environ.get("FACTORY_ADMIN_PASSWORD_REF", "env://FACTORY_ADMIN_PASSWORD")
    )


def resolve_ref(ref: str) -> str | None:
    """env://NAME or file:///path; the auth service needs nothing more to bootstrap."""
    if ref.startswith("env://"):
        return os.environ.get(ref[6:]) or None
    if ref.startswith("file://"):
        p = Path(ref[7:])
        return p.read_text().strip() if p.is_file() else None
    raise ValueError(f"unsupported secret reference for the admin password: {ref}")


def bootstrap(store: Store, s: Settings) -> str | None:
    """Create the first admin. Returns the path of a generated password file, if one was needed."""
    if store.count_users():
        return None
    password = resolve_ref(s.admin_password_ref)
    if password:
        store.create_user(s.admin_user, password, "admin")
        store.audit(None, "bootstrap admin (password from secret reference)", s.admin_user)
        return None
    password = secrets.token_urlsafe(18)
    store.create_user(s.admin_user, password, "admin", must_change=True)
    path = s.data_dir / "initial-admin-password"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password + "\n")
    path.chmod(0o600)
    store.audit(None, "bootstrap admin (generated one-time password)", s.admin_user)
    log.warning("created user '%s' with a one-time password in %s (change it at first sign-in)", s.admin_user, path)
    return str(path)


class Throttle:
    """Slows brute force: after MAX_FAILURES failed sign-ins for a user, lock it briefly."""

    def __init__(self) -> None:
        self.failures: dict[str, list[float]] = defaultdict(list)

    def locked(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.failures[key] if now - t < LOCKOUT_SECONDS]
        self.failures[key] = recent
        return len(recent) >= MAX_FAILURES

    def fail(self, key: str) -> None:
        self.failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)


# ------------------------------------------------------------------ models --
class LoginInput(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class UserView(BaseModel):
    username: str
    role: Role
    disabled: bool
    must_change: bool
    created_at: str


class CreateUserInput(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    role: Role = "member"
    password: str = Field(max_length=256)  # temporary; the user must change it at first sign-in


class UpdateUserInput(BaseModel):
    role: Role | None = None
    disabled: bool | None = None
    reset_password: str | None = Field(default=None, max_length=256)


class ChangePasswordInput(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class TokenInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class TokenView(BaseModel):
    id: str
    name: str
    username: str
    created_at: str
    last_used_at: str | None


class NewTokenView(TokenView):
    token: str  # shown once


def _view(u: User) -> UserView:
    return UserView(**asdict(u))


# --------------------------------------------------------------------- app --
def create_app(store: Store | None = None, settings: Settings | None = None) -> FastAPI:
    s = settings or Settings()
    st = store or Store(s.data_dir / "auth.db")
    bootstrap(st, s)
    throttle = Throttle()
    app = FastAPI(title="Factory auth", docs_url="/auth/docs", openapi_url="/auth/openapi.json", redoc_url=None)
    app.state.store = st

    def current(request: Request, authorization: Annotated[str | None, Header()] = None) -> tuple[User, str]:
        """(user, how) from a Bearer API token or the session cookie."""
        if authorization and authorization.lower().startswith("bearer "):
            user = st.token_user(authorization[7:].strip())
            if user:
                return user, "token"
            raise HTTPException(401, "invalid API token")
        sid = request.cookies.get(COOKIE)
        user = st.session_user(sid) if sid else None
        if user:
            return user, "session"
        raise HTTPException(401, "sign in required")

    Current = Annotated[tuple[User, str], Depends(current)]

    def admin(cur: Current) -> User:
        user, _ = cur
        if user.role != "admin":
            raise HTTPException(403, "admin only")
        if user.must_change:
            raise HTTPException(403, "change your password first")
        return user

    Admin = Annotated[User, Depends(admin)]

    @app.get("/auth/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/check")
    def check(cur: Current, response: Response) -> dict[str, str]:
        """For nginx auth_request: 200 + identity headers, 401 unauthenticated, 403 must change password."""
        user, how = cur
        if user.must_change:
            raise HTTPException(403, "password change required")
        response.headers["X-Auth-User"] = user.username
        response.headers["X-Auth-Role"] = user.role
        response.headers["X-Auth-Method"] = how
        return {"user": user.username, "role": user.role}

    @app.post("/auth/login")
    def login(body: LoginInput, request: Request, response: Response) -> UserView:
        key = body.username.lower()
        if throttle.locked(key):
            raise HTTPException(429, f"too many attempts; try again in {LOCKOUT_SECONDS} seconds")
        user = st.authenticate(body.username, body.password)
        if not user:
            throttle.fail(key)
            st.audit(body.username, "sign-in failed")
            raise HTTPException(401, "wrong username or password")
        throttle.reset(key)
        sid = st.create_session(user.username, timedelta(hours=s.session_hours))
        response.set_cookie(
            COOKIE,
            sid,
            max_age=s.session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=s.cookie_secure,
            path="/",
        )
        st.audit(user.username, "signed in")
        return _view(user)

    @app.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        sid = request.cookies.get(COOKIE)
        if sid:
            st.delete_session(sid)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/auth/me")
    def me(cur: Current) -> UserView:
        return _view(cur[0])

    @app.post("/auth/me/password")
    def change_password(body: ChangePasswordInput, cur: Current, request: Request, response: Response) -> UserView:
        user, how = cur
        if how != "session":
            raise HTTPException(403, "change passwords from a signed-in session, not an API token")
        if not st.authenticate(user.username, body.current_password):
            raise HTTPException(401, "current password is wrong")
        try:
            st.set_password(user.username, body.new_password)  # also signs out other sessions
        except AuthError as exc:
            raise HTTPException(422, str(exc)) from exc
        sid = st.create_session(user.username, timedelta(hours=s.session_hours))
        response.set_cookie(
            COOKIE,
            sid,
            max_age=s.session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=s.cookie_secure,
            path="/",
        )
        st.audit(user.username, "changed password")
        if user.username == s.admin_user:
            # the one-time password is spent: stop `make up` from printing a stale one
            (s.data_dir / "initial-admin-password").unlink(missing_ok=True)
        return _view(st.get_user(user.username))  # type: ignore[arg-type]

    # ---- API tokens (own) ----
    @app.get("/auth/me/tokens")
    def my_tokens(cur: Current) -> list[TokenView]:
        return [TokenView(**asdict(t)) for t in st.list_tokens(cur[0].username)]

    @app.post("/auth/me/tokens")
    def create_token(body: TokenInput, cur: Current) -> NewTokenView:
        user, how = cur
        if how != "session":
            raise HTTPException(403, "create API tokens from a signed-in session")
        if user.must_change:
            raise HTTPException(403, "change your password first")
        tok, raw = st.create_token(user.username, body.name)
        st.audit(user.username, "created API token", tok.name)
        return NewTokenView(**asdict(tok), token=raw)

    @app.delete("/auth/me/tokens/{tid}")
    def revoke_token(tid: str, cur: Current) -> dict[str, bool]:
        if not st.revoke_token(tid, cur[0].username):
            raise HTTPException(404, "token not found")
        st.audit(cur[0].username, "revoked API token", tid)
        return {"ok": True}

    # ---- admin: users ----
    @app.get("/auth/users")
    def list_users(_: Admin) -> list[UserView]:
        return [_view(u) for u in st.list_users()]

    @app.post("/auth/users")
    def create_user(body: CreateUserInput, who: Admin) -> UserView:
        try:
            u = st.create_user(body.username, body.password, body.role, must_change=True)
        except AuthError as exc:
            raise HTTPException(409, str(exc)) from exc
        st.audit(who.username, f"created {body.role}", body.username)
        return _view(u)

    @app.patch("/auth/users/{username}")
    def update_user(username: str, body: UpdateUserInput, who: Admin) -> UserView:
        try:
            u = st.update_user(username, body.role, body.disabled)
            if body.reset_password:
                st.set_password(username, body.reset_password, must_change=True)
                u = st.get_user(username)  # type: ignore[assignment]
        except AuthError as exc:
            raise HTTPException(409, str(exc)) from exc
        changes = [k for k, v in body.model_dump().items() if v is not None and k != "reset_password"]
        if body.reset_password:
            changes.append("password reset")
        st.audit(who.username, "updated user: " + ", ".join(changes), username)
        return _view(u)

    @app.delete("/auth/users/{username}")
    def delete_user(username: str, who: Admin) -> dict[str, bool]:
        if username == who.username:
            raise HTTPException(409, "you cannot delete yourself")
        try:
            st.delete_user(username)
        except AuthError as exc:
            raise HTTPException(409, str(exc)) from exc
        st.audit(who.username, "deleted user", username)
        return {"ok": True}

    @app.get("/auth/tokens")
    def all_tokens(_: Admin) -> list[TokenView]:
        return [TokenView(**asdict(t)) for t in st.list_tokens()]

    @app.delete("/auth/tokens/{tid}")
    def admin_revoke_token(tid: str, who: Admin) -> dict[str, bool]:
        if not st.revoke_token(tid):
            raise HTTPException(404, "token not found")
        st.audit(who.username, "revoked API token", tid)
        return {"ok": True}

    @app.get("/auth/audit")
    def audit(_: Admin, limit: int = 100) -> list[dict[str, str | None]]:
        return st.audit_log(min(limit, 500))

    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()
