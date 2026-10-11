"""Scan stations named after their check, answered by a chosen provider (ADR-0035)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from conftest import PRODUCT, wait_run
from test_repo_changes import TOKEN, GitHubExecutor, _assessed, _factory, _origin
from test_tools import CRITICAL, ScanExecutor

from agent_factory import checks, tools
from agent_factory.agents import FakeAgentRunner
from agent_factory.engine.workflows import WorkflowError
from agent_factory.executor import CommandResult
from agent_factory.models import ChangeKind, ChangeStatus
from agent_factory.workflow import AgentSpec, WorkflowStation

WF = "fastapi-service"
SNYK = "snyk_never_in_logs_0987654321"  # noqa: S105 - a test value


def _report(f, change_id: str, station: str) -> dict:
    return json.loads((f.manager.ws.data_dir / "artifacts" / change_id / "tools" / f"{station}.json").read_text())


def _leaks(f, change_id: str, value: str, ex) -> list[str]:  # noqa: ANN001
    """Every place a secret value must never appear: commands, events, evidence."""
    out = [c for c in ex.calls if value in c]
    out += [e.message for e in f.store.list_events(change_id) if value in json.dumps(e.model_dump(mode="json"))]
    folder = f.manager.ws.data_dir / "artifacts" / change_id
    out += [str(p) for p in folder.rglob("*") if p.is_file() and value in p.read_text(errors="replace")]
    return out


# ------------------------------------------------------------------- pure --
def test_the_station_is_named_after_its_check_whoever_answers():
    for check, title in [
        ("secrets", "Secret scan"),
        ("code", "Code scan"),
        ("dependencies", "Dependency scan"),
        ("config", "Config scan"),
    ]:
        for provider in (None, "local", "github", "snyk", "agent"):
            s = WorkflowStation(id="x", kind="check", handler="scan", check=check, provider=provider)
            assert s.label() == title
    # a station saved before ADR-0035 names a tool: it reads as its check, and runs that tool
    legacy = WorkflowStation(id="sast", kind="check", handler="tool", tool="semgrep", fail_on="critical")
    assert (legacy.resolved_handler(), legacy.check_name(), legacy.label()) == ("scan", "code", "Code scan")
    assert checks.plan("code", None, "semgrep", {}) == [checks.Attempt("local", "semgrep"), checks.Attempt("agent")]


def test_the_provider_is_the_factorys_choice_unless_the_station_overrides_it():
    cfg = {"providers": {"code": "snyk", "secrets": "github"}, "local_tools": {"config": "my-scanner"}}
    assert checks.plan("code", None, None, cfg)[0] == checks.Attempt("snyk", "snyk-code")
    assert checks.plan("secrets", None, None, cfg)[0] == checks.Attempt("github", "github-secret-scanning")
    assert checks.plan("config", None, None, cfg)[0] == checks.Attempt("local", "my-scanner")
    assert checks.plan("dependencies", None, None, cfg)[0] == checks.Attempt("local", "osv-scanner")
    assert checks.plan("code", "local", None, cfg)[0] == checks.Attempt("local", "semgrep")  # station wins
    assert checks.plan("secrets", "snyk", None, cfg)[0] == checks.Attempt("snyk", "")  # Snyk has no secrets check
    # the agent is the fallback, unless it already answers or the factory said none
    assert checks.plan("code", "agent", None, cfg) == [checks.Attempt("agent")]
    assert len(checks.plan("code", None, None, {**cfg, "fallback": "none"})) == 1


def test_every_provider_tool_answers_its_check_and_parses_clean_output():
    for provider, by_check in checks.PROVIDER_TOOLS.items():
        for check, tid in by_check.items():
            tool = tools.TOOLS[tid]
            assert (tool.check, tool.provider) == (check, provider)
            assert check in checks.CHECKS
    # services get their token by name, never in the command
    for tid in ("snyk-code", "snyk-open-source", "snyk-iac"):
        assert "-e SNYK_TOKEN " in tools.TOOLS[tid].command and tools.TOOLS[tid].credential_env == "SNYK_TOKEN"
    for tid in ("github-code-scanning", "github-dependabot", "github-secret-scanning"):
        t = tools.TOOLS[tid]
        assert t.needs_repo and t.strict_exit and t.credential_env == "GH_TOKEN"
        assert "repos/acme/app/" in t.command.format(image="", path="/w", repo="acme/app")


def test_github_alerts_keep_rule_severity_and_place_only():
    alerts = [
        {"rule": "js/sql-injection", "severity": "critical", "title": "SQL injection", "tool": "CodeQL",
         "file": "src/db.js", "line": 12},
        {"rule": "js/unused-local-variable", "severity": "note", "title": "Unused variable", "file": "./app.js"},
        {"rule": "GHSA-xxxx", "severity": "moderate", "title": "express: open redirect", "file": "package-lock.json"},
        {"rule": "github_personal_access_token", "severity": "high", "title": "possible secret: GitHub PAT"},
    ]  # fmt: skip
    found = tools.parse(tools.TOOLS["github-code-scanning"], json.dumps(alerts))
    assert [(f.severity, f.file, f.line) for f in found] == [
        ("critical", "src/db.js", 12),
        ("high", "", None),
        ("medium", "package-lock.json", None),
        ("low", "app.js", None),
    ]
    assert found[0].title == "SQL injection (CodeQL)"
    with pytest.raises(tools.ToolOutputError):
        tools.parse(tools.TOOLS["github-dependabot"], '{"message": "Not Found", "status": "404"}')


def test_github_alerts_inform_a_pull_request_but_never_block_it():
    tool = tools.TOOLS["github-code-scanning"]
    found = [tools.Finding(tool.id, "r", "critical", "bad", "app.js")]
    report = tools.judge(tool, found, "high", changed=["app.js"], title="Code scan")
    assert report.findings and report.passed and report.advisory
    assert not tools.judge(tool, found, "high", title="Code scan").passed  # an assessment or new app: it counts


def test_the_agents_findings_must_cite_real_files_and_cannot_be_critical():
    report = {
        "summary": "two issues",
        "findings": [
            {"severity": "critical", "title": "shell injection", "file": "./app.js:14", "line": 14, "fix": "quote"},
            {"severity": "medium", "title": "imagined", "file": "nope.js"},
            {"severity": "loud", "title": "odd severity", "file": "Dockerfile"},
        ],
    }
    kept, dropped = checks.judge_agent("agent", report, ["app.js", "Dockerfile"])
    assert [(f.severity, f.file, f.line) for f in kept] == [("high", "app.js", 14), ("medium", "Dockerfile", None)]
    assert dropped == ["imagined: cites 'nope.js', which isn't in the repository"]
    prompt = checks.agent_prompt("dependencies", ["package.json"], None, reason="no token")
    assert "Dependency scan" in prompt and "Don't name CVE" in prompt
    assert "no token" in prompt and "package.json" in prompt


def test_scan_stations_must_say_what_they_check_and_their_agent_only_reads(make_factory):
    d = make_factory().workflows[WF].draft
    at = [s.id for s in d.get()[1].stations].index("code-review")  # in the verify column (ADR-0037)
    d.add_station(WorkflowStation(id="extra-scan", kind="check", handler="scan"), position=at)
    assert any("must say what it checks" in p for p in d.problems())
    d.update_station("extra-scan", {"check": "secrets", "provider": "github"})
    assert d.problems() == []
    with pytest.raises(WorkflowError, match="provider must be one of"):
        d.update_station("extra-scan", {"provider": "sonar"})
    d.update_station("extra-scan", {"agent": "developer"})
    assert any("must be observe-only" in p for p in d.problems())
    d.update_station("extra-scan", {"agent": "checker"})
    assert d.problems() == []


def test_the_checker_agent_is_observe_only_in_every_template():
    from test_station_phases import DEFAULT

    from agent_factory.workflow import load_workflow_dir

    for path in (DEFAULT,):  # the standard lane (ADR-0037)
        doc = load_workflow_dir(path)
        scans = [s for s in doc.stations if s.resolved_handler() == "scan"]
        assert scans and all(s.agent == "checker" and s.check for s in scans)
        spec: AgentSpec = doc.agents["checker"]
        assert spec.observe_only and spec.effective_tools() == ["Read", "Glob", "Grep"]


# --------------------------------------------------------------- stations --
@dataclass
class EnvScanExecutor(ScanExecutor):
    """Scanner answers from `queue`, recording the environment each command got."""

    envs: list[dict[str, str]] = field(default_factory=list)
    exits: dict[str, int] = field(default_factory=dict)

    async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN201
        self.envs.append(dict(env or {}))
        for needle, code in self.exits.items():
            if needle in command:
                self.calls.append(command)
                return CommandResult(command, code, "gh: Resource not accessible by integration (HTTP 403)")
        return await super().run(command, cwd, timeout, env)


def _live(make_factory, ex, agents=None):  # noqa: ANN001
    f = make_factory(ex, agents)
    f.settings.factory_mode = "live"
    return f


async def test_snyk_answers_with_its_token_only_in_the_environment(make_factory, monkeypatch):
    medium = json.loads(CRITICAL)
    medium["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["security-severity"] = 5.0
    ex = EnvScanExecutor(queue={"snyk code test": [json.dumps(medium)]})
    f = _live(make_factory, ex)
    f.manager.cfg.checks.providers = {"code": "snyk"}
    monkeypatch.setenv("SNYK_TOKEN", SNYK)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    report = _report(f, change.id, "code-scan")
    assert (report["title"], report["provider"], report["via"], report["counts"]["medium"]) == (
        "Code scan",
        "snyk",
        "Snyk Code",
        1,
    )
    assert any("snyk code test" in c and "-e SNYK_TOKEN " in c for c in ex.calls)
    assert any(e.get("SNYK_TOKEN") == SNYK for e in ex.envs)
    assert _leaks(f, change.id, SNYK, ex) == []
    msgs = [e.message for e in f.store.list_events(change.id)]
    assert "used secret env://SNYK_TOKEN for Code scan (Snyk Code)" in msgs


async def test_without_a_token_the_check_falls_back_to_the_agent_and_says_why(make_factory, monkeypatch):
    ex = EnvScanExecutor()
    f = _live(make_factory, ex)
    f.manager.cfg.checks.providers = {"code": "snyk", "dependencies": "github"}
    monkeypatch.delenv("SNYK_TOKEN", raising=False)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    code, deps = _report(f, change.id, "code-scan"), _report(f, change.id, "dependency-scan")
    assert (code["provider"], code["confidence"]) == ("agent", "agent")
    assert code["fallback_from"] == [
        {"provider": "snyk", "tool": "snyk-code", "reason": "no token: set env://SNYK_TOKEN"}
    ]
    assert deps["fallback_from"][0]["reason"] == "the product has no repository on GitHub yet"  # a new app
    msgs = [e.message for e in f.store.list_events(change.id)]
    assert "Code scan: Snyk can't answer: no token: set env://SNYK_TOKEN; trying the factory agent" in msgs
    assert not any("snyk" in c for c in ex.calls)


async def test_the_agent_as_provider_is_judged_like_a_tool(make_factory):
    agents = FakeAgentRunner(
        scan_findings=[
            {"severity": "critical", "title": "runs as root", "file": "app/main.py", "line": 3},
            {"severity": "high", "title": "imagined", "file": "k8s/nope.yaml"},
        ]
    )
    ex = EnvScanExecutor()
    f = _live(make_factory, ex, agents)
    f.manager.cfg.checks.providers = {"config": "agent"}
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    # the default lane fails on critical; the agent's critical is capped at high, so it warns
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    report = _report(f, change.id, "config-scan")
    assert (report["provider"], report["fallback_from"], report["passed"]) == ("agent", [], True)
    assert [(x["severity"], x["file"]) for x in report["findings"]] == [("high", "app/main.py")]
    assert len(report["dropped"]) == 1
    assert not any(" config --format json" in c for c in ex.calls)  # Trivy never ran


async def test_github_alerts_on_a_pull_request_are_for_information(make_factory, tmp_path, monkeypatch):
    @dataclass
    class GitHubScans(GitHubExecutor):
        envs_seen: list[dict[str, str]] = field(default_factory=list)

        async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN201
            if "/code-scanning/alerts" in command:
                self.calls.append(command)
                self.envs_seen.append(dict(env or {}))
                alerts = [
                    {"rule": "js/xss", "severity": "high", "title": "XSS", "file": "app.js", "line": 30},
                    {"rule": "js/redos", "severity": "high", "title": "ReDoS", "file": "package.json"},
                ]
                return CommandResult(command, 0, json.dumps(alerts))
            return await super().run(command, cwd, timeout, env)

    ex = GitHubScans()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    product = await _assessed(f)
    f.manager.settings.factory_mode = "live"
    f.manager.cfg.checks.providers = {"code": "github"}
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)  # touches package.json + test/
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback  # a high in its file, yet not blocked
    assert any(c.startswith("gh api -X GET repos/acme/hello-node/code-scanning/alerts") for c in ex.calls)
    assert ex.envs_seen and all(e.get("GH_TOKEN") == TOKEN for e in ex.envs_seen)
    report = _report(f, change.id, "code-scan")
    assert (report["provider"], report["via"], report["passed"], report["outside"]) == (
        "github",
        "GitHub code scanning",
        True,
        1,
    )
    assert [x["file"] for x in report["findings"]] == ["package.json"] and "default branch" in report["advisory"]
    body = (f.manager.ws.data_dir / "artifacts" / change.id / "pull-request.md").read_text()
    assert "- **Code scan** via GitHub code scanning: 1 high in the changed files (for information)" in body
    assert _leaks(f, change.id, TOKEN, ex) == []


async def test_a_github_feature_that_is_off_falls_back(make_factory, tmp_path, monkeypatch):
    from test_existing_repos import URL

    from agent_factory.models import OnboardRepoInput

    ex = EnvScanExecutor(exits={"/code-scanning/alerts": 1})  # e.g. code scanning not enabled on the repo
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    f.manager.settings.factory_mode = "live"
    f.manager.cfg.checks.providers = {"code": "github", "dependencies": "github"}
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)
    change = f.manager.start_change(f.manager.onboard_repo(OnboardRepoInput(repo_url=URL)))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    code, deps = _report(f, change.id, "code-scan"), _report(f, change.id, "dependency-scan")
    assert code["provider"] == "agent" and "GitHub code scanning failed (exit 1)" in code["fallback_from"][0]["reason"]
    # an assessment: Dependabot's alerts are the answer (empty here), not advisory
    assert (deps["provider"], deps["via"], deps["advisory"], deps["passed"]) == (
        "github",
        "GitHub Dependabot",
        "",
        True,
    )
    assert any(c.startswith("gh api -X GET repos/acme/hello-node/dependabot/alerts") for c in ex.calls)
    assert _leaks(f, change.id, TOKEN, ex) == []
