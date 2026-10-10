"""Tool stations (ADR-0034): security and quality tools as visible, configurable stations.

One contract for every tool:
- it runs in dind against the change's worktree, read-only;
- its output is normalised into `Finding`s with one severity scale;
- a policy (`fail_on`) decides pass or fail; findings below it are warnings;
- only the normalised findings are kept as evidence, never the raw output (a secret
  scanner's raw report can contain the secret).

The catalogue is data: `TOOLS` ships the defaults and `tools:` in the config can add or
override entries. Parsers are pure functions of the tool's output.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "info")
RANK = {s: i for i, s in enumerate(SEVERITIES)}  # lower is worse
FAIL_ON = (*SEVERITIES[:4], "none")
MAX_FINDINGS = 500  # kept per station; counts cover all
SRC = "/src"  # where the worktree is mounted inside the tool's container


@dataclass(frozen=True)
class Finding:
    tool: str
    rule: str
    severity: str
    title: str
    file: str = ""
    line: int | None = None
    detail: str = ""

    def as_data(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Tool:
    id: str
    title: str  # what people read
    area: str  # security | supply chain | configuration
    image: str
    command: str  # {image} and {path} are filled in; runs in dind, the worktree mounted at /src
    parser: str  # key in PARSERS
    pillar: str = "security"  # quality pillar of its findings (ADR-0024)


# Images follow the repo's convention for scanners: a moving tag until a digest is reviewed
# and pinned in the config (see `scan_command`). Exit codes are ignored: the parsed output
# decides, and output that can't be parsed means the tool didn't run.
TOOLS: dict[str, Tool] = {
    t.id: t
    for t in (
        Tool(
            "semgrep",
            "Semgrep (code)",
            "security",
            "semgrep/semgrep:latest",
            "docker run --rm -v {path}:/src:ro {image} semgrep scan --config p/default --metrics=off "
            "--sarif --quiet --disable-version-check /src",
            "sarif",
        ),
        Tool(
            "osv-scanner",
            "OSV-Scanner (dependencies)",
            "supply chain",
            "ghcr.io/google/osv-scanner:latest",
            "docker run --rm -v {path}:/src:ro {image} scan source --format json -r /src",
            "osv",
        ),
        Tool(
            "gitleaks",
            "gitleaks (secrets)",
            "security",
            "zricethezav/gitleaks:v8.24.3",
            "docker run --rm -v {path}:/src:ro {image} detect --no-git --source /src --redact --no-banner "
            "--report-format json --report-path /dev/stdout --exit-code 0",
            "gitleaks",
        ),
        Tool(
            "trivy-config",
            "Trivy (Dockerfile, Kubernetes)",
            "configuration",
            "aquasec/trivy:latest",
            "docker run --rm -v {path}:/src:ro -v trivy-cache:/root/.cache {image} config --format json --quiet /src",
            "trivy-config",
            pillar="reliability",
        ),
    )
}


class ToolOutputError(ValueError):
    """The tool's output isn't what its parser expects: the tool did not run properly."""


def _rel(path: str) -> str:
    p = (path or "").replace("file://", "")
    for prefix in (SRC + "/", SRC):
        if p.startswith(prefix):
            p = p[len(prefix) :]
    return p.removeprefix("./").lstrip("/")


def _json(text: str) -> Any:
    """The tool's JSON document, skipping any log lines printed before it."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ToolOutputError("no JSON in the tool's output")
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError as exc:
        raise ToolOutputError(f"the tool's output is not valid JSON: {exc.msg}") from exc


def _cvss(score: Any) -> str | None:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    return "critical" if s >= 9 else "high" if s >= 7 else "medium" if s >= 4 else "low" if s > 0 else "info"


def parse_sarif(tool: str, text: str) -> list[Finding]:
    doc = _json(text)
    if not isinstance(doc, dict) or "runs" not in doc:
        raise ToolOutputError("not a SARIF document")
    out = []
    for run in doc.get("runs") or []:
        rules = {r.get("id"): r for r in ((run.get("tool") or {}).get("driver") or {}).get("rules") or []}
        for r in run.get("results") or []:
            rule = rules.get(r.get("ruleId"), {})
            sev = _cvss((rule.get("properties") or {}).get("security-severity")) or {
                "error": "high",
                "warning": "medium",
                "note": "low",
            }.get(r.get("level") or (rule.get("defaultConfiguration") or {}).get("level"), "medium")
            loc = (r.get("locations") or [{}])[0].get("physicalLocation") or {}
            out.append(
                Finding(
                    tool,
                    str(r.get("ruleId") or "?").split(".")[-1],
                    sev,
                    str((r.get("message") or {}).get("text") or "")[:300],
                    _rel((loc.get("artifactLocation") or {}).get("uri", "")),
                    (loc.get("region") or {}).get("startLine"),
                )
            )
    return out


def parse_osv(tool: str, text: str) -> list[Finding]:
    doc = _json(text)
    if not isinstance(doc, dict) or "results" not in doc:
        raise ToolOutputError("not an OSV-Scanner report")
    out = []
    for res in doc.get("results") or []:
        source = _rel((res.get("source") or {}).get("path", ""))
        for pkg in res.get("packages") or []:
            p = pkg.get("package") or {}
            vulns = {v.get("id"): v for v in pkg.get("vulnerabilities") or []}
            groups = pkg.get("groups") or [{"ids": [i], "max_severity": ""} for i in vulns]
            for g in groups:
                ids = g.get("ids") or []
                v = vulns.get(ids[0], {}) if ids else {}
                sev = (
                    _cvss(g.get("max_severity"))
                    or str((v.get("database_specific") or {}).get("severity") or "medium").lower()
                )
                sev = {"moderate": "medium"}.get(sev, sev)
                out.append(
                    Finding(
                        tool,
                        ids[0] if ids else "?",
                        sev if sev in RANK else "medium",
                        f"{p.get('name')} {p.get('version')}: {v.get('summary') or ', '.join(ids)}"[:300],
                        source,
                        detail=f"{p.get('ecosystem', '')} · also {', '.join(ids[1:4])}" if len(ids) > 1 else "",
                    )
                )
    return out


def parse_gitleaks(tool: str, text: str) -> list[Finding]:
    """Rule, file and line only: the matched text is never kept, even redacted."""
    if not text.strip() or text.strip() == "null":
        return []
    doc = _json(text)
    if not isinstance(doc, list):
        raise ToolOutputError("not a gitleaks report")
    return [
        Finding(
            tool,
            str(d.get("RuleID") or "?"),
            "high",
            f"possible secret: {d.get('Description') or d.get('RuleID')}"[:300],
            _rel(str(d.get("File") or "")),
            d.get("StartLine"),
            "remove it from the code and rotate it",
        )
        for d in doc
        if isinstance(d, dict)
    ]


def parse_trivy_config(tool: str, text: str) -> list[Finding]:
    doc = _json(text)
    if not isinstance(doc, dict):
        raise ToolOutputError("not a Trivy report")
    out = []
    for res in doc.get("Results") or []:
        target = _rel(str(res.get("Target") or ""))
        for m in res.get("Misconfigurations") or []:
            if m.get("Status") == "PASS":
                continue
            sev = str(m.get("Severity") or "MEDIUM").lower()
            out.append(
                Finding(
                    tool,
                    str(m.get("ID") or m.get("AVDID") or "?"),
                    sev if sev in RANK else "medium",
                    str(m.get("Title") or m.get("Message") or "")[:300],
                    target,
                    (m.get("CauseMetadata") or {}).get("StartLine"),
                    str(m.get("Resolution") or "")[:300],
                )
            )
    return out


PARSERS: dict[str, Callable[[str, str], list[Finding]]] = {
    "sarif": parse_sarif,
    "osv": parse_osv,
    "gitleaks": parse_gitleaks,
    "trivy-config": parse_trivy_config,
}


def parse(tool: Tool, text: str) -> list[Finding]:
    if tool.parser not in PARSERS:
        raise ToolOutputError(f"unknown parser '{tool.parser}'")
    findings = PARSERS[tool.parser](tool.id, text)
    return sorted(findings, key=lambda f: (RANK.get(f.severity, 9), f.file, f.line or 0, f.rule))


@dataclass
class Report:
    """A tool station's judgement: what counts, what blocks, what is only a warning."""

    tool: str
    title: str
    fail_on: str
    scope: str  # all | changed
    findings: list[Finding] = field(default_factory=list)  # in scope
    outside: int = 0  # findings in files this change didn't touch (scope: changed)

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(1 for f in self.findings if f.severity == s) for s in SEVERITIES}

    @property
    def blocking(self) -> list[Finding]:
        if self.fail_on == "none":
            return []
        return [f for f in self.findings if RANK.get(f.severity, 9) <= RANK[self.fail_on]]

    @property
    def passed(self) -> bool:
        return not self.blocking

    def headline(self) -> str:
        c = {s: n for s, n in self.counts.items() if n}
        found = ", ".join(f"{n} {s}" for s, n in c.items()) or "no findings"
        extra = f"; {self.outside} in files this change didn't touch" if self.outside else ""
        verdict = "passed" if self.passed else f"{len(self.blocking)} at or above {self.fail_on}"
        return f"{self.title}: {found}{extra} ({verdict})"

    def as_data(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "title": self.title,
            "fail_on": self.fail_on,
            "scope": self.scope,
            "passed": self.passed,
            "counts": self.counts,
            "blocking": len(self.blocking),
            "outside": self.outside,
            "findings": [f.as_data() for f in self.findings[:MAX_FINDINGS]],
            "truncated": len(self.findings) > MAX_FINDINGS,
        }


def judge(tool: Tool, findings: list[Finding], fail_on: str, changed: list[str] | None = None) -> Report:
    """Apply the station's policy. With `changed` (a change to an existing repo), only
    findings in the files the change touched count: problems it didn't make are counted
    separately and never block it."""
    if fail_on not in FAIL_ON:
        raise ValueError(f"fail_on must be one of {FAIL_ON}")
    if changed is None:
        return Report(tool.id, tool.title, fail_on, "all", list(findings))
    touched = set(changed)
    inside = [f for f in findings if f.file in touched]
    return Report(tool.id, tool.title, fail_on, "changed", inside, outside=len(findings) - len(inside))


def catalogue(overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Tool]:
    """The built-in tools, with the config's additions and overrides (image, command …)."""
    out = dict(TOOLS)
    for tid, o in (overrides or {}).items():
        base = out.get(tid)
        fields = {**(base.__dict__ if base else {"id": tid, "pillar": "security"}), **o, "id": tid}
        out[tid] = Tool(**fields)
    return out
