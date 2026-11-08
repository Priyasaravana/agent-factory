"""Following a change's pull request on a team's repo (J3). Pure: parsing what the repo's host says and
deciding what it means for the change. The watcher (`engine/pr_watch.py`) fetches and applies.

The host is the source of truth. Poll and webhook only differ in *when* the factory looks: a webhook
names the PRs to look at again, it is never read as the PR's state, so a forged or replayed delivery can
at worst cause one extra read. Both paths end in `parse` of the PR's own REST answers, then `transition`.

What a transition means (roadmap J3):
- merged → the change's outcome is **merged** (by whoever merged it); its work items await verification
  by the next assessment, as ADR-0040 already says of a delivered change.
- closed without merging → **abandoned**; its work items go back to open.
- anything else (CI, reviews, a new head commit) is recorded on the change and shown; acting on red CI
  and review comments is the next slice.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from agent_factory.models import PullRequestState

OWN_STATUS = "agent-factory/change-risk"  # the status the pull-request station sets: not the team's CI
MAX_FAILING = 20
_PR_URL = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+)/(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(?P<n>[0-9]+)/?$"
)
# GitHub: an owner is letters, digits and single hyphens; a repo name never "." or ".."
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.\.?$)[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")

# check run conclusions that mean the check did not pass (GitHub's list); neutral and skipped are not failures
_FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
_STATUS_FAILED = {"failure", "error"}


@dataclass(frozen=True)
class PrRef:
    host: str
    slug: str  # owner/name
    number: int


def parse_pr_url(url: str | None) -> PrRef | None:
    m = _PR_URL.match((url or "").strip())
    return PrRef(m["host"], m["slug"], int(m["n"])) if m else None


# ------------------------------------------------------------------ reading --
def checks_of(check_runs: Any, statuses: Any) -> tuple[str, list[str]]:
    """Fold the head commit's check runs and commit statuses into one word, plus the failing names.
    Our own change-risk status is evidence we set, not the team's CI, so it never counts."""
    failing: list[str] = []
    pending = passed = 0
    for run in (check_runs or {}).get("check_runs", []) if isinstance(check_runs, dict) else []:
        name = str(run.get("name") or "check")
        if run.get("status") != "completed":
            pending += 1
        elif run.get("conclusion") in _FAILED:
            failing.append(name)
        else:
            passed += 1
    for st in (statuses or {}).get("statuses", []) if isinstance(statuses, dict) else []:
        name = str(st.get("context") or "status")
        if name == OWN_STATUS:
            continue
        state = st.get("state")
        if state in _STATUS_FAILED:
            failing.append(name)
        elif state == "pending":
            pending += 1
        elif state == "success":
            passed += 1
    failing = sorted(set(failing))
    if failing:
        return "failing", failing[:MAX_FAILING]
    if pending:
        return "pending", []
    return ("passing" if passed else "none"), []


def review_of(reviews: Any) -> str:
    """Each reviewer's latest decisive review counts (as GitHub's own review decision does): any
    'changes requested' outstanding wins over approvals; a dismissal clears that reviewer."""
    latest: dict[str, str] = {}
    commented = False
    for r in reviews if isinstance(reviews, list) else []:
        who = str((r.get("user") or {}).get("login") or "")
        state = str(r.get("state") or "").upper()
        if state == "COMMENTED":
            commented = True
        elif state in {"APPROVED", "CHANGES_REQUESTED"}:
            latest[who] = state
        elif state == "DISMISSED":
            latest.pop(who, None)
    if "CHANGES_REQUESTED" in latest.values():
        return "changes_requested"
    if "APPROVED" in latest.values():
        return "approved"
    return "commented" if commented else "none"


def _when(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse(
    ref: PrRef,
    pr: dict[str, Any],
    *,
    check_runs: Any,
    statuses: Any,
    reviews: Any,
    unread: list[str],
    now: datetime,
    source: str,
) -> PullRequestState:
    """The PR's state from its REST answers. Anything that couldn't be read is listed in `unread`, and
    its part stays at its "don't know" value (checks `none`, review `none`), never a clean result."""
    merged = bool(pr.get("merged")) or bool(pr.get("merged_at"))
    state = "merged" if merged else ("closed" if pr.get("state") == "closed" else "open")
    checks, failing = checks_of(check_runs, statuses)
    head = str((pr.get("head") or {}).get("sha") or "")
    mergeable = pr.get("mergeable")
    return PullRequestState(
        repo=ref.slug,
        number=ref.number,
        url=str(pr.get("html_url") or f"https://{ref.host}/{ref.slug}/pull/{ref.number}"),
        state=state,  # type: ignore[arg-type]
        head_sha=head if _SHA.match(head) else "",
        checks=checks,  # type: ignore[arg-type]
        failing=failing,
        review=review_of(reviews),  # type: ignore[arg-type]
        mergeable=mergeable if isinstance(mergeable, bool) else None,
        merged_by=str((pr.get("merged_by") or {}).get("login") or "") or None if merged else None,
        closed_at=_when(pr.get("merged_at") if merged else pr.get("closed_at")),
        unread=sorted(set(unread)),
        checked_at=now,
        source=source,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------- deciding --
@dataclass
class Transition:
    kind: Literal["merged", "abandoned", "updated", "unchanged"]
    lines: list[str] = field(default_factory=list)  # what moved, in a person's words, for the event log


def _short(sha: str) -> str:
    return sha[:7] if sha else "?"


def summary(s: PullRequestState) -> str:
    """One line for the change page and the event log."""
    if s.state == "merged":
        return f"#{s.number} merged" + (f" by {s.merged_by}" if s.merged_by else "")
    if s.state == "closed":
        return f"#{s.number} closed without merging"
    ci = {
        "none": "no CI reported",
        "pending": "CI running",
        "passing": "CI passing",
        "failing": "CI failing" + (f": {', '.join(s.failing[:5])}" if s.failing else ""),
    }[s.checks]
    review = {
        "none": "no review yet",
        "commented": "review comments",
        "approved": "approved",
        "changes_requested": "changes requested",
    }[s.review]
    tail = f" · couldn't read {', '.join(s.unread)}" if s.unread else ""
    return f"#{s.number} open on {_short(s.head_sha)} · {ci} · {review}{tail}"


def transition(prev: PullRequestState | None, now: PullRequestState) -> Transition:
    if now.state == "merged" and (prev is None or prev.state != "merged"):
        return Transition("merged", [summary(now)])
    if now.state == "closed" and (prev is None or prev.state != "closed"):
        return Transition("abandoned", [summary(now)])
    if prev is None:
        return Transition("updated", [summary(now)])
    lines: list[str] = []
    if now.head_sha != prev.head_sha:
        lines.append(f"new commit on the branch: {_short(prev.head_sha)} → {_short(now.head_sha)}")
    if (now.checks, now.failing) != (prev.checks, prev.failing):
        lines.append(summary(now))
    elif now.review != prev.review:
        lines.append(summary(now))
    elif now.unread != prev.unread:
        lines.append(summary(now))
    return Transition("updated", lines) if lines else Transition("unchanged")


# ---------------------------------------------------------------- webhook --
SIGNATURE_HEADER = "x-hub-signature-256"
EVENT_HEADER = "x-github-event"
MAX_BODY = 1_000_000  # GitHub caps payloads at 25 MB; the events we read are small, and a larger body is refused
EVENTS = {
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "pull_request_review_thread",
    "check_suite",
    "check_run",
    "status",
}


def signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub's `X-Hub-Signature-256: sha256=<hex HMAC of the raw body>`, compared in constant time."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.removeprefix("sha256="))


@dataclass(frozen=True)
class Target:
    """What a delivery says to look at again: a PR number, or a head commit (check and status events
    name commits; the watcher matches them to the PRs it follows)."""

    slug: str
    number: int | None = None
    sha: str | None = None


def targets(event: str, payload: Any) -> list[Target]:
    """Which PRs a delivery is about. Only the repo, numbers and commit SHAs are read, and each is
    checked for shape; nothing else in the payload is trusted or kept."""
    if event not in EVENTS or not isinstance(payload, dict):
        return []
    slug = str((payload.get("repository") or {}).get("full_name") or "")
    if not _SLUG.match(slug):
        return []
    out: list[Target] = []

    def add(number: Any = None, sha: Any = None) -> None:
        n = number if isinstance(number, int) and number > 0 else None
        s = sha if isinstance(sha, str) and _SHA.match(sha) else None
        if n or s:
            out.append(Target(slug, n, s))

    if event.startswith("pull_request"):
        add((payload.get("pull_request") or {}).get("number"))
    elif event in {"check_suite", "check_run"}:
        body = payload.get(event) or {}
        suite = body.get("check_suite") or body if event == "check_run" else body
        prs = (suite.get("pull_requests") if isinstance(suite, dict) else None) or body.get("pull_requests") or []
        for pr in prs if isinstance(prs, list) else []:
            add((pr or {}).get("number"))
        add(sha=body.get("head_sha") or (suite or {}).get("head_sha"))
    elif event == "status":
        add(sha=payload.get("sha"))
    return list(dict.fromkeys(out))


def matches(target: Target, followed: PullRequestState | None, ref: PrRef) -> bool:
    if target.slug.lower() != ref.slug.lower():
        return False
    if target.number is not None:
        return target.number == ref.number
    return bool(target.sha and followed and followed.head_sha and followed.head_sha.startswith(target.sha))
