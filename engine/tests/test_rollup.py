"""The assessment rollup (rollup.py): every station's findings in one deduplicated list,
recommendations consolidated and ranked, and scan coverage stated, never implied."""

from __future__ import annotations

from agent_factory import assess, rollup


def _rule(rule: str, sev: str, title: str, file: str, line: int | None = None, area: str = "security") -> dict:
    return {"rule": rule, "severity": sev, "area": area, "title": title, "detail": "", "file": file, "line": line}


def _scan(station: str, check: str, findings: list[dict], **extra) -> dict:
    return {"station": station, "check": check, "via": "tool", "provider": "local", "findings": findings,
            "counts": {}, **extra}  # fmt: skip


def _f(rule: str, sev: str, title: str, file: str, line: int | None = None) -> dict:
    return {"tool": "t", "rule": rule, "severity": sev, "title": title, "file": file, "line": line}


PLANNED = [
    {"station": "code-scan", "check": "code", "title": "Code scan"},
    {"station": "dependency-scan", "check": "dependencies", "title": "Dependency scan"},
    {"station": "config-scan", "check": "config", "title": "Config scan"},
]


def test_the_same_problem_from_two_stations_is_one_issue_with_both_sources() -> None:
    rules = [_rule("dockerfile-root", "medium", "Container runs as root", "Dockerfile", 1)]
    scans = [_scan("config-scan", "config", [_f("DS002", "high", "Container runs as root!", "Dockerfile", 3)])]
    [issue] = rollup.issues(rules, scans)
    assert issue["severity"] == "high", "the worst severity wins"
    assert issue["at"] == ["Dockerfile:1", "Dockerfile:3"] and issue["places"] == 2
    assert [(s["station"], s["rule"]) for s in issue["sources"]] == [("repo-scan", "dockerfile-root"),
                                                                     ("config-scan", "DS002")]  # fmt: skip


def test_issues_are_ranked_worst_first_and_numbered() -> None:
    scans = [
        _scan("code-scan", "code", [_f("a", "low", "Weak hash", "a.py")]),
        _scan("dependency-scan", "dependencies", [_f("CVE-1", "critical", "lodash RCE", "package-lock.json")]),
    ]
    found = rollup.issues([], scans)
    assert [(i["id"], i["severity"]) for i in found] == [("I1", "critical"), ("I2", "low")]
    assert rollup.counts(found)["critical"] == 1


def test_scan_findings_become_ranked_recommendations_with_evidence() -> None:
    scans = [
        _scan("dependency-scan", "dependencies", [_f("CVE-1", "critical", "lodash RCE", "package-lock.json")]),
        _scan("code-scan", "code", [_f("s1", "medium", "Unvalidated redirect", "app.js", 9)]),
    ]
    assessor = [{"title": "Add a CI workflow", "why": "No CI runs the tests.", "kind": "upkeep", "effort": "S"}]
    out = rollup.consolidate([], scans, assessor, PLANNED)
    recs = out["recommendations"]
    assert [(r["title"], r["severity"], r["impact"], r["source"]) for r in recs] == [
        ("Upgrade vulnerable dependencies", "critical", "high", "engine"),
        ("Fix insecure code found by the code scan", "medium", "medium", "engine"),
        ("Add a CI workflow", "medium", "medium", "assessor"),
    ]
    assert recs[0]["issues"] == ["I1"] and "lodash RCE (critical)" in recs[0]["why"]
    assert recs[2]["basis"] == "assessor judgement (not measured)"


def test_an_assessor_recommendation_that_covers_a_check_takes_its_evidence_no_duplicate() -> None:
    scans = [_scan("dependency-scan", "dependencies", [_f("CVE-1", "high", "Old express", "package.json")])]
    assessor = [{"title": "Upgrade Express and other outdated dependencies", "why": "Express 3 is end-of-life.",
                 "kind": "upkeep", "effort": "M"}]  # fmt: skip
    [rec] = rollup.recommendations(assessor, rollup.issues([], scans))
    assert (rec["source"], rec["severity"], rec["impact"], rec["issues"]) == ("assessor", "high", "high", ["I1"])
    assert rec["basis"] == "assessor, backed by the dependency scan"


def test_rule_findings_below_high_are_left_to_the_assessor() -> None:
    rules = [_rule("no-ci", "medium", "No CI", ".github", area="maintainability")]
    assert rollup.recommendations([], rollup.issues(rules, [])) == []
    rules.append(_rule("eol", "high", "Runtime is end-of-life", "Dockerfile", 1, area="reliability"))
    [rec] = rollup.recommendations([], rollup.issues(rules, []))
    assert (rec["title"], rec["basis"]) == ("Fix the high reliability findings", "factory rules")


def test_coverage_says_what_did_not_really_run() -> None:
    scans = [
        _scan("code-scan", "code", [], simulated=True),
        _scan(
            "dependency-scan",
            "dependencies",
            [],
            provider="agent",
            via="factory agent",
            fallback_from=[{"provider": "local", "reason": "osv-scanner did not run"}],
        ),  # fmt: skip
    ]
    cov = rollup.coverage(PLANNED, scans, secret_scan_ok=False)
    assert not cov["complete"]
    assert [c["status"] for c in cov["checks"]] == ["simulated", "agent", "not run"]
    assert "local could not answer: osv-scanner did not run" in cov["checks"][1]["note"]
    assert cov["gaps"] == [
        "Code scan was simulated: no real results.",
        "Dependency scan was answered by the factory agent, not a scanner.",
        "Config scan did not run: its check is not covered.",
        "The repo-scan secret scan did not complete.",
    ]


def test_a_clean_repo_with_every_scan_run_is_complete_and_keeps_the_assessor_list() -> None:
    scans = [_scan(p["station"], p["check"], []) for p in PLANNED]
    assessor = [{"title": "Document the API", "why": "No README section.", "kind": "feature", "effort": "S"}]
    out = rollup.consolidate([], scans, assessor, PLANNED, secret_scan_ok=True)
    assert out["issues"] == [] and out["coverage"] == {**out["coverage"], "complete": True, "gaps": []}
    assert [r["title"] for r in out["recommendations"]] == ["Document the API"]


def test_the_markdown_shows_coverage_issues_and_ranked_recommendations() -> None:
    scans = [_scan("dependency-scan", "dependencies", [_f("CVE-1", "critical", "lodash RCE", "package-lock.json")])]
    data = {
        "repo": {"slug": "o/r", "commit": "abc", "ref": "main", "files": 3},
        "stack_summary": "Node",
        "readiness": {"level": 1, "points": 2, "max_points": 9, "signals": []},
        "findings": [],
        "risks": [],
        "test_gaps": [],
        "assessed_at": "now",
        **rollup.consolidate([], scans, [], PLANNED[1:2], True),
    }
    md = assess.render_markdown(data)
    assert "**Issues across all stations:** 1 (1 critical) · all planned scans ran" in md
    assert "## Scan coverage" in md and "| Dependency scan | tool (tool) | " in md
    assert "**I1 · critical** · lodash RCE (`package-lock.json`)" in md and "found by dependency-scan via tool" in md
    assert "1. **Upgrade vulnerable dependencies** (upkeep, effort S, critical severity, high impact)" in md
