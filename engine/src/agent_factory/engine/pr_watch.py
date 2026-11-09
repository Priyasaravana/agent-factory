"""Follows the pull requests that changes opened on team repos (J3). The rules are in `pr_loop.py`
(pure); this module fetches the PR from the host's REST API and applies what it means.

When it looks (`existing_repos.pr_updates`):
- **poll**: every `pr_poll_minutes`, every followed PR;
- **webhook**: when GitHub calls POST /api/hooks/github (signature checked first), only the PRs the
  delivery names, plus a backstop poll every `pr_webhook_backstop_minutes` (0 = none);
- **refresh**: whenever a person presses Refresh on the change, in either mode.

A followed PR is one a delivered change opened (`Change.pr_url`) that no one has given an outcome yet.
Lists (check runs, statuses, reviews) are read page by page; a source not read whole is said
(`unread`) and never supports a pass. A merged or closed PR's outcome is saved with the steps that
follow it (`Change.pr_finalise`), resumed by the next refresh, poll or restart until all are done.
Each PR is read with its product's repository source (ADR-0047): that source's API and token, and
only while the source still covers the repo. The token is resolved for each read and sent only in the
`Authorization` header; it is never logged or stored. Each source has its own webhook secret, and a
delivery one source's secret verifies refreshes only the PRs of repos that source covers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from agent_factory import ledger, pr_loop
from agent_factory.models import Change, ChangeStatus, EventKind, Outcome, PullRequestState
from agent_factory.repo_sources import RepoSource, SourceError

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import ChangeManager

log = logging.getLogger(__name__)


# GET url with headers → (status, JSON body, Link header)
PageHttp = Callable[[str, dict[str, str]], Awaitable[tuple[int, Any, str | None]]]


async def urllib_get(url: str, headers: dict[str, str]) -> tuple[int, Any, str | None]:
    def call() -> tuple[int, Any, str | None]:
        req = urllib.request.Request(url, method="GET")  # noqa: S310 - the configured https API
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                raw, status, link = resp.read().decode(), resp.status, resp.headers.get("Link")
        except urllib.error.HTTPError as exc:
            raw, status, link = exc.read().decode(errors="replace"), exc.code, None
        try:
            return status, json.loads(raw) if raw else None, link
        except ValueError:
            return status, raw, link

    return await asyncio.to_thread(call)


class PrReadError(Exception):
    """The PR itself couldn't be read (no token, the host refused or is unreachable)."""


class PrWatcher:
    def __init__(self, m: ChangeManager, http: PageHttp | None = None) -> None:
        self.m = m
        self.http: PageHttp = http or urllib_get
        self._loop: asyncio.Task[None] | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self.background: set[asyncio.Task[list[str]]] = set()  # webhook reads still running

    # ------------------------------------------------------------ which --
    def _changes(self) -> list[Change]:
        return [
            c
            for product in self.m.store.list_products()
            if not product.eval_run_id
            for c in self.m.store.list_changes(product.id)
        ]

    def followed(self) -> list[Change]:
        """Open PRs to read, and merged or closed ones whose follow-up is unfinished."""
        return [c for c in self._changes() if self.is_followed(c) or c.pr_finalise]

    def finalising(self) -> list[Change]:
        return [c for c in self._changes() if c.pr_finalise]

    @staticmethod
    def is_followed(c: Change) -> bool:
        return (
            bool(c.pr_url)
            and c.status == ChangeStatus.awaiting_feedback
            and c.outcome is None
            and (c.pull_request is None or c.pull_request.state == "open")
        )

    # ------------------------------------------------------------- loop --
    def interval_minutes(self) -> int:
        cfg = self.m.cfg.existing_repos
        return cfg.pr_poll_minutes if cfg.pr_updates == "poll" else cfg.pr_webhook_backstop_minutes

    def start(self) -> None:
        if self.m.settings.factory_mode == "dry-run" or self.interval_minutes() == 0:
            return  # dry-run PRs are simulated; webhook mode with no backstop never polls
        if self._loop is None or self._loop.done():
            self._loop = asyncio.create_task(self._periodic(), name="pr-watch")

    async def stop(self) -> None:
        for task in [self._loop, *self.background]:
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in [self._loop, *self.background] if t), return_exceptions=True)

    async def _periodic(self) -> None:
        while True:
            await asyncio.sleep(self.interval_minutes() * 60)
            try:
                await self.refresh_all("poll")
            except Exception:  # noqa: BLE001 - keep following
                log.exception("pull request poll crashed")

    async def refresh_all(self, source: str = "poll") -> int:
        n = 0
        for c in self.followed():
            try:
                await self.refresh(c.id, source)
                n += 1
            except PrReadError as exc:
                log.warning("pull request of change %s not read: %s", c.id, exc)
            except Exception:  # noqa: BLE001 - a finalisation step failed; the next poll resumes it
                log.exception("pull request of change %s: follow-up failed", c.id)
        return n

    # ---------------------------------------------------------- webhook --
    def webhook_secrets(self) -> list[tuple[RepoSource, str]]:
        """Every source's webhook secret that resolves (ADR-0047 §4); none: the webhook is off (404)."""
        if self.m.cfg.existing_repos.pr_updates != "webhook":
            return []
        out: list[tuple[RepoSource, str]] = []
        for src in self.m.sources.all():
            secret = self.m.sources.resolve(src.webhook_secret_ref)
            if secret:
                out.append((src, secret))
        return out

    def webhook_secret(self) -> str | None:
        """The first source's secret (kept for callers that know one source)."""
        found = self.webhook_secrets()
        return found[0][1] if found else None

    def verified_by(self, body: bytes, signature: str | None) -> RepoSource | None:
        """The source whose secret signed this delivery, each compared in constant time; None: nobody's."""
        found = None
        for src, secret in self.webhook_secrets():
            if pr_loop.signature_ok(secret, body, signature) and found is None:
                found = src  # keep comparing: the time taken never says which source matched
        return found

    def schedule_webhook(self, event: str, payload: Any, source: RepoSource | None = None) -> asyncio.Task[list[str]]:
        """Read the named PRs after answering the delivery (the host waits 10 s at most)."""
        task = asyncio.get_running_loop().create_task(self.on_webhook(event, payload, source), name="pr-webhook")
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def on_webhook(self, event: str, payload: Any, source: RepoSource | None = None) -> list[str]:
        """Look again at the followed PRs a (verified) delivery names. With `source` (the one whose secret
        verified it), only PRs of products on that source, on repos it covers. Returns the change ids refreshed."""
        wanted = pr_loop.targets(event, payload)
        if not wanted:
            return []
        done: list[str] = []
        for c in self.followed():
            if c.pr_finalise:
                continue
            ref = pr_loop.parse_pr_url(c.pr_url)
            if ref and source is not None and not self._on_source(c, ref, source):
                continue
            if ref and any(pr_loop.matches(t, c.pull_request, ref) for t in wanted):
                try:
                    await self.refresh(c.id, "webhook")
                    done.append(c.id)
                except PrReadError as exc:
                    log.warning("pull request of change %s not read after a webhook: %s", c.id, exc)
                except Exception:  # noqa: BLE001 - a finalisation step failed; the next poll resumes it
                    log.exception("pull request of change %s: follow-up failed after a webhook", c.id)
        return done

    def _on_source(self, change: Change, ref: pr_loop.PrRef, source: RepoSource) -> bool:
        product = self.m.store.get_product(change.product_id)
        if product is None:
            return False
        if not product.repo_source:  # from before ADR-0047: chosen (and recorded) now
            try:
                self.m.sources.for_product(product, change.id)
            except SourceError:
                return False
        if product.repo_source != source.id:
            return False
        owner = ref.slug.split("/", 1)[0]
        return ref.host == source.host and (source.covers_any_owner() or source.names_owner(owner))

    # ------------------------------------------------------------- read --
    async def refresh(self, change_id: str, source: str = "refresh") -> Change:
        lock = self._locks.setdefault(change_id, asyncio.Lock())
        async with lock:
            change = self.m.store.get_change(change_id)
            if change is None:
                raise PrReadError(f"change {change_id} not found")
            if change.pr_finalise:
                return await self._finalise(change)  # the outcome stands; finish what follows it
            if not self.is_followed(change):
                return change  # nothing to follow any more (merged, closed, or a person's outcome)
            ref = pr_loop.parse_pr_url(change.pr_url)
            if ref is None:
                raise PrReadError(f"not a pull request address: {change.pr_url}")
            src = self._source_for(change, ref)
            state = await self._read(ref, source, src)
            return await self._apply(change, state)

    def _source_for(self, change: Change, ref: pr_loop.PrRef) -> RepoSource:
        """The product's source, still covering its repo, and the PR on that same repo (ADR-0047 §3)."""
        product = self.m.store.get_product(change.product_id)
        if product is None:
            raise PrReadError(f"product of change {change.id} not found")
        try:
            repo, src = self.m.sources.for_product(product, change.id)
        except SourceError as exc:
            raise PrReadError(str(exc)) from exc
        if (ref.host, ref.slug.lower()) != (repo.host, repo.slug.lower()):
            raise PrReadError(f"{ref.host}/{ref.slug}#{ref.number} is not on the product's repo {repo.slug}")
        return src

    async def _get(self, url: str, token: str) -> tuple[int, Any, str | None]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-software-factory",
        }
        try:
            return await self.http(url, headers)
        except OSError as exc:  # unreachable, TLS, timeout
            return 0, f"{type(exc).__name__}", None

    async def _pages(self, url: str, token: str, key: str | None, api: str) -> tuple[list[Any] | None, str | None]:
        """Every page of a list (`key`: the list's field, None: the body is the list), oldest page first.
        Returns the rows and, when not read whole, why (rows read so far are kept: a failure on them
        still counts). Next pages are followed only on the configured API origin, at most MAX_PAGES."""
        rows: list[Any] = []
        total: int | None = None
        for page in range(1, pr_loop.MAX_PAGES + 1):
            code, body, link = await self._get(url, token)
            got = body if key is None else (body.get(key) if isinstance(body, dict) else None)
            if code != 200 or not isinstance(got, list):
                why = f"{code or 'unreachable'}" if page == 1 else f"page {page}: {code or 'unreachable'}"
                return (None if page == 1 else rows), why
            rows.extend(got)
            if isinstance(body, dict) and isinstance(body.get("total_count"), int):
                total = body["total_count"]
            nxt = pr_loop.next_link(link)
            if nxt is None:
                # check runs: the runs on the commit; combined status: its status contexts (one row each).
                # Either way fewer rows than the count, with no next page, is a short read.
                if total is not None and len(rows) < total:
                    return rows, f"{len(rows)} of {total}"
                return rows, None
            if not pr_loop.same_origin(nxt, api):
                return rows, "next page off the API host"
            url = nxt
        return rows, f"more than {pr_loop.MAX_PAGES} pages"

    async def _read(self, ref: pr_loop.PrRef, source: str, src: RepoSource) -> PullRequestState:
        if ref.host not in self.m.cfg.existing_repos.allowed_hosts or ref.host != src.host:
            raise PrReadError(f"{ref.host} is not an allowed host")
        token = self.m.sources.resolve(src.token_ref)
        if not token:
            raise PrReadError(
                f"no token to read pull requests: set {src.token_ref or 'a token reference'} on source '{src.id}'"
            )
        origin = src.api_url.rstrip("/")
        api = origin + f"/repos/{quote(ref.slug, safe='/')}"
        status, pr, _ = await self._get(f"{api}/pulls/{ref.number}", token)
        if status != 200 or not isinstance(pr, dict):
            raise PrReadError(f"the host answered {status or 'nothing'} for {ref.slug}#{ref.number}")
        sha = str((pr.get("head") or {}).get("sha") or "")
        unread: list[str] = []
        incomplete: set[str] = set()

        async def read(name: str, url: str, key: str | None) -> list[Any] | None:
            rows, why = await self._pages(url, token, key, origin)
            if why:
                unread.append(f"{name} ({why})")
                incomplete.add(name)
            return rows

        runs = statuses = None
        if sha:
            runs = await read(pr_loop.RUNS, f"{api}/commits/{sha}/check-runs?filter=latest&per_page=100", "check_runs")
            statuses = await read(pr_loop.STATUSES, f"{api}/commits/{sha}/status?per_page=100", "statuses")
        else:
            incomplete |= {pr_loop.RUNS, pr_loop.STATUSES}
            unread.append("CI (no head commit)")
        reviews = await read(pr_loop.REVIEWS, f"{api}/pulls/{ref.number}/reviews?per_page=100", None)
        return pr_loop.parse(
            ref,
            pr,
            check_runs=runs,
            statuses=statuses,
            reviews=reviews,
            unread=unread,
            incomplete=incomplete,
            now=datetime.now(UTC),
            source=source,
        )

    # ------------------------------------------------------------ apply --
    async def _apply(self, change: Change, state: PullRequestState) -> Change:
        t = pr_loop.transition(change.pull_request, state)
        change.pull_request = state
        if t.kind not in ("merged", "abandoned"):
            self.m.store.save_change(change)
            for line in t.lines:
                self.m.store.add_event(
                    change.id,
                    EventKind.status,
                    f"pull request {line}",
                    data={"pull_request": state.model_dump(mode="json")},
                )
            return change
        # the outcome and what is left to do are saved together, first: from here every step resumes
        change.outcome = Outcome(
            state="merged" if t.kind == "merged" else "abandoned",
            by=state.merged_by or "the repo's team",
            at=state.closed_at or datetime.now(UTC),
            note=pr_loop.summary(state),
            status_before=str(change.status),
        )
        change.pr_finalise = pr_loop.finalising_steps()
        self.m.store.save_change(change)
        return await self._finalise(change)

    async def _finalise(self, change: Change) -> Change:
        """Bring the event log, the work items and the sealed evidence in line with a merged or closed
        PR. Each step is idempotent and is cleared only once it is done, so a failure leaves the rest to
        the next refresh, poll or restart (`resume`)."""
        state, outcome = change.pull_request, change.outcome
        assert state is not None and outcome is not None
        merged = outcome.state == "merged"
        for step in list(change.pr_finalise):
            if step == "event":
                if not any(
                    (e.data or {}).get("pull_request_final") == state.number
                    for e in self.m.store.list_events(change.id)
                ):
                    self.m.store.add_event(
                        change.id,
                        EventKind.decision,
                        f"pull request {pr_loop.summary(state)}",
                        data={"pull_request": state.model_dump(mode="json"), "pull_request_final": state.number},
                    )
            elif step == "items":
                items = self.m.store.list_work_items(change.product_id)
                now = datetime.now(UTC)
                changed = (
                    ledger.pr_merged(items, change_id=change.id, now=now, pr_url=state.url, by=state.merged_by)
                    if merged
                    else ledger.pr_abandoned(items, change_id=change.id, now=now, pr_url=state.url)
                )
                if changed:
                    self.m.store.save_work_items(changed)
            elif step == "seal":
                await self.m._reseal(change)  # the outcome goes into the sealed evidence, as accept/close do
            change.pr_finalise.remove(step)
            self.m.store.save_change(change)
        return change

    async def resume(self) -> int:
        """At startup: finish any merged or closed PR whose follow-up was cut short."""
        n = 0
        for c in self.finalising():
            try:
                await self.refresh(c.id, "poll")
                n += 1
            except Exception:  # noqa: BLE001 - the next refresh or poll tries again
                log.exception("finalising the pull request of change %s failed", c.id)
        return n
