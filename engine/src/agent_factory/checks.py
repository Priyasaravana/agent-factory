"""Checks (ADR-0035): a scan station is named after what it checks, not the tool behind it.

A check (secrets, code, dependencies, config) is answered by a provider:
- `local`: an open-source tool from the catalogue (`tools.TOOLS`), run in dind; the default,
  free and deterministic;
- `github` / `snyk`: a service the team already has, reached with a token reference;
- `agent`: an observe-only agent reads the code. Lower confidence: it can't know today's
  advisories, so its findings are capped at `high` (a critical needs a tool's evidence),
  and each must cite a file that exists.

When the chosen provider can't answer (no token, not a repository, the tool didn't run),
the station falls back (by default to the agent) and says so in its report. Whatever the
provider, findings are normalised and judged by the same `fail_on` policy (`tools.judge`).
Everything here is pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_factory.tools import RANK, SEVERITIES, Finding, Tool


@dataclass(frozen=True)
class Check:
    id: str
    title: str  # the station's name people read
    looks_for: str  # one line, for the agent's prompt and the UI hint


CHECKS: dict[str, Check] = {
    c.id: c
    for c in (
        Check("secrets", "Secret scan", "credentials, keys and tokens committed to the code"),
        Check("code", "Code scan", "insecure code: injection, unsafe deserialisation, weak crypto, missing checks"),
        Check("dependencies", "Dependency scan", "vulnerable, unpinned or end-of-life dependencies"),
        Check("config", "Config scan", "risky Dockerfile, Kubernetes, Helm and IaC settings"),
    )
}
PROVIDERS = ("local", "github", "snyk", "agent")
FALLBACKS = ("agent", "none")
PROVIDER_TITLES = {"local": "open-source tool", "github": "GitHub", "snyk": "Snyk", "agent": "factory agent"}

# Which catalogue entry answers a check for each provider. `local` can be overridden per
# check in the config (`checks.local_tools`); a provider missing a check doesn't offer it.
PROVIDER_TOOLS: dict[str, dict[str, str]] = {
    "local": {"secrets": "gitleaks", "code": "semgrep", "dependencies": "osv-scanner", "config": "trivy-config"},
    "github": {
        "secrets": "github-secret-scanning",
        "code": "github-code-scanning",
        "dependencies": "github-dependabot",
    },
    "snyk": {"code": "snyk-code", "dependencies": "snyk-open-source", "config": "snyk-iac"},
}

AGENT_MAX_SEVERITY = "high"


def check_of(tool: Tool | None, tool_id: str = "") -> str:
    """The check a catalogue tool answers (stations saved before ADR-0035 name a tool)."""
    if tool and tool.check:
        return tool.check
    for by_check in PROVIDER_TOOLS.values():
        for check, tid in by_check.items():
            if tid == (tool.id if tool else tool_id):
                return check
    return ""


def title_of(check: str, tool_id: str = "") -> str:
    if check in CHECKS:
        return CHECKS[check].title
    return tool_id or "Scan"


@dataclass(frozen=True)
class Attempt:
    provider: str
    tool_id: str = ""  # empty for the agent


def plan(
    check: str,
    provider: str | None,
    tool_id: str | None,
    settings: dict[str, Any],
) -> list[Attempt]:
    """What the station tries, in order: the station's provider (or the factory's choice for
    this check), then the fallback. A station naming a `tool` (as before ADR-0035) runs that
    tool, i.e. local."""
    providers = settings.get("providers") or {}
    local_tools = settings.get("local_tools") or {}
    fallback = settings.get("fallback", "agent")
    if tool_id and not provider:
        first = Attempt("local", tool_id)
    else:
        chosen = provider or providers.get(check, "local")
        if chosen == "agent":
            first = Attempt("agent")
        elif chosen == "local":
            first = Attempt("local", local_tools.get(check) or PROVIDER_TOOLS["local"].get(check, ""))
        else:
            first = Attempt(chosen, PROVIDER_TOOLS.get(chosen, {}).get(check, ""))
    out = [first]
    if fallback == "agent" and first.provider != "agent":
        out.append(Attempt("agent"))
    return out


# ------------------------------------------------------------------ agent --
AGENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "title": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "rule": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "title", "file"],
            },
        },
    },
    "required": ["summary", "findings"],
}

_AGENT_LIMITS = {
    "dependencies": (
        "You cannot see today's vulnerability advisories. Don't name CVE or GHSA ids unless the "
        "repository itself mentions them. Report what the files show: unpinned or floating "
        "versions, missing lockfiles, very old major versions, end-of-life runtimes."
    ),
    "secrets": (
        "Never copy a secret's value into your answer: name the file, the line and what kind of "
        "credential it looks like. Test fixtures and documented example values are not findings."
    ),
}


def agent_prompt(check: str, paths: list[str], changed: list[str] | None, reason: str = "") -> str:
    c = CHECKS.get(check, Check(check, check, check))
    scope = (
        "Only the files this change touched count:\n" + "\n".join(f"- {p}" for p in changed[:200])
        if changed is not None
        else "\n".join(paths[:300]) + (f"\n… and {len(paths) - 300} more" if len(paths) > 300 else "")
    )
    why = f"\n(The configured provider couldn't answer: {reason}.)\n" if reason else ""
    return f"""{c.title}: look for {c.looks_for}.
{why}
You are observe-only: read files; never change, build, install or run anything. A tool may
be unavailable here, so you are the check: be concrete and verifiable. Every finding names a
real file (and line when you can) and says how to fix it. No speculation: if you're unsure,
leave it out. {_AGENT_LIMITS.get(check, "")}

Severity: critical is reserved for tools; use high for exploitable problems, medium for
weaknesses, low for hardening.

## Files
{scope}"""


def judge_agent(tool_id: str, report: dict[str, Any], paths: list[str]) -> tuple[list[Finding], list[str]]:
    """The agent's findings the repository supports: a real file, a known severity, capped
    at `high`. Returns the findings kept and a note for each one left out."""
    known = set(paths)
    kept: list[Finding] = []
    dropped: list[str] = []
    cap = RANK[AGENT_MAX_SEVERITY]
    for f in report.get("findings") or []:
        if not isinstance(f, dict):
            continue
        file = str(f.get("file") or "").removeprefix("./").split(":")[0]
        title = str(f.get("title") or "")[:300]
        if file not in known:
            dropped.append(f"{title or '?'}: cites '{file}', which isn't in the repository")
            continue
        sev = str(f.get("severity") or "medium").lower()
        sev = sev if sev in RANK else "medium"
        if RANK[sev] < cap:
            sev = AGENT_MAX_SEVERITY
        line = f.get("line")
        kept.append(
            Finding(
                tool_id,
                str(f.get("rule") or "agent")[:80],
                sev,
                title,
                file,
                line if isinstance(line, int) and line > 0 else None,
                str(f.get("fix") or "")[:300],
            )
        )
    return sorted(kept, key=lambda x: (RANK[x.severity], x.file, x.line or 0)), dropped


AGENT_TOOL = Tool("agent", "factory agent", "review", "", "", "", check="", provider="agent")
