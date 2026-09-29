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
  id_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, username TEXT NOT NULL, name TEXT NOT NULL,
  created_at TEXT NOT NULL, last_used_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, target TEXT);
"""


class AuthError(Exception):
    pass


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


@dataclass
class Token:
    id: str
    name: str
    username: str
    created_at: str
    last_used_at: str | None


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.executescript(_SCHEMA)
        # a dummy hash so login timing does not reveal whether a user exists
        self._dummy = hash_password(secrets.token_hex(8))

    def _q(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    # ----------------------------------------------------------------- users --
    def count_users(self) -> int:
        return int(self._q("SELECT COUNT(*) FROM users").fetchone()[0])

    def get_user(self, username: str) -> User | None:
        row = self._q(
            "SELECT username, role, disabled, must_change, created_at FROM users WHERE username=?", (username,)
        ).fetchone()
        return User(row[0], row[1], bool(row[2]), bool(row[3]), row[4]) if row else None

    def list_users(self) -> list[User]:
        rows = self._q("SELECT username, role, disabled, must_change, created_at FROM users ORDER BY username")
        return [User(r[0], r[1], bool(r[2]), bool(r[3]), r[4]) for r in rows.fetchall()]

    def create_user(self, username: str, password: str, role: Role, must_change: bool = False) -> User:
        if role not in ROLES:
            raise AuthError(f"role must be one of {ROLES}")
        if not username or not username.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise AuthError("username may contain letters, digits, '.', '-' and '_'")
        check_password_policy(password)
        if self.get_user(username):
            raise AuthError(f"user '{username}' already exists")
        self._q(
            "INSERT INTO users(username, role, pw, must_change, created_at) VALUES (?,?,?,?,?)",
            (username, role, hash_password(password), int(must_change), _now().isoformat()),
        )
        return self.get_user(username)  # type: ignore[return-value]

    def authenticate(self, username: str, password: str) -> User | None:
        row = self._q("SELECT pw, disabled FROM users WHERE username=?", (username,)).fetchone()
        stored = row[0] if row else self._dummy
        ok = verify_password(password, stored)
        if not row or not ok or row[1]:
            return None
        return self.get_user(username)

    def set_password(self, username: str, password: str, must_change: bool = False) -> None:
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
        self._q("DELETE FROM users WHERE username=?", (username,))

    def _active_admins(self) -> int:
        return int(self._q("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0])

    # -------------------------------------------------------------- sessions --
    def create_session(self, username: str, ttl: timedelta) -> str:
        sid = secrets.token_urlsafe(32)
        now = _now()
        self._q(
            "INSERT INTO sessions(id_hash, username, expires_at, created_at) VALUES (?,?,?,?)",
            (_sha(sid), username, (now + ttl).isoformat(), now.isoformat()),
        )
        return sid

    def session_user(self, sid: str) -> User | None:
        row = self._q("SELECT username, expires_at FROM sessions WHERE id_hash=?", (_sha(sid),)).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row[1]) < _now():
            self._q("DELETE FROM sessions WHERE id_hash=?", (_sha(sid),))
            return None
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
        return user if user and not user.disabled else None

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
