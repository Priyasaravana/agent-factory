"""Scanner images are pinned by version and index digest, never a moving tag (roadmap J1, "pin the scanner images").

A moving tag changes what the factory runs without a change in this repo: findings shift between two
runs of the same product, and a re-pointed tag runs whatever was pushed (Trivy's `latest` was
re-pointed to malicious images in March 2026). Every Trivy reference uses one version: the image
scan, the config check and CI share rules and the `trivy-cache` DB volume.
"""

import re
from pathlib import Path

from agent_factory.config import FactoryConfig
from agent_factory.tools import TOOLS

REPO = Path(__file__).resolve().parents[2]
PINNED = ("aquasec/trivy", "anchore/syft")
# a reference: the image name (not `aquasec/trivy-db`), an optional tag, an optional digest
REF = re.compile(
    r"(?P<name>aquasec/trivy|anchore/syft)(?![\w.-])(?::(?P<tag>[\w.-]+))?(?:@sha256:(?P<digest>[0-9a-f]{64}))?"
)


def _refs(text: str) -> list[re.Match[str]]:
    return list(REF.finditer(text))


def _sources(cfg: FactoryConfig) -> dict[str, str]:
    """Every place the factory names a scanner image: blueprint commands, the tool catalogue, CI."""
    out = {f"tool {t.id}": t.image for t in TOOLS.values()}
    for name, bp in cfg.blueprints.items():
        for field in ("scan_command", "sbom_command", "secret_scan_command"):
            out[f"blueprint {name}.{field}"] = getattr(bp, field) or ""
    out["ci.yml"] = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    return out


def _pinned(m: re.Match[str]) -> bool:
    tag = m["tag"]
    return bool(m["digest"]) or bool(tag and tag != "latest" and re.fullmatch(r"v?\d+\.\d+\.\d+", tag))


def test_the_pattern_reads_a_reference() -> None:
    assert [_pinned(m) for m in _refs("aquasec/trivy:latest image a:1")] == [False]
    assert [_pinned(m) for m in _refs("anchore/syft {image}")] == [False]
    assert [_pinned(m) for m in _refs("aquasec/trivy:0.76.0 image")] == [True]
    assert [_pinned(m) for m in _refs("anchore/syft:v1.54.1 x")] == [True]
    assert [_pinned(m) for m in _refs(f"aquasec/trivy:0.76.0@sha256:{'a' * 64} image")] == [True]
    assert _refs("mirror.gcr.io/aquasec/trivy-db:2") == []


def test_trivy_and_syft_are_pinned_by_version_everywhere(cfg: FactoryConfig) -> None:
    sources = _sources(cfg)
    found = {name: [m for m in _refs(text)] for name, text in sources.items()}
    loose = [f"{where}: {m.group(0)}" for where, ms in found.items() for m in ms if not _pinned(m)]
    assert not loose, f"scanner images on a moving tag: {loose}"
    names = {m["name"] for ms in found.values() for m in ms}
    assert names == set(PINNED), "the image scan (Trivy) and the SBOM (syft) are both configured"


def test_one_trivy_version_across_the_factory_and_ci(cfg: FactoryConfig) -> None:
    refs = {
        (m["tag"], m["digest"]) for text in _sources(cfg).values() for m in _refs(text) if m["name"] == "aquasec/trivy"
    }
    assert len(refs) == 1, f"Trivy references differ: {sorted(map(str, refs))}"


def test_every_scanner_reference_carries_its_digest(cfg: FactoryConfig) -> None:
    """A version tag can still be re-pushed; the index digest can't. Digests resolved 2026-10-28."""
    bare = [
        f"{where}: {m.group(0)}"
        for where, text in _sources(cfg).items()
        for m in _refs(text)
        if not (m["tag"] and m["digest"])
    ]
    assert not bare, f"scanner images without a version tag and an @sha256 digest: {bare}"
