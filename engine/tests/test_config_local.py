"""The config split (phase B): the tracked config.yaml is the shipped default, an install's own edits go in
the gitignored config.local.yaml, merged over it. A pull never collides with an install's settings again."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from agent_factory import cli
from agent_factory.config import LOCAL_NAME, diff, load_config, load_factory_config, local_path_for, merge
from agent_factory.settings import Settings

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / ".agent-factory" / "config.yaml"


# -------------------------------------------------------------------- pure --
def test_mappings_merge_key_by_key_and_everything_else_replaces():
    base = {"factory": {"name": "f", "max": 1}, "packs": ["a", "b"], "timezone": "UTC", "models": {"x": 1}}
    local = {"factory": {"max": 2}, "packs": ["c"], "new": {"k": True}, "models": None}
    got, paths = merge(base, local)
    assert got == {
        "factory": {"name": "f", "max": 2},
        "packs": ["c"],
        "timezone": "UTC",
        "models": None,
        "new": {"k": True},
    }
    assert paths == ["factory.max", "packs", "new", "models"]
    assert base["factory"]["max"] == 1, "the default is never changed in place"


def test_diff_is_the_smallest_override_and_merges_back_exactly():
    base = {"factory": {"name": "f", "max": 1, "deep": {"a": 1, "b": 2}}, "packs": ["a"], "tz": "UTC"}
    mine = {"factory": {"name": "f", "max": 3, "deep": {"a": 1, "b": 5}}, "packs": ["a", "b"], "tz": "UTC", "x": 1}
    over, dropped = diff(base, mine)
    assert over == {"factory": {"max": 3, "deep": {"b": 5}}, "packs": ["a", "b"], "x": 1} and not dropped
    assert merge(base, over)[0] == mine
    assert diff(base, base) == ({}, [])


def test_a_removed_key_is_reported_never_guessed():
    _, dropped = diff({"a": 1, "b": {"c": 1, "d": 2}}, {"b": {"c": 1}})
    assert dropped == ["b.d", "a"]


# ------------------------------------------------------------------ loading --
def test_the_override_beside_the_config_is_read_only_when_asked(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG.read_text())
    local = local_path_for(cfg)
    assert local.name == LOCAL_NAME
    local.write_text("factory:\n  max_concurrent_changes: 3\nmodels:\n  default: opus\n")
    assert load_config(cfg).factory.max_concurrent_changes == 1, "no override unless the caller passes one"
    got = load_config(cfg, local)
    assert got.factory.max_concurrent_changes == 3 and got.models.default == "opus"
    assert got.factory.data_dir == load_config(cfg).factory.data_dir, "the rest of the mapping is kept"
    assert got.local_overrides == ["factory.max_concurrent_changes", "models.default"]
    assert load_config(cfg).local_overrides == []
    s = Settings(factory_config=str(cfg), factory_config_local=str(local))
    assert load_factory_config(s).factory.max_concurrent_changes == 3
    assert load_factory_config(Settings(factory_config=str(cfg))).local_overrides == []
    missing = Settings(factory_config=str(cfg), factory_config_local=str(tmp_path / "none.yaml"))
    assert load_factory_config(missing).local_overrides == [], "an install without an override starts as before"


def test_an_override_is_checked_like_the_config(tmp_path):
    local = tmp_path / LOCAL_NAME
    local.write_text("- not\n- a mapping\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(CONFIG, local)
    local.write_text("factory:\n  max_concurrent_changes: lots\n")
    with pytest.raises(ValueError, match="max_concurrent_changes"):
        load_config(CONFIG, local)


# --------------------------------------------------------- moving edits once --
def _edited(tmp_path: Path) -> Path:
    doc = yaml.safe_load(CONFIG.read_text())
    doc["factory"]["max_concurrent_changes"] = 2
    doc["timezone"] = "Asia/Kolkata"
    doc["packs"] = [*doc.get("packs", []), "packs/fastapi-service"][:1]
    doc.setdefault("environments", {})["staging"] = {"provider": "helm", "kubeconfig_ref": "env://STAGING_KUBECONFIG"}
    mine = tmp_path / "config.yaml"
    mine.write_text(yaml.safe_dump(doc, sort_keys=False))
    return mine


def test_split_local_writes_an_override_that_loads_exactly_as_the_edited_file(tmp_path, capsys):
    mine = _edited(tmp_path)
    out = tmp_path / LOCAL_NAME
    cli._config_split(CONFIG, mine, out, force=False)
    printed = capsys.readouterr().out
    over = yaml.safe_load(out.read_text())
    assert over["factory"] == {"max_concurrent_changes": 2} and over["timezone"] == "Asia/Kolkata"
    assert "blueprints" not in over, "only what changed"
    assert load_config(CONFIG, out).model_dump() == load_config(mine).model_dump()
    assert "factory.max_concurrent_changes" in printed and "git checkout" in printed
    assert out.read_text().startswith("# This install's own settings"), "says what it is"


def test_split_local_never_replaces_an_override_or_drops_a_setting(tmp_path):
    out = tmp_path / LOCAL_NAME
    out.write_text("factory: {}\n")
    with pytest.raises(SystemExit, match="exists"):
        cli._config_split(CONFIG, _edited(tmp_path), out, force=False)
    assert out.read_text() == "factory: {}\n"
    doc = yaml.safe_load(CONFIG.read_text())
    del doc["timezone"]
    mine = tmp_path / "removed.yaml"
    mine.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit, match="timezone"):
        cli._config_split(CONFIG, mine, tmp_path / "other.yaml", force=False)
    assert not (tmp_path / "other.yaml").exists()


def test_an_unedited_config_splits_to_an_empty_override(tmp_path):
    out = tmp_path / LOCAL_NAME
    cli._config_split(CONFIG, CONFIG, out, force=False)
    assert yaml.safe_load(out.read_text()) == {}
    assert load_config(CONFIG, out).local_overrides == []


# --------------------------------------------------------------- the wiring --
def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_the_override_is_gitignored_and_its_example_loads(tmp_path):
    res = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", ".agent-factory/config.local.yaml"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
    )
    assert res.returncode == 0, "config.local.yaml must never be committed"
    example = REPO / ".agent-factory" / "config.local.example.yaml"
    assert yaml.safe_load(example.read_text()) is None, "the example is all comments: copying it changes nothing"
    examples = example.read_text().split("# --- examples", 1)[1].split("\n", 1)[1]
    uncommented = "\n".join(line.removeprefix("# ") for line in examples.splitlines() if line.strip() != "#")
    over = yaml.safe_load(uncommented)
    assert load_config(CONFIG, _write(tmp_path / LOCAL_NAME, uncommented)).local_overrides
    assert set(over) <= set(yaml.safe_load(CONFIG.read_text())), "every example sets a real section"


def test_the_image_reads_the_override_and_upgrade_refuses_edits_to_the_default():
    docker = (REPO / "images" / "factory" / "Dockerfile").read_text()
    assert docker.count("FACTORY_CONFIG_LOCAL=/opt/factory/.agent-factory/config.local.yaml") == 2
    make = (REPO / "Makefile").read_text()
    upgrade = make.split("\nupgrade:", 1)[1].split("\n\n", 1)[0]
    assert "git diff --quiet -- .agent-factory/config.yaml" in upgrade
    assert upgrade.index("git diff --quiet") < upgrade.index("git pull"), "checked before anything is pulled"
    assert "config-split:" in make
