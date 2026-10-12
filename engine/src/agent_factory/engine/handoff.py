"""Artifact handoffs (ADR-0037 §2): the engine records what a change starts from and what each
station gives in the change's ledger, and checks each against its schema.

- **Entry**: before the first station, the workflow's entry artifacts are recorded; one that
  fails its schema rejects the change before anything runs (held).
- **Needs**: before a station runs, every artifact it needs must be in the ledger (held if not).
- **Gives**: after a station passes, each artifact it gives is collected from where the station
  left it and validated; one that fails fails the station (routed by `on_fail`).

Collectors read what today's handlers already write (files in the worktree, evidence files under
`artifacts/<change>/`, decision events). They never copy holdout text or secret values into a
manifest: the holdout is recorded by count and digest only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml

from agent_factory import artifacts, contract
from agent_factory.artifacts import ENTRY, Ledger, file_ref
from agent_factory.contract import ArtifactRef
from agent_factory.engine.stations import StationContext, artifacts_dir
from agent_factory.workflow import Plan


class MissingArtifact(Exception):
    """The station passed but left nothing that makes this artifact."""


def ledger_for(ctx: StationContext) -> Ledger:
    return Ledger(artifacts_dir(ctx))


def write_plan(ctx: StationContext, plan: Plan) -> None:
    """The run's plan, with every skipped station and why, as sealed evidence (ADR-0037 §4)."""
    out = artifacts_dir(ctx)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan.model_dump(), indent=2) + "\n")


def missing_needs(ctx: StationContext) -> list[str]:
    """Needs of the current station the ledger cannot satisfy (as `a@1 | b@1` text)."""
    station = ctx.workflow_doc.station(ctx.station_id)
    have = ledger_for(ctx).have()
    return [contract.need_text(n) for n in station.needs_refs() if not contract.satisfied(n, have)]


async def record_entry(ctx: StationContext) -> list[str]:
    """Record the workflow's entry artifacts once per change; return why any is rejected."""
    ledger = ledger_for(ctx)
    if ledger.has_entry():
        return []
    made: list[tuple[ArtifactRef, dict[str, Any], str]] = []
    errors: list[str] = []
    for ref in ctx.workflow_doc.entry_refs():
        try:
            manifest, location = await collect(ref, ctx, summary="")
        except MissingArtifact as exc:
            errors.append(f"{ref}: {exc}")
            continue
        errors += artifacts.problems(ref, manifest)
        made.append((ref, manifest, location))
    if not errors:
        for ref, manifest, location in made:
            ledger.record(ref, manifest, ENTRY, location)
    return errors


async def record_gives(ctx: StationContext, summary: str) -> list[str]:
    """Collect and validate what the station gives; record the valid ones. Returns problems
    (any problem fails the station; nothing of a failing station is recorded)."""
    station = ctx.workflow_doc.station(ctx.station_id)
    made: list[tuple[ArtifactRef, dict[str, Any], str]] = []
    errors: list[str] = []
    for ref in station.gives_refs():
        try:
            manifest, location = await collect(ref, ctx, summary)
        except MissingArtifact as exc:
            errors.append(f"{ref}: {exc}")
            continue
        errors += artifacts.problems(ref, manifest)
        made.append((ref, manifest, location))
    if errors:
        return errors
    ledger = ledger_for(ctx)
    for ref, manifest, location in made:
        ledger.record(ref, manifest, ctx.station_id, location)
    return []


# ----------------------------------------------------------- collectors ---
Collector = Callable[[ArtifactRef, StationContext, str], Awaitable[tuple[dict[str, Any], str]]]


async def collect(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    """(manifest, location) of an artifact as the station left it."""
    return await COLLECTORS[ref.type](ref, ctx, summary)


async def _request(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    text = (ctx.change.change_request or ctx.product.requirements or "").strip()
    manifest = {
        "text": text,
        "kind": str(ctx.change.kind),
        "source": str(ctx.change.source),
        "requested_by": ctx.change.requested_by,
    }
    return manifest, f"change:{ctx.change.id}"


async def _branch(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    commit = await ctx.ws.head_sha(ctx.worktree)
    target = "repo" if ctx.blueprint.target == "repo" else "factory"
    branch = f"run/{ctx.change.id}"
    manifest = {
        "target": target,
        "repo": ctx.product.repo_url or ctx.product.slug,
        "ref": branch,
        "commit": commit,
        "base": ctx.product.repo_ref if target == "repo" else "main",
    }
    return manifest, f"{branch}@{commit}"


def _yaml_list(path: Path) -> list[Any]:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return []
    return data if isinstance(data, list) else []


async def _spec(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    wt = ctx.worktree
    if not (wt / "docs" / "spec.md").is_file():
        raise MissingArtifact("docs/spec.md was not written")
    reqs = _yaml_list(wt / "docs" / "requirements.yaml")
    hold = ctx.ws.holdout_dir(ctx.product.slug) / "scenarios.yaml"
    sealed = hashlib.sha256(hold.read_bytes()).hexdigest() if hold.is_file() else None  # digest only, never text
    manifest = {
        "path": "docs/spec.md",
        "requirements": [
            {"id": str(r.get("id", "")), "title": str(r.get("title", ""))} for r in reqs if isinstance(r, dict)
        ],
        "acceptance_scenarios": len(_yaml_list(wt / "tests" / "acceptance" / "scenarios.yaml")),
        "holdout": {"scenarios": len(_yaml_list(hold)), "sha256": sealed},
    }
    return manifest, "docs/spec.md"


DESIGN_FILES = ["docs/design.md", "docs/openapi.yaml", "docs/tasks.md"]


async def _design(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    st = ctx.workflow_doc.station(ctx.station_id)
    wanted = ctx.workflow_doc.agents[st.agent].produces if st.agent in ctx.workflow_doc.agents else []
    files = [f for f in (file_ref(ctx.worktree, p) for p in (wanted or DESIGN_FILES)) if f]
    return {"files": files}, "docs/"


async def _stories(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    raise MissingArtifact("no station gives stories yet (composable SDLC step 4)")


def _json(folder: Path, name: str) -> dict[str, Any]:
    try:
        data = json.loads((folder / name).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _report(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    out = artifacts_dir(ctx)
    handler = _handler(ctx)
    kind = ref.kind or ""
    files: list[str] = []
    data: dict[str, Any] = {}
    if kind == "repo":
        files = ["repo.json"]
    elif kind == "readiness":
        name = "repo-scan.json" if handler == "repo-scan" else "readiness.json"
        files = [name]
        card = _json(out, name)
        card = card.get("readiness", {}) if handler == "repo-scan" else card
        if isinstance(card, dict) and "level" in card:
            data["level"] = card["level"]
    elif kind == "assessment":
        files = (
            ["assessment-agent.json"]
            if handler == "assess"
            else ["assessment.json", "assessment.md", "AGENTS.proposed.md"]
        )
    elif kind == "test":
        if handler == "repo-test":
            files = ["repo-test.json"]
            got = _json(out, "repo-test.json")
            data = {"ok": got.get("ok"), "command": got.get("command")}
        else:  # the factory's own verify command; its output stays in the event log
            data = {"ok": True, "command": ctx.blueprint.verify_command}
    elif kind == "scan":
        name = f"tools/{ctx.station_id}.json"
        files = [name]
        got = _json(out, name)
        data = {k: got[k] for k in ("check", "passed") if k in got}
    elif kind == "review":
        name = "repo-review.json" if handler == "repo-review" else "review.json"
        files = [name]
        judgement = _json(out, name).get("judgement") or {}
        if isinstance(judgement, dict) and "passed" in judgement:
            data["passed"] = judgement["passed"]
    elif kind == "risk":
        files = ["change-risk.json"]
        got = _json(out, "change-risk.json")
        data = {k: got[k] for k in ("digest", "holds") if k in got}
    elif kind == "acceptance":
        files = ["traceability.json"]
    elif kind == "change":
        files = ["repo-change.json"]
        data["commit"] = await ctx.ws.head_sha(ctx.worktree)
    evidence = [f for f in (file_ref(out, n) for n in files) if f]
    manifest = {"kind": kind, "station": ctx.station_id, "summary": summary[:500], "evidence": evidence, "data": data}
    return manifest, f"artifacts/{ctx.change.id}/" + (evidence[0]["path"] if evidence else "")


def _handler(ctx: StationContext) -> str:
    station = ctx.workflow_doc.station(ctx.station_id)
    return station.handler_for(ctx.target) or station.resolved_handler()


def _latest_event(ctx: StationContext, key: str) -> Any:
    found = ctx.store.events_with_key([ctx.change.id], key)
    return found[-1][2] if found else None


async def _image(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    image = _latest_event(ctx, "image")
    if not image:
        raise MissingArtifact("the build recorded no image")
    out = artifacts_dir(ctx)
    manifest = {
        "ref": str(image),
        "sbom": file_ref(out, "sbom.spdx.json"),
        "provenance": file_ref(out, "provenance.json"),
    }
    return manifest, str(image)


async def _url(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    deploy = ctx.providers.deploy
    url = deploy.public_url(ctx)
    return {"url": url, "internal": deploy.internal_url(ctx)}, url


async def _pr(ref: ArtifactRef, ctx: StationContext, summary: str) -> tuple[dict[str, Any], str]:
    branch = f"run/{ctx.change.id}"
    if _handler(ctx) == "pull-request":
        info = _latest_event(ctx, "pull_request") or {}
        manifest = {
            "via": "pull-request",
            "branch": str(info.get("branch") or branch),
            "url": info.get("url"),
            "base": info.get("base"),
        }
        return manifest, str(info.get("url") or manifest["branch"])
    merged = ctx.cfg.policies.merge.mode == "auto"
    manifest = {
        "via": "handover",
        "branch": branch,
        "url": ctx.product.repo_url,
        "tag": f"v{ctx.change.iteration}" if merged else None,
    }
    return manifest, ctx.product.repo_url or branch


COLLECTORS: dict[str, Collector] = {
    "request": _request,
    "spec": _spec,
    "design": _design,
    "stories": _stories,
    "branch": _branch,
    "report": _report,
    "image": _image,
    "url": _url,
    "pr": _pr,
}
assert set(COLLECTORS) == set(contract.ARTIFACT_TYPES)  # noqa: S101 - every type has a collector
