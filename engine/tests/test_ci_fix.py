"""Red CI on a change's pull request is fixed on the same PR (J3 slice 2): a fix change continues the PR on
its branch from the failing head commit, pushes fast-forward to the same branch (never forced, never a
second PR), and the PR loop reads it again. A member starts it, or the lane does when its CI fix is
autonomous."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import wait_run
from test_access import ADMIN
from test_api import _client
from test_existing_repos import _factory, _origin
from test_repo_changes import TOKEN, GitHubExecutor, _assessed

from agent_factory import pr_loop
from agent_factory.attention import pending_actions
from agent_factory.models import ChangeKind, ChangeSource, ChangeStatus, PullRequestState

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
SHA = "a" * 40
SLUG = "acme/hello-node"


def _pr(**kw: Any) -> PullRequestState:
    base: dict[str, Any] = {
        "repo": SLUG,
        "number": 7,
        "url": f"https://github.com/{SLUG}/pull/7",
        "state": "open",
        "head_sha": SHA,
        "head_ref": "factory/abc",
        "head_repo": SLUG,
        "checks": "failing",
        "failing": ["test"],
        "checked_at": NOW,
    }
    return PullRequestState(**{**base, **kw})


def _fix(sha: str = SHA, *, open_: bool = False, auto: bool = False, pushed: bool = False) -> pr_loop.FixRecord:
    return pr_loop.FixRecord(change_id="fix1", from_sha=sha, open=open_, auto=auto, pushed=pushed)


def _decide(pr: PullRequestState | None, fixes: list[pr_loop.FixRecord] | None = None, **kw: Any) -> pr_loop.CiFix:
    args: dict[str, Any] = {"followed": True, "slug": SLUG, "mode": "ask", "max_auto": 2, **kw}
    return pr_loop.ci_fix(pr, fixes or [], **args)


# ------------------------------------------------------------------ pure --
def test_a_fix_is_offered_only_for_red_ci_that_was_read():
    assert _decide(_pr()).offer and not _decide(_pr()).auto, "ask: a member starts it"
    for checks in ("passing", "pending", "none", "unknown"):
        assert not _decide(_pr(checks=checks, failing=[])).offer, f"{checks} is not red CI"
    assert not _decide(_pr(), followed=False).offer, "a PR with an outcome isn't fixed"
    assert not _decide(_pr(state="merged")).offer
    assert not _decide(None).offer
    no_branch = _decide(_pr(head_ref=""))
    assert not no_branch.offer and "refresh" in no_branch.reason
    fork = _decide(_pr(head_repo="someone/hello-node"))
    assert not fork.offer and "fork" in fork.reason, "a fork's branch is never pushed to"


def test_one_fix_at_a_time_and_nothing_more_once_pushed():
    running = _decide(_pr(), [_fix(open_=True)])
    assert not running.offer and running.running == "fix1", "a running or held fix is finished or closed first"
    waiting = _decide(_pr(), [_fix(pushed=True)])
    assert not waiting.offer and "new commit" in waiting.reason, "the PR is read again on the pushed commit"
    assert _decide(_pr(), [_fix("b" * 40, pushed=True)]).offer, "an older commit's fix doesn't block this one"


def test_an_autonomous_lane_starts_it_once_per_commit_and_within_its_limit():
    assert _decide(_pr(), mode="autonomous").auto
    tried = _decide(_pr(), [_fix()], mode="autonomous")
    assert tried.offer and not tried.auto and "tried" in tried.reason, "a failed fix from this commit doesn't loop"
    others = [_fix("b" * 40, auto=True), _fix("c" * 40, auto=True)]
    capped = _decide(_pr(), others, mode="autonomous")
    assert capped.offer and not capped.auto and "limit 2" in capped.reason, "a member may still start one"
    assert _decide(_pr(), [_fix("b" * 40, auto=False)], mode="autonomous", max_auto=1).auto, "only auto ones count"
    assert not _decide(_pr(), mode="autonomous", max_auto=0).auto


def test_the_branch_and_what_the_checks_said_are_read_carefully():
    pr = {
        "state": "open",
        "head": {"sha": SHA, "ref": "factory/abc", "repo": {"full_name": SLUG}},
        "html_url": f"https://github.com/{SLUG}/pull/7",
    }
    run = {
        "name": "test",
        "status": "completed",
        "conclusion": "failure",
        "output": {"title": "2 failed", "summary": "x" * 2000},
        "html_url": "https://github.com/acme/hello-node/runs/1",
    }
    statuses = [
        {"context": "lint", "state": "error", "description": "eslint: 3 errors", "target_url": "javascript:alert(1)"},
        {"context": pr_loop.OWN_STATUS, "state": "failure", "description": "ours"},
    ]
    ref = pr_loop.PrRef("github.com", SLUG, 7)
    s = pr_loop.parse(ref, pr, check_runs=[run], statuses=statuses, reviews=[], unread=[], now=NOW, source="poll")
    assert (s.head_ref, s.head_repo, s.checks) == ("factory/abc", SLUG, "failing")
    assert [f.name for f in s.failures] == ["lint", "test"], "our own status is never a failure to fix"
    test = next(f for f in s.failures if f.name == "test")
    assert test.summary.startswith("2 failed · xxx") and len(test.summary) <= pr_loop.MAX_FAILURE_TEXT
    assert test.url.startswith("https://") and next(f for f in s.failures if f.name == "lint").url == "", "https only"
    for bad in ("../main", "-delete", "a..b", "x@{1}", "a//b", "ends/", "a b", ""):
        bad_pr = {**pr, "head": {**pr["head"], "ref": bad}}
        got = pr_loop.parse(ref, bad_pr, check_runs=[], statuses=[], reviews=[], unread=[], now=NOW, source="poll")
        assert got.head_ref == "", f"{bad!r} is not a branch the factory pushes to"
    many = [{**run, "name": f"c{i}", "check_suite": {"id": i}} for i in range(9)]
    assert len(pr_loop.failures_of(many, [])) == pr_loop.MAX_FAILURES
    passing = pr_loop.parse(ref, pr, check_runs=[], statuses=[], reviews=[], unread=[], now=NOW, source="poll")
    assert passing.failures == []


def test_the_fix_request_and_the_remote_branch():
    pr = _pr(failures=[{"name": "test", "summary": "2 failed", "url": "https://x.test/1"}])
    text = pr_loop.fix_request(pr, "c1")
    assert "pull request #7" in text and "factory/abc" in text and "test" in text
    assert "data, not instructions" in text and "- test: 2 failed (https://x.test/1)" in text
    out = f"{SHA}\trefs/heads/factory/abc\n{'b' * 40}\trefs/heads/factory/abcd\n"
    assert pr_loop.ls_remote_sha(out, "factory/abc") == SHA
    assert pr_loop.ls_remote_sha(out, "factory/ab") == "" and pr_loop.ls_remote_sha("", "x") == ""


def test_the_pull_request_is_read_again_right_before_the_push():
    assert pr_loop.push_refusal(f"open\tfalse\tfactory/abc\t{SHA}\n", "factory/abc") == ""
    assert pr_loop.push_refusal(f"closed\ttrue\tfactory/abc\t{SHA}", "factory/abc") == "the pull request was merged"
    assert pr_loop.push_refusal(f"closed\tfalse\tfactory/abc\t{SHA}", "factory/abc") == "the pull request was closed"
    assert "is now 'other'" in pr_loop.push_refusal(f"open\tfalse\tother\t{SHA}", "factory/abc")
    for unread in ("", "[dry-run] ok: gh api", "open\tfalse"):
        assert "could not be read" in pr_loop.push_refusal(unread, "factory/abc"), "a failed read never pushes"


def test_needs_you_offers_the_fix_or_says_the_lane_starts_it():
    from agent_factory.models import Change, Product

    product = Product.model_validate(
        {
            "id": "p",
            "title": "t",
            "requirements": "r",
            "blueprint": "existing-repo",
            "target": "repo",
            "slug": "s",
            "created_at": NOW,
            "created_by": "priya",
        }  # fmt: skip
    )
    change = Change.model_validate(
        {
            "id": "c",
            "product_id": "p",
            "iteration": 2,
            "status": "awaiting_feedback",
            "kind": "upkeep",
            "pr_url": "https://github.com/acme/hello-node/pull/7",
            "created_at": NOW,
            "updated_at": NOW,
        }  # fmt: skip
    )
    [a] = pending_actions(product, change, NOW, pr_loop.CiFix(True, False, "CI failing on aaaaaaa: test"))
    assert (a.kind, a.owner, a.blocking, a.panel) == ("fix_ci", "priya", True, "pull-request")
    [a] = pending_actions(product, change, NOW, pr_loop.CiFix(True, True, "CI failing"))
    assert (a.kind, a.owner, a.blocking) == ("wait", "system", False)
    [a] = pending_actions(product, change, NOW, pr_loop.CiFix(False, False, "a fix is running", "fix1"))
    assert a.kind == "wait" and "in progress" in a.title
    [a] = pending_actions(product, change, NOW, None)
    assert a.kind == "feedback"


# ------------------------------------------------------------- end to end --
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class FakeHost:
    """GitHub's REST answers for PR #7, whose head follows the branch on the local origin."""

    def __init__(self, origin: Path, branch: str) -> None:
        self.origin, self.branch = origin, branch
        self.ci: dict[str, str] = {}  # head sha -> conclusion
        self.merged = False
        self.reviews: list[dict[str, Any]] = []
        self.inline: list[dict[str, Any]] = []  # review comments on lines (slice 3)
        self.comments: list[dict[str, Any]] = []  # conversation comments (slice 3)

    def head(self) -> str:
        return _git(self.origin, "rev-parse", self.branch)

    async def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, Any, str | None]:
        path = url.removeprefix(f"https://api.github.com/repos/{SLUG}")
        sha = self.head()
        if path == "/pulls/7":
            pr = {
                "html_url": f"https://github.com/{SLUG}/pull/7",
                "state": "closed" if self.merged else "open",
                "merged": self.merged,
                "merged_by": {"login": "lead"} if self.merged else None,
                "head": {"sha": sha, "ref": self.branch, "repo": {"full_name": SLUG}},
            }
            return 200, pr, None
        if path.startswith(f"/commits/{sha}/check-runs"):
            done = sha in self.ci
            run = {"name": "test", "status": "completed" if done else "in_progress", "conclusion": self.ci.get(sha)}
            run["output"] = {"title": "1 test failed", "summary": "expected 2, got 3"}
            return 200, {"check_runs": [run], "total_count": 1}, None
        if path.startswith(f"/commits/{sha}/status"):
            return 200, {"statuses": [], "total_count": 0}, None
        if path.startswith("/pulls/7/reviews"):
            return 200, self.reviews, None
        if path.startswith("/pulls/7/comments"):
            return 200, self.inline, None
        if path.startswith("/issues/7/comments"):
            return 200, self.comments, None
        return 404, {"message": "Not Found"}, None


async def _red_pr(make_factory, tmp_path, monkeypatch, *, lane: str = "ask"):
    """A delivered change to a team repo whose PR #7 is on the (local) origin, with its CI red."""
    origin = _origin(tmp_path)
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), origin)
    product = await _assessed(f)
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)

    async def no_gate(*a: Any) -> None:
        return None

    monkeypatch.setattr(f.manager.preflight, "gate", no_gate)
    item = next(i for i in f.store.list_work_items(product.id) if i.status == "open")
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep, items=[item.id])
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    branch = f"factory/{change.id}"
    # what the pull-request station pushed (the fake `gh` only says so): the change's branch on the origin
    _git(origin, "fetch", "-q", str(f.manager.ws.change_dir(change.id)), f"run/{change.id}:{branch}")
    reg = f.manager.workflows[f.store.get_change(change.id).workflow_id or ""]
    reg.get(reg.active_version()).ci_fix = lane  # the lane as it is now (a published setting)
    host = FakeHost(origin, branch)
    host.ci[host.head()] = "failure"
    f.manager.pr_watcher.http = host
    ex.scan_outputs["git ls-remote"] = f"{host.head()}\trefs/heads/{branch}\n"
    ex.scan_outputs["/pulls/7 --jq"] = f"open\tfalse\t{branch}\t{host.head()}\n"  # the PR read before the push
    return f, ex, host, f.store.get_change(change.id), item.id


async def test_a_member_fixes_red_ci_on_the_same_pull_request(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, item_id = await _red_pr(make_factory, tmp_path, monkeypatch)
    red = host.head()
    f.settings.auth_mode = "gateway"
    app, ctx, c = await _client(f)
    async with c:
        r = await c.post(f"/api/changes/{change.id}/pull-request/refresh", headers=ADMIN)
        assert r.status_code == 200 and r.json()["pull_request"]["checks"] == "failing"
        detail = (await c.get(f"/api/changes/{change.id}", headers=ADMIN)).json()
        assert detail["ci_fix"]["offer"] and not detail["ci_fix"]["auto"] and detail["ci_fix"]["mode"] == "ask"
        assert [a["kind"] for a in detail["actions"]] == ["fix_ci"]
        assert detail["change"]["pull_request"]["failures"][0]["summary"] == "1 test failed · expected 2, got 3"
        r = await c.post(f"/api/changes/{change.id}/ci-fix", headers=ADMIN)
        assert r.status_code == 200, r.text
        fix_id = r.json()["id"]
        again = await c.post(f"/api/changes/{change.id}/ci-fix", headers=ADMIN)
        assert again.status_code in (409, 422), "one fix at a time"
        assert await wait_run(f, fix_id) == ChangeStatus.awaiting_feedback
        fix = f.store.get_change(fix_id)
        assert fix.source == ChangeSource.ci and fix.kind == change.kind and fix.requested_by == "saravana"
        assert fix.continues and fix.continues.change_id == change.id and fix.continues.from_sha == red
        assert fix.pr_url is None and fix.id not in [x.id for x in f.manager.pr_watcher.followed()]
        # it built on the PR's head, and the push to the same branch was a plain fast-forward
        wt = f.manager.ws.change_dir(fix_id)
        assert _git(wt, "rev-parse", "HEAD~1") == red and fix.continues.pushed_sha == _git(wt, "rev-parse", "HEAD")
        branch = host.branch
        cmds = [x for x in ex.calls if "git push" in x or x.startswith("gh pr") or "ls-remote" in x]
        mine = cmds[cmds.index(next(x for x in cmds if "ls-remote" in x)) :]
        assert f"HEAD:refs/heads/{branch}" in mine[1] and "--force" not in mine[1] and ":+" not in mine[1]
        assert mine[2].startswith(f"gh pr comment 7 --repo {SLUG}"), "said on the PR, never a second one"
        assert sum(x.startswith("gh pr create") for x in ex.calls) == 1, "only the first change opened a PR"
        # only the fix's own files were scanned, reviewed and listed
        out = f.manager.ws.data_dir / "artifacts" / fix_id / "pull-request.json"
        assert '"CI-FIXES.md"' in out.read_text() and "app.test.js" not in out.read_text()
        assert any("pushed" in e.message and fix_id in e.message for e in f.store.list_events(change.id))

        # the host now has the fix (the fake push didn't reach it): CI passes and the loop reads it again
        _git(host.origin, "fetch", "-q", str(wt), f"run/{fix_id}:{branch}")
        host.ci[host.head()] = "success"
        c1 = await f.manager.pr_watcher.refresh(change.id, "poll")
        assert (c1.pull_request.head_sha, c1.pull_request.checks) == (host.head(), "passing")
        assert not f.manager.ci_fix_offer(c1).offer

        host.merged = True
        c1 = await f.manager.pr_watcher.refresh(change.id, "poll")
        assert c1.outcome.state == "merged"
        fix = f.store.get_change(fix_id)
        assert fix.outcome and fix.outcome.state == "merged" and f"of change {change.id}" in fix.outcome.note
        item = next(i for i in f.store.list_work_items(change.product_id) if i.id == item_id)
        assert item.status == "in_progress", "the original change's items await verification, once"
    await ctx.__aexit__(None, None, None)


async def test_an_autonomous_lane_fixes_by_itself_once_per_commit(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch, lane="autonomous")
    w = f.manager.pr_watcher
    await w.refresh(change.id, "poll")
    fixes = f.manager.continuations(change)
    assert len(fixes) == 1 and fixes[0].continues.auto and fixes[0].requested_by == change.requested_by
    assert await wait_run(f, fixes[0].id) == ChangeStatus.awaiting_feedback
    # the fix didn't reach the host here: CI is still red on the same commit, and nothing loops
    await w.refresh(change.id, "poll")
    assert len(f.manager.continuations(change)) == 1
    offer = f.manager.ci_fix_offer(f.store.get_change(change.id))
    assert not offer.offer and "new commit" in offer.reason


async def test_a_branch_that_moved_is_never_pushed_over(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    await f.manager.pr_watcher.refresh(change.id, "poll")
    pushes = sum("git push" in x for x in ex.calls)  # the original change's own push of its branch
    fix = f.manager.fix_ci(change.id, "priya")
    # someone else pushed to the branch on the host while the fix ran
    ex.scan_outputs["git ls-remote"] = f"{'d' * 40}\trefs/heads/{host.branch}\n"
    assert await wait_run(f, fix.id) == ChangeStatus.held
    fix = f.store.get_change(fix.id)
    assert "moved since the fix started" in (fix.summary or "") and fix.continues.pushed_sha is None
    assert sum("git push" in x for x in ex.calls) == pushes, "nothing pushed, nothing forced"
    offer = f.manager.ci_fix_offer(f.store.get_change(change.id))
    assert not offer.offer and offer.running == fix.id, "the held fix is resumed or closed first"
    await f.manager.close(fix.id, "the team pushed their own fix", "priya")
    assert f.manager.ci_fix_offer(f.store.get_change(change.id)).offer


async def test_a_pr_merged_while_the_fix_ran_is_left_alone(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    await f.manager.pr_watcher.refresh(change.id, "poll")
    fix = f.manager.fix_ci(change.id, "priya")
    await f.manager.accept(change.id, "merged by hand", "priya")  # the change's outcome lands first
    assert await wait_run(f, fix.id) == ChangeStatus.held
    assert "was not pushed" in (f.store.get_change(fix.id).summary or "")
    assert not any("ls-remote" in x for x in ex.calls)


async def test_a_fix_is_refused_where_it_does_not_apply(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    from agent_factory.engine.pipeline import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="isn't failing|wasn't read|followed"):
        f.manager.fix_ci(change.id, "priya")  # not read yet: nothing is known to be red
    host.ci[host.head()] = "success"
    await f.manager.pr_watcher.refresh(change.id, "poll")
    with pytest.raises(InvalidRequestError, match="isn't failing"):
        f.manager.fix_ci(change.id, "priya")
    first = next(x for x in f.store.list_changes(change.product_id) if x.kind == ChangeKind.assess)
    with pytest.raises(InvalidRequestError, match="no pull request"):
        f.manager.fix_ci(first.id, "priya")


async def test_a_fix_starts_only_on_the_commit_whose_ci_failed(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    await f.manager.pr_watcher.refresh(change.id, "poll")
    red = host.head()
    tree = _git(host.origin, "rev-parse", f"{red}^{{tree}}")
    newer = _git(host.origin, "commit-tree", tree, "-p", red, "-m", "the team's own commit")
    _git(host.origin, "update-ref", f"refs/heads/{host.branch}", newer)  # after the offer, before the fix runs
    fix = f.manager.fix_ci(change.id, "priya")
    assert await wait_run(f, fix.id) == ChangeStatus.held
    assert "moved since the fix was offered" in (f.store.get_change(fix.id).summary or "")
    assert not any("ls-remote" in x for x in ex.calls), "no agent ran, nothing was pushed"


async def test_the_lane_setting_is_a_settings_only_change(make_factory):
    from agent_factory.actions import set_workflow_settings, settings_only
    from agent_factory.models import WorkflowSettingsInput

    f = make_factory()
    reg = f.workflows["repo-change"]
    view = set_workflow_settings(f, "repo-change", WorkflowSettingsInput(ci_fix="autonomous"))
    assert view.ci_fix == "autonomous"
    assert settings_only(reg.get(reg.active_version()), reg.draft.get()[1]), "no evaluation measures who starts it"


async def test_a_pr_closed_on_the_host_since_the_last_read_gets_no_push(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    await f.manager.pr_watcher.refresh(change.id, "poll")
    pushes = sum("git push" in x for x in ex.calls)
    fix = f.manager.fix_ci(change.id, "priya")
    # merged on GitHub after the last poll: the stored PR still says open, the branch still exists
    ex.scan_outputs["/pulls/7 --jq"] = f"closed\ttrue\t{host.branch}\t{host.head()}\n"
    assert await wait_run(f, fix.id) == ChangeStatus.held
    assert "was merged: the fix was not pushed" in (f.store.get_change(fix.id).summary or "")
    assert sum("git push" in x for x in ex.calls) == pushes
    ex.scan_outputs.pop("/pulls/7 --jq")  # resumed, and the read fails: nothing is pushed either
    f.manager.resume(fix.id)
    assert await wait_run(f, fix.id) == ChangeStatus.held
    assert "could not be read" in (f.store.get_change(fix.id).summary or "")
    assert sum("git push" in x for x in ex.calls) == pushes


async def test_a_fix_still_running_when_the_pr_closes_takes_its_outcome_when_it_stops(
    make_factory, tmp_path, monkeypatch
):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    await f.manager.pr_watcher.refresh(change.id, "poll")
    fix = f.manager.fix_ci(change.id, "priya")
    host.merged = True
    c1 = await f.manager.pr_watcher.refresh(change.id, "poll")  # the outcome lands while the fix runs
    assert c1.outcome.state == "merged" and c1.pr_finalise == []
    assert f.store.get_change(fix.id).outcome is None, "a running fix isn't given an outcome mid-run"
    ex.scan_outputs["/pulls/7 --jq"] = f"closed\ttrue\t{host.branch}\t{host.head()}\n"
    assert await wait_run(f, fix.id) == ChangeStatus.held  # P1: nothing pushed to a merged PR
    await f.manager.close(fix.id, "the PR was merged without it", "priya")
    assert f.store.get_change(fix.id).outcome.state == "closed", "a person's own decision on the fix stands"


async def test_a_fix_that_stops_after_the_pr_closed_inherits_the_outcome(make_factory, tmp_path, monkeypatch):
    f, ex, host, change, _ = await _red_pr(make_factory, tmp_path, monkeypatch)
    await f.manager.pr_watcher.refresh(change.id, "poll")
    fix = f.manager.fix_ci(change.id, "priya")
    host.merged = True
    await f.manager.pr_watcher.refresh(change.id, "poll")  # finalised while the fix runs: it is skipped now
    assert f.store.get_change(change.id).pr_finalise == [] and f.store.get_change(fix.id).outcome is None
    f.manager.cancel(fix.id)  # however it stops (cancelled, failed, delivered), it takes the PR's outcome
    await f.manager._tasks[fix.id]
    got = f.store.get_change(fix.id)
    assert got.outcome and got.outcome.state == "merged" and f"of change {change.id}" in got.outcome.note
    assert got.outcome.status_before == "cancelled"
    assert f.manager.inherit_pr_outcome(got).outcome == got.outcome, "once"
