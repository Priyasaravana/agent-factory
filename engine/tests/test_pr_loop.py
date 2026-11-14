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
    runs = [
        {"name": "build", "status": "completed", "conclusion": "success"},
        {"name": "docs", "status": "completed", "conclusion": "skipped"},
        {"name": "lint", "status": "completed", "conclusion": "neutral"},
    ]
    own = [{"context": pr_loop.OWN_STATUS, "state": "failure"}]
    assert pr_loop.checks_of(runs, own) == ("passing", []), "skipped and neutral pass; our status is evidence"
    assert pr_loop.checks_of([], own) == ("none", []), "only our own status: the team's CI reported nothing"
    runs.append({"name": "test", "status": "in_progress", "id": 10})
    assert pr_loop.checks_of(runs, []) == ("pending", [])
    runs.append({"name": "unit", "status": "completed", "conclusion": "failure"})
    ci = [{"context": "ci/jenkins", "state": "error"}]
    assert pr_loop.checks_of(runs, ci) == ("failing", ["ci/jenkins", "unit"]), "a failure wins over pending"


def test_only_the_latest_status_per_context_and_run_per_check_counts():
    """Review of #166, P1: a failure superseded by a passing retry on the same commit no longer wins,
    whatever order the rows arrive in."""
    failed = {"id": 1, "context": "ci/jenkins", "state": "failure", "updated_at": "2026-11-08T10:00:00Z"}
    passed = {"id": 2, "context": "ci/jenkins", "state": "success", "updated_at": "2026-11-08T10:05:00Z"}
    own = {"id": 3, "context": pr_loop.OWN_STATUS, "state": "failure", "updated_at": "2026-11-08T11:00:00Z"}
    assert pr_loop.checks_of([], [failed, passed, own]) == ("passing", [])
    assert pr_loop.checks_of([], [passed, failed, own]) == ("passing", []), "newest by time, not by position"
    later_fail = {**failed, "id": 4, "updated_at": "2026-11-08T10:10:00Z"}
    assert pr_loop.checks_of([], [failed, passed, later_fail]) == ("failing", ["ci/jenkins"])
    run = {"name": "test", "app": {"id": 15368}, "check_suite": {"id": 500}, "status": "completed"}
    rerun = [{**run, "id": 1, "conclusion": "failure"}, {**run, "id": 2, "conclusion": "success"}]
    assert pr_loop.checks_of(rerun, []) == ("passing", []), "a re-run in the same suite replaces the failure"
    other_app = {**run, "id": 3, "app": {"id": 99}, "conclusion": "failure"}
    assert pr_loop.checks_of([*rerun, other_app], []) == ("failing", ["test"]), "another app's check is its own"


def test_independent_checks_with_the_same_name_both_count():
    """Review of #166, P1 follow-up: two workflows report `test` through the same app on one commit (two
    check suites); the later one passing must not hide the other's failure."""
    run = {"name": "test", "app": {"id": 15368}, "status": "completed"}
    failed = {
        **run,
        "id": 1,
        "check_suite": {"id": 500},
        "conclusion": "failure",
        "completed_at": "2026-11-08T10:00:00Z",
    }
    passed = {
        **run,
        "id": 2,
        "check_suite": {"id": 501},
        "conclusion": "success",
        "completed_at": "2026-11-08T10:05:00Z",
    }
    assert pr_loop.checks_of([failed, passed], []) == ("failing", ["test"])
    assert pr_loop.checks_of([passed, failed], []) == ("failing", ["test"])
    no_suite = [{**run, "id": 3, "conclusion": "failure"}, {**run, "id": 4, "conclusion": "success"}]
    assert pr_loop.checks_of(no_suite, []) == ("failing", ["test"]), "no verified re-run identity: both count"


def test_a_ci_source_not_read_whole_never_passes():
    """Review of #166, P1: check runs refused (403) and a success status is unknown, not passing."""
    ok = [{"context": "ci/jenkins", "state": "success"}]
    assert pr_loop.checks_of(None, ok) == ("unknown", [])
    assert pr_loop.checks_of([], ok, runs_complete=False) == ("unknown", [])
    assert pr_loop.checks_of([], [], statuses_complete=False) == ("unknown", []), "nothing seen is not none"
    bad = [{"context": "ci/jenkins", "state": "failure"}]
    assert pr_loop.checks_of(None, bad) == ("failing", ["ci/jenkins"]), "a failure that was read still counts"
    pend = [{"context": "ci/jenkins", "state": "pending"}]
    assert pr_loop.checks_of(None, pend) == ("pending", []), "pending is not a pass either way"


def test_each_reviewers_latest_word_counts():
    def r(who: str, state: str) -> dict[str, Any]:
        return {"user": {"login": who}, "state": state}

    assert pr_loop.review_of([]) == "none"
    assert pr_loop.review_of([r("a", "COMMENTED")]) == "commented"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED"), r("b", "APPROVED")]) == "changes_requested"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED"), r("a", "APPROVED")]) == "approved"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED"), r("a", "DISMISSED"), r("b", "APPROVED")]) == "approved"
    assert pr_loop.review_of([r("a", "APPROVED")], complete=False) == "unknown", "page two could decide"
    assert pr_loop.review_of([r("a", "CHANGES_REQUESTED")], complete=False) == "changes_requested"
    assert pr_loop.review_of(None) == "unknown"


def test_what_couldnt_be_read_is_said_never_clean():
    s = pr_loop.parse(
        REF,
        {"state": "open", "head": {"sha": SHA}, "html_url": "https://github.com/acme/hello-node/pull/7"},
        check_runs=None,
        statuses=[{"context": "ci/jenkins", "state": "success"}],
        reviews=[],
        unread=["check runs (403)"],
        incomplete={pr_loop.RUNS},
        now=NOW,
        source="poll",
    )
    assert (s.state, s.checks, s.review, s.unread) == ("open", "unknown", "none", ["check runs (403)"])
    assert "CI unknown (not read whole)" in pr_loop.summary(s) and "couldn't read check runs (403)" in pr_loop.summary(
        s
    )


def test_next_pages_stay_on_the_api_origin():
    link = (
        '<https://api.github.com/repos/a/b/pulls/7/reviews?page=2>; rel="next", '
        '<https://api.github.com/x?page=5>; rel="last"'
    )
    assert pr_loop.next_link(link) == "https://api.github.com/repos/a/b/pulls/7/reviews?page=2"
    assert pr_loop.next_link('<https://api.github.com/x?page=1>; rel="prev"') is None
    assert pr_loop.next_link(None) is None
    api = "https://api.github.com"
    assert pr_loop.same_origin("https://api.github.com/repos/a/b?page=2", api)
    for bad in ("https://evil.example/repos/a/b", "http://api.github.com/repos/a", "https://api.github.com.evil/x"):
        assert not pr_loop.same_origin(bad, api), bad
    assert not pr_loop.same_origin("https://ghe.local/other/x", "https://ghe.local/api/v3"), "under the API's path"


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
    """The REST answers for PR #7, paged like GitHub (`page_size` rows, a `Link: rel="next"`); records
    each request's headers."""

    def __init__(self) -> None:
        self.pr: dict[str, Any] = {"state": "open", "merged": False, "head": {"sha": SHA}, "mergeable": True}
        self.runs: list[dict[str, Any]] = [{"name": "test", "status": "in_progress"}]
        self.statuses: list[dict[str, Any]] = [{"context": pr_loop.OWN_STATUS, "state": "success"}]
        self.reviews: list[dict[str, Any]] = []
        self.inline: list[dict[str, Any]] = []  # review comments on lines (slice 3)
        self.comments: list[dict[str, Any]] = []  # conversation comments (slice 3)
        self.denied: set[str] = set()  # path parts answered 403 (e.g. "/check-runs", "page=2")
        self.page_size = 100
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _page(self, url: str, rows: list[Any], key: str | None) -> tuple[int, Any, str | None]:
        page = int(url.split("page=")[-1]) if "&page=" in url else 1
        chunk = rows[(page - 1) * self.page_size : page * self.page_size]
        more = page * self.page_size < len(rows)
        link = f'<{url.split("&page=")[0]}&page={page + 1}>; rel="next"' if more else None
        body = chunk if key is None else {key: chunk, "total_count": len(rows)}
        return 200, body, link

    async def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, Any, str | None]:
        self.calls.append((url, headers))
        path = url.removeprefix(API)
        for part in self.denied:
            if part in path:
                return 403, {"message": "Resource not accessible by personal access token"}, None
        if path == "/pulls/7":
            return 200, {"html_url": "https://github.com/acme/hello-node/pull/7", **self.pr}, None
        if path.startswith(f"/commits/{SHA}/check-runs"):
            return self._page(url, self.runs, "check_runs")
        if path.startswith(f"/commits/{SHA}/status"):
            return self._page(url, self.statuses, "statuses")
        if path.startswith("/pulls/7/reviews"):
            return self._page(url, self.reviews, None)
        if path.startswith("/pulls/7/comments"):
            return self._page(url, self.inline, None)
        if path.startswith("/issues/7/comments"):
            return self._page(url, self.comments, None)
        return 404, {"message": "Not Found"}, None


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
    gh.runs = [{"name": "test", "status": "completed", "conclusion": "failure"}]
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
    assert c.pull_request.unread == ["check runs (403)"] and c.pull_request.checks == "unknown"
    gh.denied = {"/pulls/7"}
    with pytest.raises(PrReadError, match="answered 403"):
        await f.manager.pr_watcher.refresh(change.id, "poll")
    assert f.store.get_change(change.id).pull_request.unread == ["check runs (403)"], "last read kept"
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


async def test_a_decisive_result_on_page_two_is_read(make_factory, tmp_path, monkeypatch):
    """Review of #166, P2: a failing check and a changes-requested review past the first page count."""
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    gh.page_size = 2
    ok = {"status": "completed", "conclusion": "success"}
    gh.runs = [{**ok, "name": f"job{n}"} for n in range(3)] + [
        {"name": "e2e", "status": "completed", "conclusion": "failure"}
    ]
    gh.reviews = [{"user": {"login": "a"}, "state": "APPROVED"}, {"user": {"login": "b"}, "state": "COMMENTED"},
                  {"user": {"login": "a"}, "state": "CHANGES_REQUESTED"}]  # fmt: skip
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert (c.pull_request.checks, c.pull_request.failing) == ("failing", ["e2e"])
    assert c.pull_request.review == "changes_requested", "pages read in order: a's latest word is on page two"
    assert c.pull_request.unread == []
    assert any("check-runs?filter=latest&per_page=100&page=2" in u for u, _ in gh.calls)


async def test_a_failed_second_page_is_not_read_whole(make_factory, tmp_path, monkeypatch):
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    gh.page_size = 1
    gh.runs = [
        {"name": "a", "status": "completed", "conclusion": "success"},
        {"name": "b", "status": "completed", "conclusion": "success"},
    ]
    gh.reviews = [{"user": {"login": "a"}, "state": "APPROVED"}, {"user": {"login": "b"}, "state": "APPROVED"}]
    gh.denied = {"page=2"}
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert c.pull_request.checks == "unknown" and c.pull_request.review == "unknown"
    assert "check runs (page 2: 403)" in c.pull_request.unread and "reviews (page 2: 403)" in c.pull_request.unread


async def test_the_page_limit_and_a_short_total_are_not_read_whole(make_factory, tmp_path, monkeypatch):
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    gh.page_size = 1
    gh.runs = [{"name": f"j{n}", "status": "completed", "conclusion": "success"} for n in range(pr_loop.MAX_PAGES + 1)]
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert c.pull_request.checks == "unknown"
    assert f"check runs (more than {pr_loop.MAX_PAGES} pages)" in c.pull_request.unread


async def test_a_terminal_outcome_converges_after_failures(make_factory, tmp_path, monkeypatch):
    """Review of #166, P1: the outcome is saved first with what follows it; a failure in the work items
    or the seal is resumed by the next refresh (or a restart), with no duplicate event or history."""
    f, change, item_id, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    w, store = f.manager.pr_watcher, f.store
    gh.pr.update(state="closed", merged=True, merged_by={"login": "lead"})

    real_items, real_seal = store.save_work_items, f.manager._reseal
    fail = {"items": 1, "seal": 1}

    def flaky_items(items):  # noqa: ANN001, ANN202
        if fail["items"]:
            fail["items"] -= 1
            raise RuntimeError("database is locked")
        return real_items(items)

    async def flaky_seal(c):  # noqa: ANN001, ANN202
        if fail["seal"]:
            fail["seal"] -= 1
            raise OSError("disk full")
        return await real_seal(c)

    monkeypatch.setattr(store, "save_work_items", flaky_items)
    monkeypatch.setattr(f.manager, "_reseal", flaky_seal)

    with pytest.raises(RuntimeError, match="locked"):
        await w.refresh(change.id, "poll")
    c = store.get_change(change.id)
    assert c.outcome.state == "merged" and c.pr_finalise == ["items", "continuations", "seal"], "outcome kept, the rest"
    assert [x.id for x in w.followed()] == [change.id], "still followed until it agrees"
    calls = len(gh.calls)

    with pytest.raises(OSError, match="disk full"):
        await w.refresh(change.id, "poll")
    assert store.get_change(change.id).pr_finalise == ["seal"]
    assert len(gh.calls) == calls, "finishing never reads the PR again: the outcome stands"

    assert await w.resume() == 1  # what a restart does
    c = store.get_change(change.id)
    assert c.pr_finalise == [] and w.followed() == []
    finals = [e for e in store.list_events(c.id) if e.data.get("pull_request_final") == 7]
    assert len(finals) == 1, "one event, however many tries"
    it = _item(f, c.product_id, item_id)
    assert [h.event for h in it.history].count("merged") == 1 and it.status == "in_progress"
    assert "pull request #7 merged by lead" in _sealed(f, c.id)
    await w.refresh(change.id, "poll")
    assert len([e for e in store.list_events(c.id) if e.data.get("pull_request_final") == 7]) == 1


async def test_a_short_combined_status_cannot_pass(make_factory, tmp_path, monkeypatch):
    """Review of #166, P2 follow-up: the combined status's `total_count` counts its status contexts, so
    fewer contexts than that with no next page is a short read: CI unknown, never passing."""
    f, change, _, gh = await _pr_change(make_factory, tmp_path, monkeypatch)
    gh.runs = []
    gh.statuses = [{"context": "ci/jenkins", "state": "success"}, {"context": "ci/lint", "state": "failure"}]
    real = gh._page

    def short(url, rows, key):  # noqa: ANN001, ANN202 - GitHub answering one context of two, no next link
        code, body, link = real(url, rows, key)
        if key == "statuses":
            body = {"statuses": rows[:1], "total_count": len(rows)}
        return code, body, link

    gh._page = short
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert c.pull_request.checks == "unknown" and "commit statuses (1 of 2)" in c.pull_request.unread
    gh._page = real
    c = await f.manager.pr_watcher.refresh(change.id, "poll")
    assert (c.pull_request.checks, c.pull_request.failing) == ("failing", ["ci/lint"]), "read whole: the failure shows"
    assert any("check-runs?filter=latest&per_page=100" in u for u, _ in gh.calls), "GitHub drops superseded runs"
