"""Assessing an existing repository (ADR-0031). Pure functions of the repo's files.

- `parse_repo_url`: which repositories may be onboarded (https, allowed hosts, no credentials).
- `detect_stack`: languages, frameworks, build and deploy tooling, from marker files.
- `score`: agent-readiness signals that hold for any stack (the same ids and pillars as
  `readiness.py`, whose checks are specific to the factory's own golden path).
- `findings`: deterministic problems in Dockerfiles and Kubernetes manifests.
- `judge`: the assessor agent reports; the engine keeps only what the repo supports.
- `render_markdown`: the report people read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from agent_factory.pillars import BY_ID, IDS, tally

# ------------------------------------------------------------------ repo URL --
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class RepoUrlError(ValueError):
    pass


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.name}.git"

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.name}"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_repo_url(url: str, allowed_hosts: list[str]) -> RepoRef:
    """`https://<host>/<owner>/<repo>[.git]` on an allowed host; never credentials in the URL."""
    u = urlparse(url.strip())
    if u.scheme != "https":
        raise RepoUrlError("use an https URL, e.g. https://github.com/owner/repo")
    if u.username or u.password or "@" in u.netloc:
        raise RepoUrlError("don't put credentials in the URL: private repos use a token reference (ADR-0031)")
    host = (u.hostname or "").lower()
    if host not in {h.lower() for h in allowed_hosts}:
        raise RepoUrlError(f"host '{host}' is not allowed (existing_repos.allowed_hosts: {', '.join(allowed_hosts)})")
    if u.port or u.query or u.fragment:
        raise RepoUrlError("the URL must be just https://<host>/<owner>/<repo>")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) != 2:
        raise RepoUrlError("the URL must be https://<host>/<owner>/<repo>")
    owner, name = parts[0], parts[1].removesuffix(".git")
    if not (_NAME.match(owner) and _NAME.match(name)) or name in {".", ".."} or owner in {".", ".."}:
        raise RepoUrlError("owner and repository names may use letters, digits, '-', '_' and '.'")
    return RepoRef(host, owner, name)


# ------------------------------------------------------------------ the tree --
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", "__pycache__", ".tox", "target"}
MAX_FILES = 20_000
MAX_READ = 200_000  # bytes read per file for content checks


def files(root: Path) -> list[str]:
    """Repo-relative paths (posix), skipping dependency and build folders; capped."""
    out: list[str] = []

    def walk(d: Path) -> None:
        for p in sorted(d.iterdir()):
            if len(out) >= MAX_FILES:
                return
            if p.is_symlink():
                continue  # never follow links out of the repo
            if p.is_dir():
                if p.name not in SKIP_DIRS:
                    walk(p)
            elif p.is_file():
                out.append(p.relative_to(root).as_posix())

    walk(root)
    return out


def _read(root: Path, rel: str) -> str:
    p = root / rel
    if not p.is_file() or p.is_symlink():
        return ""
    with p.open("rb") as fh:
        return fh.read(MAX_READ).decode(errors="replace")


def _match(paths: list[str], pattern: str) -> list[str]:
    rx = re.compile(pattern)
    return [p for p in paths if rx.search(p)]


# --------------------------------------------------------------------- stack --
@dataclass
class Stack:
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    test_tools: list[str] = field(default_factory=list)
    build: list[str] = field(default_factory=list)  # docker, make …
    deploy: list[str] = field(default_factory=list)  # kubernetes, helm, argocd, terraform …
    ci: list[str] = field(default_factory=list)
    runtimes: dict[str, str] = field(default_factory=dict)  # e.g. {"node": "18"} from the Dockerfile

    def as_data(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    def summary(self) -> str:
        parts = [", ".join(self.languages) or "unknown language"]
        for label, vals in (("frameworks", self.frameworks), ("build", self.build), ("deploy", self.deploy)):
            if vals:
                parts.append(f"{label}: {', '.join(vals)}")
        return "; ".join(parts)


_LANG_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
    ".php": "php",
}
_NODE_FRAMEWORKS = {
    "express": "express",
    "fastify": "fastify",
    "koa": "koa",
    "next": "next.js",
    "react": "react",
    "vue": "vue",
    "@nestjs/core": "nestjs",
}
_NODE_TESTS = {"jest": "jest", "mocha": "mocha", "vitest": "vitest", "ava": "ava", "@playwright/test": "playwright"}
_PY_FRAMEWORKS = {"fastapi": "fastapi", "django": "django", "flask": "flask"}


def _package_json(root: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read(root, "package.json") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _node_deps(pkg: dict[str, Any]) -> set[str]:
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(key), dict):
            deps |= set(pkg[key])
    return deps


def _yaml_docs(root: Path, rel: str) -> Iterator[dict[str, Any]]:
    try:
        for doc in yaml.safe_load_all(_read(root, rel)):
            if isinstance(doc, dict):
                yield doc
    except yaml.YAMLError:
        return


def _manifests(root: Path, paths: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for p in _match(paths, r"\.ya?ml$"):
        if p.startswith(".github/"):
            continue
        for doc in _yaml_docs(root, p):
            if "apiVersion" in doc and "kind" in doc:
                out.append((p, doc))
    return out


def detect_stack(root: Path, paths: list[str] | None = None) -> Stack:
    paths = files(root) if paths is None else paths
    s = Stack()
    counts: dict[str, int] = {}
    for p in paths:
        lang = _LANG_EXT.get(Path(p).suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    s.languages = sorted(counts, key=lambda k: (-counts[k], k))

    pkg = _package_json(root)
    if pkg:
        deps = _node_deps(pkg)
        s.frameworks += [v for k, v in _NODE_FRAMEWORKS.items() if k in deps]
        s.test_tools += [v for k, v in _NODE_TESTS.items() if k in deps]
        if "javascript" not in s.languages and "typescript" not in s.languages:
            s.languages.append("javascript")
    for lock, pm in (
        ("package-lock.json", "npm"),
        ("yarn.lock", "yarn"),
        ("pnpm-lock.yaml", "pnpm"),
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("Pipfile.lock", "pipenv"),
        ("go.sum", "go modules"),
        ("Cargo.lock", "cargo"),
        ("Gemfile.lock", "bundler"),
    ):
        if lock in paths:
            s.package_managers.append(pm)
    if pkg and not any(pm in s.package_managers for pm in ("npm", "yarn", "pnpm")):
        s.package_managers.append("npm (no lockfile)")
    py_text = (_read(root, "pyproject.toml") + _read(root, "requirements.txt")).lower()
    s.frameworks += [v for k, v in _PY_FRAMEWORKS.items() if k in py_text]
    if "pytest" in py_text or _match(paths, r"(^|/)(test_[^/]*|[^/]*_test)\.py$"):
        s.test_tools.append("pytest")
    if "go" in s.languages and _match(paths, r"_test\.go$"):
        s.test_tools.append("go test")

    if _match(paths, r"(^|/)Dockerfile[^/]*$"):
        s.build.append("docker")
    if "Makefile" in paths:
        s.build.append("make")
    manifests = _manifests(root, paths)
    if manifests:
        s.deploy.append("kubernetes")
    if _match(paths, r"(^|/)Chart\.yaml$"):
        s.deploy.append("helm")
    if any(
        d.get("kind") in {"Application", "ApplicationSet"} and "argoproj.io" in str(d.get("apiVersion"))
        for _, d in manifests
    ):
        s.deploy.append("argocd")
    if _match(paths, r"\.tf$"):
        s.deploy.append("terraform")
    if _match(paths, r"(^|/)kustomization\.ya?ml$"):
        s.deploy.append("kustomize")
    if _match(paths, r"^\.github/workflows/[^/]+\.ya?ml$"):
        s.ci.append("github actions")
    if ".gitlab-ci.yml" in paths:
        s.ci.append("gitlab ci")
    if "Jenkinsfile" in paths:
        s.ci.append("jenkins")
    for df in _match(paths, r"(^|/)Dockerfile[^/]*$"):
        for image, tag in _from_images(_read(root, df)):
            runtime = image.split("/")[-1]
            if runtime in RUNTIME_EOL and tag and runtime not in s.runtimes:
                major = re.match(r"(\d+(?:\.\d+)?)", tag)
                if major:
                    s.runtimes[runtime] = major.group(1)
    return s


# ------------------------------------------------------- stack-neutral signals --
POINTS = {1: 1, 2: 2, 3: 4}


@dataclass(frozen=True)
class Signal:
    id: str  # same ids as readiness.py, so pillars and history line up
    level: int
    title: str
    check: Callable[[Path, list[str], Stack], bool]
    hint: str
    pillar: str


def _has(paths: list[str], *patterns: str) -> bool:
    return any(_match(paths, p) for p in patterns)


def _ci_text(root: Path, paths: list[str]) -> str:
    ci = _match(paths, r"^\.github/workflows/[^/]+\.ya?ml$") + [
        p for p in (".gitlab-ci.yml", "Jenkinsfile") if p in paths
    ]
    return "\n".join(_read(root, p) for p in ci)


def _test_script(root: Path) -> str:
    script = str((_package_json(root).get("scripts") or {}).get("test") or "")
    return "" if "no test specified" in script else script


def _source_text(root: Path, paths: list[str]) -> str:
    src = [p for p in paths if Path(p).suffix in _LANG_EXT and not re.search(r"(^|/)(tests?|__tests__)/", p)]
    return "\n".join(_read(root, p) for p in src[:400])


UNIT_TESTS = (
    r"(^|/)tests?/.*\.(py|js|ts|go|rb)$",
    r"(^|/)__tests__/",
    r"\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)$",
    r"(^|/)test_[^/]+\.py$",
    r"_test\.(py|go)$",
    r"(^|/)src/test/",
)

SIGNALS: list[Signal] = [
    Signal(
        "readme",
        1,
        "README present",
        lambda r, p, s: _has(p, r"^README(\.[a-z]+)?$"),
        "add a README",
        "maintainability",
    ),
    Signal(
        "linter",
        1,
        "linter configured",
        lambda r, p, s: (
            _has(p, r"(^|/)\.eslintrc", r"(^|/)eslint\.config\.", r"(^|/)\.golangci\.", r"(^|/)\.rubocop")
            or "[tool.ruff" in _read(r, "pyproject.toml")
            or "eslint" in _node_deps(_package_json(r))
            or _has(p, r"(^|/)(\.flake8|\.pylintrc|ruff\.toml)$")
        ),
        "configure a linter (eslint, ruff, golangci-lint …)",
        "maintainability",
    ),
    Signal(
        "formatter",
        1,
        "formatter configured",
        lambda r, p, s: (
            _has(p, r"(^|/)\.prettierrc", r"(^|/)prettier\.config\.", r"(^|/)\.editorconfig$")
            or "prettier" in _node_deps(_package_json(r))
            or "[tool.black" in _read(r, "pyproject.toml")
            or "ruff format" in _read(r, "Makefile")
            or "go" in s.languages
        ),
        "configure a formatter (prettier, ruff format, black …) and check it in CI",
        "maintainability",
    ),
    Signal(
        "unit_tests", 1, "unit tests exist", lambda r, p, s: _has(p, *UNIT_TESTS), "add unit tests", "maintainability"
    ),
    Signal(
        "lockfile",
        1,
        "dependencies pinned",
        lambda r, p, s: bool(s.package_managers) and not any("no lockfile" in pm for pm in s.package_managers),
        "commit the lockfile (package-lock.json, uv.lock, go.sum …)",
        "portability",
    ),
    Signal(
        "agents_md",
        2,
        "AGENTS.md present",
        lambda r, p, s: _has(p, r"^(AGENTS|CLAUDE)\.md$"),
        "add AGENTS.md (the assessment proposes one)",
        "maintainability",
    ),
    Signal(
        "verify_target",
        2,
        "one documented verify command",
        lambda r, p, s: (
            re.search(r"^(verify|test|check):", _read(r, "Makefile"), re.M) is not None or bool(_test_script(r))
        ),
        "add one command that runs lint and tests (`make verify` or `npm test`)",
        "maintainability",
    ),
    Signal(
        "container_nonroot",
        2,
        "non-root container",
        lambda r, p, s: bool(_dockerfiles(p)) and all(_nonroot(_read(r, d)) for d in _dockerfiles(p)),
        "add a non-root `USER` to every Dockerfile",
        "security",
    ),
    Signal(
        "precommit",
        2,
        "pre-commit hooks configured",
        lambda r, p, s: _has(p, r"^\.pre-commit-config\.yaml$", r"^\.husky/", r"^lefthook\.ya?ml$"),
        "add pre-commit hooks (pre-commit, husky, lefthook)",
        "maintainability",
    ),
    Signal(
        "codeowners",
        2,
        "CODEOWNERS present",
        lambda r, p, s: _has(p, r"^(\.github/|docs/)?CODEOWNERS$"),
        "add .github/CODEOWNERS",
        "security",
    ),
    Signal(
        "structured_logs",
        2,
        "structured (JSON) logging",
        lambda r, p, s: (
            re.search(
                r"\b(pino|winston|bunyan|structlog|JsonFormatter|python-json-logger|zap|zerolog|logrus|slog)\b",
                _source_text(r, p) + _read(r, "package.json"),
            )
            is not None
        ),
        "log JSON (pino, winston, structlog …)",
        "operability",
    ),
    Signal(
        "dependency_updates",
        2,
        "automated dependency updates",
        lambda r, p, s: _has(p, r"^\.github/dependabot\.ya?ml$", r"(^|/)renovate\.json5?$", r"^\.renovaterc"),
        "add .github/dependabot.yml or Renovate",
        "security",
    ),
    Signal(
        "ci",
        3,
        "CI runs the tests",
        lambda r, p, s: (
            bool(s.ci) and re.search(r"\b(test|verify|pytest|jest|vitest|go test)\b", _ci_text(r, p)) is not None
        ),
        "add a CI workflow that runs the verify command",
        "maintainability",
    ),
    Signal(
        "coverage_gate",
        3,
        "coverage gate",
        lambda r, p, s: (
            re.search(
                r"--cov-fail-under|coverageThreshold|coverage[\s\S]{0,200}?thresholds|check-coverage|fail_under",
                _read(r, "Makefile")
                + _read(r, "package.json")
                + _read(r, "pyproject.toml")
                + _ci_text(r, p)
                + "".join(_read(r, f) for f in _match(p, r"^(jest|vitest|vite)\.config\.")),
            )
            is not None
        ),
        "fail the build below a coverage threshold",
        "maintainability",
    ),
    Signal(
        "e2e_tests",
        3,
        "end-to-end tests",
        lambda r, p, s: _has(p, r"(^|/)(e2e|tests/e2e|cypress|playwright)/", r"\.e2e\.(js|ts)$"),
        "add end-to-end tests against a deployment",
        "compliance",
    ),
    Signal(
        "health_endpoints",
        3,
        "liveness and readiness checks",
        lambda r, p, s: _probes_everywhere(r, p) or all(x in _source_text(r, p) for x in ("healthz", "readyz")),
        "add /healthz and /readyz, and liveness/readiness probes in the manifests",
        "reliability",
    ),
    Signal(
        "metrics",
        3,
        "metrics endpoint",
        lambda r, p, s: (
            re.search(r"prom-client|prometheus|/metrics", _source_text(r, p) + _read(r, "package.json")) is not None
        ),
        "expose /metrics (prom-client, prometheus_client …)",
        "operability",
    ),
    Signal(
        "tracing",
        3,
        "distributed tracing hook",
        lambda r, p, s: "opentelemetry" in (_source_text(r, p) + _read(r, "package.json")).lower(),
        "add OpenTelemetry",
        "operability",
    ),
]
SECRET_SCAN = Signal("secret_scan", 3, "no secrets in the repo", lambda r, p, s: False, "remove and rotate", "security")
PILLAR_OF = {s.id: s.pillar for s in [*SIGNALS, SECRET_SCAN]}


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

    def as_data(self) -> dict[str, Any]:
        t = tally(({"id": s.id, "ok": ok, "pillar": s.pillar} for s, ok in self.results), PILLAR_OF)
        return {
            "level": self.level,
            "points": sum(POINTS[s.level] for s, ok in self.results if ok),
            "max_points": sum(POINTS[s.level] for s, _ in self.results),
            "signals": [
                {"id": s.id, "level": s.level, "title": s.title, "ok": ok, "pillar": s.pillar, "hint": s.hint}
                for s, ok in self.results
            ],
            "pillars": [{"id": p, "title": BY_ID[p].title, **t[p]} for p in IDS],
        }


def score(root: Path, stack: Stack, paths: list[str] | None = None, secret_scan_ok: bool | None = None) -> Scorecard:
    paths = files(root) if paths is None else paths
    results = []
    for s in SIGNALS:
        try:
            ok = bool(s.check(root, paths, stack))
        except (OSError, ValueError):
            ok = False
        results.append((s, ok))
    if secret_scan_ok is not None:
        results.append((SECRET_SCAN, secret_scan_ok))
    return Scorecard(results)


# ------------------------------------------------------- deterministic findings --
# End of upstream support, as of the date this table was last reviewed (2026-10).
# A runtime at or below the version is past end of life.
RUNTIME_EOL = {"node": 20, "python": 3.9, "golang": 1.24, "openjdk": 17, "ruby": 3.2, "php": 8.1}
SEVERITIES = ("high", "medium", "low")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # high | medium | low
    area: str  # security | reliability | maintainability | supply chain …
    title: str
    file: str
    line: int | None = None
    detail: str = ""
    where: str = ""  # e.g. "Deployment web, container app"

    def as_data(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _dockerfiles(paths: list[str]) -> list[str]:
    return _match(paths, r"(^|/)Dockerfile[^/]*$")


def _from_images(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M | re.I):
        ref = m.group(1)
        if ref.lower() == "scratch" or "$" in ref:
            continue
        image, _, tag = ref.partition("@")[0].partition(":")
        out.append((image.lower(), tag))
    return out


def _nonroot(text: str) -> bool:
    users = re.findall(r"^\s*USER\s+(\S+)", text, re.M | re.I)
    return bool(users) and users[-1].split(":")[0] not in {"root", "0"}


def _line_of(text: str, pattern: str, after: str | None = None) -> int | None:
    start = 0
    if after:
        a = re.search(after, text, re.M)
        start = a.end() if a else 0
    m = re.compile(pattern, re.M | re.I).search(text, start)
    return text.count("\n", 0, m.start()) + 1 if m else None


def _containers(doc: dict[str, Any]) -> list[dict[str, Any]]:
    spec = doc.get("spec") or {}
    if doc.get("kind") == "Pod":
        pod = spec
    elif doc.get("kind") == "CronJob":
        pod = (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
    else:
        pod = ((spec.get("template") or {}).get("spec")) or {}
    return [c for c in (pod.get("containers") or []) if isinstance(c, dict)]


WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Pod", "Job", "CronJob", "ReplicaSet"}


def _probes_everywhere(root: Path, paths: list[str]) -> bool:
    cs = [c for _, d in _manifests(root, paths) if d.get("kind") in WORKLOADS for c in _containers(d)]
    return bool(cs) and all(c.get("livenessProbe") and c.get("readinessProbe") for c in cs)


def findings(root: Path, stack: Stack, paths: list[str] | None = None) -> list[Finding]:
    paths = files(root) if paths is None else paths
    out: list[Finding] = []
    for df in _dockerfiles(paths):
        text = _read(root, df)
        if not _nonroot(text):
            out.append(
                Finding(
                    "docker-root",
                    "medium",
                    "security",
                    "container runs as root",
                    df,
                    None,
                    "add a non-root USER before CMD",
                )
            )
        for image, tag in _from_images(text):
            line = _line_of(text, rf"^\s*FROM\s+(?:--platform=\S+\s+)?{re.escape(image)}")
            if not tag or tag == "latest":
                out.append(
                    Finding(
                        "image-unpinned",
                        "medium",
                        "supply chain",
                        "base image not pinned",
                        df,
                        line,
                        f"{image}: pin a version (and ideally a digest) so builds are reproducible",
                    )
                )
            runtime = image.split("/")[-1]
            eol = RUNTIME_EOL.get(runtime)
            m = re.match(r"(\d+(?:\.\d+)?)", tag)
            if eol is not None and m and float(m.group(1)) <= eol:
                out.append(
                    Finding(
                        "runtime-eol",
                        "high",
                        "security",
                        "runtime past end of life",
                        df,
                        line,
                        f"{runtime} {m.group(1)}: move to a supported release; "
                        "end-of-life runtimes get no security fixes",
                    )
                )
        if re.search(r"^\s*RUN\s+npm\s+install\b", text, re.M) and "package-lock.json" not in paths:
            out.append(
                Finding(
                    "npm-install-unlocked",
                    "medium",
                    "supply chain",
                    "npm install without a lockfile",
                    df,
                    _line_of(text, r"^\s*RUN\s+npm\s+install"),
                    "commit package-lock.json and use `npm ci`",
                )
            )
    for path, doc in _manifests(root, paths):
        if doc.get("kind") not in WORKLOADS:
            continue
        name = (doc.get("metadata") or {}).get("name", "?")
        text = _read(root, path)
        for c in _containers(doc):
            cname = c.get("name", "?")
            where = f"{doc['kind']} {name}, container {cname}"
            line = _line_of(
                text, rf"^\s*-?\s*name:\s*['\"]?{re.escape(str(cname))}['\"]?\s*$", after=r"^\s*containers:"
            )

            def add(rule: str, sev: str, area: str, title: str, detail: str) -> None:
                out.append(Finding(rule, sev, area, title, path, line, detail, where))  # noqa: B023

            res = c.get("resources") or {}
            if not (res.get("limits") or {}).get("memory"):
                add(
                    "k8s-no-memory-limit",
                    "medium",
                    "reliability",
                    "no memory limit",
                    "set resources.limits.memory so one pod can't starve the node",
                )
            if not res.get("requests"):
                add(
                    "k8s-no-requests",
                    "low",
                    "reliability",
                    "no resource requests",
                    "set resources.requests so the scheduler can place the pod",
                )
            missing = [p for p in ("readinessProbe", "livenessProbe") if not c.get(p)]
            if missing:
                which = " and ".join(m.removesuffix("Probe") for m in missing)
                add(
                    "k8s-no-probes",
                    "medium",
                    "reliability",
                    f"no {which} probe",
                    "readiness keeps traffic away from a pod that isn't ready; liveness restarts a stuck one",
                )
            image = str(c.get("image") or "")
            if image and (":" not in image.split("/")[-1] or image.endswith(":latest")):
                add(
                    "k8s-image-latest",
                    "medium",
                    "supply chain",
                    "image not pinned",
                    f"'{image}': deploy an immutable tag or digest",
                )
            sc = c.get("securityContext") or {}
            if sc.get("privileged") or sc.get("allowPrivilegeEscalation") is True:
                add(
                    "k8s-privileged",
                    "high",
                    "security",
                    "privileged container",
                    "drop privileged mode and privilege escalation",
                )
            elif not sc.get("runAsNonRoot"):
                add(
                    "k8s-root",
                    "low",
                    "security",
                    "not forced to run as non-root",
                    "set securityContext.runAsNonRoot: true",
                )
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(out, key=lambda f: (rank.get(f.severity, 9), f.file, f.line or 0, f.rule))


# ------------------------------------------------------------- agent report --
ASSESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "test_gaps", "risks", "recommendations", "agents_md"],
    "properties": {
        "summary": {"type": "string"},
        "test_gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["area", "why", "files"],
                "properties": {
                    "area": {"type": "string"},
                    "why": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "title", "detail", "files"],
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "why", "kind", "effort"],
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "kind": {"type": "string", "enum": ["feature", "bug", "upkeep"]},
                    "effort": {"type": "string", "enum": ["S", "M", "L"]},
                },
            },
        },
        "agents_md": {"type": "string"},
    },
}
MAX_AGENTS_MD = 12_000


@dataclass
class Judgement:
    test_gaps: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    agents_md: str
    summary: str
    dropped: list[str]  # what the engine refused, and why

    def as_data(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _cited(item: dict[str, Any], paths: set[str], dirs: set[str]) -> tuple[list[str], list[str]]:
    cited = [str(f).strip().removeprefix("./").split(":")[0] for f in item.get("files") or [] if str(f).strip()]
    ok = [f for f in cited if f in paths or f.rstrip("/") in dirs]
    return ok, [f for f in cited if f not in ok]


def judge(report: dict[str, Any], paths: list[str]) -> Judgement:
    """Keep what the repo supports: a test gap or risk must cite at least one file that
    exists, and its unknown citations are removed; a risk's severity must be known.
    Recommendations need a title, a reason and a known kind. Everything refused is listed."""
    known = set(paths)
    dirs = {str(Path(p).parent) for p in paths} | {
        "/".join(Path(p).parts[:i]) for p in paths for i in range(1, len(Path(p).parts))
    }
    dropped: list[str] = []

    def keep(items: Any, what: str, check: Callable[[dict[str, Any]], str | None]) -> list[dict[str, Any]]:
        out = []
        for raw in items if isinstance(items, list) else []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or raw.get("area") or "?")[:120]
            why = check(raw)
            if why:
                dropped.append(f"{what} '{title}': {why}")
                continue
            out.append(raw)
        return out

    def cited(item: dict[str, Any]) -> str | None:
        ok, unknown = _cited(item, known, dirs)
        if not ok:
            return "cites no file in the repo" + (f" ({', '.join(unknown[:3])})" if unknown else "")
        item["files"] = ok
        if unknown:
            dropped.append(f"citations not in the repo removed: {', '.join(unknown[:5])}")
        return None

    def risk(item: dict[str, Any]) -> str | None:
        if item.get("severity") not in SEVERITIES:
            return f"unknown severity '{item.get('severity')}'"
        return cited(item)

    def rec(item: dict[str, Any]) -> str | None:
        if item.get("kind") not in ("feature", "bug", "upkeep"):
            return f"unknown kind '{item.get('kind')}'"
        if item.get("effort") not in ("S", "M", "L"):
            item["effort"] = "M"
        if not str(item.get("title") or "").strip() or not str(item.get("why") or "").strip():
            return "needs a title and a reason"
        return None

    gaps = keep(report.get("test_gaps"), "test gap", cited)
    risks = keep(report.get("risks"), "risk", risk)
    recs = keep(report.get("recommendations"), "recommendation", rec)
    agents_md = str(report.get("agents_md") or "").strip()
    if len(agents_md) > MAX_AGENTS_MD:
        agents_md = agents_md[:MAX_AGENTS_MD]
        dropped.append(f"proposed AGENTS.md cut to {MAX_AGENTS_MD} characters")
    return Judgement(gaps, risks, recs, agents_md, str(report.get("summary") or "").strip(), dropped)


# ------------------------------------------------------------------ report --
def group_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per rule and title, with every place it was found (order kept)."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for f in items:
        g = groups.setdefault(
            (f["rule"], f["title"]),
            {k: f[k] for k in ("rule", "severity", "area", "title", "detail")} | {"at": []},
        )
        g["at"].append(f["file"] + (f":{f['line']}" if f.get("line") else ""))
    return list(groups.values())


def render_markdown(report: dict[str, Any]) -> str:
    """The assessment as people read it (artifacts/<change>/assessment.md)."""
    r = report
    card = r["readiness"]
    lines = [
        f"# Assessment: {r['repo']['slug']}",
        "",
        f"Commit `{r['repo']['commit']}` on `{r['repo']['ref']}` · {r['repo']['files']} files"
        f" · assessed {r['assessed_at']}",
        "",
        f"**Stack:** {r['stack_summary']}",
        "",
        f"**Agent readiness: Level {card['level']}** ({card['points']}/{card['max_points']} points)",
        "",
    ]
    if r.get("summary"):
        lines += [r["summary"], ""]
    lines += ["## Readiness signals", "", "| | Signal | Level | Pillar | To fix |", "|---|---|---|---|---|"]
    for s in card["signals"]:
        fix = "" if s["ok"] else s["hint"]
        lines.append(f"| {'✅' if s['ok'] else '❌'} | {s['title']} | {s['level']} | {s['pillar']} | {fix} |")
    lines += ["", "## Findings (deterministic rules)", ""]
    for g in group_findings(r["findings"]) or []:
        at = ", ".join(f"`{x}`" for x in g["at"][:6]) + (f" and {len(g['at']) - 6} more" if len(g["at"]) > 6 else "")
        lines.append(f"- **{g['severity']}** · {g['title']}: {g['detail']} ({at})")
    if not r["findings"]:
        lines.append("None.")
    lines += ["", "## Risks (assessor, checked against the repo)", ""]
    lines += [f"- **{x['severity']}** · {x['title']}: {x['detail']} ({', '.join(x['files'])})" for x in r["risks"]] or [
        "None reported."
    ]
    lines += ["", "## Test gaps", ""]
    lines += [f"- **{x['area']}**: {x['why']} ({', '.join(x['files'])})" for x in r["test_gaps"]] or ["None reported."]
    lines += ["", "## Recommended changes", ""]
    lines += [
        f"{i}. **{x['title']}** ({x['kind']}, effort {x['effort']}): {x['why']}"
        for i, x in enumerate(r["recommendations"], 1)
    ] or ["None."]
    if r.get("dropped"):
        lines += ["", "## Not included", "", "The engine left out these parts of the assessor's report:", ""]
        lines += [f"- {d}" for d in r["dropped"]]
    if r.get("agents_md"):
        lines += [
            "",
            "## Proposed AGENTS.md",
            "",
            "Not committed: copy it into the repo if it fits.",
            "",
            "````markdown",
            r["agents_md"],
            "````",
        ]
    return "\n".join(lines) + "\n"
