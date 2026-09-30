"""Deterministic AgentRunner for dry-run mode and tests: zero model usage.
Lets you exercise a whole workflow (UI, gates, loops, resume) for free."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_factory.agents.runner import AgentRequest, AgentResult, EventSink


@dataclass
class FakeAgentRunner:
    # roles that fail on their first call (simulates a fix loop)
    fail_once: list[str] = field(default_factory=list)
    # questions intake asks on its first call (simulates needs_input)
    intake_questions: list[str] = field(default_factory=list)
    rate_limit_once: bool = False
    # roles whose first call tries something a guardrail refuses (simulates a denial)
    deny_once: list[str] = field(default_factory=list)
    calls: list[AgentRequest] = field(default_factory=list)

    async def run(self, req: AgentRequest, sink: EventSink) -> AgentResult:
        self.calls.append(req)
        await sink("agent", {"text": f"[dry-run] {req.role} working on {req.station}"})
        await sink("transcript", {"type": "text", "text": f"[dry-run] {req.role} reads AGENTS.md first"})
        await sink("tool", {"tool": "Read", "input": "AGENTS.md"})
        read = {"type": "tool_use", "id": "t1", "tool": "Read", "input": '{"file_path": "AGENTS.md"}'}
        await sink("transcript", read)
        await sink("transcript", {"type": "tool_result", "id": "t1", "is_error": False, "content": "# AGENTS.md"})
        if req.role in self.deny_once:
            self.deny_once.remove(req.role)
            reason = "blocked: pushing is done by the engine after checks"
            await sink("denied", {"tool": "Bash", "input": "git push origin main", "reason": reason})
            await sink("transcript", {"type": "denied", "tool": "Bash", "input": "git push", "reason": reason})
        await asyncio.sleep(0.05)

        if self.rate_limit_once:
            self.rate_limit_once = False
            return AgentResult(
                ok=False,
                rate_limited=True,
                limit_utilization=1.0,
                limit_resets_at=None,
                error="rate limited (simulated)",
            )

        generic = bool(req.output_schema and "findings" in req.output_schema.get("properties", {}))
        if req.role in self.fail_once:
            self.fail_once.remove(req.role)
            if generic:
                return AgentResult(
                    ok=True,
                    structured={
                        "passed": False,
                        "summary": "simulated review failure",
                        "findings": [f"{req.role}: app/main.py:1 simulated finding"],
                    },
                )
            if req.role == "verifier":
                return AgentResult(
                    ok=True,
                    structured={
                        "passed": False,
                        "results": [
                            {"scenario": "H1", "passed": False, "evidence": "GET /bookmarks?tag=x returned 500"}
                        ],
                        "summary": "tag filtering fails",
                    },
                )
            return AgentResult(ok=False, error=f"{req.role} simulated failure")

        self._write_artifacts(req)
        return AgentResult(ok=True, structured=self._structured(req), text="done", turns=1)

    def _write_artifacts(self, req: AgentRequest) -> None:
        docs = req.cwd / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        if req.role == "architect":
            (docs / "design.md").write_text("# Design (dry-run)\n")
            (docs / "openapi.yaml").write_text("openapi: 3.1.0\ninfo: {title: dry-run, version: 0.1.0}\npaths: {}\n")
            (docs / "tasks.md").write_text("1. dry-run task\n")
        elif req.role not in ("intake", "verifier") and not req.observe_only:
            for rel in req.produces:  # custom agents: honour the spec's promised outputs
                out = req.cwd / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(f"# {req.role} output (dry-run)\n")
        if req.role == "developer":
            tests = req.cwd / "tests"
            tests.mkdir(exist_ok=True)
            reqs = req.cwd / "docs" / "requirements.yaml"
            ids = re.findall(r"id: (R\d+)", reqs.read_text()) if reqs.exists() else ["R1"]
            tags = ", ".join(f'"{i}"' for i in ids)
            body = f"@pytest.mark.req({tags})\ndef test_acceptance_dry_run() -> None:\n    assert True\n"
            (tests / "test_acceptance.py").write_text("import pytest\n\n\n" + body)
        if req.role in ("developer", "devops"):
            with (docs / "build-log.md").open("a") as fh:
                fh.write(f"- {req.role} pass for {req.station}\n")

    def _structured(self, req: AgentRequest) -> Any:
        props = (req.output_schema or {}).get("properties", {})
        if "proposals" in props:  # retro (ADR-0021): one lesson for the builder
            return {
                "proposals": [
                    {
                        "agent": "developer",
                        "lesson": "Run the full test suite, including acceptance scenarios, before finishing a build.",
                        "why": "the build was sent back after a downstream station failed",
                        "evidence": "routing failure from verify to build",
                    }
                ]
            }
        if "findings" in props and "requirements" in props:  # spec review (ADR-0020)
            reqs = yaml.safe_load((Path(req.cwd) / "docs" / "requirements.yaml").read_text()) or []
            return {
                "summary": "every requirement is implemented and tested",
                "requirements": [
                    {"id": r["id"], "status": "implemented", "where": f"app/main.py, test tagged {r['id']}"}
                    for r in reqs
                ],
                "findings": [{"severity": "minor", "message": "consider pagination later", "file": "app/main.py"}],
            }
        if req.output_schema and "findings" in req.output_schema.get("properties", {}):
            return {"passed": True, "summary": f"{req.role} checks passed", "findings": []}
        if req.role == "intake":
            asked = any(c.role == "intake" for c in self.calls[:-1])
            if self.intake_questions and not asked:
                return {**_SPEC, "blocking_questions": list(self.intake_questions)}
            return _SPEC
        if req.role == "verifier":
            return {
                "passed": True,
                "results": [{"scenario": "H1", "passed": True, "evidence": "201 then 200 with tag filter"}],
                "summary": "all holdout scenarios passed",
            }
        return None


_SPEC: dict[str, Any] = {
    "product_name": "Bookmarks",
    "spec_markdown": "# Bookmarks service\n\nSave, tag and search bookmarks and notes.\n",
    "requirements": [
        {"id": "R1", "title": "Save a bookmark", "detail": "POST /bookmarks with url and title returns it with an id"},
        {"id": "R2", "title": "Filter by tag", "detail": "GET /bookmarks?tag=x returns only bookmarks tagged x"},
    ],
    "acceptance_scenarios": [
        {
            "id": "A1",
            "given": "an empty store",
            "when": "POST /bookmarks with url+title",
            "then": "201 and the bookmark is returned with an id",
            "covers": ["R1"],
        },
        {
            "id": "A2",
            "given": "a bookmark tagged 'x'",
            "when": "GET /bookmarks?tag=x",
            "then": "it is listed",
            "covers": ["R2"],
        },
    ],
    "holdout_scenarios": [
        {
            "id": "H1",
            "given": "two bookmarks tagged 'x' and 'y'",
            "when": "GET /bookmarks?tag=x",
            "then": "only the 'x' bookmark is returned",
            "covers": ["R2"],
        },
    ],
    "assumptions": ["No authentication in v0 (single user, local cluster)."],
    "blocking_questions": [],
}
