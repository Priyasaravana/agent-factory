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
    command: str  # {image}, {path} and {repo} are filled in; most run in dind, the worktree at /src
    parser: str  # key in PARSERS
    pillar: str = "security"  # quality pillar of its findings (ADR-0024)
    check: str = ""  # the check it answers (ADR-0035): secrets | code | dependencies | config
    provider: str = "local"  # local | github | snyk (ADR-0035)
    # a service account the command needs: the provider's token reference is resolved for
    # the step and reaches the command as this environment variable, never its arguments
    credential_env: str = ""
    strict_exit: bool = False  # a non-zero exit means it didn't run (API calls); scanners: the output decides
    needs_repo: bool = False  # asks a service about the product's repository ({repo} is owner/name)


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
            check="code",
        ),
        Tool(
            "osv-scanner",
            "OSV-Scanner (dependencies)",
            "supply chain",
            "ghcr.io/google/osv-scanner:latest",
            "docker run --rm -v {path}:/src:ro {image} scan source --format json -r /src",
            "osv",
            check="dependencies",
        ),
        Tool(
            "gitleaks",
            "gitleaks (secrets)",
            "security",
            "zricethezav/gitleaks:v8.24.3",
            "docker run --rm -v {path}:/src:ro {image} detect --no-git --source /src --redact --no-banner "
            "--report-format json --report-path /dev/stdout --exit-code 0",
            "gitleaks",
            check="secrets",
        ),
        Tool(
            "trivy-config",
            "Trivy (Dockerfile, Kubernetes)",
            "configuration",
            "aquasec/trivy:latest",
            "docker run --rm -v {path}:/src:ro -v trivy-cache:/root/.cache {image} config --format json --quiet /src",
            "trivy-config",
            pillar="reliability",
            check="config",
        ),
        # --- a SaaS the team already has (ADR-0035). Snyk runs its CLI in dind with the
        # token passed by name (`-e SNYK_TOKEN`); its findings come back as SARIF.
        Tool(
            "snyk-code",
            "Snyk Code",
            "security",
            "snyk/snyk:linux",
            "docker run --rm -e SNYK_TOKEN -v {path}:/src:ro -w /src {image} snyk code test --sarif /src",
            "sarif",
            check="code",
            provider="snyk",
            credential_env="SNYK_TOKEN",
        ),
        Tool(
            "snyk-open-source",
            "Snyk Open Source",
            "supply chain",
            "snyk/snyk:linux",
            "docker run --rm -e SNYK_TOKEN -v {path}:/src:ro -w /src {image} snyk test --all-projects --sarif /src",
            "sarif",
            check="dependencies",
            provider="snyk",
            credential_env="SNYK_TOKEN",
        ),
        Tool(
            "snyk-iac",
            "Snyk IaC",
            "configuration",
            "snyk/snyk:linux",
            "docker run --rm -e SNYK_TOKEN -v {path}:/src:ro -w /src {image} snyk iac test --sarif /src",
            "sarif",
            pillar="reliability",
            check="config",
            provider="snyk",
            credential_env="SNYK_TOKEN",
        ),
        # GitHub's own scanners: open alerts on the repository's default branch, read with
        # `gh api`. `--jq` keeps only the fields we use, so a secret-scanning alert's secret
        # value never leaves gh.
        Tool(
            "github-code-scanning",
            "GitHub code scanning",
            "security",
            "",
            "gh api -X GET repos/{repo}/code-scanning/alerts -f state=open -f per_page=100 --jq "
            "'[.[] | {{rule: .rule.id, severity: (.rule.security_severity_level // .rule.severity), "
            "title: .rule.description, tool: .tool.name, file: .most_recent_instance.location.path, "
            "line: .most_recent_instance.location.start_line}}]'",
            "github-alerts",
            check="code",
            provider="github",
            credential_env="GH_TOKEN",
            strict_exit=True,
            needs_repo=True,
        ),
        Tool(
            "github-dependabot",
            "GitHub Dependabot",
            "supply chain",
            "",
            "gh api -X GET repos/{repo}/dependabot/alerts -f state=open -f per_page=100 --jq "
            "'[.[] | {{rule: .security_advisory.ghsa_id, severity: .security_advisory.severity, "
            'title: (.dependency.package.name + ": " + .security_advisory.summary), '
            "file: .dependency.manifest_path}}]'",
            "github-alerts",
            check="dependencies",
            provider="github",
            credential_env="GH_TOKEN",
            strict_exit=True,
            needs_repo=True,
        ),
        Tool(
            "github-secret-scanning",
            "GitHub secret scanning",
            "security",
            "",
            "gh api -X GET repos/{repo}/secret-scanning/alerts -f state=open -f per_page=100 --jq "
            '\'[.[] | {{rule: .secret_type, severity: "high", title: ("possible secret: " + '
            ".secret_type_display_name)}}]'",
            "github-alerts",
            check="secrets",
            provider="github",
            credential_env="GH_TOKEN",
            strict_exit=True,
            needs_repo=True,
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


GITHUB_SEVERITY = {"error": "high", "warning": "medium", "note": "low", "none": "info", "moderate": "medium"}


def parse_github_alerts(tool: str, text: str) -> list[Finding]:
    """GitHub alerts, already reduced by `--jq` to rule, severity, title, file and line."""
    doc = _json(text)
    if not isinstance(doc, list):
        raise ToolOutputError("not a list of GitHub alerts")
    out = []
    for a in doc:
        if not isinstance(a, dict):
            continue
        sev = str(a.get("severity") or "medium").lower()
        sev = GITHUB_SEVERITY.get(sev, sev)
        title = str(a.get("title") or a.get("rule") or "")
        if a.get("tool"):
            title = f"{title} ({a['tool']})"
        out.append(
            Finding(
                tool,
                str(a.get("rule") or "?"),
                sev if sev in RANK else "medium",
                title[:300],
                _rel(str(a.get("file") or "")),
                a.get("line") if isinstance(a.get("line"), int) else None,
            )
        )
    return out


PARSERS: dict[str, Callable[[str, str], list[Finding]]] = {
    "github-alerts": parse_github_alerts,
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
    # why these findings can't block (e.g. GitHub's alerts describe the default branch,
    # not this change): reported, never routed back
    advisory: str = ""

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(1 for f in self.findings if f.severity == s) for s in SEVERITIES}

    @property
    def blocking(self) -> list[Finding]:
        if self.fail_on == "none" or self.advisory:
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
            "advisory": self.advisory,
        }


def judge(
    tool: Tool, findings: list[Finding], fail_on: str, changed: list[str] | None = None, title: str = ""
) -> Report:
    """Apply the station's policy. With `changed` (a change to an existing repo), only
    findings in the files the change touched count: problems it didn't make are counted
    separately and never block it."""
    if fail_on not in FAIL_ON:
        raise ValueError(f"fail_on must be one of {FAIL_ON}")
    title = title or tool.title
    if changed is None:
        return Report(tool.id, title, fail_on, "all", list(findings))
    touched = set(changed)
    inside = [f for f in findings if f.file in touched]
    report = Report(tool.id, title, fail_on, "changed", inside, outside=len(findings) - len(inside))
    if tool.needs_repo:
        report.advisory = (
            f"{tool.title} describes the default branch, not this change; it scans the pull request once it's open"
        )
    return report


def catalogue(overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Tool]:
    """The built-in tools, with the config's additions and overrides (image, command …)."""
    out = dict(TOOLS)
    for tid, o in (overrides or {}).items():
        base = out.get(tid)
        fields = {**(base.__dict__ if base else {"id": tid, "pillar": "security"}), **o, "id": tid}
        out[tid] = Tool(**fields)
    return out
