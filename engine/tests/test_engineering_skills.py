"""Engineering standards skills (ADR-0032): one set of skills, used by people working on
this repo in Claude Code (.claude/skills) and by the factory's agents (plugin/skills)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_factory.workflow import load_workflow_dir

REPO = Path(__file__).resolve().parents[2]
CLAUDE = REPO / ".claude" / "skills"
PLUGIN = REPO / "plugin" / "skills"
ENGINEERING = {
    "well-architected-standards",
    "security-pillar",
    "reliability-pillar",
    "performance-pillar",
    "operability-pillar",
    "cost-pillar",
    "sustainability-pillar",
    "devsecops-practices",
    "iso-12207-sdlc",
}
# which agent uses which skills, per stage (the workflow templates must keep at least these)
EXPECTED = {
    "intake": {"iso-12207-sdlc", "well-architected-standards"},
    "architect": ENGINEERING - {"devsecops-practices", "iso-12207-sdlc"},
    "developer": {"devsecops-practices", "security-pillar"},
    "reviewer": {"well-architected-standards", "security-pillar", "reliability-pillar", "performance-pillar"},
    "devops": {"devsecops-practices", "reliability-pillar", "operability-pillar"},
    "security-reviewer": {"security-pillar", "devsecops-practices"},
    "assessor": ENGINEERING - {"iso-12207-sdlc"},
}


def _front(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} needs YAML front matter"
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_claude_code_and_the_factory_use_the_same_skills():
    assert {p.name for p in CLAUDE.iterdir() if p.is_dir()} == ENGINEERING
    for name in ENGINEERING:
        assert (CLAUDE / name / "SKILL.md").read_bytes() == (PLUGIN / name / "SKILL.md").read_bytes(), (
            f"{name}: .claude/skills and plugin/skills differ; edit plugin/skills and run `make skills-sync`"
        )


@pytest.mark.parametrize("name", sorted(ENGINEERING))
def test_each_skill_is_well_formed(name):
    meta = _front(PLUGIN / name / "SKILL.md")
    assert meta["name"] == name and 20 <= len(meta["description"]) <= 1024
    assert len((PLUGIN / name / "SKILL.md").read_text()) < 8_000  # loaded on demand, but kept short


@pytest.mark.parametrize("template", ["default", "api-security-review", "assess"])
def test_agents_get_the_skills_of_their_stage(template):
    doc = load_workflow_dir(REPO / "workflow-templates" / template)
    for aid, spec in doc.agents.items():
        want = EXPECTED.get(aid, set())
        assert want <= set(spec.skills), f"{template}/{aid} is missing {sorted(want - set(spec.skills))}"
        assert not set(spec.preload_skills) & ENGINEERING, "standards skills load on demand, never preloaded"
    assert "verifier" not in EXPECTED  # acceptance stays focused on the hidden scenarios


def test_factory_guidance_keeps_the_standards_in_proportion(cfg):
    for name in ("well-architected-standards", "devsecops-practices"):
        assert "request" in cfg.skill_prompts[name]
    assert "never a blocker" in cfg.skill_prompts["well-architected-standards"]
