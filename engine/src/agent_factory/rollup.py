"""One view of an assessment across every station that looked at the repo (pure).

The report station runs after the scans; this module folds what each station found into
deduplicated issues, consolidated recommendations and a coverage statement:

- **issues**: repo-scan rule findings and every scan station's findings, merged when they
  describe the same problem (same normalised title) and keeping every place and every source
  (station, provider, rule) as evidence;
- **recommendations**: the assessor's recommendations plus one per scan check (and per rule
  area with high findings) the assessor did not already ask for, each classified by severity
  and impact and tied to the issues behind it (`source`: assessor | engine, with its `basis`);
- **coverage**: which checks ran, how (tool, agent, fallback, simulated) and what that leaves
  out, so an incomplete scan is never read as a clean one.

Nothing here calls a model: the same inputs always give the same report.
"""

from __future__ import annotations

import re
from typing import Any

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}
MAX_LOCATIONS = 20  # per issue, in the report; the station evidence keeps them all

CHECK_TITLES = {
    "secrets": "Secret scan",
    "code": "Code scan",
    "dependencies": "Dependency scan",
    "config": "Config scan",
}
# what a check's findings ask for, as a change the factory can make (kind, title, keywords
# that show an assessor recommendation already covers it)
_CHECK_FIX: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "secrets": ("bug", "Remove committed secrets and rotate them", ("secret", "credential", "token", "api key")),
    "code": ("bug", "Fix insecure code found by the code scan", ("injection", "insecure code", "code scan", "sast")),
    "dependencies": (
        "upkeep",
        "Upgrade vulnerable dependencies",
        ("dependenc", "vulnerab", "cve", "upgrade", "outdated", "end-of-life", "eol"),
    ),
    "config": (
        "upkeep",
        "Harden container and deployment config",
        ("dockerfile", "config", "kubernetes", "helm", "iac"),
    ),
}


def rank(severity: str) -> int:
    return _RANK.get(severity, len(SEVERITY_ORDER))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _mentions(rec: dict[str, Any], words: tuple[str, ...]) -> bool:
    """Whether a recommendation's title or reason names one of the words (at a word start)."""
    text = _norm(f"{rec.get('title', '')} {rec.get('why', '')}")
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def _at(f: dict[str, Any]) -> str:
    file = str(f.get("file") or "")
    return file + (f":{f['line']}" if f.get("line") else "") if file else ""


def issues(rule_findings: list[dict[str, Any]], tool_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every finding once: merged by normalised title, worst severity wins, all places and sources kept."""
    merged: dict[str, dict[str, Any]] = {}

    def add(f: dict[str, Any], category: str, source: dict[str, str]) -> None:
        title = str(f.get("title") or f.get("rule") or "?")
        key = _norm(title) or _norm(str(f.get("rule") or "?"))
        sev = str(f.get("severity") or "info")
        issue = merged.setdefault(
            key,
            {
                "id": "",
                "title": title,
                "severity": sev,
                "category": category,
                "detail": str(f.get("detail") or ""),
                "at": [],
                "sources": [],
            },
        )
        if rank(sev) < rank(issue["severity"]):
            issue["severity"] = sev
        if not issue["detail"] and f.get("detail"):
            issue["detail"] = str(f["detail"])
        loc = _at(f)
        if loc and loc not in issue["at"]:
            issue["at"].append(loc)
        if source not in issue["sources"]:
            issue["sources"].append(source)

    for f in rule_findings:
        src = {"station": "repo-scan", "via": "factory rules", "rule": str(f.get("rule") or "")}
        add(f, f"rules:{f.get('area') or 'other'}", src)
    for r in tool_reports:
        category = str(r.get("check") or r.get("tool") or "scan")
        for f in r.get("findings") or []:
            src = {"station": str(r.get("station") or ""), "via": str(r.get("via") or r.get("tool") or ""),
                   "rule": str(f.get("rule") or "")}  # fmt: skip
            add(f, category, src)
    out = sorted(merged.values(), key=lambda i: (rank(i["severity"]), -len(i["at"]), i["title"]))
    for n, i in enumerate(out, 1):
        i["id"] = f"I{n}"
        i["places"] = len(i["at"])
        i["at"] = i["at"][:MAX_LOCATIONS]
    return out


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {s: sum(1 for i in items if i["severity"] == s) for s in SEVERITY_ORDER}


def _impact(severity: str, n_issues: int, places: int) -> str:
    """How much fixing it matters: severity first, then how widespread it is."""
    if rank(severity) <= rank("high") or (severity == "medium" and places >= 10):
        return "high"
    if severity == "medium" or n_issues >= 5:
        return "medium"
    return "low"


def _effort(places: int) -> str:
    return "S" if places <= 3 else "M" if places <= 15 else "L"


def recommendations(assessor: list[dict[str, Any]], all_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The assessor's recommendations and one per scan check (or rule area with high
    findings) they don't cover, ranked by severity then impact. Each carries the issue ids
    behind it; an assessor recommendation that covers a check takes that check's evidence."""
    by_category: dict[str, list[dict[str, Any]]] = {}
    for i in all_issues:
        if i["severity"] == "info":
            continue
        by_category.setdefault(i["category"], []).append(i)

    out: list[dict[str, Any]] = []
    for raw in assessor:
        rec = {**raw, "source": "assessor", "severity": "medium", "impact": "medium", "issues": [],
               "basis": "assessor judgement (not measured)"}  # fmt: skip
        out.append(rec)

    for category, items in by_category.items():
        worst = min((i["severity"] for i in items), key=rank)
        places = sum(i["places"] for i in items)
        ids = [i["id"] for i in items]
        if category in _CHECK_FIX:
            kind, title, words = _CHECK_FIX[category]
            basis = CHECK_TITLES[category]
            match = next((r for r in out if r["source"] == "assessor" and _mentions(r, words)), None)
            if match is not None:  # the assessor already asked for it: back it with the evidence
                if rank(worst) < rank(match["severity"]):
                    match["severity"] = worst
                match["impact"] = _impact(match["severity"], len(items), places)
                match["issues"] = sorted({*match["issues"], *ids}, key=lambda x: int(x[1:]))
                match["basis"] = f"assessor, backed by the {basis.lower()}"
                continue
        elif rank(worst) <= rank("high"):
            kind, title, basis = "upkeep", f"Fix the high {category.split(':', 1)[1]} findings", "factory rules"
        else:
            continue  # rule findings below high are left to the assessor's judgement
        top = ", ".join(f"{i['title']} ({i['severity']})" for i in items[:3])
        more = f" and {len(items) - 3} more" if len(items) > 3 else ""
        out.append(
            {
                "title": title,
                "why": f"{len(items)} issue(s) in {places} place(s): {top}{more}.",
                "kind": kind,
                "effort": _effort(places),
                "source": "engine",
                "severity": worst,
                "impact": _impact(worst, len(items), places),
                "issues": ids,
                "basis": basis,
            }
        )
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: (rank(r["severity"]), order.get(r["impact"], 3), r["source"] != "engine"))
    return out


def coverage(
    planned: list[dict[str, str]], tool_reports: list[dict[str, Any]], secret_scan_ok: bool | None
) -> dict[str, Any]:
    """What ran and what that leaves out. `planned` lists the lane's scan stations
    (`station`, `check`, `title`); a station without a report did not run."""
    by_station = {str(r.get("station") or ""): r for r in tool_reports}
    checks: list[dict[str, Any]] = []
    gaps: list[str] = []
    for p in planned:
        r = by_station.get(p["station"])
        title = p.get("title") or CHECK_TITLES.get(p.get("check", ""), p["station"])
        if r is None:
            checks.append({**p, "title": title, "status": "not run", "note": "no report"})
            gaps.append(f"{title} did not run: its check is not covered.")
            continue
        notes: list[str] = []
        status = "tool"
        if r.get("simulated"):
            status = "simulated"
            notes.append("simulated (dry-run): nothing was scanned")
            gaps.append(f"{title} was simulated: no real results.")
        elif r.get("provider") == "agent":
            status = "agent"
            notes.append("checked by the factory agent: lower confidence than a scanner, severity capped at high")
            gaps.append(f"{title} was answered by the factory agent, not a scanner.")
        for t in r.get("fallback_from") or []:
            notes.append(f"{t.get('provider')} could not answer: {t.get('reason')}")
        if r.get("truncated"):
            notes.append("only the first findings were kept (counts cover all)")
            gaps.append(f"{title} found more issues than the report lists.")
        if r.get("advisory"):
            notes.append(str(r["advisory"]))
        checks.append({**p, "title": title, "status": status, "via": r.get("via", ""),
                       "counts": r.get("counts", {}), "note": "; ".join(notes)})  # fmt: skip
    if secret_scan_ok is False:
        gaps.append("The repo-scan secret scan did not complete.")
    return {"complete": not gaps, "checks": checks, "gaps": gaps}


def consolidate(
    rule_findings: list[dict[str, Any]],
    tool_reports: list[dict[str, Any]],
    assessor_recommendations: list[dict[str, Any]],
    planned_scans: list[dict[str, str]],
    secret_scan_ok: bool | None = None,
) -> dict[str, Any]:
    found = issues(rule_findings, tool_reports)
    return {
        "issues": found,
        "issue_counts": counts(found),
        "recommendations": recommendations(assessor_recommendations, found),
        "coverage": coverage(planned_scans, tool_reports, secret_scan_ok),
    }
