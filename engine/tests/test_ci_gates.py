"""The factory's own CI has security gates (assessment 2026-11-15, S-08)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"


def _steps(workflow: str, job: str) -> list[dict]:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return doc["jobs"][job]["steps"]


def test_every_action_is_pinned_to_a_commit_with_its_version_named():
    for path in WORKFLOWS.glob("*.yml"):
        for line in path.read_text().splitlines():
            m = re.search(r"uses: (\S+)", line)
            if not m or m.group(1).startswith("./"):
                continue
            ref = m.group(1).rsplit("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{path.name}: {m.group(1)} is a tag, not a commit"
            assert re.search(r"# v\d+\.\d+\.\d+$", line), f"{path.name}: say which version {ref[:7]} is"


def test_the_whole_history_is_scanned_for_secrets_with_a_verified_tool():
    doc = yaml.safe_load((WORKFLOWS / "security.yml").read_text())
    job = doc["jobs"]["secrets"]
    checkout = job["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0, "every commit, not the tip"
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "sha256sum -c" in runs and re.fullmatch(r"[0-9a-f]{64}", job["env"]["GITLEAKS_SHA256"])
    assert '--log-opts="--all"' in runs and "--exit-code 1" in runs and "--config .gitleaks.toml" in runs
    assert not any(s.get("continue-on-error") for s in job["steps"])


def test_the_secret_allowlist_names_values_never_paths_or_rules():
    conf = (REPO / ".gitleaks.toml").read_text()
    assert "useDefault = true" in conf
    for key in ("paths", "commits", "disabledRules", "stopwords"):
        assert not re.search(rf"^\s*{key}\s*=", conf, re.M), f"{key} would hide real secrets"
    for pattern in re.findall(r"'''(.*?)'''", conf):
        assert pattern.startswith("^") and pattern.endswith("$"), f"anchored: {pattern}"


def test_locked_dependencies_and_the_ui_bundle_are_audited_and_block():
    steps = _steps("security.yml", "dependencies")
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "for d in engine auth" in runs and "pip-audit" in runs and "--strict" in runs
    assert "uv export --frozen" in runs, "what the lockfile pins, not a fresh resolve"
    assert "npm audit --omit=dev --audit-level=high" in runs
    assert not any(s.get("continue-on-error") for s in steps)


def test_static_analysis_covers_the_engine_the_ui_and_the_workflows():
    doc = yaml.safe_load((WORKFLOWS / "security.yml").read_text())
    job = doc["jobs"]["codeql"]
    assert set(job["strategy"]["matrix"]["language"]) == {"python", "javascript-typescript", "actions"}
    assert job["permissions"]["security-events"] == "write"
    assert doc["permissions"] == {"contents": "read"}, "write only where the job needs it"


def test_the_images_an_install_runs_are_scanned_and_a_fixable_critical_fails():
    steps = _steps("ci.yml", "images")
    scan = next(s for s in steps if "sandbox images have no fixable critical" in s.get("name", ""))
    assert not scan.get("continue-on-error"), "blocking"
    for img in ("af-factory:ci", "af-auth:ci", "af-web:ci", "af-sandbox:ci"):
        assert img in scan["run"]
    assert "--severity CRITICAL --ignore-unfixed --exit-code 1" in scan["run"]
    names = [s.get("name", "") for s in steps]
    assert names.index(scan["name"]) > names.index("build the sandbox image"), "after every image is built"
