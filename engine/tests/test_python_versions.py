"""One Python version everywhere that runs our code or the code agents write.

The sandbox runs the generated app's tests with the template's pinned Python and
no interpreter downloads (UV_PYTHON_DOWNLOADS=never), and the app ships on the
template's runtime image. If the images, CI or the template drift apart, verify
fails in every live run (or passes on a different Python than production)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _from_python(dockerfile: Path) -> set[str]:
    return set(re.findall(r"^FROM python:(\d+\.\d+)", dockerfile.read_text(), re.M))


def test_images_ci_and_golden_path_use_the_same_python_minor():
    template = (ROOT / "templates" / "fastapi-service" / ".python-version").read_text().strip()
    versions = {
        "template .python-version": {template},
        "template Dockerfile": _from_python(ROOT / "templates" / "fastapi-service" / "Dockerfile"),
        "sandbox image": _from_python(ROOT / "images" / "sandbox" / "Dockerfile"),
        "factory image": _from_python(ROOT / "images" / "factory" / "Dockerfile"),
        "auth image": _from_python(ROOT / "images" / "auth" / "Dockerfile"),
        "CI": set(re.findall(r"--python (\d+\.\d+)", (ROOT / ".github" / "workflows" / "ci.yml").read_text())),
    }
    assert all(versions.values()), versions
    assert all(v == {template} for v in versions.values()), f"Python versions drifted: {versions}"
