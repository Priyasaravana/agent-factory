"""S-06: secrets as mounted files, and short-lived GitHub App tokens in place of long-lived personal ones."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from agent_factory.secret_refs import (
    REDACTOR,
    GitHubAppBackend,
    SecretError,
    SecretRef,
    SecretResolver,
    register_backend,
)

REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------ NAME_FILE --
def test_env_reads_the_file_name_file_names_and_the_value_never_enters_the_environment(tmp_path, monkeypatch):
    f = tmp_path / "github_token"
    f.write_text("ghp_from_a_mounted_file\n")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(f))
    r = SecretResolver()
    assert r.resolve("env://GITHUB_TOKEN") == "ghp_from_a_mounted_file"
    assert REDACTOR.text("push with ghp_from_a_mounted_file") == "push with ***"
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_the_variable_wins")
    assert r.resolve("env://GITHUB_TOKEN") == "ghp_the_variable_wins", "the variable, when set, wins as before"


def test_a_file_name_that_is_not_a_file_says_so_without_a_value(tmp_path, monkeypatch):
    monkeypatch.delenv("REPO_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("REPO_GITHUB_TOKEN_FILE", str(tmp_path / "missing"))
    ok, why = SecretResolver().available("env://REPO_GITHUB_TOKEN")
    assert not ok and "REPO_GITHUB_TOKEN_FILE names" in why and "not a file" in why


# --------------------------------------------------------------- GitHub App --
def _key(tmp_path: Path, app: str = "123") -> rsa.RSAPrivateKey:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path = tmp_path / f"github-app-{app}.pem"
    path.write_bytes(pem)
    return k


class FakeGitHub:
    def __init__(self, key: rsa.RSAPrivateKey | None, status: int = 201, expires: str = "2026-11-25T22:00:00Z"):
        self.key, self.status, self.expires, self.calls = key, status, expires, []

    def __call__(self, req):  # noqa: ANN001, ANN204
        self.calls.append(req)
        if self.status != 201:
            raise urllib.error.HTTPError(req.full_url, self.status, "no", {}, io.BytesIO(b'{"echo": "secret"}'))
        auth = req.get_header("Authorization").removeprefix("Bearer ")
        head, body, sig = auth.split(".")
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        assert self.key is not None
        self.key.public_key().verify(pad(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        self.claims = json.loads(pad(body))
        self.body = json.loads(req.data)
        n = len(self.calls)
        return io.BytesIO(json.dumps({"token": f"ghs_installation_token_{n}", "expires_at": self.expires}).encode())


NOW = 1795636800.0  # 2026-11-25T20:00:00Z


def _backend(tmp_path, monkeypatch, gh, now=NOW):  # noqa: ANN001, ANN202
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", str(tmp_path / "github-app-123.pem"))
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    clock = {"t": now}
    return GitHubAppBackend(opener=gh, clock=lambda: clock["t"]), clock


def test_an_installation_token_is_minted_with_a_signed_short_lived_jwt(tmp_path, monkeypatch):
    gh = FakeGitHub(_key(tmp_path))
    b, _ = _backend(tmp_path, monkeypatch, gh)
    assert b.get(SecretRef.parse("github-app://123/456")) == "ghs_installation_token_1"
    [req] = gh.calls
    assert req.full_url == "https://api.github.com/app/installations/456/access_tokens" and req.method == "POST"
    assert gh.claims["iss"] == "123" and gh.claims["exp"] - gh.claims["iat"] <= 600, "GitHub's ten-minute limit"
    assert gh.claims["iat"] <= NOW <= gh.claims["exp"]
    assert gh.body == {}, "the installation's own repositories"


def test_a_token_can_be_narrowed_to_named_repositories(tmp_path, monkeypatch):
    gh = FakeGitHub(_key(tmp_path))
    b, _ = _backend(tmp_path, monkeypatch, gh)
    b.get(SecretRef.parse("github-app://123/456#kudos,plant-watering"))
    assert gh.body == {"repositories": ["kudos", "plant-watering"]}
    with pytest.raises(SecretError, match="repository names"):
        b.get(SecretRef.parse("github-app://123/456#../evil"))


def test_a_token_is_reused_until_ten_minutes_before_it_expires(tmp_path, monkeypatch):
    gh = FakeGitHub(_key(tmp_path), expires="2026-11-25T21:00:00Z")  # an hour after NOW
    b, clock = _backend(tmp_path, monkeypatch, gh)
    ref = SecretRef.parse("github-app://123/456")
    assert b.get(ref) == b.get(ref) == "ghs_installation_token_1"
    clock["t"] = NOW + 49 * 60
    assert b.get(ref) == "ghs_installation_token_1"
    clock["t"] = NOW + 51 * 60
    assert b.get(ref) == "ghs_installation_token_2", "replaced before it dies mid-step"
    assert b.get(SecretRef.parse("github-app://123/456#kudos")) == "ghs_installation_token_3", "scopes don't share"


@pytest.mark.parametrize(
    ("status", "why"), [(401, "private key was refused"), (404, "no such installation"), (500, "refused")]
)
def test_a_refusal_names_the_status_never_the_answer(tmp_path, monkeypatch, status, why):
    b, _ = _backend(tmp_path, monkeypatch, FakeGitHub(_key(tmp_path), status=status))
    with pytest.raises(SecretError, match=why) as exc:
        b.get(SecretRef.parse("github-app://123/456"))
    assert "secret" not in str(exc.value) and "echo" not in str(exc.value)


def test_the_request_never_follows_a_redirect():
    import urllib.request

    from agent_factory import secret_refs

    handler = secret_refs._NoRedirect()
    req = urllib.request.Request("https://api.github.com/app/installations/1/access_tokens", method="POST")
    assert handler.redirect_request(req, None, 307, "moved", {}, "https://elsewhere.example/") is None


def test_the_key_must_be_an_rsa_pem_file_and_the_api_https(tmp_path, monkeypatch):
    gh = FakeGitHub(None)
    b, _ = _backend(tmp_path, monkeypatch, gh)
    ref = SecretRef.parse("github-app://123/456")
    with pytest.raises(SecretError, match="is not a file"):
        b.get(ref)
    (tmp_path / "github-app-123.pem").write_text("not a key")
    with pytest.raises(SecretError, match="not a PEM private key"):
        b.get(ref)
    ec_key = ec.generate_private_key(ec.SECP256R1())
    (tmp_path / "github-app-123.pem").write_bytes(
        ec_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    with pytest.raises(SecretError, match="not an RSA key"):
        b.get(ref)
    _key(tmp_path)
    monkeypatch.setenv("GITHUB_API_URL", "http://github.internal")
    with pytest.raises(SecretError, match="must be https"):
        b.get(ref)
    assert gh.calls == [], "nothing is sent before every check passes"
    for bad in ("github-app://abc/456", "github-app://123"):
        with pytest.raises(SecretError, match="expected github-app://"):
            b.get(SecretRef.parse(bad))


def test_the_resolver_redacts_an_installation_token(tmp_path, monkeypatch):
    gh = FakeGitHub(_key(tmp_path))
    b, _ = _backend(tmp_path, monkeypatch, gh)
    register_backend("github-app-test", lambda settings: b)
    token = SecretResolver().resolve("github-app-test://123/456")
    assert REDACTOR.text(f"remote: {token}") == "remote: ***"


# --------------------------------------------------------- moving values out --
def _run_split(tmp_path: Path, env_text: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / ".env").write_text(env_text)
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(REPO / "scripts" / "secrets-to-files.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_secrets_move_from_env_to_files_and_env_keeps_only_their_paths(tmp_path):
    r = _run_split(
        tmp_path,
        "FACTORY_MODE=live\nGITHUB_TOKEN=ghp_publish_value\nSKILLS_GITHUB_TOKEN=\nREPO_GITHUB_TOKEN='ghp_repo value'\n"
        "SNYK_TOKEN=snyk_value\nGIT_AUTHOR_NAME=Someone\n",
    )
    assert r.returncode == 0, r.stderr
    env = (tmp_path / ".env").read_text()
    assert "ghp_publish_value" not in env and "ghp_repo value" not in env and "snyk_value" not in env
    assert (
        "GITHUB_TOKEN_FILE=/run/secrets/github_token\n" in env
        and "REPO_GITHUB_TOKEN_FILE=/run/secrets/repo_github_token\n" in env
    )
    assert "SKILLS_GITHUB_TOKEN=\n" in env, "an empty one is left as it was"
    assert "FACTORY_MODE=live\n" in env and "GIT_AUTHOR_NAME=Someone\n" in env
    secrets = tmp_path / ".secrets"
    assert (secrets / "github_token").read_text() == "ghp_publish_value\n"
    assert (secrets / "repo_github_token").read_text() == "ghp_repo value\n"
    assert oct(secrets.stat().st_mode)[-3:] == "700" and oct((secrets / "github_token").stat().st_mode)[-3:] == "600"
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1 and oct(backups[0].stat().st_mode)[-3:] == "600", "the old .env, kept privately"
    again = _run_split(tmp_path, (tmp_path / ".env").read_text())
    assert again.returncode == 0 and "nothing to move" in again.stdout


def test_split_refuses_to_overwrite_a_different_existing_secret_file(tmp_path):
    (tmp_path / ".secrets").mkdir(mode=0o700)
    (tmp_path / ".secrets" / "github_token").write_text("ghp_other\n")
    r = _run_split(tmp_path, "GITHUB_TOKEN=ghp_publish_value\n")
    assert r.returncode == 1 and "already exists" in r.stdout + r.stderr
    assert "GITHUB_TOKEN=ghp_publish_value" in (tmp_path / ".env").read_text(), "nothing changed"


# ---------------------------------------------------------------- the wiring --
def test_secret_files_are_mounted_read_only_and_never_in_git_or_a_build():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["factory"]
    assert "./.secrets:/run/secrets:ro" in compose["volumes"]
    assert ".secrets" in (REPO / ".dockerignore").read_text().splitlines()
    res = subprocess.run(["git", "check-ignore", "-q", ".secrets/github_token"], cwd=REPO, check=False)  # noqa: S603, S607
    assert res.returncode == 0
    example = (REPO / ".env.example").read_text()
    for name in ("GITHUB_TOKEN", "REPO_GITHUB_TOKEN", "SKILLS_GITHUB_TOKEN", "SNYK_TOKEN"):
        assert f"{name}_FILE=/run/secrets/{name.lower()}" in example, name
    assert "github-app://" in example
