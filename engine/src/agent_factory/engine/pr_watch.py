"""Follows the pull requests that changes opened on team repos (J3). The rules are in `pr_loop.py`
(pure); this module fetches the PR from the host's REST API and applies what it means.

When it looks (`existing_repos.pr_updates`):
- **poll**: every `pr_poll_minutes`, every followed PR;
- **webhook**: when GitHub calls POST /api/hooks/github (signature checked first), only the PRs the
  delivery names, plus a backstop poll every `pr_webhook_backstop_minutes` (0 = none);
- **refresh**: whenever a person presses Refresh on the change, in either mode.

A followed PR is one a delivered change opened (`Change.pr_url`) that no one has given an outcome yet.
The write token (`existing_repos.write_token_ref`) is resolved for each read and sent only in the
`Authorization` header; it is never logged or stored.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from agent_factory import ledger, pr_loop
from agent_factory.gateway import Http, urllib_http
from agent_factory.models import Change, ChangeStatus, EventKind, Outcome, PullRequestState
from agent_factory.secret_refs import SecretError

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import ChangeManager

log = logging.getLogger(__name__)


class PrReadError(Exception):
    """The PR itself couldn't be read (no token, the host refused or is unreachable)."""


class PrWatcher:
    def __init__(self, m: ChangeManager, http: Http | None = None) -> None:
        self.m = m
        self.http: Http = http or urllib_http
        self._loop: asyncio.Task[None] | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self.background: set[asyncio.Task[list[str]]] = set()  # webhook reads still running

    # ------------------------------------------------------------ which --
    def followed(self) -> list[Change]:
        out = []
        for product in self.m.store.list_products():
            if product.eval_run_id:
                continue
            for c in self.m.store.list_changes(product.id):
                if self.is_followed(c):
                    out.append(c)
        return out

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
        return n

    # ---------------------------------------------------------- webhook --
    def webhook_secret(self) -> str | None:
        cfg = self.m.cfg.existing_repos
        if cfg.pr_updates != "webhook" or not cfg.pr_webhook_secret_ref:
            return None
        try:
            return self.m.secrets.resolve(cfg.pr_webhook_secret_ref)
        except SecretError:
            return None

    def schedule_webhook(self, event: str, payload: Any) -> asyncio.Task[list[str]]:
        """Read the named PRs after answering the delivery (the host waits 10 s at most)."""
        task = asyncio.get_running_loop().create_task(self.on_webhook(event, payload), name="pr-webhook")
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def on_webhook(self, event: str, payload: Any) -> list[str]:
        """Look again at the followed PRs a (verified) delivery names. Returns the change ids refreshed."""
        wanted = pr_loop.targets(event, payload)
        if not wanted:
            return []
        done: list[str] = []
        for c in self.followed():
            ref = pr_loop.parse_pr_url(c.pr_url)
            if ref and any(pr_loop.matches(t, c.pull_request, ref) for t in wanted):
                try:
                    await self.refresh(c.id, "webhook")
                    done.append(c.id)
                except PrReadError as exc:
                    log.warning("pull request of change %s not read after a webhook: %s", c.id, exc)
        return done

    # ------------------------------------------------------------- read --
    async def refresh(self, change_id: str, source: str = "refresh") -> Change:
        lock = self._locks.setdefault(change_id, asyncio.Lock())
        async with lock:
            change = self.m.store.get_change(change_id)
            if change is None:
                raise PrReadError(f"change {change_id} not found")
            if not self.is_followed(change):
                return change  # nothing to follow any more (merged, closed, or a person's outcome)
            ref = pr_loop.parse_pr_url(change.pr_url)
            if ref is None:
                raise PrReadError(f"not a pull request address: {change.pr_url}")
            state = await self._read(ref, source)
            return await self._apply(change, state)

    async def _get(self, url: str, token: str) -> tuple[int, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-software-factory",
        }
        try:
            return await self.http("GET", url, headers, None)
        except OSError as exc:  # unreachable, TLS, timeout
            return 0, f"{type(exc).__name__}"

    async def _read(self, ref: pr_loop.PrRef, source: str) -> PullRequestState:
        cfg = self.m.cfg.existing_repos
        if ref.host not in cfg.allowed_hosts:
            raise PrReadError(f"{ref.host} is not an allowed host")
        try:
            token = self.m.secrets.resolve(cfg.write_token_ref or "")
        except SecretError as exc:
            raise PrReadError(f"no token to read pull requests: set {cfg.write_token_ref} ({exc})") from exc
        api = cfg.api_url.rstrip("/") + f"/repos/{quote(ref.slug, safe='/')}"
        status, pr = await self._get(f"{api}/pulls/{ref.number}", token)
        if status != 200 or not isinstance(pr, dict):
            raise PrReadError(f"the host answered {status or 'nothing'} for {ref.slug}#{ref.number}")
        sha = str((pr.get("head") or {}).get("sha") or "")
        unread: list[str] = []
        runs: Any = None
        statuses: Any = None
        if sha:
            code, runs = await self._get(f"{api}/commits/{sha}/check-runs?per_page=100", token)
            if code != 200:
                unread.append(f"check runs ({code or 'unreachable'})")
                runs = None
            code, statuses = await self._get(f"{api}/commits/{sha}/status?per_page=100", token)
            if code != 200:
                unread.append(f"commit statuses ({code or 'unreachable'})")
                statuses = None
        code, reviews = await self._get(f"{api}/pulls/{ref.number}/reviews?per_page=100", token)
        if code != 200:
            unread.append(f"reviews ({code or 'unreachable'})")
            reviews = None
        return pr_loop.parse(
            ref,
            pr,
            check_runs=runs,
            statuses=statuses,
            reviews=reviews,
            unread=unread,
            now=datetime.now(UTC),
            source=source,
        )

    # ------------------------------------------------------------ apply --
    async def _apply(self, change: Change, state: PullRequestState) -> Change:
        t = pr_loop.transition(change.pull_request, state)
        change.pull_request = state
        now = datetime.now(UTC)
        if t.kind in ("merged", "abandoned"):
            outcome = "merged" if t.kind == "merged" else "abandoned"
            change.outcome = Outcome(
                state=outcome,  # type: ignore[arg-type]
                by=state.merged_by or "the repo's team",
                at=state.closed_at or now,
                note=pr_loop.summary(state),
                status_before=str(change.status),
            )
        self.m.store.save_change(change)
        for line in t.lines:
            self.m.store.add_event(
                change.id,
                EventKind.decision if t.kind in ("merged", "abandoned") else EventKind.status,
                f"pull request {line}",
                data={"pull_request": state.model_dump(mode="json")},
            )
        if t.kind in ("merged", "abandoned"):
            items = self.m.store.list_work_items(change.product_id)
            pr_url = state.url
            changed = (
                ledger.pr_merged(items, change_id=change.id, now=now, pr_url=pr_url, by=state.merged_by)
                if t.kind == "merged"
                else ledger.pr_abandoned(items, change_id=change.id, now=now, pr_url=pr_url)
            )
            if changed:
                self.m.store.save_work_items(changed)
            await self.m._reseal(change)  # the outcome goes into the sealed evidence, as accept/close do
        return change
