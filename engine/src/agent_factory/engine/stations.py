"""Station implementations. Agent stations do judgment work; check stations
are deterministic and produce the evidence the line trusts.

Contract (every station): return a StationResult. Missing or partial evidence
is FAILED or HELD — never a plausible PASS.
"""

from __future__ import annotations

import json
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
from agent_factory.settings import Settings
from agent_factory.state.base import StateStore


@dataclass
class StationResult:
    outcome: StationOutcome
    summary: str
    failure_evidence: str | None = None  # handed to the on_fail station
    questions: list[str] = field(default_factory=list)
    resets_at: int | None = None


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

    @property
    def line(self) -> ProductLine:
        return self.cfg.product_lines[self.order.product_line]

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
        role: str,
        prompt: str,
        model_tier: str | None,
        schema: dict[str, Any] | None = None,
        protect_holdout: bool = True,
    ) -> AgentResult:
        skills = self.cfg.role_skills.get(role, [])
        protected = [str(self.ws.data_dir / "holdout")] if protect_holdout else []

        async def sink(kind: str, data: dict[str, Any]) -> None:
            if kind == "agent":
                await self.emit(EventKind.agent, data.get("text", ""), {})
            elif kind == "tool":
                await self.emit(EventKind.log, f"{data['tool']}: {data['input']}", data)
            else:
                await self.emit(EventKind.log, kind, data)

        req = AgentRequest(
            run_id=self.run.id,
            station=self.station_id,
            role=role,
            prompt=prompt,
            cwd=self.worktree,
            model=self.cfg.models.resolve(model_tier or "default"),
            max_turns=self.cfg.budgets.agent_max_turns.get(role, 50),
            skills=skills,
            skill_overlay=self.cfg.skill_overlay(skills),
            output_schema=schema,
            protected_paths=protected,
            subagent_model=self.cfg.models.fast,
        )
        res = await self.agents.run(req, sink)
        self.run.cost_usd += res.cost_usd
        return res


Station = Callable[[StationContext], Awaitable[StationResult]]


def _limit_result(res: AgentResult) -> StationResult | None:
    if res.rate_limited:
        return StationResult(StationOutcome.paused_limits, "usage limit reached", resets_at=res.limit_resets_at)
    return None


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


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

Product line: {ctx.order.product_line} — {ctx.line.description}.
Only put a question in blocking_questions if no reasonable assumption exists.
Holdout scenarios must cover behaviour NOT restated in the acceptance scenarios."""
    res = await ctx.agent("intake", prompt, ctx.cfg.station("intake").model, SPEC_SCHEMA)
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
    res = await ctx.agent("architect", prompt, ctx.cfg.station("design").model)
    if lim := _limit_result(res):
        return lim
    missing = [f for f in ("docs/design.md", "docs/openapi.yaml", "docs/tasks.md") if not (ctx.worktree / f).exists()]
    if not res.ok or missing:
        return StationResult(StationOutcome.failed, "design incomplete", res.error or f"missing artifacts: {missing}")
    await ctx.ws.commit_all(ctx.worktree, "docs: design, API contract and task plan")
    return StationResult(StationOutcome.passed, "design, openapi and tasks written")


# ------------------------------------------------------------------- build --
async def build(ctx: StationContext) -> StationResult:
    fix = ctx.run.last_failure
    prompt = f"""Implement docs/tasks.md against docs/design.md and docs/openapi.yaml.
Read AGENTS.md first. Write tests alongside code. `{ctx.line.verify_command}` must pass.
Acceptance scenarios to satisfy: tests/acceptance/scenarios.yaml.

{"## Fix this failure from a downstream station (evidence only):" + chr(10) + fix if fix else ""}"""
    res = await ctx.agent("developer", prompt, ctx.cfg.station("build").model)
    if lim := _limit_result(res):
        return lim
    if not res.ok:
        return StationResult(StationOutcome.failed, "developer agent failed", res.error)
    await ctx.ws.commit_all(ctx.worktree, "feat: implementation" + (" (fix)" if fix else ""))
    return StationResult(StationOutcome.passed, "implementation committed")


# ------------------------------------------------------------------ verify --
async def verify(ctx: StationContext) -> StationResult:
    res = await ctx.cmd(ctx.line.verify_command, timeout=1200)
    if not res.ok:
        return StationResult(
            StationOutcome.failed,
            "verification failed",
            f"`{res.command}` exited {res.returncode}:\n{res.output[-4000:]}",
        )
    return StationResult(StationOutcome.passed, "lint, tests, coverage and security checks passed")


# ----------------------------------------------------------------- package --
async def package(ctx: StationContext) -> StationResult:
    sha = await ctx.ws.head_sha(ctx.worktree)
    image = f"{ctx.order.product_slug}:{sha}"
    steps = [
        f"docker build -t {image} .",
        f"trivy image --quiet --exit-code 1 --severity CRITICAL --ignore-unfixed {image}",
        f"kind load docker-image {image} --name {ctx.settings.cluster_name}",
    ]
    for step in steps:
        res = await ctx.cmd(step, timeout=1500)
        if not res.ok:
            return StationResult(
                StationOutcome.failed,
                f"packaging failed at: {step.split()[0]}",
                f"`{step}` exited {res.returncode}:\n{res.output[-4000:]}",
            )
    await ctx.emit(EventKind.decision, f"image built and loaded: {image}", {"image": image})
    return StationResult(StationOutcome.passed, f"image {image} built, scanned and loaded")


# ------------------------------------------------------------------ deploy --
def namespace(order: Order) -> str:
    return f"app-{order.product_slug}"[:63]


def internal_url(ctx: StationContext) -> str:
    return f"http://{ctx.settings.dind_host}:{ctx.order.host_port}"


async def deploy(ctx: StationContext) -> StationResult:
    sha = await ctx.ws.head_sha(ctx.worktree)
    ns, slug = namespace(ctx.order), ctx.order.product_slug
    helm = (
        f"helm upgrade --install {slug} {ctx.line.chart_path} --namespace {ns} --create-namespace "
        f"--set image.repository={slug} --set image.tag={sha} "
        f"--set service.nodePort={ctx.order.node_port} --wait --timeout 5m"
    )
    res = await ctx.cmd(helm, timeout=420)
    if res.ok:
        smoke = await ctx.cmd(
            f"for i in $(seq 1 30); do curl -fsS {internal_url(ctx)}/healthz && exit 0; sleep 2; done; exit 1",
            timeout=90,
        )
        if smoke.ok:
            return StationResult(StationOutcome.passed, f"deployed {slug}:{sha} to {ns}; healthz ok")
        res = smoke
    diag = await ctx.cmd(
        f"kubectl -n {ns} get pods -o wide; kubectl -n {ns} describe pods | tail -60; "
        f"kubectl -n {ns} logs -l app.kubernetes.io/instance={slug} --tail=80 --all-containers",
        timeout=60,
    )
    return StationResult(
        StationOutcome.failed, "deployment failed", f"{res.output[-2500:]}\n--- diagnostics ---\n{diag.output[-3500:]}"
    )


async def deploy_fix(ctx: StationContext) -> StationResult:
    prompt = f"""The deployment to namespace {namespace(ctx.order)} failed. Diagnose from the
evidence below (you may run kubectl get/describe/logs in that namespace) and fix the
chart ({ctx.line.chart_path}), Dockerfile or app config. Do not delete namespaces.

## Evidence
{ctx.run.last_failure}"""
    res = await ctx.agent("devops", prompt, ctx.cfg.station("deploy_fix").model)
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
    res = await ctx.agent(
        "verifier", prompt, ctx.cfg.station("acceptance").model, VERDICT_SCHEMA, protect_holdout=False
    )
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
    elif pub.mode == "auto" and ctx.settings.github_token:
        env = {"GH_TOKEN": ctx.settings.github_token}
        owner = getattr(pub, "owner", "") or ""
        name = f"{owner}/{ctx.order.product_slug}" if owner else ctx.order.product_slug
        has_remote = (await ws.git("remote get-url origin", repo)).ok
        if not has_remote:
            vis = "--private" if getattr(pub, "visibility", "private") == "private" else "--public"
            c = await ctx.cmd(f"gh repo create {name} {vis} --source . --remote origin", env=env, cwd=repo, timeout=120)
            if not c.ok:
                return StationResult(StationOutcome.held, "GitHub repo creation failed", c.output)
        p = await ctx.cmd(
            "gh auth setup-git && git push -u origin main --tags --force-with-lease",
            env={**env, **ws.git_env},
            cwd=repo,
            timeout=300,
        )
        if not p.ok:
            return StationResult(StationOutcome.held, "push to GitHub failed", p.output)
        url = await ctx.cmd("gh repo view --json url -q .url", env=env, cwd=repo, timeout=60)
        ctx.order.repo_url = url.output.strip().splitlines()[-1] if url.ok else None
        notes.append(f"pushed to {ctx.order.repo_url or name}")
    elif pub.mode == "auto":
        await ctx.emit(EventKind.decision, "publish NOT done: GITHUB_TOKEN is not set")
        notes.append("NOT pushed (no GITHUB_TOKEN)")

    ctx.order.app_url = f"{ctx.cfg.factory.public_app_base_url}:{ctx.order.host_port}"
    return StationResult(StationOutcome.passed, "; ".join(notes) + f"; live at {ctx.order.app_url}")


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
}


def dump(obj: Any) -> str:
    return json.dumps(obj, default=str)
