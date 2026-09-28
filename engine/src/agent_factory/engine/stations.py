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

from agent_factory.agents.runner import AgentRequest, AgentResult, AgentRunner
from agent_factory.config import FactoryConfig, ProductLine
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import CommandResult, Executor
from agent_factory.models import EventKind, Order, Run, StationOutcome
from agent_factory.providers import LocalProvider, ProviderSet
from agent_factory.providers.local import failed_detail
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
    imported_plugin: Path | None = None  # the version's pinned imported skills, as a local plugin
    skill_pins: dict[str, str] = field(default_factory=dict)  # imported skill -> commit for this run

    @property
    def product_line(self) -> ProductLine:
        return self.cfg.product_lines[self.order.product_line]

    @property
    def spec(self) -> AgentSpec:
        """The agent spec bound to this station (agent stations only)."""
        return self.workflow_doc.agent_for(self.station_id)

    async def emit(self, kind: EventKind, message: str, data: dict[str, Any] | None = None) -> None:
        self.store.add_event(self.run.id, kind, message, station=self.station_id, data=data)

    async def cmd(
        self, command: str, timeout: float = 900, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> CommandResult:
        res = await self.ex.run(command, cwd=cwd or self.worktree, timeout=timeout, env=env)
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
            elif kind == "agent_init":
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
        res = await self.agents.run(req, sink)
        self.run.cost_usd += res.cost_usd
        return res


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
        "acceptance_scenarios",
        "holdout_scenarios",
        "assumptions",
        "blocking_questions",
    ],
    "properties": {
        "product_name": {"type": "string"},
        "spec_markdown": {"type": "string"},
        "acceptance_scenarios": {"type": "array", "items": {"$ref": "#/$defs/scenario"}},
        "holdout_scenarios": {"type": "array", "items": {"$ref": "#/$defs/scenario"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "blocking_questions": {"type": "array", "items": {"type": "string"}},
    },
    "$defs": {
        "scenario": {
            "type": "object",
            "required": ["id", "given", "when", "then"],
            "properties": {k: {"type": "string"} for k in ("id", "given", "when", "then")},
        }
    },
}


async def intake(ctx: StationContext) -> StationResult:
    spec_path = ctx.worktree / "docs" / "spec.md"
    qa = "\n".join(f"Q: {q}\nA: {a}" for q, a in zip(ctx.run.questions, ctx.run.answers, strict=False))
    prompt = f"""Turn these requirements into a build-ready specification.

## Order: {ctx.order.title}
{ctx.order.requirements}

## Change request for this iteration (from human feedback)
{ctx.run.change_request or "none — first iteration"}

## Answers to your earlier questions
{qa or "none"}

## Current spec (update it, do not start over, when present)
{_read(spec_path) or "none"}

Product line: {ctx.order.product_line} — {ctx.product_line.description}.
Only put a question in blocking_questions if no reasonable assumption exists.
Holdout scenarios must cover behaviour NOT restated in the acceptance scenarios."""
    res = await ctx.agent(prompt, SPEC_SCHEMA)
    if lim := _limit_result(res):
        return lim
    if not res.ok:
        return StationResult(StationOutcome.failed, "intake agent failed", res.error)
    spec = res.structured
    questions = [q for q in spec.get("blocking_questions", []) if q.strip()]
    if questions and not ctx.run.answers:
        return StationResult(StationOutcome.needs_input, "intake needs answers", questions=questions)

    (ctx.worktree / "docs").mkdir(exist_ok=True)
    spec_path.write_text(spec["spec_markdown"])
    acc = ctx.worktree / "tests" / "acceptance"
    acc.mkdir(parents=True, exist_ok=True)
    (acc / "scenarios.yaml").write_text(yaml.safe_dump(spec["acceptance_scenarios"], sort_keys=False))
    hold = ctx.ws.holdout_dir(ctx.order.product_slug)
    hold.mkdir(parents=True, exist_ok=True)
    (hold / "scenarios.yaml").write_text(yaml.safe_dump(spec["holdout_scenarios"], sort_keys=False))
    for a in spec.get("assumptions", []):
        await ctx.emit(EventKind.decision, f"assumption: {a}")
    await ctx.ws.commit_all(ctx.worktree, "docs: specification and acceptance scenarios")
    return StationResult(
        StationOutcome.passed,
        f"spec written; {len(spec['acceptance_scenarios'])} acceptance, "
        f"{len(spec['holdout_scenarios'])} holdout scenarios",
    )


# ------------------------------------------------------------------ design --
async def design(ctx: StationContext) -> StationResult:
    prompt = f"""Design the implementation for the spec in docs/spec.md, following the
golden path already in this repo (read AGENTS.md first).

Write:
  docs/design.md   — architecture, data model, key decisions (ADR style)
  docs/openapi.yaml — the HTTP contract
  docs/tasks.md    — ordered implementation tasks, each with its test

Change request this iteration: {ctx.run.change_request or "none"}"""
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
    res = await ctx.cmd(ctx.product_line.verify_command, timeout=1200)
    if not res.ok:
        return StationResult(
            StationOutcome.failed,
            "verification failed",
            f"`{res.command}` exited {res.returncode}:\n{res.output[-4000:]}",
        )
    return StationResult(StationOutcome.passed, "lint, tests, coverage and security checks passed")


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
    ref = p.registry.image_ref(ctx, sha)
    push = await p.registry.push(ctx, local_image, ref)
    if not push.ok:
        return StationResult(StationOutcome.failed, push.summary, push.detail)
    await ctx.emit(EventKind.decision, f"image built and {push.summary}: {ref}", {"image": str(ref)})
    return StationResult(StationOutcome.passed, f"image {ref} built, {scan.summary} and {push.summary}")


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
evidence below (you may run kubectl get/describe/logs there) and fix the
chart ({ctx.product_line.chart_path}), Dockerfile or app config. Do not delete namespaces.

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
    "package": package,
    "deploy": deploy,
    "deploy_fix": deploy_fix,
    "acceptance": acceptance,
    "deliver": deliver,
    "agent": generic_agent,
}


def dump(obj: Any) -> str:
    return json.dumps(obj, default=str)
