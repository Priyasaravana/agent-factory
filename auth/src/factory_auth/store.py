"""Users, sessions and API tokens in the auth service's own SQLite database.

Passwords are hashed with scrypt (stdlib). Session ids and API tokens are
random 256-bit values; only their SHA-256 is stored, so a copy of the database
does not let anyone sign in.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

Role = Literal["admin", "member"]
ROLES: tuple[Role, ...] = ("admin", "member")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
MIN_PASSWORD = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, role TEXT NOT NULL, pw TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0, must_change INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
  last_seen_at TEXT);
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, username TEXT NOT NULL, name TEXT NOT NULL,
  created_at TEXT NOT NULL, last_used_at TEXT);
CREATE TABLE IF NOT EXISTS retired_usernames (
  username TEXT PRIMARY KEY, uid TEXT, retired_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sso_identities (
  issuer TEXT NOT NULL, subject TEXT NOT NULL, uid TEXT NOT NULL, provider TEXT NOT NULL,
  username_claim TEXT, email TEXT, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  PRIMARY KEY (issuer, subject));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, target TEXT);
"""


class AuthError(Exception):
    pass


_USER_COLS = "username, role, disabled, must_change, created_at, source, uid"


def _user(r: tuple) -> User:
    return User(r[0], r[1], bool(r[2]), bool(r[3]), r[4], r[5], r[6] or "")


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(dk.hex(), digest)


def check_username(username: str) -> None:
    # '@' for identity-provider names (ADR-0052); nothing that could break a header or a path
    if not username or len(username) > 64 or not username.translate(str.maketrans("", "", "-_.@")).isalnum():
        raise AuthError("username may contain letters, digits, '.', '-', '_' and '@'")


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters")


@dataclass
class User:
    username: str
    role: Role
    disabled: bool
    must_change: bool
    created_at: str
    source: str = "local"  # local, or sso:<provider id> (ADR-0052): how this account signs in
    uid: str = ""  # the account's immutable internal id; an SSO identity is bound to it, never to the name


@dataclass
class Token:
    id: str
    name: str
    username: str
    created_at: str
    last_used_at: str | None


class _Result:
    """Rows of a finished statement (see Store._q)."""

    __slots__ = ("rowcount", "rows")

    def __init__(self, rows: list[tuple], rowcount: int) -> None:
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple]:
        return self.rows


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.executescript(_SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(sessions)").fetchall()}
        if "last_seen_at" not in cols:  # databases from before the idle timeout (S-14)
            self._db.execute("ALTER TABLE sessions ADD COLUMN last_seen_at TEXT")
        ucols = {r[1] for r in self._db.execute("PRAGMA table_info(users)").fetchall()}
        if "source" not in ucols:  # databases from before single sign-on (ADR-0052): every account was local
            self._db.execute("ALTER TABLE users ADD COLUMN source TEXT NOT NULL DEFAULT 'local'")
        if "uid" not in ucols:
            self._db.execute("ALTER TABLE users ADD COLUMN uid TEXT")
        for (name,) in self._db.execute("SELECT username FROM users WHERE uid IS NULL").fetchall():
            self._db.execute("UPDATE users SET uid=? WHERE username=?", (secrets.token_hex(16), name))
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_uid ON users(uid)")
        # a dummy hash so login timing does not reveal whether a user exists
        self._dummy = hash_password(secrets.token_hex(8))

    def _q(self, sql: str, args: tuple = ()) -> _Result:
        """Run one statement and read its rows while holding the lock. The
        connection is shared by the server's worker threads: a cursor read after
        the lock is released interleaves with other threads' statements
        ("bad parameter or other API misuse", rows from another query, valid
        sessions seen as missing), and the gateway turns that into 500/401."""
        with self._lock:
            cur = self._db.execute(sql, args)
            return _Result(cur.fetchall(), cur.rowcount)

    # ----------------------------------------------------------------- users --
    def count_users(self) -> int:
        return int(self._q("SELECT COUNT(*) FROM users").fetchone()[0])

    def get_user(self, username: str) -> User | None:
        row = self._q(f"SELECT {_USER_COLS} FROM users WHERE username=?", (username,)).fetchone()  # noqa: S608
        return _user(row) if row else None

    def user_by_uid(self, uid: str) -> User | None:
        row = self._q(f"SELECT {_USER_COLS} FROM users WHERE uid=?", (uid,)).fetchone()  # noqa: S608
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self._q(f"SELECT {_USER_COLS} FROM users ORDER BY username")  # noqa: S608
        return [_user(r) for r in rows.fetchall()]

    def create_user(self, username: str, password: str, role: Role, must_change: bool = False) -> User:
        if role not in ROLES:
            raise AuthError(f"role must be one of {ROLES}")
        check_username(username)
        check_password_policy(password)
        if self.get_user(username):
            raise AuthError(f"user '{username}' already exists")
        self._refuse_retired(username)
        self._q(
            "INSERT INTO users(username, role, pw, must_change, created_at, uid) VALUES (?,?,?,?,?,?)",
            (username, role, hash_password(password), int(must_change), _now().isoformat(), secrets.token_hex(16)),
        )
        return self.get_user(username)  # type: ignore[return-value]

    def authenticate(self, username: str, password: str) -> User | None:
        row = self._q("SELECT pw, disabled FROM users WHERE username=?", (username,)).fetchone()
        local = bool(row and "$" in row[0])  # an SSO account (pw "sso") has no password: same timing, never ok
        stored = row[0] if row and local else self._dummy
        ok = verify_password(password, stored)
        if not row or not local or not ok or row[1]:
            return None
        return self.get_user(username)

    def set_password(self, username: str, password: str, must_change: bool = False) -> None:
        user = self.get_user(username)
        if user and user.source != "local":
            raise AuthError(f"'{username}' signs in with {user.source}; it has no password here")
        check_password_policy(password)
        self._q(
            "UPDATE users SET pw=?, must_change=? WHERE username=?",
            (hash_password(password), int(must_change), username),
        )
        self.revoke_sessions(username)

    def update_user(self, username: str, role: Role | None = None, disabled: bool | None = None) -> User:
        user = self.get_user(username)
        if not user:
            raise AuthError(f"user '{username}' not found")
        new_role = role or user.role
        new_disabled = user.disabled if disabled is None else disabled
        if user.role == "admin" and (new_role != "admin" or new_disabled) and self._active_admins() <= 1:
            raise AuthError("the last active admin cannot be demoted or disabled")
        self._q("UPDATE users SET role=?, disabled=? WHERE username=?", (new_role, int(new_disabled), username))
        if new_disabled:
            self.revoke_sessions(username)
        return self.get_user(username)  # type: ignore[return-value]

    def delete_user(self, username: str) -> None:
        user = self.get_user(username)
        if not user:
            raise AuthError(f"user '{username}' not found")
        if user.role == "admin" and not user.disabled and self._active_admins() <= 1:
            raise AuthError("the last active admin cannot be deleted")
        self.revoke_sessions(username)
        self._q("DELETE FROM tokens WHERE username=?", (username,))
        self._q("DELETE FROM sso_identities WHERE uid=?", (user.uid,))
        # The engine knows people by name (X-Auth-User): products they made, changes they asked for, who may steer.
        # A deleted account's name is retired for good, so it can never point at someone else (PR #221 review).
        self._q(
            "INSERT OR REPLACE INTO retired_usernames(username, uid, retired_at) VALUES (?,?,?)",
            (username, user.uid, _now().isoformat()),
        )
        self._q("DELETE FROM users WHERE username=?", (username,))

    def is_retired(self, username: str) -> bool:
        return self._q("SELECT 1 FROM retired_usernames WHERE username=?", (username,)).fetchone() is not None

    def _refuse_retired(self, username: str) -> None:
        if self.is_retired(username):
            raise AuthError(
                f"'{username}' belonged to a deleted account; names are never reused, because the factory's products "
                "and history refer to people by name"
            )

    def sso_user(
        self,
        provider: str,
        issuer: str,
        subject: str,
        username: str,
        role: Role | None,
        email: str | None = None,
    ) -> User:
        """The account an identity provider vouched for (ADR-0052, PR #221 review). An identity is the verified
        `(issuer, subject)` pair (OIDC Core §5.7), bound to the account's immutable `uid` on its first sign-in;
        the name and email it arrives with are profile attributes only. So a renamed person keeps their account,
        a different person given a reused name never inherits it, and a replaced issuer is a new identity. A
        first sign-in whose name the factory already has (local, or bound to another identity) is refused: an
        identity provider can't take over `admin`, or anyone. The role follows the provider's groups when a
        mapping gives one; disabled stays disabled (the caller refuses it)."""
        if not issuer or not subject:
            raise AuthError("the identity provider gave no issuer or subject")
        now = _now().isoformat()
        with self._lock:  # one transaction: two first sign-ins can't both bind, or both take the name
            db = self._db
            db.execute("BEGIN IMMEDIATE")
            try:
                bound = db.execute(
                    "SELECT uid FROM sso_identities WHERE issuer=? AND subject=?", (issuer, subject)
                ).fetchone()
                row = (
                    db.execute(f"SELECT {_USER_COLS} FROM users WHERE uid=?", (bound[0],)).fetchone()  # noqa: S608
                    if bound
                    else None
                )
                if bound and row is None:  # its account was deleted: the identity starts again
                    db.execute("DELETE FROM sso_identities WHERE issuer=? AND subject=?", (issuer, subject))
                if row is not None:
                    db.execute(
                        "UPDATE sso_identities SET username_claim=?, email=?, last_seen_at=? "
                        "WHERE issuer=? AND subject=?",
                        (username, email, now, issuer, subject),
                    )
                    user = _user(row)
                else:
                    check_username(username)
                    if db.execute("SELECT 1 FROM retired_usernames WHERE username=?", (username,)).fetchone():
                        raise AuthError(f"the name '{username}' belonged to a deleted account and is never reused")
                    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                        raise AuthError(f"the name '{username}' already belongs to another account here; ask an admin")
                    uid = secrets.token_hex(16)
                    db.execute(
                        "INSERT INTO users(username, role, pw, must_change, created_at, source, uid) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (username, role or "member", "sso", 0, now, f"sso:{provider}", uid),
                    )
                    db.execute(
                        "INSERT INTO sso_identities(issuer, subject, uid, provider, username_claim, email, "
                        "created_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                        (issuer, subject, uid, provider, username, email, now, now),
                    )
                    user = _user(db.execute(f"SELECT {_USER_COLS} FROM users WHERE uid=?", (uid,)).fetchone())  # noqa: S608
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        if role and role != user.role and not user.disabled:
            return self.update_user(user.username, role=role)  # the last active admin rule still holds
        return user

    def sso_identities(self, username: str) -> list[dict[str, str]]:
        """The identities bound to an account (for admins): issuer, subject and what they last said."""
        user = self.get_user(username)
        if not user:
            return []
        rows = self._q(
            "SELECT issuer, subject, provider, username_claim, email, last_seen_at FROM sso_identities WHERE uid=?",
            (user.uid,),
        ).fetchall()
        keys = ("issuer", "subject", "provider", "username_claim", "email", "last_seen_at")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def _active_admins(self) -> int:
        return int(self._q("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0])

    # -------------------------------------------------------------- sessions --
    def create_session(self, username: str, ttl: timedelta) -> str:
        sid = secrets.token_urlsafe(32)
        now = _now()
        self._q(
            "INSERT INTO sessions(id_hash, username, expires_at, created_at, last_seen_at) VALUES (?,?,?,?,?)",
            (_sha(sid), username, (now + ttl).isoformat(), now.isoformat(), now.isoformat()),
        )
        return sid

    def session_user(self, sid: str, idle: timedelta | None = None) -> User | None:
        """The session's user, or None when it is unknown, expired, or unused for longer than `idle`
        (S-14: a session left open on a shared screen ends on its own). Use is recorded at most once
        a minute, so a busy page doesn't write on every request."""
        key = _sha(sid)
        row = self._q("SELECT username, expires_at, last_seen_at FROM sessions WHERE id_hash=?", (key,)).fetchone()
        if not row:
            return None
        now = _now()
        seen = datetime.fromisoformat(row[2]) if row[2] else None
        if datetime.fromisoformat(row[1]) < now or (idle is not None and seen is not None and now - seen > idle):
            self._q("DELETE FROM sessions WHERE id_hash=?", (key,))
            return None
        if seen is None or now - seen > timedelta(minutes=1):
            self._q("UPDATE sessions SET last_seen_at=? WHERE id_hash=?", (now.isoformat(), key))
        user = self.get_user(row[0])
        return user if user and not user.disabled else None

    def delete_session(self, sid: str) -> None:
        self._q("DELETE FROM sessions WHERE id_hash=?", (_sha(sid),))

    def revoke_sessions(self, username: str) -> None:
        self._q("DELETE FROM sessions WHERE username=?", (username,))

    # ---------------------------------------------------------------- tokens --
    def create_token(self, username: str, name: str) -> tuple[Token, str]:
        raw = "afk_" + secrets.token_urlsafe(32)
        tid = secrets.token_hex(6)
        self._q(
            "INSERT INTO tokens(id, token_hash, username, name, created_at) VALUES (?,?,?,?,?)",
            (tid, _sha(raw), username, name[:60] or "token", _now().isoformat()),
        )
        return self.list_tokens(username, tid)[0], raw

    def list_tokens(self, username: str | None = None, tid: str | None = None) -> list[Token]:
        sql = "SELECT id, name, username, created_at, last_used_at FROM tokens WHERE 1=1"
        args: list[str] = []
        if username:
            sql += " AND username=?"
            args.append(username)
        if tid:
            sql += " AND id=?"
            args.append(tid)
        rows = self._q(sql + " ORDER BY created_at", tuple(args)).fetchall()
        return [Token(*r) for r in rows]

    def token_user(self, raw: str) -> User | None:
        row = self._q("SELECT id, username FROM tokens WHERE token_hash=?", (_sha(raw),)).fetchone()
        if not row:
            return None
        self._q("UPDATE tokens SET last_used_at=? WHERE id=?", (_now().isoformat(), row[0]))
        user = self.get_user(row[1])
        # ADR-0052 (PR #221 review): an SSO account's access must end with the identity provider's say, so it
        # holds no long-lived token; one made before this rule counts for nothing
        return user if user and not user.disabled and user.source == "local" else None

    def revoke_token(self, tid: str, username: str | None = None) -> bool:
        sql, args = "DELETE FROM tokens WHERE id=?", [tid]
        if username:
            sql += " AND username=?"
            args.append(username)
        return self._q(sql, tuple(args)).rowcount > 0

    # ----------------------------------------------------------------- audit --
    def audit(self, actor: str | None, action: str, target: str | None = None) -> None:
        self._q(
            "INSERT INTO audit(ts, actor, action, target) VALUES (?,?,?,?)", (_now().isoformat(), actor, action, target)
        )

    def audit_log(self, limit: int = 100) -> list[dict[str, str | None]]:
        rows = self._q("SELECT ts, actor, action, target FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "actor": r[1], "action": r[2], "target": r[3]} for r in rows]
