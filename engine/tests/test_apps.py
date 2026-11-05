"""`agent-factory apps` (make apps / app-login): each product's address and its first admin's sign-in."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from agent_factory import apps
from agent_factory.models import Product

T = datetime(2026, 11, 5, tzinfo=UTC)


def _product(slug: str, **more) -> Product:
    return Product(
        id=slug[:6],
        title=slug.replace("-", " "),
        requirements="x",
        blueprint="nextjs-web",
        slug=slug,
        created_at=T,
        **more,
    )


def _secret(email: str, password: str) -> str:
    enc = lambda v: base64.b64encode(v.encode()).decode()  # noqa: E731
    return json.dumps({"kind": "Secret", "data": {"email": enc(email), "password": enc(password)}})


def test_the_admin_secret_is_the_chart_s_name_in_the_app_namespace():
    assert apps.secret_name(_product("plant-watering")) == "plant-watering-admin"
    long = "a" * 49 + "-b" * 5
    assert apps.secret_name(_product(long)) == ("a" * 49 + "-").rstrip("-") + "-admin", "the chart cuts to 50"


def test_a_secret_gives_the_account_and_anything_else_gives_none():
    assert apps.admin_from_secret(_secret("admin@example.com", "s3cret")) == apps.Admin("admin@example.com", "s3cret")
    for bad in ("", "not json", json.dumps({"data": {"email": "!!"}}), json.dumps({"data": {}}), _secret("", "x")):
        assert apps.admin_from_secret(bad) is None


def test_the_list_never_shows_a_password_and_says_why_an_app_has_no_admin():
    rows = [
        apps.row(
            _product(
                "plant-watering", app_url="http://plant-watering.localtest.me:8180", latest_status="awaiting_feedback"
            ),
            apps.Admin("admin@example.com", "s3cret"),
        ),
        apps.row(_product("bookmarks"), None),
        apps.row(_product("kudos", archived_at=T, app_url="http://x"), None),
    ]
    out = apps.table(rows)
    assert "s3cret" not in out
    assert out.splitlines()[0].split()[:4] == ["APP", "TITLE", "STATUS", "ADDRESS"]
    assert "http://plant-watering.localtest.me:8180" in out and "admin@example.com" in out
    assert "— (not delivered yet)" in out and "— (no admin account in the cluster)" in out
    assert "— (archived: app removed)" in out
    assert apps.table([]) == "no products yet"


@pytest.mark.parametrize("show", [False, True])
def test_one_app_shows_the_password_only_when_asked(show):
    p = _product("plant-watering", app_url="http://plant-watering.localtest.me:8180")
    out = apps.detail(p, apps.Admin("admin@example.com", "s3cret"), show_password=show)
    assert "address: http://plant-watering.localtest.me:8180" in out and "email:   admin@example.com" in out
    assert ("s3cret" in out) is show
    assert "none in the cluster" in apps.detail(p, None, show_password=True)


def test_the_command_lists_apps_and_copies_out_one_password(tmp_path, monkeypatch, capsys):
    from agent_factory import cli
    from agent_factory.state import SqliteStateStore

    store = SqliteStateStore(tmp_path / "factory.db")
    store.create_product(_product("plant-watering", app_url="http://plant-watering.localtest.me:8180"))
    store.create_product(_product("evaluated", eval_run_id="ev1"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(apps, "kubectl_secret", lambda p: _secret("admin@example.com", "s3cret"))

    def run(*argv: str) -> str:
        monkeypatch.setattr("sys.argv", ["agent-factory", "apps", *argv])
        cli.main()
        return capsys.readouterr().out

    listed = run()
    assert "plant-watering" in listed and "admin@example.com" in listed and "s3cret" not in listed
    assert "evaluated" not in listed, "evaluation products are not apps"
    assert "s3cret" not in run("plant-watering") and "s3cret" in run("plant-watering", "--password")
    assert run("plant-watering", "--password-only") == "s3cret\n"
    with pytest.raises(SystemExit, match="no app 'nope'"):
        run("nope")


# ----------------------------------------- make app-login, run for real with stand-ins (PR #159 review) --
def _make_app_login(tmp_path, *, lookup_ok: bool, password: str = "s3cret"):
    """Run `make app-login` from the repo with `docker` and `pbcopy` replaced on PATH: docker answers
    the detail line, and the password lookup either prints the password or fails as a missing Secret
    does; pbcopy records what it was given."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    if shutil.which("make") is None:
        pytest.skip("make not installed")
    repo = Path(__file__).resolve().parents[2]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    clip = tmp_path / "clipboard"
    lookup = f'printf "%s\\n" "{password}"' if lookup_ok else 'echo "no admin account for x in the cluster" >&2; exit 1'
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        'case "$*" in *--password-only*) ' + lookup + " ;;\n"
        '  *) echo "x (x)"; echo "  address: http://x" ;; esac\n'
    )
    (bin_dir / "pbcopy").write_text(f"#!/bin/sh\ncat > {clip}\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    res = subprocess.run(  # noqa: S603
        ["make", "--no-print-directory", "-C", str(repo), "app-login", "APP=x"],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    return res, (clip.read_text() if clip.exists() else None)


def test_a_failed_password_lookup_never_touches_the_clipboard_and_fails(tmp_path):
    res, clip = _make_app_login(tmp_path, lookup_ok=False)
    out = res.stdout + res.stderr
    assert res.returncode != 0, "the recipe fails"
    assert clip is None, "pbcopy is never run: the clipboard keeps what it had"
    assert "copied to the clipboard" not in out
    assert "no password copied: the lookup failed (clipboard untouched)" in out


def test_an_empty_password_is_not_copied_either(tmp_path):
    res, clip = _make_app_login(tmp_path, lookup_ok=True, password="")
    assert res.returncode != 0 and clip is None
    assert "the lookup returned nothing" in res.stdout


def test_a_found_password_goes_to_the_clipboard_and_never_to_the_screen(tmp_path):
    res, clip = _make_app_login(tmp_path, lookup_ok=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert clip == "s3cret", "exactly the password, no trailing newline"
    assert "password copied to the clipboard (never printed)" in res.stdout
    assert "s3cret" not in res.stdout + res.stderr
