from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from factory_auth.app import COOKIE, MAX_FAILURES, Settings, create_app
from factory_auth.store import Store, verify_password

ADMIN_PW = "correct-horse-battery"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ADMIN_PASSWORD", ADMIN_PW)
    store = Store(tmp_path / "auth.db")
    app = create_app(store, Settings(data_dir=tmp_path))
    return TestClient(app), store


def login(c: TestClient, user="admin", pw=ADMIN_PW):
    return c.post("/auth/login", json={"username": user, "password": pw})


def test_bootstrap_admin_from_secret_reference(env):
    c, store = env
    assert [u.username for u in store.list_users()] == ["admin"]
    assert c.get("/auth/check").status_code == 401
    r = login(c)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    chk = c.get("/auth/check")
    assert chk.status_code == 200 and chk.headers["x-auth-user"] == "admin" and chk.headers["x-auth-role"] == "admin"


def test_generated_password_when_no_reference(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_ADMIN_PASSWORD", raising=False)
    store = Store(tmp_path / "auth.db")
    c = TestClient(create_app(store, Settings(data_dir=tmp_path)))
    pwfile = tmp_path / "initial-admin-password"
    assert pwfile.exists() and oct(pwfile.stat().st_mode)[-3:] == "600"
    pw = pwfile.read_text().strip()
    assert login(c, pw=pw).status_code == 200
    assert c.get("/auth/check").status_code == 403, "must change the one-time password first"
    assert c.get("/auth/users").status_code == 403
    r = c.post("/auth/me/password", json={"current_password": pw, "new_password": "a-new-long-password"})
    assert r.status_code == 200 and r.json()["must_change"] is False
    assert c.get("/auth/check").status_code == 200


def test_wrong_password_and_lockout(env):
    c, store = env
    for _ in range(MAX_FAILURES):
        assert login(c, pw="nope-nope-nope").status_code == 401
    assert login(c).status_code == 429, "locked even with the right password"
    assert any(a["action"] == "sign-in failed" for a in store.audit_log())
    assert login(c, user="ghost", pw="x" * 12).status_code == 401


def test_admin_manages_users_members_cannot(env):
    c, _ = env
    login(c)
    r = c.post("/auth/users", json={"username": "priya", "role": "member", "password": "temporary-pass-1"})
    assert r.status_code == 200 and r.json()["must_change"] is True
    assert c.post("/auth/users", json={"username": "priya", "password": "temporary-pass-1"}).status_code == 409
    assert c.post("/auth/users", json={"username": "shorty", "password": "short"}).status_code == 409

    m = TestClient(c.app)
    assert login(m, "priya", "temporary-pass-1").status_code == 200
    assert m.get("/auth/check").status_code == 403
    m.post("/auth/me/password", json={"current_password": "temporary-pass-1", "new_password": "priya-long-password"})
    chk = m.get("/auth/check")
    assert chk.status_code == 200 and chk.headers["x-auth-role"] == "member"
    assert m.get("/auth/users").status_code == 403
    assert m.post("/auth/users", json={"username": "evil", "password": "x" * 12}).status_code == 403

    # disabling signs the member out everywhere
    assert c.patch("/auth/users/priya", json={"disabled": True}).status_code == 200
    assert m.get("/auth/check").status_code == 401
    assert login(m, "priya", "priya-long-password").status_code == 401


def test_last_admin_is_protected(env):
    c, _ = env
    login(c)
    assert c.patch("/auth/users/admin", json={"role": "member"}).status_code == 409
    assert c.patch("/auth/users/admin", json={"disabled": True}).status_code == 409
    assert c.delete("/auth/users/admin").status_code == 409


def test_api_tokens(env):
    c, store = env
    login(c)
    r = c.post("/auth/me/tokens", json={"name": "ci"})
    raw = r.json()["token"]
    assert raw.startswith("afk_")
    stored = [r[0] for r in store._db.execute("SELECT token_hash FROM tokens").fetchall()]
    assert raw not in stored and len(stored) == 1, "only the hash is stored"
    bare = TestClient(c.app)
    chk = bare.get("/auth/check", headers={"Authorization": f"Bearer {raw}"})
    assert chk.status_code == 200 and chk.headers["x-auth-method"] == "token"
    assert (
        bare.post("/auth/me/tokens", json={"name": "x"}, headers={"Authorization": f"Bearer {raw}"}).status_code == 403
    )
    tid = r.json()["id"]
    assert c.delete(f"/auth/me/tokens/{tid}").status_code == 200
    assert bare.get("/auth/check", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_logout_and_session_revocation_on_password_change(env):
    c, _ = env
    login(c)
    other = TestClient(c.app)
    login(other)
    c.post("/auth/me/password", json={"current_password": ADMIN_PW, "new_password": "rotated-password-9"})
    assert other.get("/auth/check").status_code == 401, "other sessions are signed out"
    assert c.get("/auth/check").status_code == 200, "the current session gets a fresh cookie"
    c.post("/auth/logout")
    assert COOKIE not in c.cookies or c.get("/auth/check").status_code == 401


def test_passwords_are_hashed(env):
    _, store = env
    row = store._db.execute("SELECT pw FROM users WHERE username='admin'").fetchone()[0]
    assert row.startswith("scrypt$") and ADMIN_PW not in row and verify_password(ADMIN_PW, row)
