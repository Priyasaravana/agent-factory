"""Spec-conformance review (ADR-0020): did this iteration build what the spec says?

The reviewer agent reports, for every numbered requirement, whether the change
implements it and where, plus concrete findings. The ENGINE decides the outcome
from that report (`judge`), not the agent's opinion of itself:
- every requirement in docs/requirements.yaml must be reviewed (else the review
  is incomplete: retried once, then the run is held);
- a requirement that is `partial` or `missing`, or a `blocker`/`major` finding,
  sends the run back to build with the findings as evidence;
- `minor` findings never block; they are recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "requirements", "findings"],
    "properties": {
        "summary": {"type": "string"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "status", "where"],
                "properties": {
                    "id": {"type": "string", "pattern": "^R[0-9]+$"},
                    "status": {"enum": ["implemented", "partial", "missing"]},
                    "where": {"type": "string"},  # file:line of the implementation and its tagged test
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "message"],
                "properties": {
                    "severity": {"enum": ["blocker", "major", "minor"]},
                    "message": {"type": "string"},
                    "file": {"type": "string"},
                    "requirement": {"type": "string"},
                },
            },
        },
    },
}

BLOCKING_STATUS = {"partial", "missing"}
BLOCKING_SEVERITY = {"blocker", "major"}


@dataclass
class Judgement:
    passed: bool
    complete: bool
    problems: list[str] = field(default_factory=list)  # why the review itself is unusable
    blocking: list[str] = field(default_factory=list)  # what the builder must fix
    minor: list[str] = field(default_factory=list)
    implemented: int = 0
    total: int = 0

    def headline(self) -> str:
        if not self.complete:
            return f"review incomplete: {'; '.join(self.problems)}"
        n_block = len(self.blocking)
        return (
            f"review: {self.implemented}/{self.total} requirements implemented, "
            f"{n_block} blocking, {len(self.minor)} minor"
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "complete": self.complete,
            "implemented": self.implemented,
            "total": self.total,
            "problems": self.problems,
            "blocking": self.blocking,
            "minor": self.minor,
        }


def _finding(f: dict[str, Any]) -> str:
    where = f" ({f['file']})" if f.get("file") else ""
    req = f"[{f['requirement']}] " if f.get("requirement") else ""
    return f"{f.get('severity', '?')}: {req}{f.get('message', '').strip()}{where}"


def judge(requirement_ids: list[str], report: dict[str, Any]) -> Judgement:
    reviewed = {str(r.get("id")): r for r in report.get("requirements", []) if isinstance(r, dict)}
    known = set(requirement_ids)
    problems = [f"requirement {i} not reviewed" for i in requirement_ids if i not in reviewed]
    problems += [f"review cites unknown requirement {i}" for i in reviewed if i not in known]
    if not requirement_ids:
        problems.append("docs/requirements.yaml has no requirements to review against")
    if problems:
        return Judgement(passed=False, complete=False, problems=problems, total=len(requirement_ids))

    blocking = [
        f"{rid} {r.get('status')}: {r.get('where', '').strip() or 'no implementation found'}"
        for rid in requirement_ids
        if (r := reviewed[rid]).get("status") in BLOCKING_STATUS
    ]
    findings = [f for f in report.get("findings", []) if isinstance(f, dict)]
    blocking += [_finding(f) for f in findings if f.get("severity") in BLOCKING_SEVERITY]
    minor = [_finding(f) for f in findings if f.get("severity") not in BLOCKING_SEVERITY]
    implemented = sum(1 for rid in requirement_ids if reviewed[rid].get("status") == "implemented")
    return Judgement(
        passed=not blocking,
        complete=True,
        blocking=blocking,
        minor=minor,
        implemented=implemented,
        total=len(requirement_ids),
    )
