"""Changes to existing repositories, delivered as pull requests (ADR-0033)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import wait_run
from test_existing_repos import NODE_APP, URL, _factory, _git, _origin

from agent_factory import repo_change
from agent_factory.agents import FakeAgentRunner
from agent_factory.engine.pipeline import InvalidRequestError
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import ChangeKind, ChangeStatus, OnboardRepoInput

TOKEN = "ghp_never_in_logs_1234567890"  # noqa: S105 - a test value


async def _assessed(f, **kw):
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL, **kw))
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    return f.store.get_product(product.id)


def _events(f, change_id: str) -> list[str]:
    return [e.message for e in f.store.list_events(change_id)]


# ----------------------------------------------------------------- pure --
def test_the_repos_own_checks_are_found(tmp_path):
    def repo(files: dict[str, str]) -> Path:
        d = tmp_path / str(len(list(tmp_path.iterdir())))
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        d.mkdir(exist_ok=True)
        return d

    assert repo_change.test_commands(repo(NODE_APP)) == []  # `npm start` only: no checks
    npm = repo({"package.json": json.dumps({"scripts": {"test": "jest"}}), "package-lock.json": "{}"})
    assert repo_change.test_commands(npm) == ["npm ci && npm test"]
    default = repo({"package.json": json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})})
    assert repo_change.test_commands(default) == []
    make = repo({"Makefile": "build:\n\techo\ntest:\n\tpytest\n", "tests/test_x.py": "", "pyproject.toml": ""})
    assert repo_change.test_commands(make) == ["make test", "uv run --with pytest pytest -q"]


def test_only_serious_findings_on_changed_files_block():
    report = {
        "summary": "mostly fine",
        "findings": [
            {"severity": "major", "file": "app.js", "message": "crashes on empty input"},
            {"severity": "blocker", "file": "./legacy.js:10", "message": "old bug, not in this change"},
            {"severity": "minor", "file": "app.js", "message": "naming"},
        ],
    }
    v = repo_change.judge_review(report, ["app.js", "test/app.test.js"])
    assert not v.passed and v.blocking == ["major: app.js: crashes on empty input"]
    assert len(v.notes) == 2
    assert repo_change.judge_review({"summary": "ok", "findings": []}, ["app.js"]).passed


def test_pull_request_text_carries_the_evidence():
    body = repo_change.pr_body(
        kind="upkeep",
        request="Add unit tests and CI",
        requested_by="ada",
        summary="Added node:test tests and a CI workflow.",
        notes=["consider coverage later"],
        test={"command": "npm install && npm test", "ok": True},
        review={"summary": "looks good", "notes": ["minor: app.js: naming"]},
        risk={"holds": 0, "findings": []},
        change_id="abc123",
        files=["package.json", "test/app.test.js"],
    )
    for text in (
        "ada",
        "> Add unit tests and CI",
        "`npm install && npm test` → passed",
        "no risky changes",
        "minor: app.js: naming",
        "never merges",
    ):
        assert text in body
    assert repo_change.pr_title("bug", "tags with spaces are dropped") == "fix: tags with spaces are dropped"
    assert len(repo_change.pr_title("feature", "x" * 200)) == 72


# -------------------------------------------------------------- dry-run --
async def test_an_upkeep_change_becomes_a_pull_request(make_factory, tmp_path):
    origin = _origin(tmp_path)
    before = _git(origin, "rev-parse", "HEAD").strip()
    f = _factory(make_factory, origin)
    product = await _assessed(f)
    with pytest.raises(InvalidRequestError):
        f.manager.feedback(product.id, "x", ChangeKind.new)
    change = f.manager.feedback(product.id, "Add unit tests and a CI workflow", ChangeKind.upkeep)
    assert (change.kind, change.workflow_id) == (ChangeKind.upkeep, "existing-repo-change")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    change = f.store.get_change(change.id)
    assert "pull request simulated (dry-run): factory/" in change.summary and "→ trunk" in change.summary

    out = f.manager.ws.data_dir / "artifacts" / change.id
    assert json.loads((out / "repo-test.json").read_text())["command"] == "npm install --no-audit --no-fund && npm test"
    risk = json.loads((out / "change-risk.json").read_text())
    assert risk["base"] == "origin/trunk" and risk["files_changed"] == 2
    body = (out / "pull-request.md").read_text()
    assert body.startswith("# chore: Add unit tests and a CI workflow") and "`test/app.test.js`" in body
    stations = [e.station for e in f.store.list_events(change.id) if e.message.endswith("started (attempt 1)")]
    assert stations == [
        "onboard", "implement", "test", "secrets", "sast", "dependencies", "iac", "review", "change-risk",
        "pull-request",
    ]  # fmt: skip

    # the team's repository is untouched: the change only leaves as a pull request
    assert _git(origin, "rev-parse", "HEAD").strip() == before
    assert _git(origin, "branch", "--format=%(refname:short)").split() == ["trunk"]


async def test_a_change_without_tests_goes_back_to_the_developer(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    f.manager.agents = FakeAgentRunner(repo_no_tests=True)
    product = await _assessed(f)
    change = f.manager.feedback(product.id, "Rename the greeting", ChangeKind.feature)
    assert await wait_run(f, change.id) == ChangeStatus.held
    msgs = _events(f, change.id)
    assert any("routing failure from test to implement" in m for m in msgs)
    assert any("no test command in the repository" in m for m in msgs)


async def test_a_repo_must_be_assessed_first(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    with pytest.raises(InvalidRequestError, match="first change"):
        f.manager.start_change(product, kind=ChangeKind.feature)


# ----------------------------------------------------------------- live --
@dataclass
class GitHubExecutor(FakeExecutor):
    """Fake commands, with `gh pr create` answering like GitHub; records each env."""

    envs: list[dict[str, str]] = field(default_factory=list)

    async def run(self, command, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN201
        self.envs.append(dict(env or {}))
        res = await super().run(command, cwd, timeout, env)
        if res.ok and command.startswith("gh pr create"):
            return CommandResult(command, 0, "Creating pull request\nhttps://github.com/acme/hello-node/pull/7\n")
        return res


async def test_live_opens_the_pr_with_the_token_only_in_the_environment(make_factory, tmp_path, monkeypatch):
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    product = await _assessed(f)
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    change = f.store.get_change(change.id)
    assert change.pr_url == "https://github.com/acme/hello-node/pull/7"
    branch = f"factory/{change.id}"
    cmds = [c for c in ex.calls if c.startswith(("gh ", "git push", "gh auth"))]
    assert f"git push https://github.com/acme/hello-node.git HEAD:refs/heads/{branch}" in cmds[0]
    assert cmds[1].startswith(f"gh pr create --repo acme/hello-node --base trunk --head {branch}")
    assert "statuses/" in cmds[2] and "context=agent-factory/change-risk" in cmds[2]
    # the token reached the commands through the environment, never the command line or the log
    assert any(e.get("GH_TOKEN") == TOKEN for e in ex.envs)
    assert all(TOKEN not in c for c in ex.calls)
    assert all(TOKEN not in json.dumps(e.model_dump(mode="json")) for e in f.store.list_events(change.id))
    out = f.manager.ws.data_dir / "artifacts" / change.id
    assert all(TOKEN not in p.read_text(errors="replace") for p in out.rglob("*") if p.is_file())


async def test_live_without_a_token_holds_before_pushing(make_factory, tmp_path, monkeypatch):
    ex = GitHubExecutor()
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    product = await _assessed(f)
    f.manager.settings.factory_mode = "live"
    monkeypatch.delenv("REPO_GITHUB_TOKEN", raising=False)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)
    assert await wait_run(f, change.id) == ChangeStatus.held
    change = f.store.get_change(change.id)
    assert "no write token" in change.summary and "REPO_GITHUB_TOKEN" in (change.last_failure or "")
    assert not any(c.startswith(("git push", "gh pr")) for c in ex.calls)


def test_the_change_workflow_is_registered_per_repo_blueprint(cfg):
    assert cfg.workflow_templates()["existing-repo-change"] == "workflow-templates/repo-change"
    assert cfg.blueprint_of("existing-repo-change") == "existing-repo"
    assert cfg.workflow_for("existing-repo", "bug") == "existing-repo-change"
    assert cfg.workflow_for("existing-repo", "assess") == "existing-repo"
    assert cfg.workflow_for("fastapi-service", "bug") == "fastapi-service"
    with pytest.raises(KeyError):
        cfg.blueprint_of("fastapi-service-change")


def test_git_is_available():  # the end-to-end tests clone a local repository
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0


async def test_tools_on_a_pr_answer_only_for_the_changed_files(make_factory, tmp_path, monkeypatch):
    from test_tools import TRIVY, ScanExecutor

    ex = ScanExecutor(queue={" config --format json": [json.dumps(TRIVY)]})  # Dockerfile + k8s findings
    f = _factory(lambda **kw: make_factory(executor=ex, **kw), _origin(tmp_path))
    product = await _assessed(f)
    f.manager.settings.factory_mode = "live"
    monkeypatch.setenv("REPO_GITHUB_TOKEN", TOKEN)
    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)  # touches package.json + test/
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback  # fail_on high, but not its files
    out = f.manager.ws.data_dir / "artifacts" / change.id
    iac = json.loads((out / "tools" / "iac.json").read_text())
    assert (iac["scope"], iac["passed"], iac["outside"], iac["findings"]) == ("changed", True, 3, [])
    body = (out / "pull-request.md").read_text()
    assert "- **Trivy (Dockerfile, Kubernetes):** no findings in the changed files (passed)" in body
    assert "- **gitleaks (secrets):** no findings in the changed files (passed)" in body
