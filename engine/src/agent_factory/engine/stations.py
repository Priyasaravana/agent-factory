"""Station implementations. Agent stations do judgment work; check stations
are deterministic and produce the evidence the workflow trusts.

Contract (every station): return a StationResult. Missing or partial evidence
is FAILED or HELD — never a plausible PASS.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_factory import traceability
from agent_factory.agents.runner import AgentRequest, AgentResult, AgentRunner
from agent_factory.config import FactoryConfig, ProductLine
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import CommandResult, Executor
from agent_factory.models import EventKind, Order, Run, StationOutcome
from agent_factory.observe import AgentCall
from agent_factory.pillars import BY_ID, IDS
from agent_factory.providers import LocalProvider, ProviderSet
from agent_factory.providers.local import failed_detail
from agent_factory.readiness import score
from agent_factory.review import REVIEW_SCHEMA, judge
from agent_factory.secret_refs import SecretError, SecretResolver
from agent_factory.settings import Settings
from agent_factory.state.base import StateStore
from agent_factory.workflow import AgentSpec, WorkflowDoc


@dataclass
class StationResult:
    outcome: StationOutcome
    summary: str
    failure_evidence: str | None = None  # handed to the on_fail station
    questions: list[str] = field(default_factory=list)
    resets_at: int | None = None


def _local_set() -> ProviderSet:
    p = LocalProvider()
    return ProviderSet(environment="local", registry=p, scan=p, deploy=p, publish=p)


@dataclass
class StationContext:
    cfg: FactoryConfig
    settings: Settings
    store: StateStore
    ws: Workspace
    ex: Executor
    agents: AgentRunner
    order: Order
    run: Run
    worktree: Path
    station_id: str
    workflow_doc: WorkflowDoc
    providers: ProviderSet = field(default_factory=lambda: _local_set())
    secrets: SecretResolver = field(default_factory=SecretResolver)
    imported_plugin: Path | None = None  # the version's pinned imported skills, as a local plugin
    skill_pins: dict[str, str] = field(default_factory=dict)  # imported skill -> commit for this run
    # where untrusted commands (the generated app's own build/tests) run: a sandbox
    # executor in live mode; None = the engine's executor (dry-run, tests, dev)
    untrusted_ex: Executor | None = None

    @property
    def product_line(self) -> ProductLine:
        return self.cfg.product_lines[self.order.product_line]

    @property
    def spec(self) -> AgentSpec:
        """The agent spec bound to this station (agent stations only)."""
        return self.workflow_doc.agent_for(self.station_id)

    def secret(self, ref: str, purpose: str) -> str | None:
        """Resolve a secret reference for one step. The value is returned, never
        logged (the store masks it); the audit trail records only the reference."""
        try:
            value = self.secrets.resolve(ref)
        except SecretError as exc:
            self.store.add_event(
                self.run.id, EventKind.log, f"secret {ref} for {purpose} unavailable: {exc}", station=self.station_id
            )
            return None
        self.store.add_event(
            self.run.id,
            EventKind.decision,
            f"used secret {ref} for {purpose}",
            station=self.station_id,
            data={"secret_ref": ref, "purpose": purpose},
        )
        return value

    async def emit(self, kind: EventKind, message: str, data: dict[str, Any] | None = None) -> None:
        self.store.add_event(self.run.id, kind, message, station=self.station_id, data=data)

    async def cmd(
        self,
        command: str,
        timeout: float = 900,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        untrusted: bool = False,
    ) -> CommandResult:
        """Run a command. `untrusted=True` for code the agents wrote (tests, the
        app's Makefile): it runs in the sandbox, never next to engine secrets."""
        ex = (self.untrusted_ex or self.ex) if untrusted else self.ex
        res = await ex.run(command, cwd=cwd or self.worktree, timeout=timeout, env=env)
        await self.emit(
            EventKind.command,
            f"{'✓' if res.ok else '✗'} {command}",
            {"returncode": res.returncode, "output": res.output[-3000:]},
        )
        return res

    async def agent(
        self,
        prompt: str,
        schema: dict[str, Any] | None = None,
        protect_holdout: bool = True,
    ) -> AgentResult:
        spec = self.spec
        protected = [str(self.ws.data_dir / "holdout")] if protect_holdout else []

        async def sink(kind: str, data: dict[str, Any]) -> None:
            if kind == "agent":
                await self.emit(EventKind.agent, data.get("text", ""), {})
            elif kind == "tool":
                await self.emit(EventKind.log, f"{data['tool']}: {data['input']}", data)
            elif kind in ("agent_init", "sandbox"):
                await self.emit(EventKind.log, data["text"], data)
            else:
                await self.emit(EventKind.log, kind, data)

        on_demand = [k for k in spec.skills if k not in spec.preload_skills]
        if spec.preload_skills:
            await self.emit(
                EventKind.log, f"preloaded skills: {spec.preload_skills}", {"preloaded": spec.preload_skills}
            )
        req = AgentRequest(
            run_id=self.run.id,
            station=self.station_id,
            role=spec.id,
            prompt=prompt,
            cwd=self.worktree,
            model=self.cfg.models.resolve(spec.model),
            system_prompt=compose_system_prompt(self, spec),
            tools=spec.effective_tools(),
            observe_only=spec.observe_only,
            produces=spec.produces,
            max_turns=spec.max_turns,
            skills=[k for k in on_demand if k not in self.skill_pins],
            imported_skills=[k for k in on_demand if k in self.skill_pins],
            imported_plugin=self.imported_plugin,
            skill_overlay=self.cfg.skill_overlay(spec.skills),
            output_schema=schema,
            protected_paths=protected,
            subagent_model=self.cfg.models.fast,
        )
        call = AgentCall(self.ws.data_dir / "artifacts" / self.run.id, self.station_id, spec.id, req.model, self._emit)
        res = await self.agents.run(req, call.wrap(sink))
        self.run.cost_usd += res.cost_usd
        await call.finish(res)
        return res

    async def _emit(self, kind: EventKind, message: str, data: dict[str, Any]) -> None:
        await self.emit(kind, message, data)


def compose_system_prompt(ctx: StationContext, spec: AgentSpec) -> str:
    """The spec's prompt plus the context it asked for. Everything here comes from
    the pinned workflow version or this order's own history, so a run stays reproducible."""
    parts = [spec.prompt.strip()]
    docs = [ctx.workflow_doc.docs[d] for d in spec.context_docs if d in ctx.workflow_doc.docs]
    if docs:
        parts.append(
            "## Reference documents (team standards — follow them)\n\n"
            + "\n\n".join(f"### {d.title}\n{d.content.strip()}" for d in docs)
        )
    preloaded = [(name, text) for name in spec.preload_skills if (text := skill_text(ctx, name))]
    if preloaded:
        parts.append(
            "## Skills for this station (follow them; already loaded, no need to invoke)\n\n"
            + "\n\n".join(f"### Skill: {name}\n{text}" for name, text in preloaded)
        )
    if spec.learnings.strip():
        parts.append("## Learnings from earlier runs (human-approved)\n" + spec.learnings.strip())
    if spec.previous_iterations:
        history = iteration_history(ctx, spec.previous_iterations)
        if history:
            parts.append("## Earlier iterations of this product\n" + history)
    return "\n\n".join(parts)


MAX_PRELOAD_CHARS = 15_000


def skill_text(ctx: StationContext, name: str) -> str:
    """SKILL.md body (without frontmatter) of a built-in or pinned imported skill."""
    candidates = [ctx.ws.factory_home / "plugin" / "skills" / name / "SKILL.md"]
    if ctx.imported_plugin:
        candidates.insert(0, ctx.imported_plugin / "skills" / name / "SKILL.md")
    for path in candidates:
        if path.exists():
            body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", path.read_text(), count=1, flags=re.DOTALL)
            return body.strip()[:MAX_PRELOAD_CHARS]
    return ""


def iteration_history(ctx: StationContext, limit: int) -> str:
    """What was asked, what was delivered and which decisions were made in the
    previous iterations of this order (newest first)."""
    earlier = [r for r in ctx.store.list_runs(ctx.order.id) if r.iteration < ctx.run.iteration][:limit]
    blocks = []
    for r in earlier:
        decisions = [e.message for e in ctx.store.list_events(r.id) if e.kind == EventKind.decision][:12]
        lines = [
            f"### Iteration {r.iteration} ({r.status})",
            f"- asked: {r.change_request or 'initial requirements'}",
            f"- outcome: {r.summary or 'n/a'}",
        ]
        lines += [f"- decision: {d}" for d in decisions]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


Station = Callable[[StationContext], Awaitable[StationResult]]


def _limit_result(res: AgentResult) -> StationResult | None:
    if res.rate_limited:
        return StationResult(StationOutcome.paused_limits, "usage limit reached", resets_at=res.limit_resets_at)
    return None


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


def _missing_outputs(ctx: StationContext) -> list[str]:
    """Files the station's agent spec promises to produce but did not."""
    return [f for f in ctx.spec.produces if not (ctx.worktree / f).exists()]


# ------------------------------------------------------------------ intake --
SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "product_name",
        "spec_markdown",
        "requirements",
        "acceptance_scenarios",
        "holdout_scenarios",
        "assumptions",
        "blocking_questions",
    ],
    "properties": {
        "product_name": {"type": "string"},
        "spec_markdown": {"type": "string"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "title", "detail"],
                "properties": {
                    "id": {"type": "string", "pattern": "^R[0-9]+$"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
        "acceptance_scenarios": {"type": "array", "items": {"$ref": "#/$defs/scenario"}},
        "holdout_scenarios": {"type": "array", "items": {"$ref": "#/$defs/scenario"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "blocking_questions": {"type": "array", "items": {"type": "string"}},
    },
    "$defs": {
        "scenario": {
            "type": "object",
            "required": ["id", "given", "when", "then", "covers"],
            "properties": {
                **{k: {"type": "string"} for k in ("id", "given", "when", "then")},
                "covers": {"type": "array", "items": {"type": "string", "pattern": "^R[0-9]+$"}},
            },
        }
    },
}


def _review_notes(ctx: StationContext) -> str:
    notes = ctx.run.review_notes
    if not notes:
        return ""
    return "## Reviewer notes on the spec (address every point)\n" + "\n".join(f"- {n}" for n in notes) + "\n"


def _validate_spec(spec: dict[str, Any]) -> list[str]:
    reqs = spec.get("requirements") or []
    problems = [] if reqs else ["no numbered requirements (R1, R2, …)"]
    problems += traceability.coverage_problems(reqs, spec.get("acceptance_scenarios") or [], "acceptance")
    problems += [
        p
        for p in traceability.coverage_problems(reqs, spec.get("holdout_scenarios") or [], "holdout")
        if "unknown requirement" in p or "duplicate" in p  # hidden coverage may be partial
    ]
    return problems


async def intake(ctx: StationContext) -> StationResult:
    spec_path = ctx.worktree / "docs" / "spec.md"
    old_reqs = traceability.requirements(ctx.worktree)
    qa = "\n".join(f"Q: {q}\nA: {a}" for q, a in zip(ctx.run.questions, ctx.run.answers, strict=False))
    given = (
        "The requirements below are an EXISTING SPECIFICATION written by the customer. Preserve its "
        "structure and wording; map its numbered items 1:1 to requirement ids; only add what is missing "
        "(record each addition as an assumption)."
        if ctx.order.requirements_format == "spec"
        else "Turn these requirements into a build-ready specification."
    )
    current_reqs = yaml.safe_dump(old_reqs, sort_keys=False) if old_reqs else "none"
    prompt = f"""{given}

## Order: {ctx.order.title}
{ctx.order.requirements}

## Change request for this iteration (from human feedback)
{ctx.run.change_request or "none — first iteration"}

{_review_notes(ctx)}
## Answers to your earlier questions
{qa or "none"}

## Current spec (update it, do not start over, when present)
{_read(spec_path) or "none"}

## Current numbered requirements (keep ids stable; new ones get the next number; drop removed ones)
{current_reqs}

Product line: {ctx.order.product_line} — {ctx.product_line.description}.
Write the spec for a product reader (user journeys, behaviour, rules), not implementation.
Number every requirement (R1, R2, …). Every acceptance and holdout scenario lists in `covers`
the requirement ids it checks; every requirement needs at least one acceptance scenario.
Only put a question in blocking_questions if no reasonable assumption exists.
Holdout scenarios must cover behaviour NOT restated in the acceptance scenarios."""
    res = await ctx.agent(prompt, SPEC_SCHEMA)
    if lim := _limit_result(res):
        return lim
    if res.ok and isinstance(res.structured, dict) and (problems := _validate_spec(res.structured)):
        # one in-station correction: the contract must be traceable before anyone builds on it
        await ctx.emit(EventKind.log, "spec incomplete, asking intake to fix it", {"problems": problems})
        res = await ctx.agent(
            prompt + "\n\n## Your previous spec had these problems — fix all of them\n" + "\n".join(problems),
            SPEC_SCHEMA,
        )
        if lim := _limit_result(res):
            return lim
    if not res.ok or not isinstance(res.structured, dict):
        return StationResult(StationOutcome.failed, "intake agent failed", res.error)
    spec = res.structured
    problems = _validate_spec(spec)
    if problems:
        return StationResult(StationOutcome.failed, "spec is not traceable", "\n".join(problems))
    questions = [q for q in spec.get("blocking_questions", []) if q.strip()]
    if questions and not ctx.run.answers:
        return StationResult(StationOutcome.needs_input, "intake needs answers", questions=questions)

    (ctx.worktree / "docs").mkdir(exist_ok=True)
    spec_path.write_text(spec["spec_markdown"])
    reqs = [{"id": r["id"], "title": r["title"], "detail": r.get("detail", "")} for r in spec["requirements"]]
    (ctx.worktree / "docs" / "requirements.yaml").write_text(yaml.safe_dump(reqs, sort_keys=False))
    acc = ctx.worktree / "tests" / "acceptance"
    acc.mkdir(parents=True, exist_ok=True)
    (acc / "scenarios.yaml").write_text(yaml.safe_dump(spec["acceptance_scenarios"], sort_keys=False))
    hold = ctx.ws.holdout_dir(ctx.order.product_slug)
    hold.mkdir(parents=True, exist_ok=True)
    (hold / "scenarios.yaml").write_text(yaml.safe_dump(spec["holdout_scenarios"], sort_keys=False))
    for a in spec.get("assumptions", []):
        await ctx.emit(EventKind.decision, f"assumption: {a}")
    change = traceability.diff(old_reqs, reqs)
    if old_reqs:
        parts = [f"+{', +'.join(change['added'])}" if change["added"] else ""]
        parts += [f"~{', ~'.join(change['changed'])}" if change["changed"] else ""]
        parts += [f"−{', −'.join(change['removed'])}" if change["removed"] else ""]
        summary = " ".join(p for p in parts if p) or "no requirement changes"
        await ctx.emit(EventKind.decision, f"spec updated: {summary}", {"spec_changes": change})
    else:
        await ctx.emit(EventKind.decision, f"spec: {len(reqs)} numbered requirements", {"spec_changes": {}})
    await ctx.ws.commit_all(ctx.worktree, "docs: specification, requirements and acceptance scenarios")
    return StationResult(
        StationOutcome.passed,
        f"spec written; {len(reqs)} requirements, {len(spec['acceptance_scenarios'])} acceptance, "
        f"{len(spec['holdout_scenarios'])} holdout scenarios",
    )


# ------------------------------------------------------------------ design --
async def design(ctx: StationContext) -> StationResult:
    prompt = f"""Design the implementation for the spec in docs/spec.md and the numbered
requirements in docs/requirements.yaml, following the golden path already in this
repo (read AGENTS.md first).

Write:
  docs/design.md   — architecture, data model, key decisions (ADR style); cite the
                     requirement ids (R1, …) each decision serves
  docs/openapi.yaml — the HTTP contract
  docs/tasks.md    — ordered implementation tasks, each with its test and the
                     requirement ids it implements

Change request this iteration: {ctx.run.change_request or "none"}
{_review_notes(ctx)}"""
    res = await ctx.agent(prompt)
    if lim := _limit_result(res):
        return lim
    missing = _missing_outputs(ctx)
    if not res.ok or missing:
        return StationResult(StationOutcome.failed, "design incomplete", res.error or f"missing artifacts: {missing}")
    await ctx.ws.commit_all(ctx.worktree, "docs: design, API contract and task plan")
    return StationResult(StationOutcome.passed, "design, openapi and tasks written")


# ------------------------------------------------------------------- build --
async def build(ctx: StationContext) -> StationResult:
    fix = ctx.run.last_failure
    prompt = f"""Implement docs/tasks.md against docs/design.md and docs/openapi.yaml.
Read AGENTS.md first. Write tests alongside code. `{ctx.product_line.verify_command}` must pass.
Acceptance scenarios to satisfy: tests/acceptance/scenarios.yaml.
Traceability: tag every test with the requirement ids it covers, e.g.
`@pytest.mark.req("R1", "R3")` (ids from docs/requirements.yaml). Every requirement
needs at least one tagged test; the Readiness station checks this.

{"## Fix this failure from a downstream station (evidence only):" + chr(10) + fix if fix else ""}"""
    res = await ctx.agent(prompt)
    if lim := _limit_result(res):
        return lim
    if not res.ok:
        return StationResult(StationOutcome.failed, "developer agent failed", res.error)
    await ctx.ws.commit_all(ctx.worktree, "feat: implementation" + (" (fix)" if fix else ""))
    return StationResult(StationOutcome.passed, "implementation committed")


# ------------------------------------------------------------------ verify --
async def verify(ctx: StationContext) -> StationResult:
    res = await ctx.cmd(ctx.product_line.verify_command, timeout=1200, untrusted=True)
    if not res.ok:
        return StationResult(
            StationOutcome.failed,
            "verification failed",
            f"`{res.command}` exited {res.returncode}:\n{res.output[-4000:]}",
        )
    return StationResult(StationOutcome.passed, "lint, tests, coverage and security checks passed")


# --------------------------------------------------------------- readiness --
async def readiness(ctx: StationContext) -> StationResult:
    """Score the repo against the agent-readiness signals (docs/practices.md) and
    hold it to the product line's level. Deterministic: files plus a secret scan."""
    line = ctx.product_line
    scan_ok: bool | None = None
    scan_evidence = ""
    if line.secret_scan_command:
        res = await ctx.cmd(line.secret_scan_command.format(path=ctx.worktree), timeout=600)
        scan_ok = res.ok
        if not res.ok:
            scan_evidence = f"\nSecret scan (values redacted):\n{res.output[-2500:]}"
    else:
        await ctx.emit(EventKind.decision, "secret scan NOT run: no secret_scan_command configured")
        scan_ok = False
    card = score(ctx.worktree, scan_ok)
    data = card.as_data()
    out = artifacts_dir(ctx)
    out.mkdir(parents=True, exist_ok=True)
    (out / "readiness.json").write_text(json.dumps(data, indent=2))  # sealed evidence (ADR-0023)
    pillars = data["pillars"]
    uncovered = [p["id"] for p in pillars if not p["applicable"]]
    by_pillar = ", ".join(f"{p['id']} {p['passed']}/{p['applicable']}" for p in pillars if p["applicable"])
    await ctx.emit(
        EventKind.decision,
        f"agent readiness: Level {card.level} ({card.points}/{card.max_points} points) · pillars: {by_pillar}"
        + (f" · uncovered: {', '.join(uncovered)}" if uncovered else ""),
        {"readiness": data},
    )
    need = line.min_readiness_level
    if card.level >= need:
        return StationResult(StationOutcome.passed, f"agent-ready: Level {card.level}, {card.points} points")
    gaps = card.missing(need)
    missing = "\n".join(
        f"{BY_ID[pid].title}:\n" + "\n".join(f"- [L{s.level}] {s.title}: {s.hint}" for s in gaps if s.pillar == pid)
        for pid in IDS
        if any(s.pillar == pid for s in gaps)
    )
    return StationResult(
        StationOutcome.failed,
        f"below agent-readiness Level {need} (at Level {card.level})",
        f"The repo must stay at agent-readiness Level {need}. Missing signals:\n{missing}{scan_evidence}",
    )


# ----------------------------------------------------------------- package --
async def package(ctx: StationContext) -> StationResult:
    """Build the image here, then scan and publish it through the environment's providers."""
    p = ctx.providers
    sha = await ctx.ws.head_sha(ctx.worktree)
    local_image = f"{ctx.order.product_slug}:{sha}"
    build = f"docker build -t {local_image} ."
    res = await ctx.cmd(build, timeout=1500)
    if not res.ok:
        return StationResult(
            StationOutcome.failed,
            "packaging failed at: docker build",
            failed_detail(build, res),
        )
    scan = await p.scan.scan(ctx, local_image)
    if not scan.ok:
        return StationResult(StationOutcome.failed, scan.summary, scan.detail)
    sbom = await _sbom_and_provenance(ctx, local_image, sha)
    if sbom is not None and not sbom.ok:
        return StationResult(StationOutcome.failed, "packaging failed at: SBOM", failed_detail(sbom.command, sbom))
    ref = p.registry.image_ref(ctx, sha)
    push = await p.registry.push(ctx, local_image, ref)
    if not push.ok:
        return StationResult(StationOutcome.failed, push.summary, push.detail)
    await ctx.emit(EventKind.decision, f"image built and {push.summary}: {ref}", {"image": str(ref)})
    extra = ", SBOM + provenance recorded" if sbom is not None else ""
    return StationResult(StationOutcome.passed, f"image {ref} built, {scan.summary}{extra} and {push.summary}")


def artifacts_dir(ctx: StationContext) -> Path:
    return ctx.ws.data_dir / "artifacts" / ctx.run.id


async def _sbom_and_provenance(ctx: StationContext, image: str, sha: str) -> CommandResult | None:
    """SPDX SBOM of the built image (syft in dind) plus a provenance record tying
    image, commit, workflow version and run together. None = not configured."""
    template = ctx.product_line.sbom_command
    if not template:
        await ctx.emit(EventKind.decision, "SBOM NOT produced: no sbom_command configured")
        return None
    out = artifacts_dir(ctx)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o777)  # the SBOM tool's container writes here  # noqa: S103
    res = await ctx.cmd(template.format(image=image, out=out), timeout=900)
    if not res.ok:
        return res
    digest = await ctx.cmd(f"docker image inspect -f '{{{{.Id}}}}' {image}", timeout=60)
    sbom = out / "sbom.spdx.json"
    provenance = {
        "_type": "agent-factory/provenance/v1",
        "subject": {"image": image, "image_id": digest.output.strip().splitlines()[-1] if digest.ok else None},
        "source": {"product": ctx.order.product_slug, "commit": sha, "branch": f"run/{ctx.run.id}"},
        "builder": {
            "id": "agent-factory",
            "workflow": ctx.run.workflow_id,
            "workflow_version": ctx.run.workflow_version,
            "run": ctx.run.id,
            "iteration": ctx.run.iteration,
            "sandboxed": ctx.untrusted_ex is not None,
        },
        "materials": {"sbom": str(sbom) if sbom.exists() else None},
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2))
    await ctx.emit(
        EventKind.decision,
        f"SBOM and provenance recorded for {image}",
        {"sbom": str(sbom), "provenance": str(out / "provenance.json")},
    )
    return res


# ------------------------------------------------------------------ deploy --
def internal_url(ctx: StationContext) -> str:
    return ctx.providers.deploy.internal_url(ctx)


async def deploy(ctx: StationContext) -> StationResult:
    p = ctx.providers
    ref = p.registry.image_ref(ctx, await ctx.ws.head_sha(ctx.worktree))
    res = await p.deploy.deploy(ctx, ref)
    if res.ok:
        return StationResult(StationOutcome.passed, res.summary)
    diag = await p.deploy.diagnostics(ctx)
    return StationResult(StationOutcome.failed, res.summary, f"{res.detail}\n--- diagnostics ---\n{diag}")


async def deploy_fix(ctx: StationContext) -> StationResult:
    prompt = f"""The deployment to {ctx.providers.deploy.target(ctx)} failed. Diagnose from the
evidence below (pod status, events and logs collected by the engine; you have no
cluster access yourself) and fix the chart ({ctx.product_line.chart_path}), Dockerfile
or app config. The engine redeploys after you finish.

## Evidence
{ctx.run.last_failure}"""
    res = await ctx.agent(prompt)
    if lim := _limit_result(res):
        return lim
    if not res.ok:
        return StationResult(StationOutcome.failed, "devops agent failed", res.error)
    await ctx.ws.commit_all(ctx.worktree, "fix(deploy): repair from deployment diagnostics")
    return StationResult(StationOutcome.passed, "deployment fix committed")


# -------------------------------------------------------------- acceptance --
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["passed", "results", "summary"],
    "properties": {
        "passed": {"type": "boolean"},
        "summary": {"type": "string"},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["scenario", "passed", "evidence"],
                "properties": {
                    "scenario": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}


async def acceptance(ctx: StationContext) -> StationResult:
    hold = ctx.ws.holdout_dir(ctx.order.product_slug) / "scenarios.yaml"
    scenarios = _read(hold)
    if not scenarios.strip():
        return StationResult(
            StationOutcome.held, "no holdout scenarios found — cannot verify", "missing holdout evidence"
        )
    prompt = f"""Independently verify the LIVE app at {internal_url(ctx)} against these holdout
scenarios. Exercise each one with real HTTP calls (curl). Record concrete evidence
(status codes, bodies). Do not read or trust the builder's code or claims first.

## Holdout scenarios
{scenarios}"""
    res = await ctx.agent(prompt, VERDICT_SCHEMA, protect_holdout=False)
    if lim := _limit_result(res):
        return lim
    if not res.ok or not isinstance(res.structured, dict):
        return StationResult(StationOutcome.failed, "verifier produced no verdict", res.error)
    verdict = res.structured
    await _record_traceability(ctx, hold, verdict.get("results", []))
    failed = [r for r in verdict.get("results", []) if not r.get("passed")]
    all_passed = bool(verdict.get("passed")) and not failed and verdict.get("results")
    if all_passed:
        return StationResult(StationOutcome.passed, verdict.get("summary", "holdout passed"))
    # The builder gets observed behaviour only — never the scenario text.
    evidence = "\n".join(f"- observed: {r.get('evidence', '')}" for r in failed) or verdict.get("summary", "")
    return StationResult(
        StationOutcome.failed,
        f"{len(failed)} holdout scenario(s) failed",
        "Live acceptance found incorrect behaviour:\n" + evidence,
    )


async def _record_traceability(ctx: StationContext, holdout_file: Path, results: list[dict[str, Any]]) -> None:
    """Requirement -> scenarios -> tests -> live result, as evidence and as a file."""
    rows = traceability.matrix(ctx.worktree, traceability.scenarios(holdout_file), results)
    if not rows:
        return
    verified = [r for r in rows if r["holdout"] and all(h["passed"] for h in r["holdout"])]
    out = ctx.ws.data_dir / "artifacts" / ctx.run.id
    out.mkdir(parents=True, exist_ok=True)
    (out / "traceability.json").write_text(json.dumps(rows, indent=2))
    await ctx.emit(
        EventKind.decision,
        f"traceability: {len(verified)}/{len(rows)} requirements verified live by hidden scenarios",
        {"traceability": rows},
    )


# ----------------------------------------------------------------- deliver --
async def deliver(ctx: StationContext) -> StationResult:
    ws, repo, wt = ctx.ws, ctx.ws.product_dir(ctx.order.product_slug), ctx.worktree
    await ws.commit_all(wt, f"chore: deliver iteration {ctx.run.iteration}")
    notes: list[str] = []

    if ctx.cfg.policies.merge.mode == "auto":
        m = await ws.git(f"merge --ff-only run/{ctx.run.id}", repo)
        if not m.ok:
            m = await ws.git(f"merge --no-edit run/{ctx.run.id}", repo)
        if not m.ok:
            return StationResult(StationOutcome.held, "merge into main failed", m.output)
        await ws.git(f"tag -f v{ctx.run.iteration}", repo)
        notes.append("merged to main")
    else:
        notes.append("merge policy is manual — run branch left for review")

    pub = ctx.cfg.policies.publish
    if pub.mode == "auto" and ctx.settings.factory_mode == "dry-run":
        notes.append("publish simulated (dry-run)")
    elif pub.mode == "auto":
        res = await ctx.providers.publish.publish(ctx)
        if not res.ok:
            return StationResult(StationOutcome.held, res.summary, res.detail)
        if res.data.get("repo_url"):
            ctx.order.repo_url = res.data["repo_url"]
        notes.append(res.summary)

    ctx.order.app_url = ctx.providers.deploy.public_url(ctx)
    return StationResult(StationOutcome.passed, "; ".join(notes) + f"; live at {ctx.order.app_url}")


# ------------------------------------------------------------------ review --
async def review(ctx: StationContext) -> StationResult:
    """Spec-conformance review (ADR-0020): the reviewer reports per requirement;
    the engine judges. See agent_factory.review for the rules."""
    reqs = traceability.requirements(ctx.worktree)
    ids = [r["id"] for r in reqs]
    diff = await ctx.ws.git("diff --stat main...HEAD", ctx.worktree)
    out = ctx.ws.data_dir / "artifacts" / ctx.run.id
    earlier = _previous_review(out / "review.json")
    listing = "\n".join(f"- {r['id']}: {r['title']}: {r['detail']}" for r in reqs) or "(none)"
    prompt = f"""Review this iteration's change against the specification before it is packaged.
You are observe-only: read files and use read-only git (`git diff main...HEAD`, `git log`,
`git show`); never modify anything.

## Numbered requirements (docs/requirements.yaml): review EVERY one
{listing}

## What changed in this iteration (`git diff --stat main...HEAD`)
{diff.output.strip()[-3000:] or "(no changes)"}

## Change request for this iteration
{ctx.run.change_request or "none (first build)"}
{_review_notes(ctx)}
## Your earlier review of this iteration sent it back for (check each is fixed)
{earlier or "nothing: this is the first review of this iteration"}

For each requirement report `implemented`, `partial` or `missing`, and `where`: the file
and function that implement it and the test tagged @pytest.mark.req("<id>") that covers
it. Read docs/spec.md and docs/design.md for intent.
Findings: `blocker` or `major` only for concrete defects that break a requirement, the
design or the API contract (docs/openapi.yaml); style and nice-to-haves are `minor`.
Each finding names the file and says what to change."""
    report: dict[str, Any] = {}
    verdict = None
    for attempt in (1, 2):
        res = await ctx.agent(prompt, REVIEW_SCHEMA)
        if lim := _limit_result(res):
            return lim
        if not res.ok or not isinstance(res.structured, dict):
            return StationResult(StationOutcome.failed, "reviewer produced no report", res.error)
        report = res.structured
        verdict = judge(ids, report)
        if verdict.complete:
            break
        if attempt == 1:
            prompt += "\n\nYOUR PREVIOUS REPORT WAS INCOMPLETE:\n- " + "\n- ".join(verdict.problems)
    assert verdict is not None
    out.mkdir(parents=True, exist_ok=True)
    evidence = {"iteration": ctx.run.iteration, "report": report, "judgement": verdict.as_data()}
    (out / "review.json").write_text(json.dumps(evidence, indent=2))
    await ctx.emit(EventKind.decision, verdict.headline(), {"review": evidence})
    if not verdict.complete:
        # the reviewer's fault, not the builder's: hold for a person rather than loop build
        return StationResult(StationOutcome.held, verdict.headline(), "\n".join(verdict.problems))
    if verdict.passed:
        return StationResult(StationOutcome.passed, verdict.headline())
    return StationResult(
        StationOutcome.failed,
        verdict.headline(),
        "Spec review found problems to fix:\n- " + "\n- ".join(verdict.blocking),
    )


def _previous_review(path: Path) -> str:
    """Blocking items of this run's last review, if it sent the change back. The
    evidence file is read (not run.last_failure, which is cleared once build passes)."""
    try:
        judgement = json.loads(path.read_text()).get("judgement", {})
    except (OSError, ValueError):
        return ""
    if judgement.get("passed") or not judgement.get("blocking"):
        return ""
    return "\n".join(f"- {b}" for b in judgement["blocking"])


# --------------------------------------------------- generic agent station --
GENERIC_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["passed", "summary", "findings"],
    "properties": {
        "passed": {"type": "boolean"},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
}


async def generic_agent(ctx: StationContext) -> StationResult:
    """Any custom agent station: the spec's prompt defines the job; the engine
    enforces the contract (a structured verdict + the files the spec promises)."""
    spec = ctx.spec
    prompt = f"""Carry out your station `{ctx.station_id}` for this order, working in the
current repository.

## Order: {ctx.order.title}
{ctx.order.requirements}

## Change request for this iteration
{ctx.run.change_request or "none"}

## Evidence routed to you from a failed station
{ctx.run.last_failure or "none"}

Finish with a verdict. `passed` is true only if your checks succeeded. Each finding
must be concrete: file and line, command and output, or request and response."""
    res = await ctx.agent(prompt, GENERIC_VERDICT_SCHEMA)
    if lim := _limit_result(res):
        return lim
    if not res.ok or not isinstance(res.structured, dict):
        return StationResult(StationOutcome.failed, f"{spec.id} produced no verdict", res.error)
    if not spec.observe_only:
        await ctx.ws.commit_all(ctx.worktree, f"chore({ctx.station_id}): {spec.id} changes")
    missing = _missing_outputs(ctx)
    if missing:
        return StationResult(
            StationOutcome.failed, f"{spec.id} did not produce {missing}", f"missing artifacts: {missing}"
        )
    verdict = res.structured
    if verdict.get("passed") is True:
        return StationResult(StationOutcome.passed, verdict.get("summary") or f"{spec.id} passed")
    findings = "\n".join(f"- {f}" for f in verdict.get("findings", [])) or verdict.get("summary", "")
    return StationResult(
        StationOutcome.failed, f"{spec.id}: {verdict.get('summary', 'failed')}", f"{spec.id} findings:\n{findings}"
    )


STATIONS: dict[str, Station] = {
    "intake": intake,
    "design": design,
    "build": build,
    "verify": verify,
    "readiness": readiness,
    "package": package,
    "deploy": deploy,
    "deploy_fix": deploy_fix,
    "review": review,
    "acceptance": acceptance,
    "deliver": deliver,
    "agent": generic_agent,
}


def dump(obj: Any) -> str:
    return json.dumps(obj, default=str)
