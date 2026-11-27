"""The auth gateway service.

Sits beside the factory as its own container. nginx asks it about every API
request (`auth_request` -> GET /auth/check) and forwards the answer to the
engine as `X-Auth-User` / `X-Auth-Role`. The engine never sees passwords,
sessions or tokens, and this service can be replaced (e.g. by an SSO proxy
that sets the same headers) without touching the factory.
"""

import hmac
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
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from factory_auth import extensions
from factory_auth.store import AuthError, Role, Store, User

log = logging.getLogger("factory_auth")
COOKIE = "factory_session"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
SSO_STATE_COOKIE = "factory_sso_state"
SSO_PENDING_SECONDS = 600
SSO_PENDING_MAX = 10_000  # all sources together
SSO_PENDING_PER_SOURCE = 10  # one address can't use up sign-in for everyone else (PR #221 review)
# what the sign-in page may be told after a failed single sign-on: fixed codes, never the provider's text
SSO_ERRORS = ("state", "provider", "denied", "account", "disabled", "invalid")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("AUTH_DATA_DIR", "/data")))
    session_hours: int = field(default_factory=lambda: int(os.environ.get("AUTH_SESSION_HOURS", "12")))
    # minutes a session may go unused before it ends (S-14); the session still ends after session_hours
    idle_minutes: int = field(default_factory=lambda: int(os.environ.get("AUTH_IDLE_MINUTES", "60")))
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("AUTH_COOKIE_SECURE", "false") == "true")
    admin_user: str = field(default_factory=lambda: os.environ.get("FACTORY_ADMIN_USER", "admin"))
    admin_password_ref: str = field(
        default_factory=lambda: os.environ.get("FACTORY_ADMIN_PASSWORD_REF", "env://FACTORY_ADMIN_PASSWORD")
    )
    # the address people reach the factory at, e.g. https://factory.example.com (ADR-0052): the only source of
    # single sign-on's redirect address, never the request's Host header
    public_url: str = field(default_factory=lambda: os.environ.get("AUTH_PUBLIC_URL", "").rstrip("/"))


def host_name(header: str) -> str:
    """The host of a Host header, without its port: `localhost:8080` -> `localhost`, `[::1]:8080` -> `::1`."""
    h = header.strip().lower()
    if h.startswith("["):
        return h[1:].split("]")[0]
    return h.split(":")[0] if h.count(":") == 1 else h


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
    source: str = "local"


class ProviderView(BaseModel):
    id: str
    label: str


class EditionView(BaseModel):
    edition: str
    licence: dict | None = None
    extensions: list[dict] = Field(default_factory=list)
    providers: list[ProviderView] = Field(default_factory=list)


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
def _clean(text: object, limit: int = 200) -> str:
    """For the log and the audit trail: printable, one line, bounded (no forged log lines from a provider or a
    query string)."""
    return "".join(c if c.isprintable() else "?" for c in str(text))[:limit]


def _available(provider: object) -> bool:
    check = getattr(provider, "available", None)
    try:
        return bool(check()) if callable(check) else True
    except Exception:
        return False  # a failing check means unavailable


def create_app(
    store: Store | None = None, settings: Settings | None = None, registry: extensions.Registry | None = None
) -> FastAPI:
    s = settings or Settings()
    st = store or Store(s.data_dir / "auth.db")
    bootstrap(st, s)
    throttle = Throttle()
    reg = registry if registry is not None else extensions.load()
    # state -> (provider id, the provider's data, deadline, source address); one process, so memory is enough:
    # a restart only makes someone in the middle of signing in start again
    pending: dict[str, tuple[str, dict, float, str]] = {}
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
        user = st.session_user(sid, timedelta(minutes=s.idle_minutes)) if sid else None
        if user:
            return user, "session"
        raise HTTPException(401, "sign in required")

    Current = Annotated[tuple[User, str], Depends(current)]

    def plain_http_refused(request: Request) -> None:
        """S-14: a session cookie without `Secure` crosses the network in clear. That is only acceptable
        when the factory is reached on this machine (the compose file binds it to 127.0.0.1); anywhere
        else, sign-in is refused until it is served over HTTPS with AUTH_COOKIE_SECURE=true."""
        if s.cookie_secure:
            return
        host = host_name(request.headers.get("host") or "")
        if host in LOOPBACK or host.endswith(".localhost"):
            return
        raise HTTPException(
            403,
            f"sign-in refused: this factory is served over plain HTTP on '{host}', where the session cookie "
            "would cross the network in clear. Serve it over HTTPS and set AUTH_COOKIE_SECURE=true.",
        )

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
        plain_http_refused(request)
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

    def start_session(response: Response, username: str) -> None:
        sid = st.create_session(username, timedelta(hours=s.session_hours))
        response.set_cookie(
            COOKIE,
            sid,
            max_age=s.session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=s.cookie_secure,
            path="/",
        )

    # ---- single sign-on (ADR-0052): the provider proves who; the core decides everything else ----
    def redirect_uri(pid: str) -> str:
        return f"{s.public_url}/auth/sso/{pid}/callback"

    def sso_failed(code: str, who: str | None, detail: str) -> RedirectResponse:
        detail = _clean(detail)
        st.audit(who, f"single sign-on failed: {code}", detail or None)
        log.warning("single sign-on failed (%s): %s", code, detail)
        out = RedirectResponse(f"/login?sso_error={code}", status_code=303)
        out.delete_cookie(SSO_STATE_COOKIE, path="/auth/sso")
        return out

    @app.get("/auth/sso")
    def sso_providers() -> list[ProviderView]:
        """The sign-in page's buttons; empty on the core edition, or when no provider is licensed and set up."""
        if not s.public_url:
            return []
        return [ProviderView(id=p.id, label=p.label) for p in reg.providers.values() if _available(p)]

    @app.get("/auth/edition")
    def edition(_: Admin) -> EditionView:
        return EditionView(
            edition=reg.edition,
            licence=reg.licence,
            extensions=[asdict(e) for e in reg.loaded],
            providers=sso_providers(),
        )

    @app.get("/auth/sso/{pid}/start")
    def sso_start(pid: str, request: Request) -> RedirectResponse:
        plain_http_refused(request)
        provider = reg.providers.get(pid)
        if provider is None or not s.public_url or not _available(provider):
            raise HTTPException(404, "no such sign-in provider")
        now = time.monotonic()
        for k in [k for k, v in pending.items() if v[2] < now]:
            pending.pop(k, None)
        # nginx sets X-Forwarded-For to the client's address (it overwrites what the client sent)
        source = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")
        if sum(1 for v in pending.values() if v[3] == source) >= SSO_PENDING_PER_SOURCE:
            raise HTTPException(429, "too many sign-ins started from here; finish one or wait a few minutes")
        if len(pending) >= SSO_PENDING_MAX:
            raise HTTPException(429, "too many sign-ins in progress; try again in a few minutes")
        state = secrets.token_urlsafe(32)
        url, data = provider.start(redirect_uri(pid), state)
        pending[state] = (pid, data, now + SSO_PENDING_SECONDS, source)
        out = RedirectResponse(url, status_code=303)
        # binds the callback to this browser (login CSRF): Lax is sent on the provider's top-level redirect back
        out.set_cookie(
            SSO_STATE_COOKIE,
            state,
            max_age=SSO_PENDING_SECONDS,
            httponly=True,
            samesite="lax",
            secure=s.cookie_secure,
            path="/auth/sso",
        )
        return out

    @app.get("/auth/sso/{pid}/callback")
    def sso_callback(pid: str, request: Request) -> RedirectResponse:
        params = dict(request.query_params)
        state, cookie = params.get("state", ""), request.cookies.get(SSO_STATE_COOKIE, "")
        entry = pending.pop(state, None) if state else None
        if not cookie or not hmac.compare_digest(state.encode(), cookie.encode()) or entry is None:
            return sso_failed("state", None, "missing, unknown or replayed state, or another browser's")
        got_pid, data, until, _source = entry
        if got_pid != pid or until < time.monotonic():
            return sso_failed("state", None, f"state for {got_pid}, used at {pid} or too late")
        provider = reg.providers.get(pid)
        if provider is None or not _available(provider):
            return sso_failed("provider", None, f"{pid} is no longer available")
        if "error" in params:
            return sso_failed("denied", None, f"{pid}: {params.get('error', '')[:60]}")
        try:
            ident = provider.finish(params, data, redirect_uri(pid))
        except extensions.SignInFailed as exc:
            return sso_failed(exc.code if exc.code in SSO_ERRORS else "invalid", None, f"{pid}: {exc}")
        except Exception as exc:
            log.exception("sign-in provider %s failed", _clean(pid, 40))
            return sso_failed("provider", None, f"{pid}: {type(exc).__name__}")
        try:
            user = st.sso_user(pid, ident.issuer, ident.subject, ident.username, ident.role, ident.email)
        except AuthError as exc:
            return sso_failed("account", ident.username, str(exc))
        if user.disabled:
            return sso_failed("disabled", user.username, "account disabled")
        out = RedirectResponse("/", status_code=303)
        out.delete_cookie(SSO_STATE_COOKIE, path="/auth/sso")
        start_session(out, user.username)
        st.audit(user.username, f"signed in with sso:{pid}", user.role)
        return out

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
        if user.source != "local":
            # ADR-0052 (PR #221 review): access through the identity provider ends with its sessions; a token
            # would outlive the person's removal there
            raise HTTPException(403, "accounts that sign in with single sign-on can't make API tokens")
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
