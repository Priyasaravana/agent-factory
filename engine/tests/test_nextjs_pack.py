"""Stack pack #2, nextjs-web (ADR-0041 step 3), and what the engine now reads from a pack: health
paths, the e2e command, the requirement tag rule, egress and toolchain.

The template's own checks (`make verify`, `make e2e`) run in CI's golden-path job; here everything
is files and recorded commands, no Node needed."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import PRODUCT, REPO, wait_run

from agent_factory import traceability
from agent_factory.engine.preflight import Preflight
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.packs import IGNORED, PackError, load_pack
from agent_factory.readiness import RuleError, parse_stack_rules, parse_tag_rule, score
from agent_factory.sandbox import SandboxManager, toolchain
from agent_factory.traceability import PYTEST_TAGS, matrix, untraced

PACK = REPO / "packs" / "nextjs-web"
TEMPLATE = PACK / "template"
SANDBOX_DOCKERFILE = REPO / "images" / "sandbox" / "Dockerfile"
WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})


def _template_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(TEMPLATE, dst, ignore=shutil.ignore_patterns(*IGNORED))
    return dst


def _agent_written(root: Path, test_title: str = "adds a book @R1") -> Path:
    """What intake, design and the developer add to a fresh template."""
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("spec.md", "design.md", "openapi.yaml"):
        (docs / name).write_text("x\n")
    (docs / "requirements.yaml").write_text("- {id: R1, title: Add a book, detail: x}\n")
    (root / "tests" / "acceptance").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "acceptance" / "scenarios.yaml").write_text(
        "- {id: A1, given: a, when: b, then: c, covers: [R1]}\n"
    )
    (root / "tests" / "acceptance.test.ts").write_text(f'it("{test_title}", () => {{}});\n')
    return root


# ------------------------------------------------------------------ the pack --
def test_the_pack_loads_with_its_stack_and_its_own_a11y_signal():
    pack = load_pack(PACK)
    assert pack.source == "nextjs-web@1.1.1" and pack.stack is not None
    stack = pack.stack
    assert (stack.builds, stack.port, stack.health.live, stack.health.ready) == (
        "web-app",
        3000,
        "/api/healthz",
        "/api/readyz",
    )
    assert stack.e2e == "make e2e BASE_URL={url}" and stack.egress == []
    assert set(stack.toolchain) == {"node@22", "npm", "chromium"}
    assert pack.rules is not None
    [a11y] = pack.rules.extra
    assert (a11y.id, a11y.level, a11y.pillar) == ("a11y", 3, "usability")


def test_a_fresh_template_misses_only_what_the_agents_write(tmp_path):
    """Same gaps as the FastAPI template: the spec, the acceptance tests and their tags."""
    rules = load_pack(PACK).rules
    card = score(_template_copy(tmp_path), True, rules)
    assert [s.id for s in card.missing(3)] == ["acceptance_tests", "requirements_traced", "design_docs"]
    fastapi = load_pack(REPO / "packs" / "fastapi-service")
    py = score(_template_copy_fastapi(tmp_path), True, fastapi.rules)
    assert [s.id for s in py.missing(3)] == [s.id for s in card.missing(3)]
    assert card.max_points == py.max_points + 4, "a11y is one more level-3 signal"


def _template_copy_fastapi(tmp_path: Path) -> Path:
    dst = tmp_path / "py"
    shutil.copytree(REPO / "packs" / "fastapi-service" / "template", dst, ignore=shutil.ignore_patterns(*IGNORED))
    return dst


def test_with_the_agents_work_the_template_reaches_level_3_with_usability_covered(tmp_path):
    card = score(_agent_written(_template_copy(tmp_path)), True, load_pack(PACK).rules)
    assert card.level == 3 and card.points == card.max_points
    usability = next(p for p in card.pillars() if p["id"] == "usability")
    assert (usability["passed"], usability["applicable"]) == (1, 1), "no longer uncovered"


@pytest.mark.parametrize(
    ("remove", "signal"),
    [
        ("e2e/a11y.spec.ts", "a11y"),
        ("e2e/routes.ts", "a11y"),
        ("src/app/api/readyz/route.ts", "health_endpoints"),
        ("src/instrumentation.ts", "tracing"),
        ("package-lock.json", "lockfile"),
        ("playwright.config.ts", "e2e_tests"),
    ],
)
def test_removing_a_level_3_piece_drops_its_signal(tmp_path, remove, signal):
    root = _agent_written(_template_copy(tmp_path))
    (root / remove).unlink()
    card = score(root, True, load_pack(PACK).rules)
    assert [s.id for s in card.missing(3)] == [signal]


def test_an_a11y_check_weakened_to_minor_violations_only_does_not_count(tmp_path):
    root = _agent_written(_template_copy(tmp_path))
    spec = root / "e2e" / "a11y.spec.ts"
    spec.write_text(spec.read_text().replace('"serious"', '"minor"').replace('"critical"', '"moderate"'))
    assert [s.id for s in score(root, True, load_pack(PACK).rules).missing(3)] == ["a11y"]


def test_a_coverage_gate_below_80_does_not_count(tmp_path):
    root = _agent_written(_template_copy(tmp_path))
    cfg = root / "vitest.config.mts"
    cfg.write_text(re.sub(r"lines: \d+", "lines: 50", cfg.read_text()))
    assert [s.id for s in score(root, True, load_pack(PACK).rules).missing(3)] == ["coverage_gate"]


def test_the_template_keeps_its_contract_with_the_pack_and_the_chart():
    pack = load_pack(PACK).stack
    assert pack is not None
    docker = (TEMPLATE / "Dockerfile").read_text()
    values = yaml.safe_load((TEMPLATE / "deploy" / "chart" / "values.yaml").read_text())
    deployment = (TEMPLATE / "deploy" / "chart" / "templates" / "deployment.yaml").read_text()
    assert f"EXPOSE {pack.port}" in docker and values["containerPort"] == pack.port
    assert re.search(r"^USER nonroot$", docker, re.M), "non-root"
    runtime = re.findall(r"^FROM (\S+)", docker, re.M)[-1]
    assert runtime.startswith("gcr.io/distroless/nodejs22-"), "no shell, npm or Debian extras in the runtime image"
    assert "runAsUser: 65532" in deployment, "the chart runs the image's own non-root user"
    assert f"path: {pack.health.live}" in deployment and f"path: {pack.health.ready}" in deployment
    assert 'output: "standalone"' in (TEMPLATE / "next.config.ts").read_text()
    make = (TEMPLATE / "Makefile").read_text()
    assert all(re.search(rf"^{t}:", make, re.M) for t in ("verify", "test", "e2e")), "one convention (§3)"
    assert "make verify" in (TEMPLATE / ".github" / "workflows" / "ci.yml").read_text()


def test_the_template_pins_the_playwright_the_sandbox_ships_a_browser_for():
    """Chromium in the sandbox is built for one Playwright version; another one would look for a
    browser build that isn't there and every e2e run would fail."""
    tools = toolchain.read_dockerfile(SANDBOX_DOCKERFILE.read_text())
    pkg = json.loads((TEMPLATE / "package.json").read_text())
    lock = json.loads((TEMPLATE / "package-lock.json").read_text())
    assert tools.playwright and pkg["devDependencies"]["@playwright/test"] == tools.playwright
    assert lock["packages"]["node_modules/@playwright/test"]["version"] == tools.playwright
    assert "@playwright/test" in (TEMPLATE / ".github" / "dependabot.yml").read_text(), "kept out of Dependabot"


def test_the_template_needs_no_web_fonts_or_remote_assets():
    sources = [p.read_text() for p in (TEMPLATE / "src").rglob("*.ts*")]
    assert not any("next/font/google" in s or "https://" in s for s in sources)


def test_the_pack_ships_three_evaluation_cases_and_refuses_broken_ones(tmp_path):
    cases = yaml.safe_load((PACK / "evals.yaml").read_text())
    assert [c["id"] for c in cases] == ["reading-list", "rsvp", "status-board"]
    copy = tmp_path / "nextjs-web"
    shutil.copytree(PACK, copy, ignore=shutil.ignore_patterns(*IGNORED))
    (copy / "evals.yaml").write_text(yaml.safe_dump([cases[0], cases[0]]))
    with pytest.raises(PackError, match="listed twice: reading-list"):
        load_pack(copy)
    (copy / "evals.yaml").write_text("- {id: x, title: no}\n")
    with pytest.raises(PackError, match="evals.yaml"):
        load_pack(copy)


def test_build_leftovers_never_change_the_pack_hash_or_reach_a_product(tmp_path):
    copy = tmp_path / "nextjs-web"
    shutil.copytree(PACK, copy, ignore=shutil.ignore_patterns(*IGNORED))
    before = load_pack(copy).sha256
    for leftover in ("node_modules/x/index.js", ".next/cache/a", "coverage/index.html", "next-env.d.ts"):
        f = copy / "template" / leftover
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    assert load_pack(copy).sha256 == before


# -------------------------------------------------------- requirement tags --
def test_typescript_tests_are_traced_by_the_tag_in_their_title(tmp_path):
    tags = load_pack(PACK).rules.tags  # type: ignore[union-attr]
    root = _agent_written(_template_copy(tmp_path), "lists unread first @R1 @R2")
    (root / "e2e" / "journey.spec.ts").write_text('test("reads @R2", async () => {});\n')
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "x.test.ts").write_text('it("@R9", () => {});\n')
    assert traceability.test_tags(root, tags) == {"R1": 1, "R2": 2}, "node_modules is never read"
    assert untraced(root, tags) == []
    assert traceability.test_tags(root) == {}, "the pytest rule finds nothing in a TypeScript repo"
    [row] = matrix(root, tags=tags)
    assert (row["id"], row["tests"]) == ("R1", 1)


def test_an_untagged_requirement_says_how_this_stack_tags_it(tmp_path):
    rules = load_pack(PACK).rules
    root = _agent_written(_template_copy(tmp_path), "adds a book")
    assert untraced(root, rules.tags) == ['R1 has no test tagged it("adds a book @R1", …)']  # type: ignore[union-attr]
    traced = next(s for s, ok in score(root, True, rules).results if s.id == "requirements_traced")
    assert 'it("adds a book @R…", …)' in traced.hint


def test_the_fastapi_pack_keeps_the_pytest_rule_exactly():
    tags = load_pack(REPO / "packs" / "fastapi-service").rules.tags  # type: ignore[union-attr]
    text = '@pytest.mark.req("R1", "R3")\n@pytest.mark.req(\'R2\')\nreq(R4)\n'
    assert tags.found(text) == PYTEST_TAGS.found(text) == ["R1", "R3", "R2"]
    assert tags.example == PYTEST_TAGS.example


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (None, "needed"),
        ({"files": ["tests/*.ts"], "pattern": "@R\\d+", "example": "@R1"}, "one group"),
        ({"files": ["tests/*.ts"], "pattern": "@(R\\d+)", "example": "R1 only"}, "does not find R1"),
        ({"files": ["../*.ts"], "pattern": "@(R\\d+)", "example": "@R1"}, "inside the repo"),
        ({"files": [], "pattern": "@(R\\d+)", "example": "@R1"}, "non-empty list"),
        ({"files": ["t/*"], "pattern": "@(R\\d+", "example": "@R1"}, "bad regex"),
        ({"files": ["t/*"], "pattern": "@(R\\d+)", "example": "@R1", "x": 1}, "unexpected x"),
    ],
)
def test_a_tag_rule_that_could_not_trace_anything_is_refused(raw, error):
    with pytest.raises(RuleError, match=error):
        parse_tag_rule(raw)


def test_a_pack_without_a_tag_rule_is_refused():
    raw = yaml.safe_load((PACK / "readiness.yaml").read_text())
    raw.pop("requirement_tags")
    with pytest.raises(RuleError, match="requirement_tags: needed"):
        parse_stack_rules(raw)


# ---------------------------------------------------------------- toolchain --
def test_the_sandbox_dockerfile_declares_what_packs_may_ask_for():
    tools = toolchain.read_dockerfile(SANDBOX_DOCKERFILE.read_text())
    assert {"python@3.12", "uv", "node@22", "npm"} <= tools.always()
    for pack in (PACK, REPO / "packs" / "fastapi-service"):
        stack = load_pack(pack).stack
        assert stack is not None
        assert toolchain.missing(stack.toolchain, tools, toolchain.layers_for([stack.toolchain])) == []


def test_layers_are_built_only_when_an_installed_pack_asks():
    assert toolchain.layers_for([["python@3.12", "uv"]]) == []
    assert toolchain.layers_for([["python@3.12"], ["node@22", "chromium"]]) == ["chromium"]
    tools = toolchain.SandboxTools(("3.12",), "22", "1.64.1")
    assert toolchain.build_args([], tools) == []
    assert toolchain.build_args(["chromium"], tools) == [
        "--build-arg BROWSERS=chromium",
        "--build-arg PLAYWRIGHT_VERSION=1.64.1",
    ]
    assert toolchain.missing(["node@22", "chromium", "go@1.23"], tools, []) == ["chromium", "go@1.23"]


async def test_the_browser_layer_changes_the_image_and_its_build(tmp_path):
    (tmp_path / "images" / "sandbox").mkdir(parents=True)
    shutil.copy(SANDBOX_DOCKERFILE, tmp_path / "images" / "sandbox" / "Dockerfile")
    (tmp_path / "plugin").mkdir()
    from agent_factory.config import SandboxConfig

    plain = SandboxManager(SandboxConfig(), tmp_path, tmp_path / "data")
    ex = FakeExecutor(fail_on=["docker image inspect"])
    browser = SandboxManager(SandboxConfig(), tmp_path, tmp_path / "data", ex=ex, layers=["chromium"])
    assert plain.image != browser.image, "a different image, so a cached small one is never reused"
    import agent_factory.sandbox.manager as manager_mod

    real = manager_mod.assemble_context
    manager_mod.assemble_context = lambda home, ctx: None  # type: ignore[assignment]
    try:
        assert await browser._image() is None
    finally:
        manager_mod.assemble_context = real  # type: ignore[assignment]
    [build] = [c for c in ex.calls if c.startswith("docker build")]
    assert "--build-arg BROWSERS=chromium" in build and "--build-arg PLAYWRIGHT_VERSION=" in build


async def test_preflight_refuses_a_pack_whose_toolchain_the_sandbox_lacks(cfg, make_factory):
    f = make_factory()
    pre = Preflight(f.manager)
    assert pre._toolchain("nextjs-web").state == "ready"
    assert pre._toolchain("fastapi-service").state == "ready"
    binding = cfg.blueprints["nextjs-web"].pack_binding
    from dataclasses import replace

    cfg.blueprints["nextjs-web"].bind_pack(replace(binding, toolchain=("node@22", "go@1.23")))
    check = pre._toolchain("nextjs-web")
    assert check.state == "failed" and "go@1.23" in check.reasons[0] and set(check.blocks) == {"product", "change"}


# ------------------------------------------------------------ in the lane --
async def test_a_next_js_product_is_deployed_checked_and_e2e_tested_by_its_pack(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert f.store.get_product(product.id).pack == "nextjs-web@1.1.1"
    smoke = [c for c in ex.calls if "curl -fsS" in c]
    assert smoke and all("/api/healthz" in c for c in smoke), "the pack's liveness path"
    assert any(c.startswith("make e2e BASE_URL=http://") for c in ex.calls)
    messages = [e.message for e in f.store.list_events(change.id)]
    assert "end-to-end tests passed against the live app" in messages
    assert any("Level 3" in m and "usability 1/1" in m for m in messages)


async def test_failing_e2e_goes_back_to_the_builder_before_the_verifier_runs(make_factory):
    ex = FakeExecutor(fail_on=["make e2e BASE_URL=http://dind"])  # the live run, not the preview
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback  # fixed on the second pass
    events = f.store.list_events(change.id)
    failed = next(e for e in events if e.message == "end-to-end tests failed against the live app")
    finished = [e.message for e in events if e.message.startswith("acceptance: failed")]
    assert failed and finished == ["acceptance: failed — end-to-end tests failed against the live app"]


async def test_fastapi_products_keep_their_paths_and_run_their_own_e2e(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    smoke = [c for c in ex.calls if "curl -fsS" in c]
    assert smoke and all(re.search(r"/healthz &&", c) and "/api/" not in c for c in smoke)
    assert any(c.startswith("E2E_BASE_URL=http://") and "pytest tests/e2e" in c for c in ex.calls)
