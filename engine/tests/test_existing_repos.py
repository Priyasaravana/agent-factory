"""Existing repositories (ADR-0031): onboard by URL, clone read-only, assess, report."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from conftest import wait_run
from test_api import _client

from agent_factory import assess
from agent_factory.engine.pipeline import FactoryError, InvalidRequestError
from agent_factory.models import ChangeKind, ChangeStatus, OnboardRepoInput, ProductTarget
from agent_factory.workflow import HANDLER_REQUIREMENTS

URL = "https://github.com/acme/hello-node"

NODE_APP = {
    "README.md": "# Hello\n",
    "app.js": "const http = require('http');\nhttp.createServer((q, s) => s.end('hi')).listen(3000);\n",
    "package.json": json.dumps({"name": "hello", "scripts": {"start": "node app.js"}}),
    "Dockerfile": (
        "FROM node:18-alpine\nWORKDIR /app\nCOPY package.json .\nRUN npm install\nCOPY app.js .\n"
        'CMD ["node", "app.js"]\n'
    ),
    "k8s/deployment.yaml": """apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello
spec:
  template:
    spec:
      containers:
      - name: web
        image: hello:latest
        livenessProbe:
          httpGet: {path: /, port: 3000}
        resources:
          limits: {memory: 128Mi}
          requests: {memory: 64Mi}
""",
}


def _write(root: Path, tree: dict[str, str]) -> Path:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os

    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **env}
    ).stdout


def _origin(tmp_path: Path, tree: dict[str, str] = NODE_APP) -> Path:
    """A local repository standing in for GitHub."""
    repo = _write(tmp_path / "origin", tree)
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _factory(make_factory, origin: Path):
    f = make_factory()
    f.manager.ws.clone_source = lambda url: str(origin)
    f.manager.ws.local_clones = True
    return f


# ------------------------------------------------------------------ URLs --
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/hello-node",
        "https://github.com/acme/hello-node.git",
        "https://GitHub.com/acme/hello.node/",
    ],
)
def test_repo_urls_that_are_accepted(url):
    ref = assess.parse_repo_url(url, ["github.com"])
    assert ref.url.startswith("https://github.com/acme/") and ref.url.endswith(".git")


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://github.com/acme/hello", "https"),
        ("git@github.com:acme/hello.git", "https"),
        ("file:///etc", "https"),
        ("https://user:token@github.com/acme/hello", "credentials"),
        ("https://gitlab.example.com/acme/hello", "not allowed"),
        ("https://github.com/acme", "owner"),
        ("https://github.com/acme/hello/tree/main", "owner"),
        ("https://github.com/acme/hello?x=1", "just"),
        ("https://github.com:8443/acme/hello", "just"),
        ("https://github.com/../hello", "names"),
    ],
)
def test_repo_urls_that_are_refused(url, why):
    with pytest.raises(assess.RepoUrlError, match=why):
        assess.parse_repo_url(url, ["github.com"])


# ------------------------------------------------------------ pure checks --
def test_stack_signals_and_findings_of_a_small_node_service(tmp_path):
    root = _write(tmp_path / "r", NODE_APP)
    stack = assess.detect_stack(root)
    assert stack.languages == ["javascript"] and stack.runtimes == {"node": "18"}
    assert "kubernetes" in stack.deploy and "docker" in stack.build
    assert stack.package_managers == ["npm (no lockfile)"]
    card = assess.score(root, stack, secret_scan_ok=True).as_data()
    passed = {s["id"] for s in card["signals"] if s["ok"]}
    assert passed == {"readme", "secret_scan"} and card["level"] == 0
    rules = {(f.rule, f.title) for f in assess.findings(root, stack)}
    assert ("runtime-eol", "runtime past end of life") in rules
    assert ("docker-root", "container runs as root") in rules
    assert ("npm-install-unlocked", "npm install without a lockfile") in rules
    assert ("k8s-no-probes", "no readiness probe") in rules  # liveness is there
    assert ("k8s-image-latest", "image not pinned") in rules
    assert not any(r == "k8s-no-memory-limit" for r, _ in rules)  # the limit is set


def test_a_well_kept_repo_meets_the_signals(tmp_path):
    tree = {
        "README.md": "# Svc\n",
        "AGENTS.md": "# Agents\n",
        "package.json": json.dumps(
            {
                "scripts": {"test": "vitest run --coverage"},
                "devDependencies": {"eslint": "9", "prettier": "3", "vitest": "2"},
                "dependencies": {"express": "5", "pino": "9", "prom-client": "15", "@opentelemetry/api": "1"},
            }
        ),
        "package-lock.json": "{}",
        "vitest.config.ts": "export default { test: { coverage: { thresholds: { lines: 80 } } } }",
        "src/app.js": "app.get('/healthz'); app.get('/readyz'); app.get('/metrics')",
        "src/app.test.js": "test('x', () => {})",
        "e2e/smoke.spec.js": "test('y', () => {})",
        "Dockerfile": "FROM node:22.11-alpine\nUSER node\nCMD node src/app.js\n",
        ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: npm ci && npm test\n",
        ".github/dependabot.yml": "version: 2\n",
        ".github/CODEOWNERS": "* @acme/team\n",
        ".husky/pre-commit": "npm test\n",
    }
    root = _write(tmp_path / "r", tree)
    stack = assess.detect_stack(root)
    assert stack.frameworks == ["express"] and "vitest" in stack.test_tools and stack.package_managers == ["npm"]
    card = assess.score(root, stack, secret_scan_ok=True).as_data()
    assert [s["id"] for s in card["signals"] if not s["ok"]] == [] and card["level"] == 3
    assert assess.findings(root, stack) == []


def test_symlinks_and_dependency_folders_are_not_read(tmp_path):
    root = _write(tmp_path / "r", {"README.md": "x", "node_modules/a/index.js": "x"})
    (root / "outside").symlink_to("/etc")
    assert assess.files(root) == ["README.md"]


def test_the_engine_keeps_only_what_the_repo_supports():
    report = {
        "summary": "ok",
        "test_gaps": [{"area": "routes", "why": "untested", "files": ["app.js", "ghost.js"]}],
        "risks": [
            {"severity": "high", "title": "made up", "detail": "", "files": ["nope.py"]},
            {"severity": "critical", "title": "odd severity", "detail": "", "files": ["app.js"]},
            {"severity": "low", "title": "real", "detail": "", "files": ["./k8s/deployment.yaml:12"]},
            {"severity": "low", "title": "a folder", "detail": "", "files": ["k8s/"]},
            {
                "severity": "low",
                "pillar": "operational-excellence",
                "title": "a dotfile",
                "detail": "",
                "files": [".github/workflows/ci.yml"],
            },
            {"severity": "low", "pillar": "vibes", "title": "odd pillar", "detail": "", "files": ["app.js"]},
        ],
        "recommendations": [
            {"title": "Add tests", "why": "none", "kind": "upkeep", "effort": "XL"},
            {"title": "Rewrite in Rust", "why": "fun", "kind": "rewrite", "effort": "L"},
        ],
        "agents_md": "x" * (assess.MAX_AGENTS_MD + 5),
    }
    j = assess.judge(report, ["app.js", "k8s/deployment.yaml", ".github/workflows/ci.yml"])
    assert [g["files"] for g in j.test_gaps] == [["app.js"]]
    assert [r["title"] for r in j.risks] == ["real", "a folder", "a dotfile", "odd pillar"]
    assert [r.get("pillar") for r in j.risks] == [None, None, "operational-excellence", None]  # unknown pillar dropped
    assert j.risks[0]["files"] == ["k8s/deployment.yaml"]
    assert [(r["title"], r["effort"]) for r in j.recommendations] == [("Add tests", "M")]
    assert len(j.agents_md) == assess.MAX_AGENTS_MD
    text = "\n".join(j.dropped)
    for why in ("made up", "nope.py", "odd severity", "Rewrite in Rust", "ghost.js", "cut to"):
        assert why in text


def test_the_assessor_can_never_write():
    assert HANDLER_REQUIREMENTS["assess"]["write"] is False


# ------------------------------------------------------------- end to end --
async def test_onboard_assess_and_assess_again(make_factory, tmp_path):
    origin = _origin(tmp_path)
    before = _git(origin, "rev-parse", "HEAD").strip()
    f = _factory(make_factory, origin)
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL + ".git", notes="Is it ready for production?"))
    assert (product.target, product.blueprint, product.node_port) == (ProductTarget.repo, "existing-repo", None)
    assert product.repo_url == URL and product.title == "acme/hello-node"
    change = f.manager.start_change(product)
    assert change.kind == ChangeKind.assess
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    product = f.store.get_product(product.id)
    assert product.repo_ref == "trunk"  # the repo's default branch, found on clone

    out = f.manager.ws.data_dir / "artifacts" / change.id
    report = json.loads((out / "assessment.json").read_text())
    assert report["repo"]["commit"] == before and report["stack"]["runtimes"] == {"node": "18"}
    assert any(x["rule"] == "runtime-eol" for x in report["findings"])
    assert [(r["title"], r["pillar"]) for r in report["risks"]] == [("no input validation", "security")]
    assert [g["area"] for g in report["test_gaps"]] == ["request handling"]
    assert any("nope.py" in d for d in report["dropped"])
    assert report["agents_md"] and (out / "AGENTS.proposed.md").is_file()
    md = (out / "assessment.md").read_text()
    assert "Agent readiness: Level 0" in md and "runtime past end of life" in md and "Proposed AGENTS.md" in md
    msgs = [e.message for e in f.store.list_events(change.id)]
    assert any(m.startswith("cloned https://github.com/acme/hello-node (trunk) read-only") for m in msgs)

    # an assessment delivers a report, not software: Outcomes leaves it out (ADR-0031)
    from agent_factory.outcomes import outcomes

    assert outcomes(f.store).deliveries == 0

    # read-only: nothing pushed or changed upstream, and pushing is disabled in the clone
    assert _git(origin, "rev-parse", "HEAD").strip() == before
    assert _git(origin, "branch", "--format=%(refname:short)").split() == ["trunk"]
    clone = f.manager.ws.product_dir(product.slug)
    assert _git(clone, "remote", "get-url", "--push", "origin").strip() == "no-push://read-only"

    # the next assessment sees the new commit
    (origin / "README.md").write_text("# Hello, again\n")
    _git(origin, "commit", "-qam", "docs")
    after = _git(origin, "rev-parse", "HEAD").strip()
    with pytest.raises(InvalidRequestError, match="assess"):
        f.manager.feedback(product.id, "build it again", ChangeKind.new)  # changes are PRs (ADR-0033)
    again = f.manager.feedback(product.id, "assess again", ChangeKind.assess)
    assert (again.kind, again.iteration) == (ChangeKind.assess, 2)
    assert await wait_run(f, again.id) == ChangeStatus.awaiting_feedback
    second = json.loads((f.manager.ws.data_dir / "artifacts" / again.id / "assessment.json").read_text())
    assert second["repo"]["commit"] == after

    # archiving undeploys nothing and keeps the history
    archived = await f.manager.archive(product.id, "ada")
    assert archived.archived_at and archived.repo_url == URL


async def test_a_repo_that_cannot_be_read_holds_the_change(make_factory, tmp_path):
    f = _factory(make_factory, tmp_path / "missing")
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.held
    change = f.store.get_change(change.id)
    assert "could not be read" in change.summary and "could not clone" in (change.last_failure or "")


def test_a_repo_is_onboarded_once(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    with pytest.raises(FactoryError, match="already onboarded"):
        f.manager.onboard_repo(OnboardRepoInput(repo_url=URL.upper().replace("HTTPS", "https") + ".git"))


async def test_repos_over_http(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    app, ctx, c = await _client(f)
    async with c:
        bad = await c.post("/api/repos", json={"repo_url": "https://token@github.com/acme/hello-node"})
        assert bad.status_code == 422 and "credentials" in bad.json()["detail"]
        r = await c.post("/api/repos", json={"repo_url": URL, "notes": "What should we fix first?"})
        assert r.status_code == 201, r.text
        detail = r.json()
        assert detail["product"]["target"] == "repo" and detail["changes"][0]["kind"] == "assess"
        cid = detail["changes"][0]["id"]
        assert (await c.get(f"/api/changes/{cid}/assessment")).status_code in (404, 200)
        for _ in range(200):
            if (await c.get(f"/api/changes/{cid}")).json()["change"]["status"] == "awaiting_feedback":
                break
            await asyncio.sleep(0.05)
        view = (await c.get(f"/api/changes/{cid}/assessment")).json()
        assert view["level"] == 0 and view["repo"]["ref"] == "trunk" and view["stack"]["languages"] == ["javascript"]
        eol = next(x for x in view["findings"] if x["rule"] == "runtime-eol")
        assert eol["at"] == ["Dockerfile:1"]
        assert view["recommendations"][0]["kind"] == "upkeep" and view["markdown"].startswith("# Assessment")
        stations = [s["id"] for s in (await c.get(f"/api/changes/{cid}")).json()["stations"]]
        assert stations == ["onboard", "repo-scan", "assess", "report"]
        checks = {v["blueprint"]: [x["id"] for x in v["checks"]] for v in (await c.get("/api/preflight")).json()}
        assert checks["existing-repo"] == ["model", "sandbox", "workflow"]  # nothing to build or deploy
    await ctx.__aexit__(None, None, None)
