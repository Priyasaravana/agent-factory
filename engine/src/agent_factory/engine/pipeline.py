"""The workflow controller: a deterministic state machine that moves a change through
the configured stations. LLMs work *inside* stations; they never choose the
route. Every transition is persisted, so any run can be resumed or audited.
"""

from __future__ import annotations

import asyncio
import re
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from agent_factory import ledger
from agent_factory.agents.runner import AgentRunner
from agent_factory.config import Blueprint, FactoryConfig
from agent_factory.engine import handoff
from agent_factory.engine.stations import STATIONS, StationContext, StationResult
from agent_factory.engine.workflows import WorkflowRegistry
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import Executor
from agent_factory.gateway import DirectGateway, ModelGateway
from agent_factory.models import (
    AVAILABLE_KINDS,
    AVAILABLE_TARGETS,
    RESUMABLE,
    TARGET_KINDS,
    TERMINAL,
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    CreateProductInput,
    EventKind,
    OnboardRepoInput,
    Product,
    ProductTarget,
    RiskApproval,
    RiskHold,
    StationOutcome,
    WorkItem,
)
from agent_factory.providers import Providers
from agent_factory.secret_refs import SecretResolver
from agent_factory.settings import Settings
from agent_factory.state.base import StateStore
from agent_factory.workflow import target_of

if TYPE_CHECKING:
    from agent_factory.sandbox import SandboxManager


class FactoryError(Exception):
    """A request the factory refuses (surfaced as HTTP 409/404)."""


class InvalidRequestError(FactoryError):
    """A request that can never succeed as sent, e.g. a kind of change or target that is
    not available yet (ADR-0030, HTTP 422)."""


class RefusedError(FactoryError):
    """Work the factory never builds: an acceptable-use rule matched (ADR-0027, HTTP 422)."""


# a change in one of these states has stopped: feedback may start the next iteration
FEEDBACK_OPEN = {ChangeStatus.awaiting_feedback, ChangeStatus.held, ChangeStatus.failed, ChangeStatus.cancelled}


def _now() -> datetime:
    return datetime.now(UTC)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "app")[:40].strip("-")


class ChangeManager:
    def __init__(
        self,
        cfg: FactoryConfig,
        settings: Settings,
        store: StateStore,
        ws: Workspace,
        ex: Executor,
        agents: AgentRunner,
        workflows: WorkflowRegistry,
        providers: Providers | None = None,
        secrets: SecretResolver | None = None,
        sandbox: SandboxManager | None = None,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.cfg, self.settings, self.store = cfg, settings, store
        self.gateway: ModelGateway = gateway or DirectGateway()  # where agents reach the model (ADR-0036)
        self.workflows = workflows
        self.providers = providers or Providers(cfg)
        self.secrets = secrets or SecretResolver(settings)
        self.ws, self.ex, self.agents = ws, ex, agents
        self.sandbox = sandbox  # runs agent-written code (verify) and agent sessions
        from agent_factory.engine.preflight import Preflight

        self.preflight = Preflight(self)  # readiness checks + order gate (ADR-0015)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        from agent_factory.hostclock import SuspendWatcher

        self.host_watcher = SuspendWatcher(self.host_suspended)
        self._sem = asyncio.Semaphore(cfg.factory.max_concurrent_changes)

    # ------------------------------------------------------------ queries --
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def is_active(self, change_id: str) -> bool:
        t = self._tasks.get(change_id)
        return bool(t and not t.done())

    def host_suspended(self, start: datetime, end: datetime) -> None:
        """The factory host slept (hostclock.py): record it, and tell every active run."""
        from agent_factory.hostclock import human

        self.store.add_host_pause(start, end)
        secs = (end - start).total_seconds()
        for change_id, task in list(self._tasks.items()):
            if task.done() or not self.store.get_change(change_id):
                continue  # finished, or not a change (evaluation and seal tasks)
            self.store.add_event(
                change_id,
                EventKind.decision,
                f"factory host was asleep for {human(secs)} (the computer slept or Docker was paused): "
                "nothing ran, and this time is not counted as agent time",
                data={"host_suspended": {"start": start.isoformat(), "end": end.isoformat(), "seconds": secs}},
            )

    async def shutdown(self) -> None:
        """Stop in-flight changes; they are marked interrupted on next startup."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------ products ---
    def screen(self, text: str, what: str) -> None:
        """Refuse abuse apps before anything is built (ADR-0027). Every refusal is
        appended to `audit/refusals.jsonl` with the matched rule; nothing else is stored."""
        import json

        from agent_factory import risk
        from agent_factory.identity import current_identity

        extra = [risk.AupRule(r.id, r.title, r.pattern, r.unless) for r in self.cfg.change_risk.acceptable_use]
        refusal = risk.screen_request(text, extra)
        if refusal is None:
            return
        audit = self.ws.data_dir / "audit"
        audit.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": _now().isoformat(),
            "by": current_identity().user,
            "what": what,
            "rule": refusal.rule,
            "title": refusal.title,
            "matched": refusal.matched,
        }
        with (audit / "refusals.jsonl").open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        raise RefusedError(refusal.message())

    def create_product(self, data: CreateProductInput) -> Product:
        line = self.cfg.blueprints.get(data.blueprint)
        if not line:
            raise FactoryError(f"unknown blueprint '{data.blueprint}'")
        if data.target not in AVAILABLE_TARGETS:
            raise InvalidRequestError(f"target '{data.target}' is not available yet")
        if data.target != ProductTarget.new or line.target != "new":
            raise InvalidRequestError(
                "an existing repository is onboarded with its URL (POST /api/repos), not built from a blueprint"
            )
        self.screen(f"{data.title}\n{data.requirements}", "product")
        slug = self._free_slug(slugify(data.title))
        node_port = host_port = None
        if self.uses_node_ports(data.blueprint):
            free = self.usable_node_ports(data.blueprint)
            if not free:
                raise FactoryError("no free app port: archive a product you no longer need (see config node_ports)")
            node_port = free[0]
            host_port = self.settings.app_host_port_base + line.node_ports.index(node_port)
        product = Product(
            id=uuid.uuid4().hex[:12],
            title=data.title,
            requirements=data.requirements,
            blueprint=data.blueprint,
            slug=slug,
            requirements_format=data.requirements_format,
            target=data.target,
            created_at=_now(),
            node_port=node_port,
            host_port=host_port,
            pack=line.pack_binding.source if line.pack_binding else None,
        )
        return self.store.create_product(product)

    def _free_slug(self, base: str) -> str:
        taken = {o.slug for o in self.store.list_products()}
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        return slug

    def onboard_repo(self, data: OnboardRepoInput) -> Product:
        """An existing repository becomes a product with target `repo` (ADR-0031). Nothing
        is cloned yet: the first change (an assessment) clones it read-only."""
        from agent_factory.assess import RepoUrlError, parse_repo_url

        bp = self.cfg.existing_repos.blueprint
        line = self.cfg.blueprints.get(bp)
        if not line or line.target != "repo":
            raise FactoryError(f"existing_repos.blueprint '{bp}' is not a blueprint with target: repo")
        try:
            ref = parse_repo_url(data.repo_url, self.cfg.existing_repos.allowed_hosts)
        except RepoUrlError as exc:
            raise InvalidRequestError(str(exc)) from exc
        same = [
            p
            for p in self.store.list_products()
            if p.target == ProductTarget.repo
            and not p.archived_at
            and (p.repo_url or "").lower() == ref.web_url.lower()
        ]
        if same:
            raise FactoryError(f"{ref.slug} is already onboarded as '{same[0].title}': assess it again from there")
        title = data.title or ref.slug
        self.screen(f"{title}\n{data.notes or ''}", "repo")
        product = Product(
            id=uuid.uuid4().hex[:12],
            title=title,
            requirements=data.notes or f"Assess {ref.web_url}",
            blueprint=bp,
            target=ProductTarget.repo,
            slug=self._free_slug(slugify(f"{ref.owner}-{ref.name}")),
            created_at=_now(),
            repo_url=ref.web_url,
            repo_ref=data.branch,
        )
        return self.store.create_product(product)

    def free_node_ports(self, blueprint: str) -> list[int]:
        """App ports not held by a live (non-archived) order of this blueprint."""
        line = self.cfg.blueprints[blueprint]
        used = {o.node_port for o in self.store.list_products() if not o.archived_at}
        return [p for p in line.node_ports if p not in used]

    def uses_node_ports(self, blueprint: str) -> bool:
        """Local kind needs one node port per app; an ingress-based target does not (ADR-0026);
        an existing repo deploys nothing (ADR-0031)."""
        if self.cfg.blueprints[blueprint].target == "repo":
            return False
        deploy = self.providers.for_blueprint(blueprint).deploy
        return bool(getattr(deploy, "uses_node_ports", True))

    def usable_node_ports(self, blueprint: str) -> list[int]:
        """Free app ports that the local cluster actually maps (older clusters map fewer)."""
        mapped = self.preflight.mapped_node_ports
        return [p for p in self.free_node_ports(blueprint) if mapped is None or p in mapped]

    async def archive(self, product_id: str, by: str) -> Product:
        """Remove the product's app from its deploy target and free its port. The
        product repo, runs, events and feedback are kept; no new iterations."""
        product = self.store.get_product(product_id)
        if not product:
            raise FactoryError("product not found")
        if product.archived_at:
            return product
        changes = self.store.list_changes(product.id)
        # a stopped run may still be finishing post-run work (retro, evidence seal);
        # that never blocks the next iteration
        if any(self.is_active(r.id) and r.status not in FEEDBACK_OPEN for r in changes):
            raise FactoryError("a change to this product is in progress: cancel it first")
        latest = (
            self.store.get_change(product.latest_change_id)
            if product.latest_change_id
            else (changes[0] if changes else None)
        )
        if latest is not None and product.target == ProductTarget.repo:
            if self.sandbox is not None:
                for r in changes:
                    await self.sandbox.remove_change(r.id)
            self.store.add_event(
                latest.id,
                EventKind.decision,
                f"product archived by {by}: an existing repo, nothing was deployed",
                station="archive",
                data={"archived_by": by},
            )
        elif latest is not None:
            ctx = StationContext(
                self.cfg,
                self.settings,
                self.store,
                self.ws,
                self.ex,
                self.agents,
                product,
                latest,
                self.ws.change_dir(latest.id),
                "archive",
                self.workflows[latest.workflow_id or ""].get(latest.workflow_version),
            )
            ctx.providers = self.providers.for_blueprint(product.blueprint)
            res = await ctx.providers.deploy.undeploy(ctx)
            if not res.ok:
                raise FactoryError(f"archive stopped: {res.summary}\n{res.detail}")
            if self.sandbox is not None:
                for r in changes:
                    await self.sandbox.remove_change(r.id)
            self.store.add_event(
                latest.id,
                EventKind.decision,
                f"product archived by {by}: {res.summary}"
                + (f"; app port {product.host_port} freed" if product.host_port else ""),
                station="archive",
                data={"archived_by": by, "app_url": product.app_url},
            )
        product.archived_at, product.archived_by = _now(), by
        if product.target != ProductTarget.repo:
            product.app_url = None
        self.store.save_product(product)
        return product

    def start_change(
        self,
        product: Product,
        change_request: str | None = None,
        version: int | None = None,
        *,
        workflow_id: str | None = None,
        kind: ChangeKind | None = None,
        source: ChangeSource = ChangeSource.ui,
        requested_by: str | None = None,
        provided: dict[str, dict[str, Any]] | None = None,
    ) -> Change:
        """Start a change to a product (ADR-0030). The kind defaults to `new` for the
        first change and `feature` after it; the requester to the signed-in user. `provided`:
        entry artifacts from the org's own tools, by key (ADR-0039 §5)."""
        from agent_factory.identity import current_identity

        if product.archived_at:
            raise FactoryError("this product is archived: create a new product instead")
        changes = self.store.list_changes(product.id)
        # a stopped run may still be finishing post-run work (retro, evidence seal);
        # that never blocks the next iteration
        if any(self.is_active(r.id) and r.status not in FEEDBACK_OPEN for r in changes):
            raise FactoryError("a change to this product is already in progress")
        first, later = TARGET_KINDS[product.target]
        kind = kind or (later[0] if changes else first)
        if kind not in AVAILABLE_KINDS:
            raise InvalidRequestError(f"'{kind}' changes are not available yet")
        allowed = later if changes else (first,)
        if kind not in allowed:
            raise InvalidRequestError(
                f"a {'later' if changes else 'first'} change to this product can be: {', '.join(allowed)}"
            )
        now = _now()
        wid = workflow_id or self.lane_for(product, kind)
        version = version or self.workflows[wid].active_version()
        flow = self.workflows[wid].get(version)
        works_on = self._target(product.blueprint)
        if not flow.serves(works_on, kind):
            raise InvalidRequestError(f"workflow '{wid}' does not run {kind} changes on {works_on} products")
        plan = flow.plan()
        if not plan.stations:
            raise InvalidRequestError(f"workflow '{wid}' runs no station")
        wanted = {r.key for r in flow.entry_refs()} - handoff.ENGINE_ENTRY
        if unwanted := sorted(set(provided or {}) - wanted):
            raise InvalidRequestError(
                f"this lane doesn't start from {', '.join(unwanted)}"
                + (f" (it takes {', '.join(sorted(wanted))})" if wanted else "")
            )
        if problems := handoff.provided_problems(flow.entry_refs(), dict(provided or {})):
            raise InvalidRequestError("this change can't start yet: " + "; ".join(problems))
        change = Change(
            id=uuid.uuid4().hex[:12],
            product_id=product.id,
            iteration=len(changes) + 1,
            status=ChangeStatus.queued,
            workflow_id=wid,
            workflow_version=version,
            current_station=plan.stations[0],
            change_request=change_request,
            kind=kind,
            source=source,
            requested_by=requested_by or current_identity().user,
            provided=dict(provided or {}),
            created_at=now,
            updated_at=now,
        )
        self.store.create_change(change)
        product.latest_change_id, product.latest_status = change.id, change.status
        self.store.save_product(product)
        self.store.add_event(change.id, EventKind.status, f"change queued (iteration {change.iteration})")
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"work item: {change.kind} from {change.source}, asked by {change.requested_by}",
            data={"work_item": {"kind": change.kind, "source": change.source, "requested_by": change.requested_by}},
        )
        env = self.providers.for_blueprint(product.blueprint)
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"delivery environment: {env.environment} — "
            + ", ".join(f"{cap}: {who}" for cap, who in env.describe().items()),
            data={"environment": env.environment, **env.describe()},
        )
        self._schedule(change.id)
        return change

    # ----------------------------------------------------- human actions ---
    def answer(self, change_id: str, answers: list[str], by: str = "human") -> Change:
        change = self._get(change_id)
        if change.status != ChangeStatus.needs_input:
            raise FactoryError("change is not waiting for answers")
        change.answers = answers
        change.switch_to = []  # answering keeps the stack
        change.status = ChangeStatus.queued
        self.store.save_change(change)
        self.store.add_event(
            change.id, EventKind.decision, f"{by} answered intake questions", data={"answers": answers}
        )
        self._schedule(change.id)
        return change

    async def switch_stack(self, change_id: str, blueprint: str, by: str) -> Change:
        """Answer a stack conflict by building the product on another installed stack (ADR-0041
        §2). Only before the first build: the product's first change, waiting at intake, and its
        repo still only the template. The scaffold is discarded; intake runs again on the new stack."""
        change = self._get(change_id)
        if change.status != ChangeStatus.needs_input or self.is_active(change_id):
            raise FactoryError("change is not waiting for answers")
        if blueprint not in change.switch_to:
            offered = ", ".join(change.switch_to) or "none"
            raise InvalidRequestError(f"'{blueprint}' is not a stack this change may switch to (offered: {offered})")
        product = self.store.get_product(change.product_id)
        if not product:
            raise FactoryError("product not found")
        changes = self.store.list_changes(product.id)
        if len(changes) != 1 or not await self.ws.only_scaffold(product.slug):
            change.switch_to = []
            self.store.save_change(change)
            raise InvalidRequestError("the product has been built already: its stack can no longer switch")
        line = self.cfg.blueprints[blueprint]
        before, before_pack = product.blueprint, product.pack
        node_port, host_port = product.node_port, product.host_port
        if not self.uses_node_ports(blueprint):
            node_port = host_port = None
        elif node_port not in line.node_ports:
            free = self.usable_node_ports(blueprint)
            if not free:
                raise FactoryError("no free app port for that stack: archive a product you no longer need")
            node_port = free[0]
            host_port = self.settings.app_host_port_base + line.node_ports.index(node_port)
        await self.ws.discard_scaffold(product.slug, [change.id])
        product.blueprint, product.node_port, product.host_port = blueprint, node_port, host_port
        product.pack = line.pack_binding.source if line.pack_binding else None
        self.store.save_product(product)
        change.questions, change.answers, change.switch_to = [], [], []
        change.summary = None
        change.status = ChangeStatus.queued
        self.store.save_change(change)
        self._sync_product(change, product)
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"{by} switched the stack from {before} to {blueprint}; intake runs again",
            data={
                "stack_switch": {
                    "from": before,
                    "to": blueprint,
                    "from_pack": before_pack,
                    "pack": product.pack,
                    "by": by,
                }
            },
        )
        if self.cfg.blueprints[before].environment != line.environment:
            env = self.providers.for_blueprint(blueprint)
            self.store.add_event(
                change.id,
                EventKind.decision,
                f"delivery environment: {env.environment} — "
                + ", ".join(f"{cap}: {who}" for cap, who in env.describe().items()),
                data={"environment": env.environment, **env.describe()},
            )
        self._schedule(change.id)
        return change

    def resume(self, change_id: str) -> Change:
        change = self._get(change_id)
        if change.status not in RESUMABLE or self.is_active(change_id):
            raise FactoryError(f"change in status '{change.status}' cannot be resumed")
        change.attempts = {}  # a human resume authorises a fresh budget
        change.loops = 0
        change.status = ChangeStatus.queued
        self.store.save_change(change)
        self.store.add_event(change.id, EventKind.decision, "change resumed by human", station=change.current_station)
        self._schedule(change.id)
        return change

    # ------------------------------------------------------- spec review ---
    def _awaiting_spec(self, change_id: str) -> Change:
        change = self._get(change_id)
        if change.status != ChangeStatus.awaiting_approval:
            raise FactoryError(f"change is '{change.status}', not waiting for spec review")
        return change

    def approve_spec(self, change_id: str, by: str) -> Change:
        change = self._awaiting_spec(change_id)
        change.spec_approved_by = by
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(change.id, EventKind.decision, f"spec approved by {by}", data={"approved_by": by})
        self._schedule(change.id)
        return change

    def request_spec_changes(self, change_id: str, by: str, comment: str) -> Change:
        """Back to intake with the reviewer's notes; design follows, then the gate again."""
        change = self._awaiting_spec(change_id)
        flow = self.workflows[change.workflow_id or ""].get(change.workflow_version)
        plan = flow.plan()
        first = plan.stations[0]
        change.review_notes.append(f"{comment} (by {by})")
        for sid in plan.stations:
            change.attempts.pop(sid, None)
            if flow.station(sid).resolved_handler() == "design":
                break
        change.current_station = first
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(
            change.id, EventKind.decision, f"spec changes requested by {by}: {comment}", data={"requested_by": by}
        )
        self._schedule(change.id)
        return change

    async def edit_spec(self, change_id: str, by: str, product: str | None, technical: str | None) -> Change:
        change = self._awaiting_spec(change_id)
        wt = self.ws.change_dir(change.id)
        changed = []
        for rel, text in (("docs/spec.md", product), ("docs/design.md", technical)):
            if text is not None and text != ((wt / rel).read_text() if (wt / rel).exists() else None):
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                (wt / rel).write_text(text)
                changed.append(rel)
        if changed:
            await self.ws.commit_all(wt, f"docs: spec edited in review by {by}")
            self.store.add_event(
                change.id, EventKind.decision, f"spec edited by {by}: {', '.join(changed)}", data={"files": changed}
            )
        return change

    # ------------------------------------------------------- change risk ---
    def _awaiting_risk(self, change_id: str) -> tuple[Change, RiskHold]:
        change = self._get(change_id)
        if change.status != ChangeStatus.awaiting_risk_approval or change.risk_hold is None:
            raise FactoryError(f"change is '{change.status}', not waiting for a change-risk approval")
        return change, change.risk_hold

    def approve_risk(self, change_id: str, by: str, reason: str, now: datetime | None = None) -> Change:
        """A second admin accepts the risky changes with a reason (ADR-0027). The
        requester may approve their own only as break-glass: allowed by config, after
        the cooling-off delay, and flagged in the evidence."""
        change, hold = self._awaiting_risk(change_id)
        policy = self.cfg.change_risk
        now = now or _now()
        own = bool(hold.requested_by) and by == hold.requested_by
        if own:
            if not policy.break_glass:
                raise FactoryError("you asked for this change, so another admin must approve it (break-glass is off)")
            ready = hold.since + timedelta(minutes=policy.cooling_off_minutes)
            if now < ready:
                wait = int((ready - now).total_seconds() // 60) + 1
                raise FactoryError(
                    f"you asked for this change: another admin can approve it now, or you can approve it "
                    f"yourself as break-glass in {wait} minute(s) (cooling-off {policy.cooling_off_minutes} min)"
                )
        approval = RiskApproval(digest=hold.digest, by=by, reason=reason.strip(), at=now, break_glass=own)
        change.risk_approvals.append(approval)
        change.risk_hold = None
        change.last_failure = None  # the findings were accepted, not handed to a repair station
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"risky changes approved by {by}" + (" (break-glass)" if own else "") + f": {approval.reason}",
            station=hold.station,
            data={"risk_approval": approval.model_dump(mode="json")},
        )
        self._schedule(change.id)
        return change

    def send_back_risk(self, change_id: str, by: str, reason: str) -> Change:
        """The risky changes are not accepted: back to the station's repair route with
        the findings and the reason, or cancelled when there is none."""
        change, hold = self._awaiting_risk(change_id)
        flow = self.workflows[change.workflow_id or ""].get(change.workflow_version)
        target = flow.station(hold.station).on_fail
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"risky changes sent back by {by}: {reason.strip()}",
            station=hold.station,
            data={"risk_sent_back": {"by": by, "reason": reason.strip(), "digest": hold.digest}},
        )
        change.risk_hold = None
        if not target:
            self.store.save_change(change)
            return self.cancel(change.id)
        change.last_failure = (
            f"An admin did not accept these risky changes: {reason.strip()}\n"
            f"Remove them, or make the change without them.\n\n{change.last_failure or ''}"
        ).strip()
        change.current_station = target
        change.status = ChangeStatus.queued
        change.summary = None
        self.store.save_change(change)
        self._sync_product(change)
        self._schedule(change.id)
        return change

    def cancel(self, change_id: str) -> Change:
        change = self._get(change_id)
        task = self._tasks.get(change_id)
        if task and not task.done():
            task.cancel()
        if self.sandbox is not None:
            asyncio.get_running_loop().create_task(self.sandbox.remove_change(change_id))
        change.status = ChangeStatus.cancelled
        self.store.save_change(change)
        self._sync_product(change)
        self.store.add_event(change.id, EventKind.status, "change cancelled by human")
        self._tasks[change_id] = asyncio.get_running_loop().create_task(
            self._seal_after_cancel(change_id, task), name=f"seal-{change_id}"
        )
        return change

    def feedback(
        self,
        product_id: str,
        text: str,
        kind: ChangeKind = ChangeKind.feature,
        items: list[str] | None = None,
        provided: dict[str, dict[str, Any]] | None = None,
    ) -> Change:
        """A change to a delivered product (ADR-0030): a feature, a bug fix or upkeep; an
        existing repo is assessed again (ADR-0031). `items` are work items from the product's
        ledger the change addresses (ADR-0040): spelled out in the request, then in progress."""
        product = self.store.get_product(product_id)
        if not product:
            raise FactoryError("product not found")
        later = TARGET_KINDS[product.target][1]
        if kind not in later:
            raise InvalidRequestError(f"this product's next change can be: {', '.join(later)} (not '{kind}')")
        latest = self.store.get_change(product.latest_change_id) if product.latest_change_id else None
        if latest and latest.status not in FEEDBACK_OPEN:
            raise FactoryError(f"latest change is '{latest.status}'; feedback opens after delivery")
        self.screen(text, f"feedback on {product_id}")
        chosen: list[WorkItem] = []
        if items:
            if kind == ChangeKind.assess:
                raise InvalidRequestError("an assessment doesn't address work items; it verifies them")
            ledger_items = self.store.list_work_items(product_id)
            try:  # checked on copies first: nothing starts if an item can't be claimed
                ledger.claim(
                    [i.model_copy(deep=True) for i in ledger_items],
                    items,
                    change_id="-",
                    iteration=0,
                    now=datetime.now(UTC),
                    by=None,
                )
            except ValueError as exc:
                raise InvalidRequestError(str(exc)) from exc
            chosen = [i for i in ledger_items if i.id in set(items)]
            text = ledger.request_text(text, chosen)
        self.store.add_feedback(product_id, latest.id if latest else None, text)
        change = self.start_change(product, change_request=text, kind=kind, provided=provided)
        self.store.add_event(change.id, EventKind.feedback, text, data={"kind": kind, "work_items": list(items or [])})
        if chosen:
            claimed = ledger.claim(
                chosen,
                items or [],
                change_id=change.id,
                iteration=change.iteration,
                now=datetime.now(UTC),
                by=change.requested_by,
            )
            self.store.save_work_items(claimed)
            change = self._get(change.id)
            change.work_items = [i.id for i in claimed]
            self.store.save_change(change)
        return change

    def recover_on_startup(self) -> list[str]:
        """Builder `factory-recover` rule: never silently restart; mark and let a
        human resume, unless the recover policy is explicitly auto."""
        touched = []
        for change in self.store.changes_with_status({ChangeStatus.running, ChangeStatus.queued}):
            change.status = ChangeStatus.interrupted
            self.store.save_change(change)
            self._sync_product(change)
            self.store.add_event(
                change.id, EventKind.status, "engine restarted: change interrupted", station=change.current_station
            )
            touched.append(change.id)
            if self.cfg.policies.recover.mode == "auto":
                self.resume(change.id)
        for change in self.store.changes_with_status({ChangeStatus.paused_limits}):
            self._wake_later(change)
        return touched

    # -------------------------------------------------------- internals ----
    def _get(self, change_id: str) -> Change:
        change = self.store.get_change(change_id)
        if not change:
            raise FactoryError("change not found")
        return change

    def _schedule(self, change_id: str) -> None:
        self._tasks[change_id] = asyncio.create_task(self._execute(change_id), name=f"run-{change_id}")

    def _sync_product(self, change: Change, product: Product | None = None) -> None:
        product = product or self.store.get_product(change.product_id)
        if product and product.latest_change_id == change.id:
            product.latest_status = change.status
            self.store.save_product(product)

    def _wake_later(self, change: Change) -> None:
        async def wake() -> None:
            delay = max(5.0, ((change.resume_at or _now()) - _now()).total_seconds())
            await asyncio.sleep(delay)
            current = self.store.get_change(change.id)
            if current and current.status == ChangeStatus.paused_limits:
                current.status = ChangeStatus.queued
                self.store.save_change(current)
                self.store.add_event(change.id, EventKind.status, "usage window reset: resuming")
                self._schedule(change.id)

        asyncio.create_task(wake())

    async def _execute(self, change_id: str) -> None:
        cancelled = False
        async with self._sem:
            change = self._get(change_id)
            product = self.store.get_product(change.product_id)
            assert product is not None
            try:
                await self._drive(change, product)
            except asyncio.CancelledError:
                cancelled = True  # cancel() already persisted the cancelled status
                raise
            except Exception as exc:  # noqa: BLE001 - engine faults become a visible failure
                change.status = ChangeStatus.failed
                change.summary = f"engine error: {exc}"
                self.store.add_event(
                    change.id, EventKind.status, change.summary, data={"traceback": traceback.format_exc()[-4000:]}
                )
            finally:
                if not cancelled:
                    self.store.save_change(change)
                    self.store.save_product(product)
                    self._sync_product(change, product)
        # after the slot is released: learning never delays or fails a delivery (ADR-0021)
        if not cancelled and change.status == ChangeStatus.awaiting_feedback:
            await self._retro(change, product)
        if not cancelled:
            await self._seal(change, product)
            self._after_stop(change, product)

    def _after_stop(self, change: Change, product: Product) -> None:
        """The product's work ledger (ADR-0040) and evaluation runs (ADR-0025) learn how the
        change ended; neither ever fails the change."""
        try:
            self._update_ledger(self._get(change.id), product)
        except Exception as exc:  # noqa: BLE001
            self.store.add_event(change.id, EventKind.log, f"work ledger update failed: {type(exc).__name__}: {exc}")
        if not product.eval_run_id:
            return
        from agent_factory.evals import on_change_stopped

        try:
            on_change_stopped(self, self._get(change.id), self.store.get_product(product.id) or product)
        except Exception as exc:  # noqa: BLE001
            self.store.add_event(change.id, EventKind.log, f"evaluation update failed: {type(exc).__name__}: {exc}")

    def _update_ledger(self, change: Change, product: Product) -> None:
        """A delivered assessment is folded into the ledger; a change that addressed items
        leaves them in progress (delivered) or returns them to open (failed, cancelled)."""
        if change.status not in TERMINAL or product.eval_run_id:
            return
        now = datetime.now(UTC)
        items = self.store.list_work_items(product.id)
        if change.kind == ChangeKind.assess and change.status == ChangeStatus.awaiting_feedback:
            import json

            file = self.ws.data_dir / "artifacts" / change.id / "assessment.json"
            if not file.is_file():
                return
            delivered = [
                c.id
                for c in self.store.list_changes(product.id)
                if c.id != change.id
                and c.status == ChangeStatus.awaiting_feedback
                and c.kind != ChangeKind.assess
                and c.created_at < change.created_at
            ]
            before = {i.id: i.status for i in items}
            items = ledger.reconcile(
                product.id,
                items,
                json.loads(file.read_text()),
                change_id=change.id,
                iteration=change.iteration,
                now=now,
                delivered=delivered,
            )
            self.store.save_work_items(items)
            new = sum(1 for i in items if i.id not in before)
            fixed = sum(1 for i in items if i.status == "fixed" and before.get(i.id) not in (None, "fixed"))
            back = sum(1 for i in items if i.status == "open" and before.get(i.id) in ("fixed", "in_progress"))
            self.store.add_event(
                change.id,
                EventKind.decision,
                f"work ledger: {new} new, {fixed} fixed, {back} still present or back, "
                f"{sum(1 for i in items if i.status in ('open', 'in_progress'))} open in all",
                data={"work_ledger": {"new": new, "fixed": fixed, "reopened": back, **ledger.counts(items)}},
            )
        elif change.work_items:
            changed = ledger.release(
                items, change_id=change.id, status=str(change.status), now=now, pr_url=change.pr_url
            )
            self.store.save_work_items(changed)

    async def _seal(self, change: Change, product: Product) -> None:
        """Seal the change's evidence at every stop (ADR-0023); never fails the change."""
        from agent_factory.evidence import seal

        try:
            await seal(self, change, product)
        except Exception as exc:  # noqa: BLE001 - a failed seal is visible, never raised into the change
            self.store.add_event(change.id, EventKind.log, f"evidence seal failed: {type(exc).__name__}: {exc}")

    async def _seal_after_cancel(self, change_id: str, task: asyncio.Task[None] | None) -> None:
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)  # let the station stop first
        change = self.store.get_change(change_id)
        product = self.store.get_product(change.product_id) if change else None
        if change and product:
            await self._seal(change, product)
            self._after_stop(change, product)

    async def _retro(self, change: Change, product: Product) -> None:
        from agent_factory.retro import run_retro

        cost = change.cost_usd
        try:
            await run_retro(self, change, product)
        except Exception as exc:  # noqa: BLE001 - a failed retro is logged, never raised into the change
            self.store.add_event(change.id, EventKind.log, f"retro failed: {type(exc).__name__}: {exc}")
        if change.cost_usd != cost:
            self.store.save_change(change)  # the retro's model spend belongs to this change

    def _record_pack(self, change: Change, blueprint: Blueprint) -> None:
        """Which stack pack this change builds with, once per change (ADR-0041 §7)."""
        pack = blueprint.pack_binding
        if pack is None:
            return
        recorded = self.store.events_with_key([change.id], "stack_pack")
        if recorded and recorded[-1][2].get("id") == pack.id:
            return  # once per change, and again only after a stack switch (ADR-0041 §2)
        changed = f"; config overrides {', '.join(sorted(pack.overrides))}" if pack.overrides else ""
        self.store.add_event(
            change.id,
            EventKind.decision,
            f"stack pack {pack.source} (sha256 {pack.sha256[:12]}){changed}",
            data={"stack_pack": pack.evidence()},
        )

    async def _drive(self, change: Change, product: Product) -> None:
        change.status = ChangeStatus.running
        self.store.save_change(change)
        self._sync_product(change, product)
        blueprint = self.cfg.blueprints[product.blueprint]
        # pinned: later workflow edits never affect this change
        flow = self.workflows[change.workflow_id or ""].get(change.workflow_version)
        if blueprint.target == "repo":
            if not self.ws.change_dir(change.id).exists():
                from agent_factory.assess import parse_repo_url

                url = parse_repo_url(product.repo_url or "", self.cfg.existing_repos.allowed_hosts).url
                try:
                    branch, base = await self.ws.sync_repo(
                        product.slug, url, product.repo_ref, self.cfg.existing_repos.clone_timeout_seconds
                    )
                except RuntimeError as exc:
                    first = change.current_station or flow.forward_stations()[0].id
                    return self._hold(change, first, "the repository could not be read", str(exc))
                if product.repo_ref != branch:
                    product.repo_ref = branch
                    self.store.save_product(product)
                self.store.add_event(
                    change.id,
                    EventKind.decision,
                    f"cloned {product.repo_url} ({branch}) read-only",
                    data={"repo": {"url": product.repo_url, "branch": branch}},
                )
            worktree = await self.ws.create_worktree(product.slug, change.id, f"origin/{product.repo_ref}")
        else:
            owner = str(getattr(self.cfg.policies.publish, "owner", "") or "")
            await self.ws.ensure_product_repo(product.slug, blueprint.template or "", owner)
            worktree = await self.ws.create_worktree(product.slug, change.id)
            self._record_pack(change, blueprint)
        deadline = change.created_at + timedelta(minutes=self.cfg.budgets.change_wall_clock_minutes)

        plan = flow.plan()
        if change.current_station:  # the plan and entry artifacts, once per change (ADR-0037 §2, §4)
            entry_ctx = StationContext(
                self.cfg,
                self.settings,
                self.store,
                self.ws,
                self.ex,
                self.agents,
                product,
                change,
                worktree,
                change.current_station,
                flow,
            )
            entry_ctx.providers = self.providers.for_blueprint(product.blueprint)
            if not handoff.ledger_for(entry_ctx).has_entry():
                handoff.write_plan(entry_ctx, plan)
                skipped = ", ".join(f"{sid} ({why})" for sid, why in plan.skipped)
                self.store.add_event(
                    change.id,
                    EventKind.decision,
                    f"lane {change.workflow_id}: {len(plan.stations)} stations"
                    + (f"; skipped: {skipped}" if skipped else ""),
                    data={"plan": plan.model_dump()},
                )
            if rejected := await handoff.record_entry(entry_ctx):
                return self._hold(change, change.current_station, "entry artifact rejected", "\n".join(rejected))

        while change.current_station:
            sid = change.current_station
            station = flow.station(sid)
            if _now() > deadline and change.status != ChangeStatus.needs_input:
                return self._hold(change, sid, "wall-clock budget exhausted")
            change.attempts[sid] = change.attempts.get(sid, 0) + 1
            if change.attempts[sid] > self.cfg.budgets.max_attempts_per_station:
                return self._hold(change, sid, f"station '{sid}' exceeded its attempt budget")
            self.store.save_change(change)
            self.store.add_event(
                change.id, EventKind.station_started, f"{sid} started (attempt {change.attempts[sid]})", station=sid
            )

            ctx = StationContext(
                self.cfg, self.settings, self.store, self.ws, self.ex, self.agents, product, change, worktree, sid, flow
            )
            ctx.providers = self.providers.for_blueprint(product.blueprint)
            if self.sandbox is not None:
                ctx.untrusted_ex = self.sandbox.executor(change.id, sid, ctx.blueprint.pack_egress)
            ctx.secrets = self.secrets
            ctx.grant = flow.grant(sid, self.workflows.phases)  # the capability boundary (ADR-0039 §4)
            if self.workflows.library:
                ctx.skill_pins = self.workflows.library.effective_pins(flow)
                ctx.imported_plugin = self.workflows.library.materialise(ctx.skill_pins)
            if missing := handoff.missing_needs(ctx):  # the station contract's inputs (ADR-0037)
                change.attempts[sid] -= 1
                return self._hold(change, sid, f"missing input artifact(s): {', '.join(missing)}")
            try:
                handler = station.handler_for(plan.target) or station.resolved_handler()
                result = await STATIONS[handler](ctx)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                result = StationResult(StationOutcome.failed, f"station crashed: {exc}", traceback.format_exc()[-3000:])
            if result.outcome == StationOutcome.passed and (bad := await handoff.record_gives(ctx, result.summary)):
                # an output that fails its schema fails its station (ADR-0037 §2)
                result = StationResult(
                    StationOutcome.failed,
                    f"{sid} gave an invalid artifact",
                    "Artifact check failed:\n" + "\n".join(bad),
                )
            self.store.add_event(
                change.id,
                EventKind.station_finished,
                f"{sid}: {result.outcome} — {result.summary}",
                station=sid,
                data={"outcome": result.outcome},
            )

            if result.outcome == StationOutcome.passed:
                if sid in flow.repair_targets() or station.only_on_fail:
                    change.last_failure = None  # the station that consumed the evidence succeeded
                nxt = flow.next_in(plan, sid)
                change.current_station = nxt
                if nxt is None:
                    change.status = ChangeStatus.awaiting_feedback
                    change.summary = result.summary
                    self.store.add_event(change.id, EventKind.status, "delivered — feedback gate open", station=sid)
                elif (
                    station.resolved_handler() == "design"
                    and flow.spec_gate_applies(change.iteration)
                    and not change.spec_approved_by
                ):
                    change.status = ChangeStatus.awaiting_approval
                    change.summary = "specification ready for your review"
                    self.store.save_change(change)
                    self.store.add_event(
                        change.id,
                        EventKind.status,
                        "spec review gate: waiting for a person to approve the spec before build",
                        station=sid,
                    )
                    return None
                self.store.save_change(change)
                continue

            if result.outcome == StationOutcome.failed:
                change.loops += 1
                target = station.on_fail
                if not target:
                    return self._hold(change, sid, f"{result.summary} (no repair route)", result.failure_evidence)
                if not plan.runs(target):
                    return self._hold(
                        change,
                        sid,
                        f"{result.summary} (its repair route '{target}' is not in this lane)",
                        result.failure_evidence,
                    )
                if change.loops > self.cfg.budgets.max_loops_per_change:
                    return self._hold(change, sid, "fix-loop budget exhausted", result.failure_evidence)
                change.last_failure = result.failure_evidence or result.summary
                change.current_station = target
                self.store.add_event(
                    change.id,
                    EventKind.decision,
                    f"routing failure from {sid} to {target}",
                    station=sid,
                    data={"routed": {"from": sid, "to": target, "evidence": change.last_failure[-3000:]}},
                )
                self.store.save_change(change)
                continue

            if result.outcome == StationOutcome.needs_input:
                change.status = ChangeStatus.needs_input
                change.questions = result.questions
                change.switch_to = result.switch_to
                change.answers = []
                change.attempts[sid] -= 1
                change.summary = "waiting for your answers"
                return None

            if result.outcome == StationOutcome.needs_approval:
                change.attempts[sid] -= 1  # waiting for a person is not an attempt
                change.status = ChangeStatus.awaiting_risk_approval
                product_now = self.store.get_product(change.product_id) or product
                change.risk_hold = RiskHold(
                    digest=result.approval_digest or "",
                    station=sid,
                    since=_now(),
                    findings=result.approval_findings,
                    requested_by=product_now.created_by,
                )
                change.last_failure = result.failure_evidence
                change.summary = result.summary
                self.store.save_change(change)
                self.store.add_event(
                    change.id, EventKind.status, "change risk: waiting for an admin to approve", station=sid
                )
                return None

            if result.outcome == StationOutcome.paused_limits:
                change.attempts[sid] -= 1
                change.status = ChangeStatus.paused_limits
                change.resume_at = (
                    datetime.fromtimestamp(result.resets_at, UTC)
                    if result.resets_at
                    else _now() + timedelta(minutes=30)
                )
                change.summary = f"paused for usage limits until {change.resume_at:%H:%M}"
                self.store.add_event(change.id, EventKind.status, change.summary, station=sid)
                self.store.save_change(change)
                self._wake_later(change)
                return None

            return self._hold(change, sid, result.summary, result.failure_evidence)
        return None

    def lane_for(self, product: Product, kind: str) -> str:
        """The workflow a change runs (ADR-0039): the org workflow assigned to the product for this
        kind, unless the org uses factory defaults; else the factory default lane for the kind of
        work. Requesters never choose."""
        works_on = self._target(product.blueprint)
        assigned = product.workflows.get(str(kind))
        if assigned and not self.cfg.use_factory_defaults and assigned in self.workflows.ids():
            return assigned
        lane = self.workflows.default_lane(works_on, str(kind))
        if lane is None:
            raise InvalidRequestError(f"no factory lane runs {kind} changes on {works_on} products")
        return lane

    def _target(self, blueprint: str) -> str:
        return target_of(self.cfg.blueprints[blueprint].target) if blueprint in self.cfg.blueprints else "factory"

    def _hold(self, change: Change, sid: str, why: str, evidence: str | None = None) -> None:
        change.status = ChangeStatus.held
        change.summary = f"held at {sid}: {why}"
        change.last_failure = evidence or change.last_failure
        self.store.add_event(
            change.id, EventKind.status, change.summary, station=sid, data={"evidence": (evidence or "")[-3000:]}
        )
        return None
