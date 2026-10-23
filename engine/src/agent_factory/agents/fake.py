"""Deterministic AgentRunner for dry-run mode and tests: zero model usage.
Lets you exercise a whole workflow (UI, gates, loops, resume) for free."""

from __future__ import annotations

import asyncio
import json
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
    # open questions intake records for the architect (ADR-0044 §1)
    open_questions: list[str] = field(default_factory=list)
    # implementation technologies intake reports the product requires (simulates a stack conflict)
    stack_required: list[dict[str, str]] = field(default_factory=list)
    # files the developer writes on its first call and removes on its next one
    # (simulates a risky change, then fixing it after it was sent back; ADR-0027)
    risky_once: dict[str, str] = field(default_factory=dict)
    # a repo developer that never adds tests (simulates a repo with no test command, ADR-0033)
    repo_no_tests: bool = False
    # what the checker agent reports when a scan station asks it (ADR-0035)
    scan_findings: list[dict[str, Any]] = field(default_factory=list)
    # skills a role opens through the Skill tool (simulates an agent using what it was given)
    use_skills: dict[str, list[str]] = field(default_factory=dict)
    # what a role's calls end with, in turn, before it works: an infra error each (ADR-0043)
    infra_fail: dict[str, list[str]] = field(default_factory=dict)
    # roles whose next call goes silent for this many seconds (a stalled or long call, ADR-0043)
    hang: dict[str, float] = field(default_factory=dict)
    calls: list[AgentRequest] = field(default_factory=list)
    _risky_state: str = "pending"  # pending -> written -> removed

    async def run(self, req: AgentRequest, sink: EventSink) -> AgentResult:
        self.calls.append(req)
        await sink("agent", {"text": f"[dry-run] {req.role} working on {req.station}"})
        await sink("transcript", {"type": "text", "text": f"[dry-run] {req.role} reads AGENTS.md first"})
        await sink("tool", {"tool": "Read", "input": "AGENTS.md"})
        read = {"type": "tool_use", "id": "t1", "tool": "Read", "input": '{"file_path": "AGENTS.md"}'}
        await sink("transcript", read)
        await sink("transcript", {"type": "tool_result", "id": "t1", "is_error": False, "content": "# AGENTS.md"})
        for name in self.use_skills.get(req.role, []):
            await sink("tool", {"tool": "Skill", "input": name})
            await sink(
                "transcript", {"type": "tool_use", "id": "s1", "tool": "Skill", "input": f'{{"skill": "{name}"}}'}
            )
        if req.role in self.deny_once:
            self.deny_once.remove(req.role)
            reason = "blocked: pushing is done by the engine after checks"
            await sink("denied", {"tool": "Bash", "input": "git push origin main", "reason": reason})
            await sink("transcript", {"type": "denied", "tool": "Bash", "input": "git push", "reason": reason})
        await asyncio.sleep(0.05)
        if req.role in self.hang:
            await asyncio.sleep(self.hang.pop(req.role))
        if self.infra_fail.get(req.role):
            text = self.infra_fail[req.role].pop(0)
            return AgentResult(ok=False, text=text, error="agent returned no structured output (missing evidence)")

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

        if req.role == "architect" and "on the review page" in req.prompt:
            return AgentResult(ok=True, structured=self._discuss(req), text="aligned", turns=1)
        self._write_artifacts(req)
        return AgentResult(ok=True, structured=self._structured(req), text="done", turns=1)

    def _discuss(self, req: AgentRequest) -> dict[str, str]:
        """The architect aligning with a person (ADR-0044 §1): "set <question> to <option>" changes the
        sheet; "change the requirement" asks intake for a requirements change."""
        message = req.prompt.rsplit("The person's input now:", 1)[-1]
        if "also edit the code" in message:  # a reply that strays outside the design documents
            (req.cwd / "src").mkdir(exist_ok=True)
            (req.cwd / "src" / "stray.ts").write_text("export const x = 1;\n")
            agents = req.cwd / "AGENTS.md"
            if agents.is_file():
                agents.write_text(agents.read_text() + "\nstray edit\n")
        if "change the requirement" in message:
            return {"reply": "[dry-run] that is new behaviour", "requirements_change": "R2 also filters by title"}
        sheet = req.cwd / "docs" / "decisions.yaml"
        m = re.search(r"set ([a-z][a-z0-9-]+) to ([a-z][a-z0-9-]+)", message)
        if m and sheet.is_file():
            data = yaml.safe_load(sheet.read_text()) or {}
            for a in data.get("answers", []):
                if a.get("question") == m.group(1):
                    a["choice"], a["decided_by"] = m.group(2), "person"
            sheet.write_text(yaml.safe_dump(data, sort_keys=False))
            return {"reply": f"[dry-run] changed {m.group(1)} to {m.group(2)}", "requirements_change": ""}
        if "I decided:" in message:
            design = req.cwd / "docs" / "design.md"
            design.write_text(design.read_text() + "\nAligned with the person's decisions.\n")
            return {"reply": "[dry-run] the design now follows your decisions", "requirements_change": ""}
        return {"reply": "[dry-run] the design stays as it is", "requirements_change": ""}

    def _write_artifacts(self, req: AgentRequest) -> None:
        docs = req.cwd / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        if req.role == "architect":
            (docs / "design.md").write_text("# Design (dry-run)\n")
            (docs / "openapi.yaml").write_text("openapi: 3.1.0\ninfo: {title: dry-run, version: 0.1.0}\npaths: {}\n")
            (docs / "tasks.md").write_text("1. dry-run task\n")
            (docs / "roadmap.md").write_text(_ROADMAP)
            answers, opened = _fake_answers(req.prompt), _fake_open_answers(req.prompt)
            if answers or opened:
                sheet: dict[str, Any] = {"answers": answers}
                if opened:
                    sheet["open_questions"] = opened
                (docs / "decisions.yaml").write_text(yaml.safe_dump(sheet, sort_keys=False))
        elif req.role not in ("intake", "verifier") and not req.observe_only:
            for rel in req.produces:  # custom agents: honour the spec's promised outputs
                out = req.cwd / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(f"# {req.role} output (dry-run)\n")
        if req.role == "repo-developer" and not self.repo_no_tests:  # a change to an existing repo (ADR-0033)
            pkg = req.cwd / "package.json"
            if pkg.is_file():
                data = json.loads(pkg.read_text() or "{}")
                data.setdefault("scripts", {})["test"] = "node --test"
                pkg.write_text(json.dumps(data, indent=2) + "\n")
                (req.cwd / "test").mkdir(exist_ok=True)
                (req.cwd / "test" / "app.test.js").write_text(
                    "const test = require('node:test');\ntest('dry-run', () => {});\n"
                )
            else:
                (req.cwd / "tests").mkdir(exist_ok=True)
                (req.cwd / "tests" / "test_change.py").write_text("def test_dry_run() -> None:\n    assert True\n")
        elif req.role == "repo-developer":
            (req.cwd / "CHANGES.md").write_text("- dry-run change without tests\n")
        if req.role == "developer":
            tests = req.cwd / "tests"
            tests.mkdir(exist_ok=True)
            reqs = req.cwd / "docs" / "requirements.yaml"
            ids = re.findall(r"id: (R\d+)", reqs.read_text()) if reqs.exists() else ["R1"]
            if (req.cwd / "package.json").is_file():  # a TypeScript stack (nextjs-web): tags in titles
                title = " ".join(f"@{i}" for i in ids)
                body = f'import {{ it }} from "vitest";\n\nit("acceptance dry run {title}", () => {{}});\n'
                (tests / "acceptance.test.ts").write_text(body)
            else:
                tags = ", ".join(f'"{i}"' for i in ids)
                body = f"@pytest.mark.req({tags})\ndef test_acceptance_dry_run() -> None:\n    assert True\n"
                (tests / "test_acceptance.py").write_text("import pytest\n\n\n" + body)
        if req.role == "developer" and self.risky_once and self._risky_state != "removed":
            for rel, text in self.risky_once.items():
                path = req.cwd / rel
                if self._risky_state == "pending":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                elif path.exists():
                    path.unlink()
            self._risky_state = "written" if self._risky_state == "pending" else "removed"
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
        if req.role == "checker":  # a scan station's agent provider or fallback (ADR-0035)
            return {"summary": "[dry-run] checked", "findings": list(self.scan_findings)}
        if "test_command" in props:  # a change to an existing repo (ADR-0033)
            return {"summary": "[dry-run] made the requested change, with a test", "test_command": "", "notes": []}
        if "test_gaps" in props:  # repository assessment (ADR-0031): cite real files, plus one that isn't
            files = sorted(
                p.relative_to(req.cwd).as_posix()
                for p in Path(req.cwd).rglob("*")
                if p.is_file() and ".git" not in p.parts
            )
            main = files[0] if files else "README.md"
            return {
                "summary": "[dry-run] a small service; add tests and CI before changing it",
                "test_gaps": [{"area": "request handling", "why": "no test exercises it", "files": [main]}],
                "risks": [
                    {
                        "severity": "medium",
                        "pillar": "security",
                        "title": "no input validation",
                        "detail": "requests are trusted",
                        "files": [main],
                    },
                    {
                        "severity": "high",
                        "title": "imagined",
                        "detail": "cites a file that isn't there",
                        "files": ["nope.py"],
                    },
                ],
                "recommendations": [
                    {
                        "title": "Add unit tests and a CI workflow",
                        "why": "nothing checks changes",
                        "kind": "upkeep",
                        "effort": "M",
                    }
                ],
                "agents_md": "# AGENTS.md\n\n- Run the tests before every change.\n",
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
            spec = {**_SPEC, "stack_required": list(self.stack_required)}
            if self.intake_questions and not asked:
                return {**spec, "blocking_questions": list(self.intake_questions)}
            return {**spec, "open_questions": list(self.open_questions)} if self.open_questions else spec
        if req.role == "verifier":
            return {
                "passed": True,
                "results": [
                    {"scenario": "H1", "passed": True, "evidence": "201 then 200 with tag filter"},
                    {"scenario": "H2", "passed": True, "evidence": "listed with the same title and notes"},
                ],
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
        {
            "id": "H2",
            "given": "a bookmark saved with a title and notes",
            "when": "GET /bookmarks",
            "then": "it is listed with the same title and notes",
            "covers": ["R1"],
        },
    ],
    "assumptions": ["No authentication in v0 (single user, local cluster)."],
    "blocking_questions": [],
    "stack_required": [],
}


_ROADMAP = "# Roadmap (dry-run)\n\n| pillar | now | deferred | why | trigger |\n|---|---|---|---|---|\n" + "".join(
    f"| {p} | basics | {'load test' if p == 'performance' else '—'} | "
    f"{'no traffic yet' if p == 'performance' else ''} | {'first real users' if p == 'performance' else ''} |\n"
    for p in (
        "security",
        "reliability",
        "performance",
        "operability",
        "cost",
        "interoperability",
        "usability",
        "maintainability",
        "portability",
        "compliance",
    )
)


def _fake_open_answers(prompt: str) -> list[dict[str, str]]:
    """Propose an answer to every open question in the prompt (ADR-0044 §1)."""
    marker = "Open questions from the specification"
    if marker not in prompt:
        return []
    block = prompt.split(marker, 1)[1].split("\n\n", 1)[0]
    return [
        {"id": qid, "answer": f"[dry-run] default for {qid}", "rationale": "the simplest useful choice"}
        for qid in re.findall(r"^- (Q\d+): ", block, flags=re.M)
    ]


def _fake_answers(prompt: str) -> list[dict[str, Any]]:
    """Answer every design question in the prompt with its first allowed option (ADR-0044 §1)."""
    if "Design questions for this stack:" not in prompt:
        return []
    answers: list[dict[str, Any]] = []
    section = prompt.split("Design questions for this stack:", 1)[1].split("\nOpen questions from", 1)[0]
    for block in re.split(r"\n- `", section)[1:]:
        qid = block.split("`", 1)[0]
        options = re.findall(r"\n    - `([a-z0-9-]+)`:[^\n]*", block)
        allowed = [o for o in options if f"`{o}`:" in block and not re.search(rf"`{o}`:[^\n]*not allowed", block)]
        if options:
            pick = (allowed or options)[0]
            answers.append(
                {"question": qid, "choice": pick, "recommended": pick, "rationale": "dry-run: security pillar default"}
            )
    return answers
