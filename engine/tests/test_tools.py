"""Tool stations (ADR-0034): parsers over each tool's real output format, and the policy."""

from __future__ import annotations

import json

import pytest

from agent_factory import tools

SARIF = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {
                "driver": {
                    "name": "Semgrep OSS",
                    "rules": [
                        {"id": "javascript.express.security.audit.xss.direct-response-write", "properties": {}},
                        {"id": "python.lang.security.audit.eval-detected", "properties": {"security-severity": 9.1}},
                    ],
                }
            },
            "results": [
                {
                    "ruleId": "javascript.express.security.audit.xss.direct-response-write",
                    "level": "warning",
                    "message": {"text": "Detected directly writing to a Response object"},
                    "locations": [
                        {"physicalLocation": {"artifactLocation": {"uri": "/src/app.js"}, "region": {"startLine": 30}}}
                    ],
                },
                {
                    "ruleId": "python.lang.security.audit.eval-detected",
                    "level": "error",
                    "message": {"text": "Detected the use of eval()"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "file:///src/tools/run.py"},
                                "region": {"startLine": 4},
                            }
                        }
                    ],
                },
            ],
        }
    ],
}

OSV = {
    "results": [
        {
            "source": {"path": "/src/package-lock.json", "type": "lockfile"},
            "packages": [
                {
                    "package": {"name": "express", "version": "4.17.1", "ecosystem": "npm"},
                    "vulnerabilities": [
                        {
                            "id": "GHSA-rv95-896h-c2vc",
                            "summary": "Express open redirect",
                            "database_specific": {"severity": "MODERATE"},
                        },
                        {"id": "CVE-2024-29041"},
                    ],
                    "groups": [{"ids": ["GHSA-rv95-896h-c2vc", "CVE-2024-29041"], "max_severity": "6.1"}],
                }
            ],
        }
    ]
}

GITLEAKS = [
    {
        "RuleID": "generic-api-key",
        "Description": "Detected a Generic API Key",
        "File": "/src/.env.example",
        "StartLine": 3,
        "Secret": "REDACTED",
        "Match": "API_KEY=REDACTED",
    }
]

TRIVY = {
    "SchemaVersion": 2,
    "Results": [
        {
            "Target": "Dockerfile",
            "Class": "config",
            "Misconfigurations": [
                {
                    "ID": "DS002",
                    "Title": "Image user should not be 'root'",
                    "Severity": "HIGH",
                    "Status": "FAIL",
                    "Resolution": "Add 'USER <non root user name>' line to the Dockerfile",
                    "CauseMetadata": {"StartLine": 1},
                },
                {"ID": "DS026", "Title": "No HEALTHCHECK defined", "Severity": "LOW", "Status": "FAIL"},
                {"ID": "DS001", "Title": "':latest' tag used", "Severity": "MEDIUM", "Status": "PASS"},
            ],
        },
        {
            "Target": "k8s/deployment.yaml",
            "Class": "config",
            "Misconfigurations": [
                {
                    "ID": "KSV012",
                    "Title": "Runs as root user",
                    "Severity": "MEDIUM",
                    "Status": "FAIL",
                    "CauseMetadata": {"StartLine": 18},
                }
            ],
        },
    ],
}


def test_sarif_findings_with_severity_and_repo_paths():
    out = tools.parse(tools.TOOLS["semgrep"], "Scanning 19 files...\n" + json.dumps(SARIF))
    assert [(f.rule, f.severity, f.file, f.line) for f in out] == [
        ("eval-detected", "critical", "tools/run.py", 4),  # security-severity 9.1
        ("direct-response-write", "medium", "app.js", 30),  # level warning
    ]


def test_osv_groups_aliases_into_one_finding():
    (f,) = tools.parse(tools.TOOLS["osv-scanner"], json.dumps(OSV))
    assert (f.rule, f.severity, f.file) == ("GHSA-rv95-896h-c2vc", "medium", "package-lock.json")
    assert f.title.startswith("express 4.17.1: Express open redirect") and "CVE-2024-29041" in f.detail


def test_gitleaks_keeps_no_part_of_the_secret():
    (f,) = tools.parse(tools.TOOLS["gitleaks"], json.dumps(GITLEAKS))
    assert (f.rule, f.severity, f.file, f.line) == ("generic-api-key", "high", ".env.example", 3)
    assert "REDACTED" not in json.dumps(f.as_data()) and "API_KEY" not in json.dumps(f.as_data())
    assert tools.parse(tools.TOOLS["gitleaks"], "[]") == [] and tools.parse(tools.TOOLS["gitleaks"], "") == []


def test_trivy_config_skips_passing_checks():
    out = tools.parse(tools.TOOLS["trivy-config"], json.dumps(TRIVY))
    assert [(f.rule, f.severity, f.file) for f in out] == [
        ("DS002", "high", "Dockerfile"),
        ("KSV012", "medium", "k8s/deployment.yaml"),
        ("DS026", "low", "Dockerfile"),
    ]


@pytest.mark.parametrize("tool", sorted(tools.TOOLS))
def test_output_that_is_not_a_report_means_the_tool_did_not_run(tool):
    if tool == "gitleaks":
        pytest.skip("no findings is an empty output for gitleaks")
    with pytest.raises(tools.ToolOutputError):
        tools.parse(tools.TOOLS[tool], "docker: Error response from daemon: pull access denied")


def test_policy_blocks_at_or_above_fail_on_and_warns_below():
    findings = tools.parse(tools.TOOLS["trivy-config"], json.dumps(TRIVY))
    t = tools.TOOLS["trivy-config"]
    assert not tools.judge(t, findings, "high").passed
    assert tools.judge(t, findings, "critical").passed
    assert tools.judge(t, findings, "none").passed and tools.judge(t, findings, "none").blocking == []
    r = tools.judge(t, findings, "high")
    assert r.counts["high"] == 1 and len(r.blocking) == 1
    assert r.headline() == "Trivy (Dockerfile, Kubernetes): 1 high, 1 medium, 1 low (1 at or above high)"
    with pytest.raises(ValueError):
        tools.judge(t, findings, "severe")


def test_a_change_to_a_repo_answers_only_for_the_files_it_touched():
    findings = tools.parse(tools.TOOLS["trivy-config"], json.dumps(TRIVY))
    r = tools.judge(tools.TOOLS["trivy-config"], findings, "high", changed=["k8s/deployment.yaml", "app.js"])
    assert r.passed and r.outside == 2 and [f.rule for f in r.findings] == ["KSV012"]
    assert "2 in files this change didn't touch" in r.headline()


def test_the_config_can_override_a_tool_or_add_one():
    cat = tools.catalogue(
        {
            "semgrep": {"image": "semgrep/semgrep@sha256:abc"},
            "my-sast": {
                "title": "Mine",
                "area": "security",
                "image": "me/sast:1",
                "command": "docker run --rm -v {path}:/src:ro {image} scan",
                "parser": "sarif",
            },
        }
    )
    assert cat["semgrep"].image == "semgrep/semgrep@sha256:abc" and cat["semgrep"].parser == "sarif"
    assert cat["my-sast"].title == "Mine" and cat["gitleaks"] == tools.TOOLS["gitleaks"]


# --------------------------------------------------------------- stations --
from dataclasses import dataclass, field  # noqa: E402

from conftest import PRODUCT, wait_run  # noqa: E402
from test_api import _client  # noqa: E402

from agent_factory.executor import CLEAN_SCANS, FakeExecutor  # noqa: E402
from agent_factory.models import ChangeStatus  # noqa: E402

CRITICAL = json.dumps(
    {
        "runs": [
            {
                "tool": {"driver": {"rules": [{"id": "eval", "properties": {"security-severity": 9.5}}]}},
                "results": [
                    {
                        "ruleId": "eval",
                        "level": "error",
                        "message": {"text": "eval() on user input"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "/src/app/main.py"},
                                    "region": {"startLine": 7},
                                }
                            }
                        ],
                    }
                ],
            }
        ]
    }
)


@dataclass
class ScanExecutor(FakeExecutor):
    """Scanners answer from `queue` (one answer per call, by substring), then clean."""

    queue: dict[str, list[str]] = field(default_factory=dict)

    async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN201
        for needle, answers in self.queue.items():
            if needle in command and answers:
                self.calls.append(command)
                from agent_factory.executor import CommandResult

                return CommandResult(command, 1, answers.pop(0))
        return await super().run(command, cwd, timeout, env)


def _live(make_factory, ex):
    f = make_factory(ex)
    f.settings.factory_mode = "live"
    return f


async def test_warnings_pass_and_are_kept_as_evidence(make_factory):
    ex = ScanExecutor(queue={" config --format json": [json.dumps(TRIVY)]})
    f = _live(make_factory, ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback  # default policy: fail on critical
    report = json.loads((f.manager.ws.data_dir / "artifacts" / change.id / "tools" / "iac.json").read_text())
    assert report["passed"] and report["counts"]["high"] == 1 and report["fail_on"] == "critical"
    msgs = [e.message for e in f.store.list_events(change.id)]
    assert any(m.startswith("Trivy (Dockerfile, Kubernetes): 1 high, 1 medium, 1 low (passed)") for m in msgs)
    assert any("raw output not kept" in m for m in msgs)
    app, ctx, c = await _client(f)
    async with c:
        views = (await c.get(f"/api/changes/{change.id}/tools")).json()
        assert [v["station"] for v in views] == ["sast", "dependencies", "iac"]
        assert views[2]["findings"][0]["rule"] == "DS002"
    await ctx.__aexit__(None, None, None)


async def test_a_blocking_finding_goes_back_to_the_developer(make_factory):
    ex = ScanExecutor(queue={"semgrep scan": [CRITICAL]})  # found once, fixed on the next attempt
    f = _live(make_factory, ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    events = f.store.list_events(change.id)
    routed = [e for e in events if e.message == "routing failure from sast to implement"]
    assert routed and "critical: app/main.py:7: eval() on user input" in routed[0].data["routed"]["evidence"]
    dev = [c for c in f.manager.agents.calls if c.role == "developer"]
    assert len(dev) == 2 and "eval() on user input" in dev[1].prompt


async def test_a_tool_that_does_not_run_holds_the_change(make_factory):
    ex = ScanExecutor(queue={"osv-scanner": ["docker: Error response from daemon: pull access denied"]})
    f = _live(make_factory, ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held
    assert "OSV-Scanner (dependencies) did not run" in f.store.get_change(change.id).summary


def test_scanners_answer_clean_in_the_fake_executor():
    for needle in CLEAN_SCANS:
        tool = next(t for t in tools.TOOLS.values() if needle in t.command.format(image=t.image, path="/w"))
        assert tools.parse(tool, CLEAN_SCANS[needle]) == []
