"""Repository sources (ADR-0047): team repos from many hosts, orgs and tokens, each reached only by a
source that covers it, within what the config allows."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from conftest import wait_run
from test_api import _client
from test_existing_repos import URL, _factory, _origin
from test_pr_loop import SECRET, FakeGitHub, _sign
from test_repo_changes import TOKEN, GitHubExecutor

from agent_factory import repo_sources as rs
from agent_factory.assess import RepoRef
from agent_factory.config import ExistingReposConfig
from agent_factory.engine.pipeline import InvalidRequestError
from agent_factory.engine.workspace import token_env
from agent_factory.models import ChangeKind, ChangeStatus, OnboardRepoInput, RepoSourceInput
from agent_factory.secret_refs import REDACTOR

ADMIN = {"X-Auth-User": "saravana", "X-Auth-Role": "admin"}
MEMBER = {"X-Auth-User": "priya", "X-Auth-Role": "member"}
ACME_TOKEN = "ghp_acme_only_token_0987654321"  # noqa: S105 - a test value
OTHER_URL = "https://github.com/globex/billing"


def _src(**kw) -> rs.RepoSource:
    return rs.RepoSource(**{"id": "acme", "owners": ["acme"], "token_ref": "env://REPO_ACME_TOKEN", **kw})


def _acme(f, **kw):
    """An admin-made source covering only the acme org, with its own token."""
    body = {"id": "acme", "title": "Acme org", "owners": ["acme"], "token_ref": "env://REPO_ACME_TOKEN", **kw}
    return f.manager.sources.create(RepoSourceInput(**body), "saravana")


# ----------------------------------------------------------------- pure --
def test_a_source_that_names_the_owner_wins_over_any_owner():
    ref = RepoRef("github.com", "acme", "hello-node")
    default = rs.RepoSource(id="default", owners=["*"], origin="default")
    acme = _src()
    assert rs.choose(ref, [default, acme]).id == "acme"
    assert rs.choose(RepoRef("github.com", "globex", "x"), [default, acme]).id == "default"
    assert rs.choose(ref, [default, acme], "default").id == "default", "a person may choose a covering source"
    with pytest.raises(rs.SourceError, match="doesn't cover"):
        rs.choose(RepoRef("github.com", "globex", "x"), [acme], "acme")
    with pytest.raises(rs.SourceError, match="choose one"):
        rs.choose(ref, [acme, _src(id="acme-2")])
    with pytest.raises(rs.SourceError, match="no repository source covers"):
        rs.choose(RepoRef("ghe.example.com", "acme", "x"), [default, acme])
    assert rs.choose(RepoRef("github.com", "ACME", "x"), [acme]).id == "acme", "owners match case-insensitively"


def test_the_recorded_source_is_checked_at_every_use_and_never_swapped():
    ref = RepoRef("github.com", "acme", "hello-node")
    narrowed = _src(owners=["acme-labs"])
    with pytest.raises(rs.SourceError, match="no longer covers"):
        rs.still_covers(ref, [narrowed, rs.RepoSource(id="default", owners=["*"])], "acme")
    with pytest.raises(rs.SourceError, match="no longer exists"):
        rs.still_covers(ref, [rs.RepoSource(id="default", owners=["*"])], "acme")


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"api_url": "https://evil.example/api/v3"}, "must be on github.com"),
        ({"api_url": "http://api.github.com"}, "plain https"),
        ({"api_url": "https://x:y@api.github.com"}, "plain https"),
        ({"host": "https://github.com"}, "DNS name"),
        ({"token_ref": "ghp_a_raw_value"}, "reference"),
        ({"owners": ["ac me"]}, "letters, digits"),
        ({"id": "Acme Org"}, "lowercase"),
    ],
)
def test_malformed_sources_are_refused(kw, why):
    with pytest.raises(ValueError, match=why):
        _src(**kw)


def test_github_enterprise_gets_its_own_api_and_gh_environment():
    ghe = _src(host="ghe.example.com")
    assert ghe.api_url == "https://ghe.example.com/api/v3"
    assert ghe.gh_repo(RepoRef("ghe.example.com", "acme", "app")) == "ghe.example.com/acme/app"
    assert ghe.gh_env("t0k3n!") == {"GH_HOST": "ghe.example.com", "GH_ENTERPRISE_TOKEN": "t0k3n!"}
    assert _src().gh_env("t0k3n!") == {"GH_TOKEN": "t0k3n!"}


def test_the_config_floor_limits_admin_sources():
    hosts, refs = ["github.com"], ["env://REPO_*"]
    rs.check_floor(_src(origin="admin"), hosts, refs)
    with pytest.raises(rs.SourceError, match="not allowed"):
        rs.check_floor(_src(origin="admin", host="gitlab.com"), hosts, refs)
    with pytest.raises(rs.SourceError, match="not one sources may use"):
        rs.check_floor(_src(origin="admin", token_ref="env://ANTHROPIC_API_KEY"), hosts, refs)
    with pytest.raises(rs.SourceError, match="config sources only"):
        rs.check_floor(_src(origin="admin", owners=["*"]), hosts, refs)
    with pytest.raises(rs.SourceError, match="at least one owner"):
        rs.check_floor(_src(origin="admin", owners=[]), hosts, refs)
    # the owner's own config may name any reference and any owner
    rs.check_floor(_src(origin="config", owners=["*"], token_ref="vault://kv/acme#token"), hosts, refs)


def test_config_sources_and_the_default_one():
    er = ExistingReposConfig(sources=[{"id": "acme", "owners": ["acme"], "token_ref": "vault://kv/acme#t"}])
    got = [(s.id, s.origin, s.owners, s.token_ref) for s in er.config_sources()]
    assert got == [
        ("default", "default", ["*"], "env://REPO_GITHUB_TOKEN"),
        ("acme", "config", ["acme"], "vault://kv/acme#t"),
    ]
    assert [s.id for s in ExistingReposConfig(default_source=False).config_sources()] == []
    with pytest.raises(ValueError, match="used twice"):
        ExistingReposConfig(sources=[{"id": "default", "owners": ["acme"]}])
    with pytest.raises(ValueError, match="not allowed"):
        ExistingReposConfig(sources=[{"id": "ghe", "host": "ghe.example.com", "owners": ["acme"]}])
    ghe = ExistingReposConfig(allowed_hosts=["github.com", "ghe.example.com"])
    assert [(s.id, s.api_url) for s in ghe.config_sources()] == [
        ("default", "https://api.github.com"),
        ("default-2", "https://ghe.example.com/api/v3"),
    ]


def test_the_clone_token_travels_in_the_environment_to_the_repos_origin_only():
    env = token_env("https://github.com/acme/private.git", TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]).decode() == f"x-access-token:{TOKEN}"
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("http.followRedirects", "false")
    assert REDACTOR.knows(TOKEN) and REDACTOR.knows(env["GIT_CONFIG_VALUE_0"].split()[-1])
    assert token_env("http://github.com/acme/x.git", TOKEN) == {}, "never over plain http"


# --------------------------------------------------------------- engine --
async def test_two_orgs_each_reach_their_repo_with_their_own_token(make_factory, tmp_path, monkeypatch):
    f = _factory(make_factory, _origin(tmp_path))
    _acme(f)
    acme = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    other = f.manager.onboard_repo(OnboardRepoInput(repo_url=OTHER_URL))
    assert (acme.repo_source, other.repo_source) == ("acme", "default")

    seen: list[dict[str, str]] = []
    real = f.manager.ws.ex.run

    async def spy(command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN202
        if "clone" in command or "fetch" in command:
            seen.append(dict(env or {}))
        return await real(command, cwd=cwd, timeout=timeout, env=env)

    f.manager.ws.ex.run = spy  # type: ignore[method-assign]
    monkeypatch.setenv("REPO_ACME_TOKEN", ACME_TOKEN)
    first = f.manager.start_change(acme)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    header = base64.b64encode(f"x-access-token:{ACME_TOKEN}".encode()).decode()
    assert seen and seen[0]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {header}"
    msgs = [e.message for e in f.store.list_events(first.id)]
    assert any("via source 'acme' with its token" in m for m in msgs)
    assert all(ACME_TOKEN not in json.dumps(e.model_dump(mode="json")) for e in f.store.list_events(first.id))
    config = (f.manager.ws.product_dir(acme.slug) / ".git" / "config").read_text()
    assert ACME_TOKEN not in config and header not in config and "extraheader" not in config.lower()


async def test_a_refused_token_falls_back_to_reading_a_public_repo_anonymously(make_factory, tmp_path, monkeypatch):
    f = _factory(make_factory, _origin(tmp_path))
    _acme(f)
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    real = f.manager.ws.ex.run
    tried: list[bool] = []

    async def refuse_with_token(command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN202
        if "clone" in command:
            tried.append("GIT_CONFIG_COUNT" in (env or {}))
            if tried[-1]:
                from agent_factory.executor import CommandResult

                return CommandResult(command, 128, "remote: Repository not found.\nfatal: Authentication failed")
        return await real(command, cwd=cwd, timeout=timeout, env=env)

    f.manager.ws.ex.run = refuse_with_token  # type: ignore[method-assign]
    monkeypatch.setenv("REPO_ACME_TOKEN", ACME_TOKEN)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert tried == [True, False]
    msg = next(e.message for e in f.store.list_events(change.id) if e.message.startswith("cloned "))
    assert "its token was refused, read anonymously: fatal: Authentication failed" in msg


async def test_choosing_a_source_that_does_not_cover_the_repo_is_refused(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    _acme(f)
    with pytest.raises(InvalidRequestError, match="doesn't cover"):
        f.manager.onboard_repo(OnboardRepoInput(repo_url=OTHER_URL, source="acme"))
    with pytest.raises(InvalidRequestError, match="not found"):
        f.manager.onboard_repo(OnboardRepoInput(repo_url=URL, source="nope"))


async def test_a_repo_without_a_source_is_held_and_never_cloned(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    acme = _acme(f)
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    assert product.repo_source == acme.id
    f.store.delete_repo_source("acme")  # gone behind the factory's back
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.held
    change = f.store.get_change(change.id)
    assert "no repository source reaches this repo" in change.summary
    assert "no longer exists" in (change.last_failure or "")
    assert not (f.manager.ws.product_dir(product.slug) / ".git").exists(), "it never fell back to another token"


async def test_a_product_from_before_sources_gets_one_recorded(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    product.repo_source = None
    f.store.save_product(product)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert f.store.get_product(product.id).repo_source == "default"
    assert any("repository source 'default' chosen" in e.message for e in f.store.list_events(change.id))


async def test_a_read_only_source_never_pushes(make_factory, tmp_path, monkeypatch):
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    _acme(f, access="read")
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback, "assessing needs only read"
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_ACME_TOKEN", ACME_TOKEN)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)
    assert await wait_run(f, change.id) == ChangeStatus.held
    assert "is read-only: nothing pushed" in f.store.get_change(change.id).summary
    assert not any(c.startswith(("git push", "gh pr", "gh auth")) for c in ex.calls)


async def test_the_pr_uses_the_products_source_token(make_factory, tmp_path, monkeypatch):
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    _acme(f)
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_ACME_TOKEN", ACME_TOKEN)
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)  # the default source's: must not be used for acme
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    envs = [e for e in ex.envs if "GH_TOKEN" in e]
    assert envs and all(e["GH_TOKEN"] == ACME_TOKEN for e in envs)
    assert all(ACME_TOKEN not in c and TOKEN not in c for c in ex.calls)

    # the PR is read back with the same source; a source narrowed past the repo stops reading it
    gh = FakeGitHub()
    f.manager.pr_watcher.http = gh
    await f.manager.pr_watcher.refresh(change.id, "poll")
    assert gh.calls and all(h["Authorization"] == f"Bearer {ACME_TOKEN}" for _, h in gh.calls)
    src = f.store.get_repo_source("acme")
    src.owners = ["acme-labs"]
    f.store.save_repo_source(src)
    from agent_factory.engine.pr_watch import PrReadError

    with pytest.raises(PrReadError, match="no longer covers"):
        await f.manager.pr_watcher.refresh(change.id, "poll")


async def test_sources_in_use_are_not_deleted_or_narrowed(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    _acme(f)
    f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    srcs = f.manager.sources
    with pytest.raises(rs.SourceError, match="is used by"):
        srcs.delete("acme")
    body = RepoSourceInput(id="acme", owners=["acme-labs"], token_ref="env://REPO_ACME_TOKEN")
    with pytest.raises(rs.SourceError, match="stop covering"):
        srcs.update("acme", body, "saravana")
    wider = RepoSourceInput(
        id="acme", title="Acme org", owners=["acme", "acme-labs"], token_ref="env://REPO_ACME_TOKEN2"
    )
    got = srcs.update("acme", wider, "lead")
    assert got.history[-1].by == "lead" and got.history[-1].fields == ["owners", "token_ref"]
    with pytest.raises(rs.SourceError, match="config file"):
        srcs.delete("default")


async def test_an_admin_source_the_config_no_longer_allows_is_unusable(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    _acme(f)
    f.manager.cfg.existing_repos.source_token_refs = ["vault://repos/*"]
    assert "acme" not in [s.id for s in f.manager.sources.all()]


# ----------------------------------------------------------------- HTTP --
async def test_sources_over_http_admin_only_and_members_see_less(make_factory, tmp_path, monkeypatch):
    f = _factory(make_factory, _origin(tmp_path))
    f.settings.auth_mode = "gateway"
    monkeypatch.setenv("REPO_ACME_TOKEN", ACME_TOKEN)
    body = {"id": "acme", "title": "Acme", "owners": ["acme"], "token_ref": "env://REPO_ACME_TOKEN"}
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.post("/api/repo-sources", json=body, headers=MEMBER)).status_code == 403
        r = await c.post("/api/repo-sources", json=body, headers=ADMIN)
        assert r.status_code == 201 and r.json()["token_ready"] is True
        bad = {**body, "id": "other", "token_ref": "env://ANTHROPIC_API_KEY"}
        r = await c.post("/api/repo-sources", json=bad, headers=ADMIN)
        assert r.status_code == 422 and "not one sources may use" in r.json()["detail"]

        admin = (await c.get("/api/repo-sources", headers=ADMIN)).json()
        member = (await c.get("/api/repo-sources", headers=MEMBER)).json()
        a = next(s for s in admin["sources"] if s["id"] == "acme")
        m = next(s for s in member["sources"] if s["id"] == "acme")
        assert a["token_ref"] == "env://REPO_ACME_TOKEN" and a["history"][0]["by"] == "saravana"
        assert m["token_ref"] is None and m["token_ready"] is None and m["history"] == []
        assert member["editable"] is False and member["token_refs"] == []
        assert ACME_TOKEN not in json.dumps(admin)

        r = await c.post("/api/repos", json={"repo_url": URL, "source": "acme"}, headers=MEMBER)
        assert r.status_code == 201 and r.json()["product"]["repo_source"] == "acme"
        pid = r.json()["product"]["id"]
        await asyncio.sleep(0)
        for cid in [x["id"] for x in r.json()["changes"]]:
            await wait_run(f, cid)
        move = {"source": "default"}
        assert (await c.post(f"/api/products/{pid}/repo-source", json=move, headers=MEMBER)).status_code == 403
        r = await c.post(f"/api/products/{pid}/repo-source", json=move, headers=ADMIN)
        assert r.status_code == 200 and r.json()["product"]["repo_source"] == "default"
        assert (await c.delete("/api/repo-sources/acme", headers=ADMIN)).status_code == 200
    await ctx.__aexit__(None, None, None)


async def test_a_webhook_refreshes_only_the_prs_its_source_covers(make_factory, tmp_path, monkeypatch):
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    _acme(f, webhook_secret_ref="env://REPO_ACME_HOOK")
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_ACME_TOKEN", ACME_TOKEN)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    gh = FakeGitHub()
    f.manager.pr_watcher.http = gh
    f.manager.cfg.existing_repos.pr_updates = "webhook"  # type: ignore[assignment]
    other_secret = "another-orgs-webhook-secret"
    monkeypatch.setenv("REPO_WEBHOOK_SECRET", other_secret)  # the default source's
    monkeypatch.setenv("REPO_ACME_HOOK", SECRET)
    body = json.dumps({"repository": {"full_name": "acme/hello-node"}, "pull_request": {"number": 7}}).encode()
    w = f.manager.pr_watcher
    assert w.verified_by(body, _sign(body, other_secret)).id == "default"
    assert w.verified_by(body, _sign(body)).id == "acme"
    assert w.verified_by(body, _sign(body, "guess")) is None
    # signed by the default source's secret: it doesn't cover a product on acme's source
    assert await w.on_webhook("pull_request", json.loads(body), w.verified_by(body, _sign(body, other_secret))) == []
    assert gh.calls == []
    assert await w.on_webhook("pull_request", json.loads(body), w.verified_by(body, _sign(body))) == [change.id]
