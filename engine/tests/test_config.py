from pathlib import Path

import pytest

from agent_factory.config import FactoryConfig

REPO = Path(__file__).resolve().parents[2]


def test_config_points_at_a_blueprint(cfg: FactoryConfig) -> None:
    for line in cfg.product_lines.values():
        assert (REPO / line.workflow_template / "workflow.yaml").exists()
    assert cfg.models.resolve("judgment") == "opus"


def test_skill_overlay_only_for_used_skills(cfg: FactoryConfig) -> None:
    assert "plow-ahead" in cfg.skill_overlay(["plow-ahead"])
    assert cfg.skill_overlay(["quick-recap"]) == ""


def test_product_line_required() -> None:
    with pytest.raises(ValueError):
        FactoryConfig.model_validate({})
