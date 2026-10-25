"""Team repos and stack packs (ADR-0041 §5): a pack is detected from a repo's files, at the root
and one folder down; a match is guidance for that folder's changes and advisory signals in the
assessment, never the repo's build or its Level."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import REPO, wait_run
from test_existing_repos import URL, _factory, _git, _origin

from agent_factory import packs
from agent_factory.agents import FakeAgentRunner
from agent_factory.models import ChangeKind, ChangeStatus, OnboardRepoInput

NEXT = json.dumps({"name": "web", "dependencies": {"next": "15.0.0", "react": "19.0.0"}})
PLAIN_NODE = json.dumps({"name": "svc", "dependencies": {"express": "4.0.0"}})
FASTAPI = '[project]\nname = "api"\ndependencies = ["fastapi>=0.110", "uvicorn"]\n'

MONOREPO = {
    "README.md": "# Shop\n",
    "web/package.json": NEXT,
    "web/app/page.tsx": "export default function Page() { return null }\n",
    "api/pyproject.toml": FASTAPI,
    "api/app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
    "docs/package.json": NEXT,  # docs are never a stack's home
}


@pytest.fixture(scope="module")
def installed() -> list[packs.PackBinding]:
    return [packs.binding_of(packs.load_pack(REPO / "packs" / p)) for p in ("fastapi-service", "nextjs-web")]


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _found(matches: list[packs.Match]) -> list[tuple[str, str]]:
    return [(m.id, m.folder) for m in matches]


# ----------------------------------------------------------------- pure --
def test_a_nextjs_repo_is_recognised_at_its_root(tmp_path, installed):
    root = _tree(tmp_path, {"package.json": NEXT, "web/package.json": NEXT})
    matches = packs.detect(installed, root)
    assert _found(matches) == [("nextjs-web", "")], "found at the root: not looked for again below it"
    assert matches[0].matched == ("package.json", "next") and matches[0].version == "1.3.1"


def test_a_monorepo_gets_one_match_per_folder(tmp_path, installed):
    matches = packs.detect(installed, _tree(tmp_path, MONOREPO))
    assert _found(matches) == [("fastapi-service", "api"), ("nextjs-web", "web")]
    assert matches[0].matched == ("api/pyproject.toml", "fastapi")


@pytest.mark.parametrize(
    "files",
    [
        {"package.json": PLAIN_NODE},  # node, but not Next.js
        {"package.json": "{not json"},
        {"pyproject.toml": '[project]\ndependencies = ["fastapi-users"]\n'},  # a different package
        {"requirements.txt": "fastapi\n"},  # the pack's detect rule names pyproject.toml
        {"a/b/package.json": NEXT},  # two levels down
        {"node_modules/package.json": NEXT, ".hidden/package.json": NEXT, "dist/package.json": NEXT},
    ],
)
def test_what_is_not_a_match(tmp_path, installed, files):
    assert packs.detect(installed, _tree(tmp_path, files)) == []


def test_a_linked_manifest_is_not_read(tmp_path, installed):
    outside = _tree(tmp_path / "outside", {"package.json": NEXT})
    repo = _tree(tmp_path / "repo", {"README.md": "x"})
    os.symlink(outside / "package.json", repo / "package.json")
    assert packs.detect(installed, repo) == []


def test_a_pack_without_detect_rules_is_never_guessed(tmp_path, installed):
    silent = replace(installed[1], detect=packs.PackDetect())
    assert packs.detect([silent], _tree(tmp_path, {"package.json": NEXT})) == []


def test_detect_files_stay_inside_the_repo():
    for bad in ("../package.json", "/etc/passwd", "*.json"):
        with pytest.raises(ValueError, match="plain file path inside the repo"):
            packs.PackDetect(files=[bad])


def test_advisory_signals_measure_a_folder_against_the_golden_path(tmp_path, installed):
    nextjs = installed[1]
    template = REPO / "packs" / "nextjs-web" / "template"
    good = packs.advisory(nextjs, template.parent, "template")
    missing = [s["id"] for s in good["signals"] if not s["ok"]]
    assert good["folder"] == "template" and missing == ["acceptance_tests"], "only what each product generates"
    assert any(s["id"] == "a11y" for s in good["signals"]), "the pack's own signals count too"
    assert {s["id"] for s in good["signals"]} >= {"linter", "unit_tests", "health_endpoints"}
    assert "readme" not in {s["id"] for s in good["signals"]}, "stack-neutral signals stay in the Level"

    bare = packs.advisory(nextjs, _tree(tmp_path, {"web/package.json": NEXT}), "web")
    assert bare["total"] == good["total"] and bare["met"] < bare["total"]
    assert all(s["hint"] for s in bare["signals"] if not s["ok"])


# ------------------------------------------------------------- end to end --
async def test_assessment_records_matches_and_advisory_signals(make_factory, tmp_path):
    origin = _origin(tmp_path, MONOREPO)
    f = _factory(make_factory, origin)
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    product = f.store.get_product(product.id)
    assert [(m.id, m.folder) for m in product.stack_matches] == [("fastapi-service", "api"), ("nextjs-web", "web")]
    assert product.pack is None, "a team repo's build is its own: the product names no pack"
    msgs = [e.message for e in f.store.list_events(change.id)]
    assert any(m.startswith("stack pack guidance: FastAPI service (fastapi-service@1.0.0) in api/;") for m in msgs)

    out = f.manager.ws.data_dir / "artifacts" / change.id
    report = json.loads((out / "assessment.json").read_text())
    assert [(g["pack"], g["folder"]) for g in report["pack_signals"]] == [
        ("fastapi-service", "api"),
        ("nextjs-web", "web"),
    ]
    assert {s["id"] for s in report["readiness"]["signals"]}.isdisjoint({"a11y"}), "advisory only: not in the Level"
    md = (out / "assessment.md").read_text()
    assert "## Stack packs (advisory)" in md and "### Next.js web app (nextjs-web@1.3.1) in `web/`" in md

    # detected again at every assessment: dropping Next.js is a decision, and the match goes
    (origin / "web" / "package.json").write_text(PLAIN_NODE)
    _git(origin, "commit", "-qam", "drop next")
    again = f.manager.feedback(product.id, "assess again", ChangeKind.assess)
    assert await wait_run(f, again.id) == ChangeStatus.awaiting_feedback
    assert [m.id for m in f.store.get_product(product.id).stack_matches] == ["fastapi-service"]
    assert any(
        m.startswith("stack packs changed since the last assessment: FastAPI service")
        for m in (e.message for e in f.store.list_events(again.id))
    )


async def test_a_repo_without_a_matching_pack_runs_in_generic_mode(make_factory, tmp_path):
    f = _factory(make_factory, _origin(tmp_path))
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert f.store.get_product(product.id).stack_matches == []
    assert "no stack pack matches: changes run in generic mode" in [e.message for e in f.store.list_events(change.id)]
    md = (f.manager.ws.data_dir / "artifacts" / change.id / "assessment.md").read_text()
    assert "No installed stack pack matches" in md


async def test_a_repo_change_gets_the_skills_as_guidance_and_the_packs_egress(make_factory, tmp_path):
    runner = FakeAgentRunner()
    f = _factory(make_factory, _origin(tmp_path, {**MONOREPO, "package.json": json.dumps({"scripts": {}})}))
    f.manager.agents = runner
    # an install whose Next.js pack needs a registry of its own (the shipped one needs none)
    stack = f.manager.cfg.stack_packs
    stack["nextjs-web"] = replace(stack["nextjs-web"], egress=("registry.example.com:443",))
    product = f.manager.onboard_repo(OnboardRepoInput(repo_url=URL))
    assert await wait_run(f, f.manager.start_change(product).id) == ChangeStatus.awaiting_feedback

    change = f.manager.feedback(product.id, "Add unit tests", ChangeKind.upkeep)
    await wait_run(f, change.id)
    dev = next(c for c in runner.calls if c.role == "repo-developer")
    assert "### Skill: fastapi-golden-path\n_Stack pack guidance for `api/`" in dev.system_prompt
    assert "### Skill: nextjs-golden-path\n_Stack pack guidance for `web/`" in dev.system_prompt
    assert "follow the repository; never restructure it to fit this skill" in dev.system_prompt
    assert dev.egress == ["registry.example.com:443"], "the detected packs' egress, for this product only"
    others = [c for c in runner.calls if c.role != "repo-developer"]
    assert others and all("golden-path" not in c.system_prompt for c in others), "design, develop and fix only"
