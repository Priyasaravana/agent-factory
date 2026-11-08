"""Following a change's pull request on a team's repo (J3): poll, webhook and Refresh read the PR from
the host the same way; merged and closed unmerged end the change, and its work items follow."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from test_access import MEMBER
from test_api import _client
from test_existing_repos import _factory, _origin
from test_repo_changes import TOKEN, GitHubExecutor, _assessed

from agent_factory import pr_loop
from agent_factory.engine.pr_watch import PrReadError
from agent_factory.identity import NOBODY, PUBLIC_PATHS
from agent_factory.models import ChangeKind, ChangeStatus, PullRequestState

API = "https://api.github.com/repos/acme/hello-node"
SHA = "a" * 40
SECRET = "whsec_test_never_logged"  # noqa: S105 - a test value
NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
REF = pr_loop.PrRef("github.com", "acme/hello-node", 7)


def _state(**kw: Any) -> PullRequestState:
    base: dict[str, Any] = {
        "repo": "acme/hello-node",
        "number": 7,
        "url": "https://github.com/acme/hello-node/pull/7",
        "state": "open",
        "head_sha": SHA,
        "checked_at": NOW,
    }
    return PullRequestState(**{**base, **kw})


# ------------------------------------------------------------------ pure --
def test_a_pull_request_address_is_read_strictly():
    assert pr_loop.parse_pr_url("https://github.com/acme/hello-node/pull/7") == REF
    for bad in ("https://github.com/acme/hello-node/issues/7", "http://github.com/a/b/pull/1", "", None,
                "https://github.com/acme/hello-node/pull/7?x=1", "https://github.com/a/b/c/pull/1"):  # fmt: skip
        assert pr_loop.parse_pr_url(bad) is None


def test_ci_is_folded_and_our_own_status_never_counts():
    runs = {
        "check_runs": [
            {"name": "build", "status": "completed", "conclusion": "success"},
            {"name": "docs", "status": "completed", "conclusion": "skipped"},
            {"name": "lint", "status": "completed", "conclusion": "neutral"},
        ]
    }
    own = {"statuses": [{"context": pr_loop.OWN_STATUS, "state": "failure"}]}
    assert pr_loop.checks_of(runs, own) == ("passing", []), "skipped and neutral pass; our status is evidence"
    assert pr_loop.checks_of(None, own) == ("none", []), "only our own status: the team's CI reported nothing"
    runs["check_runs"].append({"name": "test", "status": "in_progress"})
    assert pr_loop.checks_of(runs, None) == ("pending", [])
    runs["check_runs"].append({"name": "test", "status": "completed", "conclusion": "failure"})
    ci = {"statuses": [{"context": "ci/jenkins", "state": "error"}]}
    assert pr_loop.checks_of(runs, ci) == ("failing", ["ci/jenkins", "test"]), "a failure wins over pending"


def test_each_reviewers_latest_word_counts():
    def r(who: str, state: str) -> dict[str, Any]:
        return {"user": {"login": who}, "state": state}

    assert pr_loop.review_of([]) == "none"
    assert pr_loop.review_of([r("a", "COMMENTED")]) == "commented"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED"), r("b", "APPROVED")]) == "changes_requested"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED"), r("a", "APPROVED")]) == "approved"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED"), r("a", "DISMISSED"), r("b", "APPROVED")]) == "approved"


def test_what_couldnt_be_read_is_said_never_clean():
    s = pr_loop.parse(
        REF,
        {"state": "open", "head": {"sha": SHA}, "html_url": REF and "https://github.com/acme/hello-node/pull/7"},
        check_runs=None,
        statuses=None,
        reviews=None,
        unread=["check runs (403)"],
        now=NOW,
        source="poll",
    )
    assert (s.state, s.checks, s.review, s.unread) == ("open", "none", "none", ["check runs (403)"])
    assert "couldn't read check runs (403)" in pr_loop.summary(s)


def test_merged_and_closed_end_the_change_once():
    merged = _state(state="merged", merged_by="lead")
    assert pr_loop.transition(_state(), merged).kind == "merged"
    assert pr_loop.transition(merged, merged).kind == "unchanged", "never twice"
    assert pr_loop.transition(_state(), _state(state="closed")).kind == "abandoned"
    t = pr_loop.transition(_state(checks="pending"), _state(checks="failing", failing=["test"]))
    assert t.kind == "updated" and t.lines == ["#7 open on aaaaaaa · CI failing: test · no review yet"]
    assert pr_loop.transition(_state(), _state()).kind == "unchanged"
    moved = pr_loop.transition(_state(), _state(head_sha="b" * 40))
    assert moved.lines == ["new commit on the branch: aaaaaaa → bbbbbbb"]


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_delivery_proves_itself_by_its_signature():
    body = b'{"x":1}'
    assert pr_loop.signature_ok(SECRET, body, _sign(body))
    assert not pr_loop.signature_ok(SECRET, body + b" ", _sign(body)), "the raw body is what's signed"
    assert not pr_loop.signature_ok(SECRET, body, _sign(body, "other"))
    assert not pr_loop.signature_ok(SECRET, body, None)
    assert not pr_loop.signature_ok(SECRET, body, "sha1=" + "0" * 40), "only SHA-256"
    assert not pr_loop.signature_ok("", body, _sign(body, "")), "no secret, no webhook"


def test_a_delivery_only_names_prs_to_look_at_again():
    repo = {"full_name": "acme/hello-node"}
    assert pr_loop.targets("pull_request", {"repository": repo, "pull_request": {"number": 7, "merged": True}}) == [
        pr_loop.Target("acme/hello-node", 7)
    ]
    suite = {"head_sha": SHA, "pull_requests": [{"number": 7}]}
    assert pr_loop.targets("check_suite", {"repository": repo, "check_suite": suite}) == [
        pr_loop.Target("acme/hello-node", 7),
        pr_loop.Target("acme/hello-node", None, SHA),
    ]
    assert pr_loop.targets("status", {"repository": repo, "sha": SHA}) == [pr_loop.Target("acme/hello-node", None, SHA)]
    for event, payload in [
        ("push", {"repository": repo}),  # not an event we follow
        ("pull_request", {"repository": {"full_name": "../etc"}, "pull_request": {"number": 7}}),
        ("pull_request", {"repository": repo, "pull_request": {"number": "7; rm"}}),
        ("status", {"repository": repo, "sha": "not-a-sha"}),
        ("pull_request", []),
    ]:
        assert pr_loop.targets(event, payload) == [], event
    # a commit names a followed PR only by its head; another repo never matches
    assert pr_loop.matches(pr_loop.Target("acme/hello-node", None, SHA[:12]), _state(), REF)
    assert not pr_loop.matches(pr_loop.Target("acme/other", 7), _state(), REF)


# ---------------------------------------------------------------- watcher --
class FakeGitHub:
    """The REST answers for PR #7; records each request's headers."""

    def __init__(self) -> None:
        self.pr: dict[str, Any] = {"state": "open", "merged": False, "head": {"sha": SHA}, "mergeable": True}
        self.runs: dict[str, Any] = {"check_runs": [{"name": "test", "status": "in_progress"}]}
        self.reviews: list[dict[str, Any]] = []
        self.denied: set[str] = set()
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, method: str, url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
        assert method == "GET", "the loop only reads"
        self.calls.append((url, headers))
        path = url.removeprefix(API)
        for part in self.denied:
            if part in path:
                return 403, {"message": "Resource not accessible by personal access token"}
        if path == "/pulls/7":
            return 200, {"html_url": "https://github.com/acme/hello-node/pull/7", **self.pr}
        if path.startswith(f"/commits/{SHA}/check-runs"):
            return 200, self.runs
        if path.startswith(f"/commits/{SHA}/status"):
            return 200, {"statuses": [{"context": pr_loop.OWN_STATUS, "state": "success"}]}
        if path.startswith("/pulls/7/reviews"):
            return 200, self.reviews
        return 404, {"message": "Not Found"}


async def _pr_change(make_factory, tmp_path, monkeypatch):
    """A delivered change to a team repo that opened PR #7 and addresses one ledger item."""
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    product = await _assessed(f)
    item = next(i for i in f.store.list_work_items(product.id) if i.status == "open")
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep, items=[item.id])
    from conftest import wait_run

    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    gh = FakeGitHub()
    f.manager.pr_watcher.http = gh
    return f, f.store.get_change(change.id), item.id, gh


def _item(f, product_id: str, item_id: str):
    return next(i for i in f.store.list_work_items(product_id) if i.id == item_id)


def _sealed(f, change_id: str) -> str:
    return (f.manager.ws.data_dir / "artifacts" / change_id / "events.jsonl").read_text()


async def test_a_merged_pr_ends_the_change_and_its_items_await_verification(make_factory, tmp_path, monkeypatch):
    f, change, item_id, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    w = f.manager.pr_watcher
    assert [c.id for c in w.followed()] == [change.id]

    c = await w.refresh(change.id, "poll")
    assert (c.pull_request.state, c.pull_request.checks, c.outcome) == ("open", "pending", None)
    gh.runs = {"check_runs": [{"name": "test", "status": "completed", "conclusion": "failure"}]}
    c = await w.refresh(change.id, "poll")
    assert (c.pull_request.checks, c.pull_request.failing) == ("failing", ["test"])
    assert any("pull request #7 open on aaaaaaa · CI failing: test" in e.message for e in f.store.list_events(c.id))

    gh.pr.update(state="closed", merged=True, merged_at="2026-11-08T13:00:00Z", merged_by={"login": "lead"})
    c = await w.refresh(change.id, "refresh")
    assert c.outcome is not None and (c.outcome.state, c.outcome.by) == ("merged", "lead")
    assert c.status == ChangeStatus.awaiting_feedback, "merged is an outcome, not a new status"
    it = _item(f, c.product_id, item_id)
    assert it.status == "in_progress", "fixed only when its check really runs again (ADR-0040)"
    assert it.history[-1].event == "merged" and "awaiting verification" in it.history[-1].note
    assert "pull request #7 merged by lead" in _sealed(f, c.id), "the outcome is sealed with the evidence"
    assert w.followed() == [], "nothing left to follow"
    calls = len(gh.calls)
    await w.refresh(change.id, "poll")
    assert len(gh.calls) == calls, "a change with an outcome is never read again"

    # the token went only into the Authorization header: never an event, the evidence or a URL
    assert all(h["Authorization"] == f"Bearer {TOKEN}" and TOKEN not in u for u, h in gh.calls)
    assert all(TOKEN not in json.dumps(e.model_dump(mode="json")) for e in f.store.list_events(c.id))


async def test_a_pr_closed_unmerged_is_abandoned_and_its_items_reopen(make_factory, tmp_path, monkeypatch):
    f, change, item_id, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    gh.pr.update(state="closed", closed_at="2026-11-08T13:00:00Z")
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert c.outcome is not None and c.outcome.state == "abandoned"
    it = _item(f, c.product_id, item_id)
    assert it.status == "open" and change.id not in it.claimed_by
    # a person's own outcome after it is refused, like any second outcome
    with pytest.raises(Exception, match="already abandoned"):
        await f.manager.accept(change.id, "", "priya")


async def test_a_person_s_outcome_stops_the_following(make_factory, tmp_path, monkeypatch):
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    await f.manager.accept(change.id, "good", "priya")
    assert f.manager.pr_watcher.followed() == []
    gh.pr.update(state="closed", merged=True)
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert c.outcome.state == "accepted" and gh.calls == [], "the person's word stands"


async def test_unreadable_parts_are_said_and_an_unreadable_pr_changes_nothing(make_factory, tmp_path, monkeypatch):
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    gh.denied = {"/check-runs"}  # a token without Checks: read
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert c.pull_request.unread == ["check runs (403)"] and c.pull_request.checks == "none"
    gh.denied = {"/pulls/7"}
    with pytest.raises(PrReadError, match="answered 403"):
        await f.manager.pr_watcher.refresh(change.id, "poll")
    assert f.store.get_change(change.id).pull_request.unread == ["check runs (403)"], "last good read kept"
    monkeypatch.delenv("REPO_GITHUB_TOKEN")
    with pytest.raises(PrReadError, match="no token"):
        await f.manager.pr_watcher.refresh(change.id, "poll")


# ------------------------------------------------------------- over HTTP --
def _hook(f, mode: str = "webhook") -> None:
    f.manager.cfg.existing_repos.pr_updates = mode  # type: ignore[assignment]


async def test_the_webhook_is_off_unless_chosen_and_signed(make_factory, tmp_path, monkeypatch):
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    f.settings.auth_mode = "gateway"
    body = json.dumps({"repository": {"full_name": "acme/hello-node"}, "pull_request": {"number": 7}}).encode()
    headers = {"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)}
    app, ctx, c = await _client(f)
    async with c:
        assert (await c.post("/api/hooks/github", content=body, headers=headers)).status_code == 404, "poll mode"
        _hook(f)
        assert (await c.post("/api/hooks/github", content=body, headers=headers)).status_code == 404, "no secret"
        monkeypatch.setenv("REPO_WEBHOOK_SECRET", SECRET)
        bad = {**headers, "X-Hub-Signature-256": _sign(body, "guess")}
        assert (await c.post("/api/hooks/github", content=body, headers=bad)).status_code == 401
        assert gh.calls == [], "an unsigned delivery reads nothing"
        ping = {"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sign(b"{}")}
        assert (await c.post("/api/hooks/github", content=b"{}", headers=ping)).status_code == 200
        big = b"x" * (pr_loop.MAX_BODY + 1)
        assert (
            await c.post("/api/hooks/github", content=big, headers={**headers, "X-Hub-Signature-256": _sign(big)})
        ).status_code == 413

        gh.pr.update(state="closed", merged=True, merged_by={"login": "lead"})
        r = await c.post("/api/hooks/github", content=body, headers=headers)  # no sign-in: public, signed
        assert r.status_code == 202 and r.json() == {"accepted": True}
        await asyncio.gather(*f.manager.pr_watcher.background)
        assert f.store.get_change(change.id).outcome.state == "merged"
        assert f.store.get_change(change.id).pull_request.source == "webhook"
    await ctx.__aexit__(None, None, None)


def test_a_public_path_is_nobody_never_the_dev_admin():
    assert "/api/hooks/github" in PUBLIC_PATHS
    assert not NOBODY.is_admin and NOBODY.role not in {"admin", "member"}


async def test_refresh_over_http(make_factory, tmp_path, monkeypatch):
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post(f"/api/changes/{change.id}/pull-request/refresh", headers=MEMBER)
        assert r.status_code == 200 and r.json()["pull_request"]["source"] == "refresh"
        assert (await c.post(f"/api/changes/{change.id}/pull-request/refresh")).status_code == 401
        first = next(x for x in f.store.list_changes(change.product_id) if not x.pr_url)
        r = await c.post(f"/api/changes/{first.id}/pull-request/refresh", headers=MEMBER)
        assert r.status_code == 422 and "opened no pull request" in r.json()["detail"]
    await ctx.__aexit__(None, None, None)


async def test_the_poll_runs_only_when_it_should(make_factory):
    f = make_factory()
    w = f.manager.pr_watcher
    assert w.interval_minutes() == 5
    _hook(f)
    assert w.interval_minutes() == 60, "webhook mode keeps a slow backstop"
    f.manager.cfg.existing_repos.pr_webhook_backstop_minutes = 0
    w.start()
    assert w._loop is None, "dry-run PRs are simulated: nothing to follow"
