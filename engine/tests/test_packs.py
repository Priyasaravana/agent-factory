"""Stack packs (ADR-0041, step 1): the pack format with a `stack:` section, the readiness rule
interpreter and its full-coverage rule, blueprint -> pack binding with recorded overrides, the
stack skill for the agents that write code, and the pack's id, version and SHA-256 in evidence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import PRODUCT, REPO, wait_run

from agent_factory.config import Blueprint, FactoryConfig, load_config
from agent_factory.models import ChangeStatus
from agent_factory.packs import PackError, bind, folder_sha256, install, load_pack
from agent_factory.readiness import (
    SIGNALS,
    STACK_SIGNALS,
    RuleError,
    holds,
    parse_rule,
    parse_stack_rules,
    score,
)

FASTAPI = REPO / "packs" / "fastapi-service"


def _copy_pack(tmp_path: Path) -> Path:
    dst = tmp_path / "packs" / "fastapi-service"
    shutil.copytree(FASTAPI, dst, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    return dst


def _readiness(pack: Path) -> dict:
    return yaml.safe_load((pack / "readiness.yaml").read_text())


def _write_readiness(pack: Path, data: dict) -> None:
    (pack / "readiness.yaml").write_text(yaml.safe_dump(data))


# ------------------------------------------------------------ the format --
def test_the_fastapi_pack_is_a_stack_pack_that_proves_every_stack_signal():
    pack = load_pack(FASTAPI)
    assert pack.source == "fastapi-service@1.0.0" and pack.stack is not None
    assert pack.stack.builds == "service" and pack.stack.port == 8000 and pack.stack.verify == "make verify"
    assert pack.skill_name == "fastapi-golden-path"
    assert pack.rules is not None and {s.id for s in pack.rules.signals} == set(STACK_SIGNALS)
    assert pack.rules.extra == []
    assert len(pack.sha256) == 64


def test_stack_neutral_signals_stay_in_the_engine_and_the_split_is_the_adrs():
    assert set(STACK_SIGNALS) == {
        "linter",
        "formatter",
        "unit_tests",
        "lockfile",
        "coverage_gate",
        "acceptance_tests",
        "e2e_tests",
        "health_endpoints",
        "metrics",
        "tracing",
        "structured_logs",
    }
    assert {s.id for s in SIGNALS if s.check is not None} == {
        "readme",
        "agents_md",
        "precommit",
        "codeowners",
        "dependency_updates",
        "container_nonroot",
        "verify_target",
        "ci",
        "design_docs",
        "requirements_traced",
    }


def test_a_pack_that_leaves_a_stack_signal_unproved_is_refused(tmp_path):
    pack = _copy_pack(tmp_path)
    data = _readiness(pack)
    del data["signals"]["metrics"]
    _write_readiness(pack, data)
    with pytest.raises(PackError, match="does not prove metrics"):
        load_pack(pack)


def test_a_pack_may_not_redefine_a_stack_neutral_signal(tmp_path):
    pack = _copy_pack(tmp_path)
    data = _readiness(pack)
    data["signals"]["readme"] = {"check": {"file": "README.md"}}
    _write_readiness(pack, data)
    with pytest.raises(PackError, match="readme: is stack-neutral"):
        load_pack(pack)


def test_a_signal_of_the_packs_own_names_a_level_a_pillar_and_a_title(tmp_path):
    pack = _copy_pack(tmp_path)
    data = _readiness(pack)
    data["signals"]["a11y"] = {"check": {"file": "a11y.json"}, "level": 3, "pillar": "looks-nice"}
    _write_readiness(pack, data)
    with pytest.raises(PackError, match="`pillar` must be one of"):
        load_pack(pack)


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "~/x", "a/../../b"])
def test_rules_only_read_inside_the_repo(path):
    with pytest.raises(RuleError, match="inside the repo"):
        parse_rule({"file": path})
    with pytest.raises(RuleError, match="inside the repo"):
        parse_rule({"contains": {"glob": path, "text": "x"}})


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"exists": "x"}, "unknown rule kind"),
        ({"file": "a", "glob": "b"}, "exactly one key"),
        ({"contains": {"file": "a", "glob": "b", "text": "x"}}, "exactly one of file or glob"),
        ({"contains": {"file": "a"}}, "text"),
        ({"matches": {"file": "a", "regex": "("}}, "bad regex"),
        ({"all": []}, "non-empty list"),
    ],
)
def test_malformed_rules_are_refused_at_load(raw, match):
    with pytest.raises(RuleError, match=match):
        parse_rule(raw)


def test_the_rule_kinds_read_files_and_never_run_anything(tmp_path):
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"scripts": {"lint": "eslint . && prettier --check ."}}')
    (tmp_path / "src" / "deep" / "health.ts").write_text("export const route = '/api/healthz'\n")
    ok = {
        "file": {"file": "package.json"},
        "glob": {"glob": "src/**/*.ts"},
        "contains": {"contains": {"file": "package.json", "text": "prettier --check"}},
        "contains a glob": {"contains": {"glob": "src/**/*.ts", "text": "/api/healthz"}},
        "matches": {"matches": {"file": "package.json", "regex": r'"lint":\s*"eslint'}},
        "all": {"all": [{"file": "package.json"}, {"glob": "src/**/*.ts"}]},
        "any": {"any": [{"file": "missing"}, {"file": "package.json"}]},
    }
    for name, raw in ok.items():
        assert holds(parse_rule(raw), tmp_path), name
    no = [{"file": "src"}, {"glob": "*.lock"}, {"contains": {"file": "missing", "text": "x"}}, {"all": [{"file": "x"}]}]
    assert not any(holds(parse_rule(raw), tmp_path) for raw in no)


def test_a_packs_own_signal_counts_for_its_products_only(tmp_path):
    rules = load_pack(FASTAPI).rules
    assert rules is not None
    raw = {
        "signals": {
            **{s.id: {"check": {"file": "nothing-here"}, "hint": "h"} for s in rules.signals},
            "a11y": {"check": {"file": "a11y.json"}, "level": 3, "pillar": "usability", "title": "accessible"},
        },
        "requirement_tags": _readiness(FASTAPI)["requirement_tags"],
    }
    own = parse_stack_rules(raw)
    card = score(tmp_path, True, own)
    a11y = next((s, ok) for s, ok in card.results if s.id == "a11y")
    assert a11y[0].pillar == "usability" and a11y[1] is False
    assert next(p for p in card.pillars() if p["id"] == "usability")["applicable"] == 1
    (tmp_path / "a11y.json").write_text("{}")
    assert card.max_points == score(tmp_path, True, own).max_points == score(tmp_path, True, rules).max_points + 4
    assert all(s.id != "a11y" for s, _ in score(tmp_path, True, rules).results)


def test_without_a_pack_stack_signals_fail_and_say_why(tmp_path):
    card = score(tmp_path, True)
    stack = [s for s, ok in card.results if s.id in STACK_SIGNALS]
    assert len(stack) == len(STACK_SIGNALS) and all("blueprint's `pack`" in s.hint for s in stack)
    assert all(not ok for s, ok in card.results if s.id in STACK_SIGNALS)


def test_the_folder_digest_covers_every_file_but_not_local_caches(tmp_path):
    pack = _copy_pack(tmp_path)
    before = folder_sha256(pack)
    (pack / "template" / ".venv").mkdir()
    (pack / "template" / ".venv" / "x").write_text("local")
    (pack / "template" / "app" / "__pycache__").mkdir(exist_ok=True)
    (pack / "template" / "app" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    assert folder_sha256(pack) == before == folder_sha256(FASTAPI)
    (pack / "template" / "README.md").write_text("changed\n")
    assert folder_sha256(pack) != before


# --------------------------------------------------------------- binding --
def test_phases_packs_is_read_as_an_alias_of_packs():
    cfg = FactoryConfig.model_validate(
        {"blueprints": {}, "packs": ["packs/a"], "phases": {"packs": ["packs/ops-pack", "packs/a"]}}
    )
    assert cfg.packs == ["packs/a", "packs/ops-pack"] and cfg.phases.packs == []


def test_a_blueprint_that_builds_new_products_names_a_pack():
    with pytest.raises(ValueError, match="needs a `pack`"):
        Blueprint()
    assert Blueprint(pack="fastapi-service").template is None


def test_the_fastapi_blueprint_takes_template_verify_stack_port_and_chart_from_its_pack():
    cfg = load_config(REPO / ".agent-factory" / "config.yaml")
    install(cfg, REPO)
    bp = cfg.blueprints["fastapi-service"]
    assert bp.template == "packs/fastapi-service/template" and (REPO / bp.template / "Makefile").is_file()
    assert bp.verify_command == "make verify" and bp.service_port == 8000 and bp.chart_path == "deploy/chart"
    assert bp.stack == ["python", "fastapi", "postgres", "sqlalchemy", "docker", "helm", "kubernetes"]
    assert bp.pack_binding is not None and bp.pack_binding.overrides == {}
    assert bp.readiness_rules is not None and {s.id for s in bp.readiness_rules.signals} == set(STACK_SIGNALS)
    install(cfg, REPO)  # binding again (a second factory on the same config) changes nothing
    assert cfg.blueprints["fastapi-service"].pack_binding.overrides == {}  # type: ignore[union-attr]


def _cfg(**blueprint: object) -> FactoryConfig:
    return FactoryConfig.model_validate(
        {"packs": ["packs/fastapi-service"], "blueprints": {"api": {"pack": "fastapi-service", **blueprint}}}
    )


def test_a_field_the_config_sets_anyway_overrides_the_pack_and_is_recorded():
    cfg = _cfg(service_port=9000, verify_command="make verify")
    install(cfg, REPO)
    bp = cfg.blueprints["api"]
    assert bp.service_port == 9000, "the config wins"
    assert bp.pack_binding is not None
    assert bp.pack_binding.overrides == {"service_port": {"pack": 8000, "config": 9000}}, "same value: no override"
    assert bp.pack_binding.evidence()["overrides"] == {"service_port": {"pack": 8000, "config": 9000}}


def test_binding_refuses_an_unknown_pack_one_without_a_stack_and_a_repo_blueprint():
    cfg = FactoryConfig.model_validate({"blueprints": {"api": {"pack": "nextjs-web"}}})
    with pytest.raises(PackError, match="names pack 'nextjs-web', which is not installed"):
        install(cfg, REPO)
    ops = load_pack(REPO / "packs" / "ops-pack")
    cfg = FactoryConfig.model_validate({"blueprints": {"api": {"pack": "ops-pack"}}})
    with pytest.raises(PackError, match="has no `stack:` section"):
        bind(cfg, [ops], REPO)
    cfg = FactoryConfig.model_validate(
        {"blueprints": {"r": {"target": "repo", "pack": "fastapi-service"}}, "packs": ["packs/fastapi-service"]}
    )
    with pytest.raises(PackError, match="only blueprints that build new products"):
        install(cfg, REPO)
    cfg = FactoryConfig.model_validate({"blueprints": {}, "packs": ["packs/fastapi-service", "packs/fastapi-service"]})
    with pytest.raises(PackError, match="listed twice"):
        install(cfg, REPO)


# ------------------------------------------------------ a change, end to end --
async def test_a_change_records_its_pack_and_the_coding_agents_get_its_skill(make_factory):
    f = make_factory()
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    pack = load_pack(FASTAPI)
    expected = {"id": "fastapi-service", "version": "1.0.0", "sha256": pack.sha256, "overrides": {}}

    events = f.store.list_events(change.id)
    recorded = [e for e in events if "stack_pack" in e.data]
    assert len(recorded) == 1 and recorded[0].data["stack_pack"] == expected
    assert recorded[0].message == f"stack pack fastapi-service@1.0.0 (sha256 {pack.sha256[:12]})"
    card = next(e for e in events if e.message.startswith("agent readiness"))
    assert card.data["readiness"]["level"] == 3, "the pack's rules hold the product at Level 3"

    out = f.manager.ws.data_dir / "artifacts" / change.id
    assert json.loads((out / "provenance.json").read_text())["materials"]["pack"] == expected
    assert json.loads((out / "manifest.json").read_text())["pack"] == expected

    # the run's own calls, not the next-feature suggestions after the delivery (read only, no pack skill)
    prompts = {c.role: c.system_prompt for c in f.manager.agents.calls if c.station != "suggest"}
    skill = "### Skill: fastapi-golden-path"
    assert skill in prompts["architect"] and skill in prompts["developer"]
    assert all(skill not in p for role, p in prompts.items() if role not in {"architect", "developer", "devops"})
