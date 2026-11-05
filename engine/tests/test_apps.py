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
