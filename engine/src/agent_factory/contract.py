"""Station contract (ADR-0037 §1–§5, ADR-0039 §2–§5): station types, artifact types, the phase
registry with its capability boundaries, and the publish checks.

Pure: no I/O. A workflow is a **container** of phase columns (the seven built-in phases, plus the
org's look-and-decide columns and phases from packs an admin installed); a station **fits** only if

1. **column**: it sits in its type's column, columns never step back along the forward order;
2. **data**: every artifact it needs is given by a station to its left or is an entry artifact;
3. **floor**: when the workflow changes code, `test`, a secret scan and `change-risk` run after
   every change to the branch and before anything that gives `image`, `url` or `pr`;
4. **capability**: what it uses and gives stays inside what its phase allows (ADR-0039 §3), so
   a step drawn in the wrong column is refused at publish and could not act at runtime either.

A **station type** fixes its column, what it needs and what it gives. Implementations differ by
target (`factory` = a product the factory owns, `repo` = a team's repo); until the handler pairs
merge (ADR-0037 build step 1b) each pair maps to one type here.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------- columns ----
COLUMNS: list[tuple[str, str]] = [
    ("discover", "Discover"),
    ("design", "Design"),
    ("plan", "Plan"),
    ("develop", "Develop"),
    ("verify", "Verify"),
    ("deliver", "Deliver"),
    ("operate", "Operate"),
]
COLUMN_IDS = [c for c, _ in COLUMNS]
# DevOps phases of ADR-0028 a custom station may still name; read as the column they belong to.
PHASE_TO_COLUMN = {
    "plan": "design",
    "code": "develop",
    "test": "verify",
    "release": "deliver",
    "deploy": "deliver",
    "validate": "deliver",
    "handover": "deliver",
}


def column_of_phase(phase: str) -> str:
    """A DevOps phase name read as its column; any other id is a column id (built-in, org or pack:
    the registry decides whether it exists)."""
    return PHASE_TO_COLUMN.get(phase, phase)


# -------------------------------------------------------------- artifacts ----
# The nine artifact types (ADR-0037 §2). `report` carries a kind: report.<kind>.
ARTIFACT_TYPES: dict[str, str] = {
    "request": "the ask: text, ticket, feedback",
    "spec": "testable spec, numbered requirements, acceptance; holdout sealed, never shown to builders",
    "design": "design doc, API contract, task plan",
    "stories": "stories with acceptance criteria",
    "branch": "repo + ref with commits (factory worktree or team repo)",
    "report": "evidence of one kind: repo, readiness, assessment, test, scan, review, risk, acceptance, change",
    "image": "image ref + SBOM + provenance",
    "url": "a running deployment",
    "pr": "a pull request (or handover tag)",
}
VERSIONS: dict[str, list[int]] = {t: [1] for t in ARTIFACT_TYPES}  # a breaking change adds a version
REPORT_KINDS = ["repo", "readiness", "assessment", "test", "scan", "review", "risk", "acceptance", "change"]
EXITS = {"image", "url", "pr"}  # what lets a change out: the floor stands before these

_REF = re.compile(r"^(?P<type>[a-z]+)(?:\.(?P<kind>[a-z][a-z0-9-]{0,40}))?@(?P<version>[1-9][0-9]*)$")


@dataclass(frozen=True, order=True)
class ArtifactRef:
    type: str
    kind: str | None
    version: int

    @property
    def key(self) -> str:
        """What a ledger entry is looked up by: `spec`, `report.test` (version apart)."""
        return f"{self.type}.{self.kind}" if self.kind else self.type

    def __str__(self) -> str:
        return f"{self.key}@{self.version}"


def parse_ref(text: str) -> ArtifactRef:
    """`spec@1`, `report.test@1`. Unknown types, versions and report kinds without one are refused."""
    m = _REF.match(text.strip())
    if not m:
        raise ValueError(f"'{text}' is not an artifact reference (type@version, e.g. spec@1 or report.test@1)")
    t, kind, v = m["type"], m["kind"], int(m["version"])
    if t not in ARTIFACT_TYPES:
        raise ValueError(f"'{text}': unknown artifact type '{t}' (one of {', '.join(ARTIFACT_TYPES)})")
    if (t == "report") != bool(kind):
        raise ValueError(f"'{text}': only report takes a kind (report.<kind>@<version>)")
    if v not in VERSIONS[t]:
        raise ValueError(f"'{text}': {t} has no version {v} (known: {VERSIONS[t]})")
    return ArtifactRef(t, kind, v)


Need = tuple[ArtifactRef, ...]  # alternatives: any one of them satisfies the need


def parse_need(text: str) -> Need:
    """`design@1 | request@1`: either satisfies it."""
    return tuple(parse_ref(p) for p in text.split("|"))


def need_text(need: Need) -> str:
    return " | ".join(str(r) for r in need)


# ----------------------------------------------------------- capabilities ---
# What a station may do (ADR-0039 §2). A phase allows a set; a station type declares what it uses;
# the engine grants a station only what it uses inside its phase (tools, credentials, write access).
CAPABILITIES: dict[str, str] = {
    "read-repo": "read the branch or the team's repo",
    "write-docs": "write docs, specs and designs",
    "write-tickets": "create or update tickets",
    "write-code": "change the code on the branch",
    "run-sandbox": "run commands in the sandbox",
    "run-scanners": "run security scanners and use their service tokens",
    "push-image": "push an image to the registry",
    "deploy": "deploy to an environment (deploy:<env>, deploy:* for any)",
    "promote": "promote an image from one environment to the next",
    "open-pr": "push a branch and open a pull request (or tag a handover)",
    "read-metrics": "read metrics",
    "read-logs": "read logs",
    "open-incident": "open an incident",
    "rollback": "roll an environment back (rollback:<env>, rollback:* for any)",
    "approve": "record a person's approval",
}
PER_ENVIRONMENT = {"deploy", "rollback"}  # named per environment: deploy:<env>
# all an org column may allow: it looks and decides, never writes, deploys or opens a PR
LOOK_AND_DECIDE = frozenset({"read-repo", "run-scanners", "read-metrics", "read-logs", "approve"})
OBSERVE = frozenset({"read-repo"})  # what a custom step gets unless an admin grants more
WRITES = frozenset({"write-code", "write-docs"})
_CAP = re.compile(r"^(?P<base>[a-z][a-z-]{1,30})(?::(?P<env>\*|[a-z][a-z0-9-]{0,30}))?$")


def capability_problem(text: str) -> str | None:
    """Why `text` is not a capability, or None."""
    m = _CAP.match(text)
    if not m or m["base"] not in CAPABILITIES:
        return f"unknown capability '{text}' (one of {', '.join(CAPABILITIES)})"
    if (m["base"] in PER_ENVIRONMENT) != bool(m["env"]):
        return (
            f"'{text}': {m['base']} names its environment ({m['base']}:<env> or {m['base']}:*)"
            if m["base"] in PER_ENVIRONMENT
            else f"'{text}': only {' and '.join(sorted(PER_ENVIRONMENT))} name an environment"
        )
    return None


def grants(allowed: Collection[str], capability: str) -> bool:
    """Whether a set of capabilities covers one: `deploy:*` covers `deploy:prod`."""
    if capability in allowed:
        return True
    base, _, env = capability.partition(":")
    return bool(env) and f"{base}:*" in allowed


def narrow(uses: Iterable[str], allowed: Collection[str]) -> frozenset[str]:
    """What a station is granted: what it uses, inside what its phase allows."""
    return frozenset(u for u in uses if grants(allowed, u))


# ----------------------------------------------------------------- phases ---
@dataclass(frozen=True)
class Phase:
    """One column of the registry (ADR-0039 §2)."""

    id: str
    title: str
    allows: frozenset[str]
    gives: frozenset[str]  # artifact types a station in this phase may give
    source: str = "built-in"  # built-in | org | <pack>@<version>
    after: str | None = None  # an org column: the built-in phase it follows

    @property
    def builtin(self) -> bool:
        return self.source == "built-in"


def _p(pid: str, title: str, allows: Iterable[str], gives: Iterable[str]) -> Phase:
    return Phase(pid, title, frozenset(allows), frozenset(gives))


# The seven built-in phases: fixed in order, never removed (the factory default lanes use only these).
BUILTIN_PHASES: list[Phase] = [
    _p("discover", "Discover", ["read-repo", "run-sandbox", "run-scanners", "write-docs", "write-tickets"], ["report"]),
    _p("design", "Design", ["read-repo", "write-docs", "write-tickets"], ["spec", "design", "stories", "report"]),
    _p("plan", "Plan", ["read-repo", "write-docs", "write-tickets"], ["design", "stories", "report"]),
    _p("develop", "Develop", ["read-repo", "write-code", "write-docs", "run-sandbox"], ["branch", "report"]),
    _p("verify", "Verify", ["read-repo", "run-sandbox", "run-scanners", "approve"], ["report"]),
    _p(
        "deliver",
        "Deliver",
        ["read-repo", "run-sandbox", "push-image", "deploy:*", "promote", "open-pr", "approve"],
        ["image", "url", "pr", "report"],
    ),
    _p("operate", "Operate", ["read-repo", "read-metrics", "read-logs", "open-incident"], ["report"]),
]
assert [p.id for p in BUILTIN_PHASES] == [c for c, _ in COLUMNS]  # noqa: S101 - one list of columns
PHASE_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
RESERVED_PHASE_IDS = set(COLUMN_IDS) | set(PHASE_TO_COLUMN)


def org_column_errors(column: Phase) -> list[str]:
    """An org column looks and decides only (ADR-0039 §2, settled 2026-10-12)."""
    errors = []
    if column.after not in COLUMN_IDS[:-1]:
        errors.append(
            f"column '{column.id}': `after` must be a built-in phase it follows "
            f"({', '.join(COLUMN_IDS[:-1])}); a column after operate comes only from a pack"
        )
    extra = sorted(c for c in column.allows if c not in LOOK_AND_DECIDE)
    if extra:
        errors.append(
            f"column '{column.id}' allows {', '.join(extra)}: an org column only looks and decides "
            f"({', '.join(sorted(LOOK_AND_DECIDE))}); a phase that writes or deploys comes only from a reviewed pack"
        )
    if column.gives - {"report"}:
        errors.append(f"column '{column.id}' may give only reports, not {', '.join(sorted(column.gives - {'report'}))}")
    return errors


class PhaseRegistry:
    """The ordered phases a workflow's columns come from: the built-ins, the org's look-and-decide
    columns each after the built-in phase it names, then the phases of installed packs (appended:
    existing workflows gain an empty column, nothing reorders)."""

    def __init__(self, columns: Sequence[Phase] = (), packs: Sequence[Phase] = ()) -> None:
        errors = registry_errors(columns, packs)
        if errors:
            raise ValueError("; ".join(errors))
        ordered: list[Phase] = []
        for b in BUILTIN_PHASES:
            ordered.append(b)
            ordered += [c for c in columns if c.after == b.id]
        self.phases: list[Phase] = ordered + list(packs)
        self._by_id = {p.id: p for p in self.phases}

    @property
    def ids(self) -> list[str]:
        return [p.id for p in self.phases]

    def __contains__(self, phase_id: object) -> bool:
        return phase_id in self._by_id

    def get(self, phase_id: str) -> Phase:
        return self._by_id[phase_id]

    def index(self, phase_id: str) -> int:
        return self.ids.index(phase_id)

    def title(self, phase_id: str) -> str:
        return self._by_id[phase_id].title if phase_id in self._by_id else phase_id

    def where_allowed(self, capability: str) -> list[str]:
        return [p.title for p in self.phases if grants(p.allows, capability)]


def registry_errors(columns: Sequence[Phase], packs: Sequence[Phase]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for p in [*columns, *packs]:
        if not PHASE_ID.match(p.id):
            errors.append(f"phase id '{p.id}' must be lowercase letters, digits and dashes")
        if p.id in RESERVED_PHASE_IDS:
            errors.append(f"phase id '{p.id}' is a built-in phase name")
        if p.id in seen:
            errors.append(f"phase id '{p.id}' is used twice")
        seen.add(p.id)
        errors += [f"phase '{p.id}': {msg}" for c in sorted(p.allows) if (msg := capability_problem(c))]
        errors += [f"phase '{p.id}' gives unknown artifact type '{g}'" for g in sorted(p.gives - set(ARTIFACT_TYPES))]
    for c in columns:
        if c.source != "org":
            errors.append(f"column '{c.id}' is not an org column")
        errors += org_column_errors(c)
    for p in packs:
        if p.source in ("built-in", "org") or "@" not in p.source:
            errors.append(f"phase '{p.id}': a pack phase names its pack as <pack>@<version>")
    return errors


BUILTIN_REGISTRY = PhaseRegistry()


# ----------------------------------------------------------- station types ---
@dataclass(frozen=True)
class StationType:
    id: str
    column: str
    needs: tuple[Need, ...]
    gives: tuple[ArtifactRef, ...]
    # the engine handler per target (None: the type does nothing there and is skipped)
    implementations: dict[str, str | None] = field(default_factory=dict)
    floor: frozenset[str] = frozenset()  # floor controls this type provides: test | secrets | risk
    uses: frozenset[str] = OBSERVE  # capabilities it needs (ADR-0039 §3), inside its column's allows

    def gives_types(self) -> set[str]:
        return {g.type for g in self.gives}


def _t(
    tid: str,
    column: str,
    needs: Sequence[str],
    gives: Sequence[str],
    factory: str | None,
    repo: str | None,
    floor: Iterable[str] = (),
    uses: Iterable[str] = OBSERVE,
) -> StationType:
    return StationType(
        tid,
        column,
        tuple(parse_need(n) for n in needs),
        tuple(parse_ref(g) for g in gives),
        {"factory": factory, "repo": repo},
        frozenset(floor),
        frozenset(uses),
    )


# The library of built-in types (ADR-0037 §3). Built-ins fix column, needs and gives in code;
# a lane sets executor, owner, routing and policy fields only.
STATION_TYPES: dict[str, StationType] = {
    t.id: t
    for t in [
        _t("onboard", "discover", ["branch@1"], ["report.repo@1"], None, "onboard", uses=["read-repo"]),
        _t(
            "repo-scan",
            "discover",
            ["branch@1", "report.repo@1"],
            ["report.readiness@1"],
            None,
            "repo-scan",
            floor=["secrets"],
            uses=["read-repo", "run-sandbox", "run-scanners"],
        ),
        _t(
            "assess",
            "discover",
            ["branch@1", "report.readiness@1"],
            ["report.assessment@1"],
            None,
            "assess",
            uses=["read-repo", "run-sandbox"],
        ),
        # how an assessment leaves the lane, after the scans it folds in (ADR-0037 amended 2026-10-11)
        _t("report", "deliver", ["report.assessment@1"], ["report.assessment@1"], None, "report"),
        _t("requirements", "design", ["request@1"], ["spec@1"], "requirements", "requirements"),
        _t("design", "design", ["spec@1"], ["design@1"], "design", "design", uses=["read-repo", "write-docs"]),
        _t(
            "implement",
            "develop",
            ["design@1 | request@1", "branch@1"],
            ["branch@1", "report.change@1"],
            "implement",
            "repo-implement",
            uses=["read-repo", "write-code", "run-sandbox"],
        ),
        _t(
            "test",
            "verify",
            ["branch@1"],
            ["report.test@1"],
            "test",
            "repo-test",
            floor=["test"],
            uses=["read-repo", "run-sandbox"],
        ),
        # the pack's e2e suite against the app in the sandbox, before any image (ADR-0043 §5);
        # team repos: their CI is theirs
        _t(
            "preview-e2e",
            "verify",
            ["branch@1"],
            ["report.test@1"],
            "preview-e2e",
            None,
            uses=["read-repo", "run-sandbox"],
        ),
        # agent-readiness Level 3 plus the blueprint's secret scan (quality_gate)
        _t(
            "quality-gate",
            "verify",
            ["branch@1"],
            ["report.readiness@1"],
            "quality-gate",
            None,
            floor=["secrets"],
            uses=["read-repo", "run-sandbox", "run-scanners"],
        ),
        # secrets floor: check: secrets
        _t(
            "scan",
            "verify",
            ["branch@1"],
            ["report.scan@1"],
            "scan",
            "scan",
            uses=["read-repo", "run-sandbox", "run-scanners"],
        ),
        _t(
            "code-review",
            "verify",
            ["branch@1", "spec@1 | request@1"],
            ["report.review@1"],
            "code-review",
            "repo-review",
            uses=["read-repo", "run-sandbox"],
        ),
        _t(
            "change-risk",
            "verify",
            ["branch@1"],
            ["report.risk@1"],
            "change-risk",
            "change-risk",
            floor=["risk"],
            uses=["read-repo", "approve"],
        ),
        _t(
            "build",
            "deliver",
            ["branch@1"],
            ["image@1"],
            "build",
            None,
            uses=["read-repo", "run-sandbox", "push-image"],
        ),
        _t("deploy", "deliver", ["image@1"], ["url@1"], "deploy", None, uses=["deploy:*"]),
        # repairs the chart, Dockerfile or app config: a branch change, so it re-enters verify
        # (a repair that re-enters verify may write code outside develop: `capability_errors`)
        _t(
            "deploy-repair",
            "deliver",
            ["branch@1"],
            ["branch@1"],
            "deploy-repair",
            None,
            uses=["read-repo", "write-code", "run-sandbox"],
        ),
        _t(
            "acceptance",
            "deliver",
            ["url@1", "spec@1"],
            ["report.acceptance@1"],
            "acceptance",
            None,
            uses=["read-repo", "run-sandbox"],
        ),
        _t("handover", "deliver", ["branch@1"], ["pr@1"], "handover", "pull-request", uses=["read-repo", "open-pr"]),
    ]
}
CUSTOM = "agent"  # the generic agent handler: a custom station declares its own contract
FLOOR = {"test": "test", "secrets": "a secret scan", "risk": "change-risk"}

# today's handlers, by the type whose contract they implement
TYPE_OF_HANDLER: dict[str, str] = {
    h: t.id for t in STATION_TYPES.values() for h in t.implementations.values() if h is not None
}


# ------------------------------------------------------------------ roles ----
BUILTIN_ROLES: dict[str, str] = {
    "product-owner": "Product owner",
    "tech-lead": "Tech lead",
    "security": "Security",
    "qa": "QA",
}
ROLE_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


# --------------------------------------------------------- the fit checks ---
class StationLike(Protocol):
    """What the checks read from a workflow station (`workflow.WorkflowStation`)."""

    id: str
    on_fail: str | None
    next: str | None
    only_on_fail: bool
    owner: str | None

    def type_id(self) -> str: ...
    def column(self, before: str) -> str: ...
    def needs_refs(self) -> tuple[Need, ...]: ...
    def gives_refs(self) -> tuple[ArtifactRef, ...]: ...
    def floor_controls(self) -> frozenset[str]: ...
    def uses_caps(self) -> frozenset[str]: ...


def satisfied(need: Need, have: Collection[ArtifactRef]) -> bool:
    return any(r in have for r in need)


def columns(stations: Sequence[StationLike], first: str = COLUMN_IDS[0]) -> dict[str, str]:
    """Each station's column; a custom station without one takes the column of the one before."""
    out: dict[str, str] = {}
    current = first
    for s in stations:
        current = s.column(current)
        out[s.id] = current
    return out


def fit_errors(
    stations: Sequence[StationLike],
    entry: Sequence[ArtifactRef],
    col: dict[str, str] | None = None,
    end_is_exit: bool = False,
    order: Sequence[str] = COLUMN_IDS,
) -> list[str]:
    """Column, data and floor rules (ADR-0037 §1, §5) over a lane's stations (`end_is_exit`: the
    lane's end counts as an exit, so a lane that changes code can't end before the controls ran).
    `order` is the registry's column order (built-ins, org columns, pack phases)."""
    errors: list[str] = []
    forward = [s for s in stations if not s.only_on_fail]
    by_id = {s.id: s for s in stations}
    col = col or columns(stations)

    # 1. column rule: free order within a column, never back to an earlier column
    errors += column_errors(forward, col, order)

    # 2. data rule: what is available before each forward station
    have: set[ArtifactRef] = set(entry)
    before: dict[str, set[ArtifactRef]] = {}
    for s in forward:
        before[s.id] = set(have)
        for need in s.needs_refs():
            if not satisfied(need, have):
                errors.append(
                    f"station '{s.id}' needs {need_text(need)}, which no station to its left gives "
                    f"and the workflow does not start from"
                )
        have |= set(s.gives_refs())
    for s in stations:
        if not s.only_on_fail:
            continue
        sources = [o for o in stations if o.on_fail == s.id and o.id in before]
        for need in s.needs_refs():
            if sources and not all(satisfied(need, before[o.id]) for o in sources):
                errors.append(
                    f"repair station '{s.id}' needs {need_text(need)}, not given before every station that routes to it"
                )

    # 3. floor rule
    errors += floor_errors(stations, by_id, col, end_is_exit, order)
    return errors


def floor_errors(
    stations: Sequence[StationLike],
    by_id: dict[str, StationLike] | None = None,
    col: dict[str, str] | None = None,
    end_is_exit: bool = False,
    order: Sequence[str] = COLUMN_IDS,
) -> list[str]:
    """Mandatory controls (ADR-0037 §5). A workflow that never gives `branch` has no scope floor.
    With `end_is_exit`, the lane's end counts as an exit: a lane that changes code must not end
    before the controls ran."""
    by_id = by_id or {s.id: s for s in stations}
    forward = [s for s in stations if not s.only_on_fail]
    pos = {s.id: i for i, s in enumerate(forward)}
    changes_code = [s for s in stations if any(g.type == "branch" for g in s.gives_refs())]
    if not changes_code:
        return []
    errors: list[str] = []

    def walk(start: int, after: str) -> None:
        seen: set[str] = set()
        for s in forward[start:]:
            exits = {g.type for g in s.gives_refs()} & EXITS
            if exits:
                missing = [FLOOR[c] for c in FLOOR if c not in seen]
                if missing:
                    errors.append(
                        f"station '{s.id}' gives {', '.join(sorted(exits))} but {', '.join(missing)} "
                        f"{'does' if len(missing) == 1 else 'do'} not run after {after} changes the branch "
                        f"(mandatory controls, ADR-0037 §5)"
                    )
                return
            if any(g.type == "branch" for g in s.gives_refs()):
                seen = set()  # a later branch change needs the controls again
            seen |= s.floor_controls()
        missing = [FLOOR[c] for c in FLOOR if c not in seen]
        if end_is_exit and missing:
            errors.append(
                f"the lane ends before {', '.join(missing)} {'runs' if len(missing) == 1 else 'run'} "
                f"after {after} changes the branch (mandatory controls, ADR-0037 §5)"
            )

    for s in changes_code:
        if s.only_on_fail:
            if not s.next or s.next not in pos:
                errors.append(f"repair station '{s.id}' changes the branch and must continue at a forward station")
                continue
            walk(pos[s.next], f"repair station '{s.id}'")
        else:
            walk(pos[s.id] + 1, f"'{s.id}'")

    # a failing control routes back to develop (or earlier) or holds; never forward
    col = col or columns(stations)
    for s in stations:
        if s.floor_controls() and s.on_fail and s.on_fail in by_id:
            target = col[s.on_fail]
            if order.index(target) > order.index("develop"):
                errors.append(
                    f"station '{s.id}' is a mandatory control: its failure must route back to develop "
                    f"or hold, not to '{s.on_fail}' ({target})"
                )
    return sorted(set(errors))


def column_errors(forward: Sequence[StationLike], col: dict[str, str], order: Sequence[str] = COLUMN_IDS) -> list[str]:
    """The column rule alone, over a whole lane."""
    errors, last = [], -1
    for s in forward:
        i = order.index(col[s.id])
        if i < last:
            errors.append(
                f"station '{s.id}' sits in {col[s.id]} but comes after a {order[last]} station: "
                f"columns run {' → '.join(order)}"
            )
        last = max(last, i)
    return errors


def reenters_verify(s: StationLike, col: dict[str, str], order: Sequence[str]) -> bool:
    """A repair whose fix goes back through verify (the floor runs again after it)."""
    return s.only_on_fail and bool(s.next) and s.next in col and order.index(col[s.next]) <= order.index("verify")


def capability_errors(
    stations: Sequence[StationLike], col: dict[str, str], registry: PhaseRegistry = BUILTIN_REGISTRY
) -> list[str]:
    """Rule 4 (ADR-0039 §3): each station uses and gives no more than its column allows. The one
    exception: a repair that re-enters verify may change the branch from a later column (deploy
    repair), since the mandatory controls run again on what it wrote."""
    errors: list[str] = []
    for s in stations:
        phase = registry.get(col[s.id])
        repair = reenters_verify(s, col, registry.ids)
        for c in sorted(s.uses_caps()):
            if problem := capability_problem(c):
                errors.append(f"station '{s.id}': {problem}")
            elif not grants(phase.allows, c) and not (repair and c == "write-code"):
                where = registry.where_allowed(c)
                errors.append(
                    f"station '{s.id}' uses {c}, which {phase.title} does not allow"
                    + (f" (it belongs to {' or '.join(where)})" if where else "")
                )
        for t in sorted({g.type for g in s.gives_refs()} - phase.gives):
            if not (repair and t == "branch"):
                errors.append(f"station '{s.id}' gives {t}, which a {phase.title} station may not give")
    return errors


def role_errors(stations: Iterable[StationLike], roles: Collection[str]) -> list[str]:
    return [
        f"station '{s.id}': owner '{s.owner}' is not a role (known: {', '.join(sorted(roles))})"
        for s in stations
        if s.owner and s.owner not in roles
    ]
