"""Agent-readiness scorecard for a generated repo (docs/practices.md §1, §7).

Binary signals grouped by maturity level, after the Autonomy Maturity Model:
present or absent, no partial credit, each checkable in milliseconds from the
files (plus one secret scan the Quality gate station runs). A repo is at level N
when every signal at levels 1..N passes; points (1/2/4 per signal by level)
show progress between levels.

Every signal has exactly one primary quality pillar (ADR-0024, `pillars.py`). The
scorecard also reports passed/applicable per pillar; the Level gate ignores pillars.

Stack packs (ADR-0041 §3): signal ids, levels, points and pillars are fixed here. The
stack-neutral signals are checked here; the stack-specific ones (`STACK_SIGNALS`) take their
check from the product's pack (`readiness.yaml`, parsed by `parse_stack_rules`), as declarative
rules this module interprets: `file`, `glob`, `contains`, `matches`, `all`, `any`. Rules only
read files, never execute anything. A pack must map every stack-specific signal, so Level 3
means the same in every stack; it may add signals of its own, which count for its products only.
It also says how its tests name requirement ids (`requirement_tags`), which
`requirements_traced` and the traceability matrix read.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from agent_factory.pillars import BY_ID, IDS, tally
from agent_factory.traceability import PYTEST_TAGS, TagRule, tag_example, untraced

POINTS = {1: 1, 2: 2, 3: 4}


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(errors="replace") if p.is_file() else ""


@dataclass(frozen=True)
class Signal:
    id: str
    level: int
    title: str
    check: Callable[[Path], bool] | None  # None: stack-specific, the product's pack proves it
    hint: str
    pillar: str  # primary quality pillar (ADR-0024)


BY_PACK = "proved by the stack pack (readiness.yaml)"

SIGNALS: list[Signal] = [
    # Level 1: functional
    Signal(
        "readme", 1, "README present", lambda r: (r / "README.md").is_file(), "add README.md", pillar="maintainability"
    ),
    Signal("linter", 1, "linter configured", None, BY_PACK, pillar="maintainability"),
    Signal("formatter", 1, "formatter enforced", None, BY_PACK, pillar="maintainability"),
    Signal("unit_tests", 1, "unit tests exist", None, BY_PACK, pillar="maintainability"),
    Signal("lockfile", 1, "dependencies pinned", None, BY_PACK, pillar="portability"),
    # Level 2: documented
    Signal(
        "agents_md",
        2,
        "AGENTS.md present",
        lambda r: (r / "AGENTS.md").is_file(),
        "keep AGENTS.md",
        pillar="maintainability",
    ),
    Signal(
        "verify_target",
        2,
        "one documented verify command",
        lambda r: re.search(r"^verify:", _read(r, "Makefile"), re.M) is not None,
        "keep the `verify` Makefile target",
        pillar="maintainability",
    ),
    Signal(
        "container_nonroot",
        2,
        "reproducible, non-root container",
        lambda r: re.search(r"^USER\s+\S+", _read(r, "Dockerfile"), re.M) is not None,
        "keep `USER` in the Dockerfile",
        pillar="security",
    ),
    Signal(
        "precommit",
        2,
        "pre-commit hooks configured",
        lambda r: (r / ".pre-commit-config.yaml").is_file(),
        "keep .pre-commit-config.yaml",
        pillar="maintainability",
    ),
    Signal(
        "codeowners",
        2,
        "CODEOWNERS present",
        lambda r: (r / ".github" / "CODEOWNERS").is_file() or (r / "CODEOWNERS").is_file(),
        "keep .github/CODEOWNERS",
        pillar="security",
    ),
    Signal("structured_logs", 2, "structured (JSON) logging", None, BY_PACK, pillar="operability"),
    Signal(
        "dependency_updates",
        2,
        "automated dependency updates",
        lambda r: (r / ".github" / "dependabot.yml").is_file(),
        "keep .github/dependabot.yml",
        pillar="security",
    ),
    # Level 3: standardized (the production floor)
    Signal(
        "ci",
        3,
        "CI runs the verify command",
        lambda r: any("make verify" in p.read_text(errors="replace") for p in r.glob(".github/workflows/*.y*ml")),
        "keep .github/workflows/ci.yml running `make verify`",
        pillar="maintainability",
    ),
    Signal("coverage_gate", 3, "coverage gate", None, BY_PACK, pillar="maintainability"),
    Signal("acceptance_tests", 3, "acceptance scenarios are tests", None, BY_PACK, pillar="compliance"),
    Signal("e2e_tests", 3, "end-to-end tests", None, BY_PACK, pillar="compliance"),
    Signal("health_endpoints", 3, "liveness and readiness endpoints", None, BY_PACK, pillar="reliability"),
    Signal("metrics", 3, "metrics endpoint", None, BY_PACK, pillar="operability"),
    Signal("tracing", 3, "distributed tracing hook", None, BY_PACK, pillar="operability"),
    Signal(
        "requirements_traced",
        3,
        "every requirement traced to a scenario and a test",
        lambda r: not untraced(r),
        "cover each requirement in docs/requirements.yaml with an acceptance scenario and a test "
        'tagged @pytest.mark.req("R…")',
        pillar="compliance",
    ),
    Signal(
        "design_docs",
        3,
        "spec, design and API contract up to date",
        lambda r: all((r / "docs" / f).is_file() for f in ("spec.md", "design.md", "openapi.yaml")),
        "keep docs/spec.md, docs/design.md and docs/openapi.yaml",
        pillar="interoperability",
    ),
]

SECRET_SCAN = Signal(
    "secret_scan", 3, "no secrets in the repo", lambda r: False, "remove the secret and rotate it", pillar="security"
)


PILLAR_OF = {s.id: s.pillar for s in [*SIGNALS, SECRET_SCAN]}

# the signals each stack proves its own way (ADR-0041 §3); the rest are stack-neutral
STACK_SIGNALS: tuple[str, ...] = tuple(s.id for s in SIGNALS if s.check is None)
ENGINE_SIGNALS = frozenset(PILLAR_OF)


# ------------------------------------------------------------ pack rules --
class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    """One declarative check over a repo's files. `kind` is file | glob | contains | matches |
    all | any; `target` is a path or glob for the first four, `parts` the sub-rules of all/any."""

    kind: str
    target: str = ""
    glob: bool = False  # contains/matches: `target` is a glob (any matching file counts)
    text: str = ""
    regex: re.Pattern[str] | None = None
    parts: tuple[Rule, ...] = ()


@dataclass(frozen=True)
class StackSignal:
    """How a pack proves one signal: a stack-specific one (`STACK_SIGNALS`), or one of its own."""

    id: str
    level: int
    title: str
    pillar: str
    hint: str
    rule: Rule


@dataclass(frozen=True)
class StackRules:
    signals: tuple[StackSignal, ...]
    tags: TagRule = PYTEST_TAGS  # how this stack's tests name requirement ids

    def get(self, sid: str) -> StackSignal | None:
        return next((s for s in self.signals if s.id == sid), None)

    @property
    def extra(self) -> list[StackSignal]:
        """The pack's own signals, beyond the engine's."""
        return [s for s in self.signals if s.id not in ENGINE_SIGNALS]


SIGNAL_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_LEAF = ("file", "glob", "contains", "matches")


def _safe_path(raw: Any, where: str) -> str:
    """A repo-relative path or glob: never absolute, never leaving the repo."""
    if not isinstance(raw, str) or not raw.strip():
        raise RuleError(f"{where}: needs a path")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("~") or "\\" in raw or ".." in path.parts:
        raise RuleError(f"{where}: '{raw}' must be a path inside the repo")
    return raw


def parse_rule(raw: Any, where: str = "rule") -> Rule:
    """A rule from its YAML form, e.g. `{contains: {file: Makefile, text: "--cov-fail-under="}}`."""
    if not isinstance(raw, Mapping) or len(raw) != 1:
        raise RuleError(f"{where}: one of {', '.join([*_LEAF, 'all', 'any'])} (exactly one key)")
    kind, arg = next(iter(raw.items()))
    if kind in ("file", "glob"):
        return Rule(kind, _safe_path(arg, f"{where}.{kind}"))
    if kind in ("contains", "matches"):
        need = "text" if kind == "contains" else "regex"
        if not isinstance(arg, Mapping) or set(arg) - {"file", "glob", need} or need not in arg:
            raise RuleError(f"{where}.{kind}: {{file or glob, {need}}}")
        if ("file" in arg) == ("glob" in arg):
            raise RuleError(f"{where}.{kind}: exactly one of file or glob")
        is_glob = "glob" in arg
        target = _safe_path(arg["glob" if is_glob else "file"], f"{where}.{kind}")
        value = arg[need]
        if not isinstance(value, str) or not value:
            raise RuleError(f"{where}.{kind}: {need} must be a non-empty string")
        if kind == "contains":
            return Rule(kind, target, is_glob, text=value)
        try:
            pattern = re.compile(value, re.M)
        except re.error as exc:
            raise RuleError(f"{where}.matches: bad regex: {exc}") from exc
        return Rule(kind, target, is_glob, regex=pattern)
    if kind in ("all", "any"):
        if not isinstance(arg, list) or not arg:
            raise RuleError(f"{where}.{kind}: a non-empty list of rules")
        return Rule(kind, parts=tuple(parse_rule(r, f"{where}.{kind}[{i}]") for i, r in enumerate(arg)))
    raise RuleError(f"{where}: unknown rule kind '{kind}' (one of {', '.join([*_LEAF, 'all', 'any'])})")


def _real_file(p: Path) -> bool:
    """A regular file, not a symlink: rules also run on team repos (ADR-0041 §5), and a link
    could point outside the repository."""
    return p.is_file() and not p.is_symlink()


def _files(root: Path, rule: Rule) -> list[Path]:
    if not rule.glob:
        p = root / rule.target
        return [p] if _real_file(p) else []
    return [p for p in sorted(root.glob(rule.target)) if _real_file(p)]


def holds(rule: Rule, root: Path) -> bool:
    """Whether the repo at `root` satisfies the rule. Pure: reads files, runs nothing."""
    if rule.kind == "file":
        return _real_file(root / rule.target)
    if rule.kind == "glob":
        return any(_real_file(p) for p in root.glob(rule.target))
    if rule.kind == "contains":
        return any(rule.text in p.read_text(errors="replace") for p in _files(root, rule))
    if rule.kind == "matches":
        assert rule.regex is not None  # noqa: S101 - set by parse_rule
        return any(rule.regex.search(p.read_text(errors="replace")) for p in _files(root, rule))
    if rule.kind == "all":
        return all(holds(r, root) for r in rule.parts)
    return any(holds(r, root) for r in rule.parts)


def parse_stack_rules(raw: Any, where: str = "readiness.yaml") -> StackRules:
    """A pack's `readiness.yaml`. Refused unless it maps every stack-specific signal; it may not
    redefine a stack-neutral one, and each signal of its own names a level and a pillar."""
    signals = (raw or {}).get("signals") if isinstance(raw, Mapping) else None
    if not isinstance(signals, Mapping):
        raise RuleError(f"{where}: needs a `signals:` map")
    base = {s.id: s for s in SIGNALS}
    out: list[StackSignal] = []
    errors: list[str] = []
    for sid, body in signals.items():
        at = f"{where}: {sid}"
        if not isinstance(body, Mapping) or "check" not in body:
            errors.append(f"{at}: needs a `check`")
            continue
        allowed = {"check", "hint"} if sid in base else {"check", "hint", "level", "title", "pillar"}
        if extra_keys := set(body) - allowed:
            errors.append(f"{at}: unexpected {', '.join(sorted(extra_keys))}")
            continue
        try:
            rule = parse_rule(body["check"], f"{at}.check")
        except RuleError as exc:
            errors.append(str(exc))
            continue
        hint = str(body.get("hint") or "")
        if sid in base:
            sig = base[sid]
            if sig.check is not None:
                errors.append(f"{at}: is stack-neutral, the engine checks it")
                continue
            out.append(StackSignal(sid, sig.level, sig.title, sig.pillar, hint or sig.hint, rule))
            continue
        level, pillar, title = body.get("level"), body.get("pillar"), body.get("title")
        if sid == SECRET_SCAN.id or not SIGNAL_ID.match(str(sid)):
            errors.append(f"{at}: signal ids are lowercase letters, digits and underscores")
        elif level not in (1, 2, 3):
            errors.append(f"{at}: a signal of the pack's own needs `level` 1, 2 or 3")
        elif pillar not in BY_ID:
            errors.append(f"{at}: `pillar` must be one of {', '.join(IDS)}")
        elif not isinstance(title, str) or not title.strip():
            errors.append(f"{at}: needs a `title`")
        else:
            out.append(StackSignal(str(sid), int(level), title.strip(), str(pillar), hint, rule))
    mapped = {s.id for s in out}
    if missing := [sid for sid in STACK_SIGNALS if sid not in mapped and sid not in signals]:
        errors.append(
            f"{where}: does not prove {', '.join(missing)}; every stack-specific signal needs a check, "
            "so Level 3 means the same in every stack"
        )
    tags: TagRule | None = None
    try:
        tags = parse_tag_rule(raw.get("requirement_tags"), f"{where}: requirement_tags")
    except RuleError as exc:
        errors.append(str(exc))
    if errors:
        raise RuleError("; ".join(errors))
    assert tags is not None  # noqa: S101
    return StackRules(tuple(out), tags)


def parse_tag_rule(raw: Any, where: str = "requirement_tags") -> TagRule:
    """`requirement_tags: {files: [globs], pattern: regex with one group, ids?: regex, example}`.
    Required: without it the traceability signal would read another stack's tests."""
    if not isinstance(raw, Mapping):
        raise RuleError(
            f"{where}: needed, so requirement ids are found in this stack's tests "
            "({files: [globs], pattern, ids?, example})"
        )
    if extra_keys := set(raw) - {"files", "pattern", "ids", "example"}:
        raise RuleError(f"{where}: unexpected {', '.join(sorted(extra_keys))}")
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise RuleError(f"{where}.files: a non-empty list of globs")
    globs = tuple(_safe_path(f, f"{where}.files") for f in files)
    compiled: dict[str, re.Pattern[str] | None] = {"pattern": None, "ids": None}
    for key in compiled:
        value = raw.get(key)
        if value is None and key == "ids":
            continue
        if not isinstance(value, str) or not value:
            raise RuleError(f"{where}.{key}: a non-empty regex")
        try:
            compiled[key] = re.compile(value)
        except re.error as exc:
            raise RuleError(f"{where}.{key}: bad regex: {exc}") from exc
    pattern = compiled["pattern"]
    assert pattern is not None  # noqa: S101
    if pattern.groups < 1:
        raise RuleError(f"{where}.pattern: needs one group holding the requirement id(s)")
    example = raw.get("example")
    if not isinstance(example, str) or "R1" not in example:
        raise RuleError(f'{where}.example: how a test tags R1, e.g. @pytest.mark.req("R1")')
    rule = TagRule(globs, pattern, compiled["ids"], example)
    if rule.found(example) != ["R1"]:
        raise RuleError(f"{where}: the pattern does not find R1 in its own example '{example}'")
    return rule


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
            "signals": [
                {"id": s.id, "level": s.level, "title": s.title, "ok": ok, "pillar": s.pillar} for s, ok in self.results
            ],
            "pillars": self.pillars(),
        }

    def pillars(self) -> list[dict[str, Any]]:
        """Passed/applicable per pillar in model order; 0/0 means uncovered (ADR-0024)."""
        t = tally(({"id": s.id, "ok": ok, "pillar": s.pillar} for s, ok in self.results), PILLAR_OF)
        return [{"id": p, "title": BY_ID[p].title, **t[p]} for p in IDS]


def _ok(check: Callable[[Path], bool] | Rule, root: Path) -> bool:
    try:
        return holds(check, root) if isinstance(check, Rule) else bool(check(root))
    except OSError:
        return False


def score(root: Path, secret_scan_ok: bool | None = None, rules: StackRules | None = None) -> Scorecard:
    """The repo's scorecard. `rules`: how the product's stack pack proves the stack-specific
    signals (and its own); without a pack those signals fail, with a hint saying why."""
    results: list[tuple[Signal, bool]] = []
    tags = rules.tags if rules else PYTEST_TAGS
    for s in SIGNALS:
        if s.id == "requirements_traced":  # stack-neutral, but read with the stack's tag rule
            hint = (
                "cover each requirement in docs/requirements.yaml with an acceptance scenario and a test "
                f"tagged {tag_example(tags, 'R…')}"
            )
            results.append((replace(s, hint=hint), _ok(lambda r: not untraced(r, tags), root)))
            continue
        if s.check is not None:
            results.append((s, _ok(s.check, root)))
            continue
        how = rules.get(s.id) if rules else None
        if how is None:
            results.append((replace(s, hint="no stack pack proves this: name the blueprint's `pack`"), False))
        else:
            results.append((replace(s, hint=how.hint), _ok(how.rule, root)))
    for x in rules.extra if rules else []:
        sig = Signal(x.id, x.level, x.title, None, x.hint, pillar=x.pillar)
        results.append((sig, _ok(x.rule, root)))
    if secret_scan_ok is not None:
        results.append((SECRET_SCAN, secret_scan_ok))
    return Scorecard(results)
