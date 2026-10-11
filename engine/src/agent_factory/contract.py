"""Station contract (ADR-0037 §1–§5): station types, artifact types and the publish checks.

Pure: no I/O. A workflow is a **container** of seven fixed columns; a station **fits** only if

1. **column**: it sits in its type's column, columns never step back along the forward order;
2. **data**: every artifact it needs is given by a station to its left or is an entry artifact;
3. **floor**: when the workflow changes code, `test`, a secret scan and `change-risk` run after
   every change to the branch and before anything that gives `image`, `url` or `pr`.

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
    return phase if phase in COLUMN_IDS else PHASE_TO_COLUMN[phase]


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
) -> StationType:
    return StationType(
        tid,
        column,
        tuple(parse_need(n) for n in needs),
        tuple(parse_ref(g) for g in gives),
        {"factory": factory, "repo": repo},
        frozenset(floor),
    )


# The library of built-in types (ADR-0037 §3). Built-ins fix column, needs and gives in code;
# a lane sets executor, owner, routing and policy fields only.
STATION_TYPES: dict[str, StationType] = {
    t.id: t
    for t in [
        _t("onboard", "discover", ["branch@1"], ["report.repo@1"], None, "onboard"),
        _t(
            "repo-scan",
            "discover",
            ["branch@1", "report.repo@1"],
            ["report.readiness@1"],
            None,
            "repo-scan",
            floor=["secrets"],
        ),
        _t("assess", "discover", ["branch@1", "report.readiness@1"], ["report.assessment@1"], None, "assess"),
        # how an assessment leaves the lane, after the scans it folds in (ADR-0037 amended 2026-10-11)
        _t("report", "deliver", ["report.assessment@1"], ["report.assessment@1"], None, "report"),
        _t("requirements", "design", ["request@1"], ["spec@1"], "requirements", "requirements"),
        _t("design", "design", ["spec@1"], ["design@1"], "design", "design"),
        _t(
            "implement",
            "develop",
            ["design@1 | request@1", "branch@1"],
            ["branch@1", "report.change@1"],
            "implement",
            "repo-implement",
        ),
        _t("test", "verify", ["branch@1"], ["report.test@1"], "test", "repo-test", floor=["test"]),
        # agent-readiness Level 3 plus the blueprint's secret scan (quality_gate)
        _t("quality-gate", "verify", ["branch@1"], ["report.readiness@1"], "quality-gate", None, floor=["secrets"]),
        _t("scan", "verify", ["branch@1"], ["report.scan@1"], "scan", "scan"),  # secrets floor: check: secrets
        _t(
            "code-review",
            "verify",
            ["branch@1", "spec@1 | request@1"],
            ["report.review@1"],
            "code-review",
            "repo-review",
        ),
        _t("change-risk", "verify", ["branch@1"], ["report.risk@1"], "change-risk", "change-risk", floor=["risk"]),
        _t("build", "deliver", ["branch@1"], ["image@1"], "build", None),
        _t("deploy", "deliver", ["image@1"], ["url@1"], "deploy", None),
        # repairs the chart, Dockerfile or app config: a branch change, so it re-enters verify
        _t("deploy-repair", "deliver", ["branch@1"], ["branch@1"], "deploy-repair", None),
        _t("acceptance", "deliver", ["url@1", "spec@1"], ["report.acceptance@1"], "acceptance", None),
        _t("handover", "deliver", ["branch@1"], ["pr@1"], "handover", "pull-request"),
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


def satisfied(need: Need, have: Collection[ArtifactRef]) -> bool:
    return any(r in have for r in need)


def columns(stations: Sequence[StationLike]) -> dict[str, str]:
    """Each station's column; a custom station without one takes the column of the one before."""
    out: dict[str, str] = {}
    current = COLUMN_IDS[0]
    for s in stations:
        current = s.column(current)
        out[s.id] = current
    return out


def fit_errors(stations: Sequence[StationLike], entry: Sequence[ArtifactRef]) -> list[str]:
    """Column, data and floor rules (ADR-0037 §1, §5) over a workflow's stations."""
    errors: list[str] = []
    forward = [s for s in stations if not s.only_on_fail]
    by_id = {s.id: s for s in stations}
    col = columns(stations)

    # 1. column rule: free order within a column, never back to an earlier column
    last = -1
    for s in forward:
        i = COLUMN_IDS.index(col[s.id])
        if i < last:
            errors.append(
                f"station '{s.id}' sits in {col[s.id]} but comes after a {COLUMN_IDS[last]} station: "
                f"columns run {' → '.join(COLUMN_IDS)}"
            )
        last = max(last, i)

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
    errors += floor_errors(stations, by_id)
    return errors


def floor_errors(stations: Sequence[StationLike], by_id: dict[str, StationLike] | None = None) -> list[str]:
    """Mandatory controls (ADR-0037 §5). A workflow that never gives `branch` has no scope floor."""
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

    for s in changes_code:
        if s.only_on_fail:
            if not s.next or s.next not in pos:
                errors.append(f"repair station '{s.id}' changes the branch and must continue at a forward station")
                continue
            walk(pos[s.next], f"repair station '{s.id}'")
        else:
            walk(pos[s.id] + 1, f"'{s.id}'")

    # a failing control routes back to develop (or earlier) or holds; never forward
    col = columns(stations)
    for s in stations:
        if s.floor_controls() and s.on_fail and s.on_fail in by_id:
            target = col[s.on_fail]
            if COLUMN_IDS.index(target) > COLUMN_IDS.index("develop"):
                errors.append(
                    f"station '{s.id}' is a mandatory control: its failure must route back to develop "
                    f"or hold, not to '{s.on_fail}' ({target})"
                )
    return sorted(set(errors))


def role_errors(stations: Iterable[StationLike], roles: Collection[str]) -> list[str]:
    return [
        f"station '{s.id}': owner '{s.owner}' is not a role (known: {', '.join(sorted(roles))})"
        for s in stations
        if s.owner and s.owner not in roles
    ]
