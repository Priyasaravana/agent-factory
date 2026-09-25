from agent_factory.config import FactoryConfig


def test_line_skips_repair_station(cfg: FactoryConfig) -> None:
    line = [s.id for s in cfg.forward_stations()]
    assert line == ["intake", "design", "build", "verify", "package", "deploy", "acceptance", "deliver"]
    assert cfg.next_forward("deploy") == "acceptance"
    assert cfg.station("deploy_fix").next == "deploy"


def test_routes_reference_real_stations(cfg: FactoryConfig) -> None:
    ids = {s.id for s in cfg.stations}
    assert all(s.on_fail in ids for s in cfg.stations if s.on_fail)


def test_every_role_has_prompt_and_skills(cfg: FactoryConfig) -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    for s in cfg.stations:
        if s.role:
            assert (repo / "prompts" / f"{s.role}.md").exists(), s.role
            for skill in cfg.role_skills.get(s.role, []):
                assert (repo / "plugin" / "skills" / skill / "SKILL.md").exists(), skill


def test_unknown_route_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        FactoryConfig.model_validate(
            {
                "product_lines": {"x": {"template": "t"}},
                "stations": [{"id": "a", "kind": "check", "on_fail": "nope"}],
            }
        )
