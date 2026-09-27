"""Skills imported from GitHub: review, install pinned to a commit, use in a
workflow, update with a diff, and removal without breaking old versions."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ORDER, wait_run
from test_api import _client

import agent_factory.actions as actions
from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.runner import AgentRequest, plugins_for, sdk_tools, skill_refs
from agent_factory.github import Fetched
from agent_factory.models import InstallSkillInput, RunStatus, SkillSourceInput
from agent_factory.skills import SkillError

WF = "fastapi-service"
SHA1, SHA2 = "1" * 40, "2" * 40
SKILL_V1 = """---
name: owasp-check
description: Checklist for reviewing web APIs against the OWASP top 10.
---
# OWASP check
Look for injection and broken auth.
"""
SKILL_V2 = SKILL_V1.replace("broken auth.", "broken auth and SSRF.")


@pytest.fixture
def gh(tmp_path, monkeypatch):
    """A fake GitHub: commits are folders; fetch_dir returns the folder for a ref or sha."""
    commits: dict[str, dict[str, str]] = {}
    refs = {"main": SHA1}
    calls: list[tuple[str, str, str]] = []

    def commit(sha: str, files: dict[str, str]) -> None:
        commits[sha] = files

    def fake_fetch(repo, path, ref, token):
        calls.append((repo, path, ref))
        sha = ref if ref in commits else refs[ref]
        root = tmp_path / "gh" / sha / path
        for rel, content in commits[sha].items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(content)
        keep = tmp_path / "tmp" / sha
        keep.mkdir(parents=True, exist_ok=True)
        return Fetched(repo=repo, path=path, ref=ref, sha=sha, root=root, _tmp=keep)

    monkeypatch.setattr(actions, "fetch_dir", fake_fetch)
    commit(SHA1, {"SKILL.md": SKILL_V1, "scripts/scan.sh": "#!/bin/sh\necho scan\n"})
    return type("GH", (), {"commit": staticmethod(commit), "refs": refs, "calls": calls})


SRC = SkillSourceInput(repo="acme/skills", path="security/owasp-check")


def _install(f, sha=SHA1, accept=True):
    return actions.install_skill(f, InstallSkillInput(**SRC.model_dump(), sha=sha, accept_scripts=accept))


def _use_in_workflow(f, skill="owasp-check"):
    d = f.workflows[WF].draft
    spec = d.get()[1].agents["verifier"]
    d.upsert_agent(spec.model_copy(update={"skills": [*spec.skills, skill]}))
    return d


async def test_preview_shows_files_scripts_and_problems(make_factory, gh):
    f = make_factory()
    pv = actions.preview_skill(f, SRC)
    assert pv.name == "owasp-check" and pv.sha == SHA1 and pv.problems == []
    assert pv.scripts == ["scripts/scan.sh"]
    assert {x.path for x in pv.files} == {"SKILL.md", "scripts/scan.sh"}
    assert pv.installed_sha is None and pv.diff is None

    gh.commit("3" * 40, {"SKILL.md": "---\nname: factory-station-contract\n---\nnope\n"})
    pv = actions.preview_skill(f, SRC.model_copy(update={"ref": "3" * 40}))
    assert any("built-in skill" in p for p in pv.problems)
    assert any("needs a description" in p for p in pv.problems)
    gh.commit("4" * 40, {"README.md": "no skill here"})
    pv = actions.preview_skill(f, SRC.model_copy(update={"ref": "4" * 40}))
    assert any("no SKILL.md" in p for p in pv.problems)


async def test_install_needs_script_consent_and_the_reviewed_commit(make_factory, gh):
    f = make_factory()
    with pytest.raises(SkillError, match="contains scripts"):
        _install(f, accept=False)
    gh.refs["main"] = SHA2  # the branch moved after the review…
    gh.commit(SHA2, {"SKILL.md": SKILL_V2})
    info = _install(f, sha=SHA1)  # …but the reviewed commit is what gets installed
    assert info.sha == SHA1 and info.ref == "main" and info.source == "github"
    assert gh.calls[-1][2] == SHA1
    names = {s.name: s for s in actions.get_catalog(f).skills}
    assert names["owasp-check"].source == "github" and names["factory-station-contract"].source == "builtin"


async def test_pinned_skill_reaches_the_agent_and_old_versions_keep_their_pin(make_factory, gh):
    runner = FakeAgentRunner()
    f = make_factory(agents=runner)
    # an unknown skill blocks publishing until it is installed
    d = _use_in_workflow(f)
    assert any("unknown skill 'owasp-check'" in p for p in d.problems())
    _install(f)
    assert d.problems() == []
    d.publish("verifier uses owasp-check")
    assert f.workflows[WF].get(2).skill_pins["owasp-check"] == SHA1

    run = f.manager.start_run(f.manager.create_order(ORDER))
    assert await wait_run(f, run.id) == RunStatus.awaiting_feedback
    req = next(c for c in runner.calls if c.role == "verifier")
    assert "owasp-check" in req.imported_skills and "owasp-check" not in req.skills
    assert (req.imported_plugin / "skills" / "owasp-check" / "SKILL.md").read_text() == SKILL_V1
    assert (req.imported_plugin / "skills" / "owasp-check" / "scripts" / "scan.sh").exists()

    # update: the check shows a diff; installing it is pending for the workflow until published
    gh.commit(SHA2, {"SKILL.md": SKILL_V2})
    gh.refs["main"] = SHA2
    pv = actions.check_skill_update(f, "owasp-check")
    assert pv.installed_sha == SHA1 and pv.sha == SHA2 and "+Look for injection and broken auth and SSRF." in pv.diff
    assert "-scripts/scan.sh" not in pv.diff and "/dev/null" in pv.diff  # the script was removed
    actions.install_skill(f, InstallSkillInput(**SRC.model_dump(), sha=SHA2))
    view = actions.get_draft(f, WF)
    assert view.skill_updates == {"owasp-check": SHA2} and not view.dirty
    f.workflows[WF].draft.publish("pick up owasp-check update")
    assert f.workflows[WF].get(3).skill_pins["owasp-check"] == SHA2
    lib = f.workflows.library
    old = lib.materialise(f.workflows[WF].get(2).skill_pins)
    new = lib.materialise(f.workflows[WF].get(3).skill_pins)
    assert (old / "skills/owasp-check/SKILL.md").read_text() == SKILL_V1
    assert (new / "skills/owasp-check/SKILL.md").read_text() == SKILL_V2
    detail = actions.get_skill(f, "owasp-check")
    assert [v.current for v in detail.versions].count(True) == 1 and len(detail.versions) == 2
    assert detail.used_by == [WF]


async def test_remove_is_blocked_while_used(make_factory, gh):
    f = make_factory()
    _install(f)
    d = _use_in_workflow(f)
    d.publish("use it")
    with pytest.raises(SkillError, match="used by workflows"):
        actions.remove_skill(f, "owasp-check")
    spec = d.get()[1].agents["verifier"]
    d.upsert_agent(spec.model_copy(update={"skills": [s for s in spec.skills if s != "owasp-check"]}))
    d.publish("stop using it")
    remaining = actions.remove_skill(f, "owasp-check")
    assert "owasp-check" not in [s.name for s in remaining]
    # v2 pinned it; its content is still available for replays/audits
    assert f.workflows.library.materialise({"owasp-check": SHA1}) is not None
    with pytest.raises(SkillError, match="not found"):
        actions.get_skill(f, "owasp-check")


async def test_switching_source_needs_removal(make_factory, gh):
    f = make_factory()
    _install(f)
    other = SRC.model_copy(update={"repo": "evil/skills"})
    assert any("already installed from acme/skills" in p for p in actions.preview_skill(f, other).problems)


def test_runner_loads_imported_plugin_only_when_used(tmp_path: Path) -> None:
    base = AgentRequest(run_id="r", station="s", role="x", prompt="p", cwd=tmp_path, model="m", skills=["plow-ahead"])
    assert len(plugins_for(tmp_path, base)) == 1 and skill_refs(base) == ["agent-factory:plow-ahead"]
    req = AgentRequest(**{**base.__dict__, "imported_skills": ["owasp-check"], "imported_plugin": tmp_path / "p"})
    assert plugins_for(tmp_path, req)[1]["path"] == str(tmp_path / "p")
    assert skill_refs(req) == ["agent-factory:plow-ahead", "imported-skills:owasp-check"]


async def test_skill_endpoints(make_factory, gh):
    app, ctx, c = await _client(make_factory())
    async with c:
        r = await c.post("/api/skills/preview", json=SRC.model_dump())
        assert r.status_code == 200 and r.json()["scripts"] == ["scripts/scan.sh"]
        r = await c.post("/api/skills/install", json={**SRC.model_dump(), "sha": SHA1})
        assert r.status_code == 409 and "accept_scripts" in r.text
        r = await c.post("/api/skills/install", json={**SRC.model_dump(), "sha": SHA1, "accept_scripts": True})
        assert r.status_code == 200 and r.json()["sha"] == SHA1
        assert "owasp-check" in [s["name"] for s in (await c.get("/api/skills")).json()]
        assert (await c.get("/api/skills/owasp-check")).json()["files"]["SKILL.md"] == SKILL_V1
        assert (await c.get("/api/skills/nope")).status_code == 404
        assert (await c.delete("/api/skills/owasp-check")).status_code == 200
    await ctx.__aexit__(None, None, None)


def test_skill_tool_is_available_only_when_skills_are_listed(tmp_path: Path) -> None:
    """Regression: `tools` is the SDK's base tool set; without Skill in it the
    agent could never load a skill (built-in or imported)."""
    base = AgentRequest(run_id="r", station="s", role="x", prompt="p", cwd=tmp_path, model="m", tools=["Read"])
    assert sdk_tools(base) == ["Read"]
    with_skill = AgentRequest(**{**base.__dict__, "skills": ["factory-station-contract"]})
    assert sdk_tools(with_skill) == ["Read", "Skill"] and with_skill.tools == ["Read"]
