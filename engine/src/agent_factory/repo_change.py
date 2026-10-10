"""Changes to existing repositories, delivered as pull requests (ADR-0033). Pure functions:

- `test_commands`: how to run a repository's own checks, from its files.
- `judge_review`: the reviewer reports; the engine decides what blocks.
- `pr_title` / `pr_body`: what the pull request says, from the change's evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMPLEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "test_command"],
    "properties": {
        "summary": {"type": "string"},  # what changed and why, for the pull request
        "test_command": {"type": "string"},  # how to run the repo's checks ("" when unknown)
        "notes": {"type": "array", "items": {"type": "string"}},  # follow-ups, assumptions
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "findings"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "file", "message"],
                "properties": {
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "file": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
    },
}

_DEFAULT_NPM_TEST = "no test specified"


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(errors="replace") if p.is_file() and not p.is_symlink() else ""


def test_commands(root: Path) -> list[str]:
    """The repository's own checks, most specific first. Empty: the repo has none.
    These run in the agent sandbox, never next to the engine's secrets."""
    out: list[str] = []
    try:
        pkg = json.loads(_read(root, "package.json") or "{}")
    except json.JSONDecodeError:
        pkg = {}
    script = str(((pkg.get("scripts") or {}) if isinstance(pkg, dict) else {}).get("test") or "")
    if script and _DEFAULT_NPM_TEST not in script:
        install = "npm ci" if (root / "package-lock.json").is_file() else "npm install --no-audit --no-fund"
        out.append(f"{install} && npm test")
    makefile = _read(root, "Makefile")
    for target in ("verify", "test", "check"):
        if re.search(rf"^{target}:", makefile, re.M):
            out.append(f"make {target}")
            break
    has_pytests = any(root.glob("tests/test_*.py")) or any(root.glob("test_*.py"))
    if has_pytests or "[tool.pytest" in _read(root, "pyproject.toml"):
        out.append("uv run --with pytest pytest -q" if (root / "pyproject.toml").is_file() else "python -m pytest -q")
    if (root / "go.mod").is_file():
        out.append("go test ./...")
    return out


@dataclass
class ReviewVerdict:
    passed: bool
    blocking: list[str]
    notes: list[str]
    summary: str

    def as_data(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def headline(self) -> str:
        if self.passed:
            return f"review passed ({len(self.notes)} note(s))" if self.notes else "review passed"
        return f"review sent the change back: {len(self.blocking)} blocking finding(s)"


def judge_review(report: dict[str, Any], changed: list[str]) -> ReviewVerdict:
    """Only `blocker` or `major` findings on files this change touched send it back:
    the change isn't held responsible for problems it didn't make. Everything else is a
    note that goes into the pull request."""
    touched = set(changed)
    blocking, notes = [], []
    for f in report.get("findings") or []:
        if not isinstance(f, dict):
            continue
        file = str(f.get("file") or "").strip().removeprefix("./").split(":")[0]
        line = f"{f.get('severity', '?')}: {file or '(no file)'}: {str(f.get('message') or '').strip()}"
        if f.get("severity") in ("blocker", "major") and file in touched:
            blocking.append(line)
        else:
            notes.append(line)
    return ReviewVerdict(not blocking, blocking, notes, str(report.get("summary") or "").strip())


def pr_title(kind: str, request: str) -> str:
    first = (request or "").strip().splitlines()[0] if (request or "").strip() else "change"
    label = {"bug": "fix", "upkeep": "chore", "feature": "feat"}.get(kind, kind)
    title = f"{label}: {first}"
    return title if len(title) <= 72 else title[:71] + "…"


def pr_body(
    *,
    kind: str,
    request: str,
    requested_by: str | None,
    summary: str,
    notes: list[str],
    test: dict[str, Any],
    review: dict[str, Any],
    risk: dict[str, Any],
    change_id: str,
    files: list[str],
    tools: list[dict[str, Any]] | None = None,
) -> str:
    """The pull request's description: what was asked, what changed, and the evidence."""
    lines = [
        f"**{kind.capitalize()} requested** by {requested_by or 'a member'}:",
        "",
        "> " + "\n> ".join((request or "").strip().splitlines() or ["(no description)"]),
        "",
        "## What changed",
        "",
        summary or "(no summary)",
        "",
        f"{len(files)} file(s): " + ", ".join(f"`{f}`" for f in files[:20]) + (" …" if len(files) > 20 else ""),
        "",
        "## Evidence",
        "",
        f"- **Tests:** `{test.get('command') or 'none'}` → {'passed' if test.get('ok') else 'not passed'}",
    ]
    review_line = f"- **Review:** {review.get('summary') or 'done'}"
    if review.get("notes"):
        review_line += f" ({len(review['notes'])} note(s) below)"
    lines.append(review_line)
    holds, findings = risk.get("holds", 0), risk.get("findings") or []
    if holds and risk.get("approved_by"):
        lines.append(f"- **Change risk:** {holds} risky change(s), approved by {risk['approved_by']}")
    elif findings:
        lines.append(f"- **Change risk:** no risky changes ({len(findings)} note(s))")
    else:
        lines.append("- **Change risk:** no risky changes")
    for t in tools or []:
        counts = ", ".join(f"{n} {s}" for s, n in (t.get("counts") or {}).items() if n) or "no findings"
        scope = " in the changed files" if t.get("scope") == "changed" else ""
        verdict = "simulated" if t.get("simulated") else ("passed" if t.get("passed") else "not passed")
        lines.append(f"- **{t.get('title')}:** {counts}{scope} ({verdict})")
    if review.get("notes") or notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in [*review.get("notes", []), *notes]]
    lines += [
        "",
        "---",
        f"Opened by the agent factory (change `{change_id}`). The full evidence is sealed in the factory. "
        "A person reviews and merges: the factory never merges its own pull requests.",
    ]
    return "\n".join(lines) + "\n"
