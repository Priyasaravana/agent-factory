"""Python versions: what must match, and what may differ.

- The engine and auth run on one Python, and CI tests exactly that one
  (factory image = auth image = CI `--python`).
- Generated apps run their tests in the sandbox with the version they pin, from
  the interpreters baked into the sandbox image (no downloads at run time); the
  golden path's pin must be one of them and must match its own runtime image.
So the factory and the apps can upgrade Python independently, and a mismatch
fails here instead of in every live run."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _from_python(dockerfile: Path) -> set[str]:
    return set(re.findall(r"^FROM python:(\d+\.\d+)", dockerfile.read_text(), re.M))


def test_engine_and_auth_images_run_the_python_ci_tests():
    ci = set(re.findall(r"--python (\d+\.\d+)", (ROOT / ".github" / "workflows" / "ci.yml").read_text()))
    factory = _from_python(ROOT / "images" / "factory" / "Dockerfile")
    auth = _from_python(ROOT / "images" / "auth" / "Dockerfile")
    assert len(ci) == 1 and factory == auth == ci, {"ci": ci, "factory": factory, "auth": auth}


def test_the_sandbox_provides_the_python_the_golden_path_pins():
    template = ROOT / "templates" / "fastapi-service"
    pinned = (template / ".python-version").read_text().strip()
    m = re.search(r'^ARG APP_PYTHONS="([^"]+)"', (ROOT / "images" / "sandbox" / "Dockerfile").read_text(), re.M)
    assert m, "images/sandbox/Dockerfile must declare APP_PYTHONS"
    assert pinned in m.group(1).split(), f"sandbox offers {m.group(1)}; golden path pins {pinned}"
    assert _from_python(template / "Dockerfile") == {pinned}, "app tests and app runtime use the same Python"
