from pathlib import Path

import pytest

from agent_factory.config import FactoryConfig

REPO = Path(__file__).resolve().parents[2]


def test_config_points_at_workflow_templates(cfg: FactoryConfig) -> None:
    for template in cfg.workflow_templates().values():
        assert (REPO / template / "workflow.yaml").exists()
    assert cfg.default_workflow in cfg.workflow_templates()
    assert cfg.models.resolve("judgment") == "opus"


def test_skill_overlay_only_for_used_skills(cfg: FactoryConfig) -> None:
    assert "plow-ahead" in cfg.skill_overlay(["plow-ahead"])
    assert cfg.skill_overlay(["quick-recap"]) == ""


def test_blueprint_required() -> None:
    with pytest.raises(ValueError):
        FactoryConfig.model_validate({})
