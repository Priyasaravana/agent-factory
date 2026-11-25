"""The cluster tools the factory image ships stay consistent with the cluster it creates (S-07, S-08)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "images" / "factory" / "Dockerfile").read_text()


def _arg(name: str) -> str:
    m = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.M)
    assert m, f"ARG {name} (one per line, no trailing comment: Dockerfile ARG lines have none)"
    return m.group(1)


def _minor(version: str) -> int:
    return int(re.match(r"v?1\.(\d+)\.", version).group(1))


def test_the_node_image_is_pinned_by_digest_and_kubectl_is_within_one_minor_of_it():
    conf = (REPO / "cluster" / "kind-config.yaml").read_text()
    m = re.search(r"image: kindest/node:(v1\.\d+\.\d+)@sha256:[0-9a-f]{64}$", conf, re.M)
    assert m, "the cluster's Kubernetes version is chosen here, not by kind's default"
    assert abs(_minor(_arg("KUBECTL_VERSION")) - _minor(m.group(1))) <= 1, "kubectl's supported version skew"


def test_ci_creates_its_cluster_with_the_same_kind_and_node_image():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert f"KIND_VERSION: {_arg('KIND_VERSION')} " in ci
    assert f"KIND_SHA256: {_arg('KIND_SHA256_AMD64')}" in ci
    assert "--image \"$(grep -oE 'kindest/node:[^ ]+' cluster/kind-config.yaml)\"" in ci


def test_every_tool_download_is_checked_before_it_is_installed():
    for tool in ("kind", "kubectl", "helm"):
        step = next(b for b in DOCKERFILE.split("\nRUN ") if f"/usr/local/bin/{tool}" in b and "curl" in b)
        assert "sha256sum -c -" in step, tool
        assert step.index("sha256sum -c -") < step.index("install -m 0755"), f"{tool}: checked, then installed"
    for arch in ("AMD64", "ARM64"):
        assert re.fullmatch(r"[0-9a-f]{64}", _arg(f"KIND_SHA256_{arch}"))
