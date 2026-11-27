"""The licence boundary (ADR-0051): the Apache-2.0 core never depends on `licensed/`, and everything in
`licensed/` says which licence it is under. CI fails here before a paid feature can leak into core, or core
start needing one."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LICENSED = REPO / "licensed"
SPDX = "SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial"
CODE = {".py", ".ts", ".tsx", ".js", ".mjs", ".sh"}
CORE_DIRS = ("engine", "auth", "web/src", "scripts", "packs", "plugin", "cluster", "images", "workflow-templates")
SKIP = {"node_modules", ".venv", "__pycache__", "dist", ".cache"}
_IMPORT = re.compile(r"^\s*(from|import)\s+agent_factory_licensed\b|['\"][./]*licensed/", re.M)


def _files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and not (SKIP & set(p.relative_to(REPO).parts)):
            yield p


def test_nothing_outside_licensed_imports_it():
    offenders = []
    for d in CORE_DIRS:
        for p in _files(REPO / d):
            if p.suffix in CODE and p.name != Path(__file__).name and _IMPORT.search(p.read_text(errors="ignore")):
                offenders.append(str(p.relative_to(REPO)))
    assert offenders == [], f"core code reaching into licensed/: {offenders}"


def test_the_core_package_does_not_depend_on_the_licensed_one():
    for pyproject in (REPO / "engine" / "pyproject.toml", REPO / "auth" / "pyproject.toml"):
        assert "agent-factory-licensed" not in pyproject.read_text()


def test_every_source_file_in_licensed_carries_its_licence_header():
    missing = []
    for p in _files(LICENSED):
        if p.name in {"LICENSE", "NOTICE", "uv.lock", "README.md"} or p.suffix not in CODE | {".toml", ".txt"}:
            continue
        head = "\n".join(p.read_text().splitlines()[:3])
        if SPDX not in head:
            missing.append(str(p.relative_to(REPO)))
    assert missing == [], f"no licence header: {missing}"
    assert (LICENSED / "LICENSE").is_file() and (LICENSED / "NOTICE").is_file()


def test_the_root_licence_maps_directories_to_licences():
    text = (REPO / "LICENSE").read_text()
    head = text.split("-" * 20, 1)[0]  # the map, above the rule that starts the Apache text
    assert '"licensed/"' in head and "licensed/LICENSE" in head and "Apache License, Version 2.0" in head
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in text
    assert "END OF TERMS AND CONDITIONS" in text


@pytest.mark.parametrize("image", ["factory", "auth"])
def test_the_core_image_is_built_without_licensed(image):
    """Only the enterprise stage of each image copies licensed/; the core stage, and the default target, never see
    it (the release job also proves it on the built images)."""
    df = (REPO / "images" / image / "Dockerfile").read_text()
    stages = re.split(r"^FROM\s", df, flags=re.M)[1:]
    named = {re.match(r"\S+(?:\s+AS\s+(\S+))?", s, re.I).group(1) or "": s for s in stages}  # type: ignore[union-attr]
    assert "core" in named and "enterprise" in named
    for name, body in named.items():
        copies = re.findall(r"^COPY\s.*\blicensed\b", body, re.M)
        assert bool(copies) == (name == "enterprise"), f"stage {name or '(last)'}: {copies}"
    last = stages[-1].split()[0]
    assert last == "core", "the default target (the last stage) is core"
