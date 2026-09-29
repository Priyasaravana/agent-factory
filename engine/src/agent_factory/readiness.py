"""Agent-readiness scorecard for a generated repo (docs/practices.md §1).

Binary signals grouped by maturity level, after the Autonomy Maturity Model:
present or absent, no partial credit, each checkable in milliseconds from the
files (plus one secret scan the Readiness station runs). A repo is at level N
when every signal at levels 1..N passes; points (1/2/4 per signal by level)
show progress between levels.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POINTS = {1: 1, 2: 2, 3: 4}


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(errors="replace") if p.is_file() else ""


def _app_text(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in sorted((root / "app").rglob("*.py")) if p.is_file())


def _any_file(root: Path, pattern: str) -> bool:
    return any(p.is_file() for p in root.glob(pattern))


@dataclass(frozen=True)
class Signal:
    id: str
    level: int
    title: str
    check: Callable[[Path], bool]
    hint: str


SIGNALS: list[Signal] = [
    # Level 1: functional
    Signal("readme", 1, "README present", lambda r: (r / "README.md").is_file(), "add README.md"),
    Signal("linter", 1, "linter configured", lambda r: "[tool.ruff" in _read(r, "pyproject.toml"), "configure ruff"),
    Signal(
        "formatter",
        1,
        "formatter enforced",
        lambda r: "ruff format --check" in _read(r, "Makefile"),
        "run `ruff format --check` in `make lint`",
    ),
    Signal("unit_tests", 1, "unit tests exist", lambda r: _any_file(r, "tests/test_*.py"), "add tests/test_*.py"),
    Signal("lockfile", 1, "dependencies pinned", lambda r: (r / "uv.lock").is_file(), "commit uv.lock"),
    # Level 2: documented
    Signal("agents_md", 2, "AGENTS.md present", lambda r: (r / "AGENTS.md").is_file(), "keep AGENTS.md"),
    Signal(
        "verify_target",
        2,
        "one documented verify command",
        lambda r: re.search(r"^verify:", _read(r, "Makefile"), re.M) is not None,
        "keep the `verify` Makefile target",
    ),
    Signal(
        "container_nonroot",
        2,
        "reproducible, non-root container",
        lambda r: re.search(r"^USER\s+\S+", _read(r, "Dockerfile"), re.M) is not None,
        "keep `USER` in the Dockerfile",
    ),
    Signal(
        "precommit",
        2,
        "pre-commit hooks configured",
        lambda r: (r / ".pre-commit-config.yaml").is_file(),
        "keep .pre-commit-config.yaml",
    ),
    Signal(
        "codeowners",
        2,
        "CODEOWNERS present",
        lambda r: (r / ".github" / "CODEOWNERS").is_file() or (r / "CODEOWNERS").is_file(),
        "keep .github/CODEOWNERS",
    ),
    Signal(
        "structured_logs",
        2,
        "structured (JSON) logging",
        lambda r: "JsonFormatter" in _app_text(r) and "configure_logging" in _app_text(r),
        "keep app/observability.py wired in app/main.py",
    ),
    Signal(
        "dependency_updates",
        2,
        "automated dependency updates",
        lambda r: (r / ".github" / "dependabot.yml").is_file(),
        "keep .github/dependabot.yml",
    ),
    # Level 3: standardized (the production floor)
    Signal(
        "ci",
        3,
        "CI runs the verify command",
        lambda r: any("make verify" in p.read_text(errors="replace") for p in r.glob(".github/workflows/*.y*ml")),
        "keep .github/workflows/ci.yml running `make verify`",
    ),
    Signal(
        "coverage_gate",
        3,
        "coverage gate",
        lambda r: "--cov-fail-under=" in _read(r, "Makefile"),
        "keep `--cov-fail-under` in `make test`",
    ),
    Signal(
        "acceptance_tests",
        3,
        "acceptance scenarios are tests",
        lambda r: (r / "tests" / "test_acceptance.py").is_file(),
        "turn tests/acceptance/scenarios.yaml into tests/test_acceptance.py",
    ),
    Signal(
        "e2e_tests",
        3,
        "end-to-end tests",
        lambda r: _any_file(r, "tests/e2e/test_*.py"),
        "keep tests/e2e/ (smoke tests against a deployment)",
    ),
    Signal(
        "health_endpoints",
        3,
        "liveness and readiness endpoints",
        lambda r: all(s in _app_text(r) for s in ('"/healthz"', '"/readyz"')),
        "keep /healthz and /readyz",
    ),
    Signal(
        "metrics",
        3,
        "metrics endpoint",
        lambda r: '"/metrics"' in _app_text(r),
        "keep /metrics (app/observability.py)",
    ),
    Signal(
        "tracing",
        3,
        "distributed tracing hook",
        lambda r: "opentelemetry" in _app_text(r),
        "keep OpenTelemetry setup (app/observability.py)",
    ),
    Signal(
        "design_docs",
        3,
        "spec, design and API contract up to date",
        lambda r: all((r / "docs" / f).is_file() for f in ("spec.md", "design.md", "openapi.yaml")),
        "keep docs/spec.md, docs/design.md and docs/openapi.yaml",
    ),
]

SECRET_SCAN = Signal("secret_scan", 3, "no secrets in the repo", lambda r: False, "remove the secret and rotate it")


@dataclass
class Scorecard:
    results: list[tuple[Signal, bool]]

    @property
    def level(self) -> int:
        level = 0
        for n in (1, 2, 3):
            if all(ok for s, ok in self.results if s.level <= n):
                level = n
            else:
                break
        return level

    @property
    def points(self) -> int:
        return sum(POINTS[s.level] for s, ok in self.results if ok)

    @property
    def max_points(self) -> int:
        return sum(POINTS[s.level] for s, _ in self.results)

    def missing(self, up_to: int = 3) -> list[Signal]:
        return [s for s, ok in self.results if not ok and s.level <= up_to]

    def as_data(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "points": self.points,
            "max_points": self.max_points,
            "signals": [{"id": s.id, "level": s.level, "title": s.title, "ok": ok} for s, ok in self.results],
        }


def score(root: Path, secret_scan_ok: bool | None = None) -> Scorecard:
    results = []
    for s in SIGNALS:
        try:
            ok = bool(s.check(root))
        except OSError:
            ok = False
        results.append((s, ok))
    if secret_scan_ok is not None:
        results.append((SECRET_SCAN, secret_scan_ok))
    return Scorecard(results)
