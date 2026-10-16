"""Station templates and scope presets for the workflow builder (ADR-0039 §3–§5, step 1c).

A template is what a phase box's "Add station" offers: a built-in station type, a scan named after
its check, a custom agent step (any phase, observe-only until an admin grants more), or a station a
reviewed pack ships with its agent. Everything here is pure: the draft (`engine.workflows.Draft`)
stores the result, and publish still runs every check (`workflow.validate_workflow`).

Placement follows the phase boxes: a new station goes at the end of its phase, so the lane's order
is the registry's column order and a station from Deliver can't land in Develop (board 2).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_factory import contract
from agent_factory.contract import BUILTIN_REGISTRY
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CUSTOM_PROMPT,
    DEFAULT_ENTRY,
    HANDLER_INFO,
    AgentSpec,
    RefDoc,
    WorkflowDoc,
    WorkflowStation,
    _role_errors,
    load_workflow_dir,
    tools_within,
)

CUSTOM_ID = "custom"
SCAN_CHECKS: dict[str, tuple[str, str]] = {
    "secrets": ("Secret scan", "finds keys, tokens and passwords committed to the code"),
    "code": ("Code scan", "static analysis for insecure patterns (SAST)"),
    "dependencies": ("Dependency scan", "known vulnerabilities in the libraries the code uses"),
    "config": ("Config scan", "Dockerfiles, Kubernetes manifests and infrastructure as code"),
}
# stations whose failure goes back to the develop station that wrote the code
_ROUTE_TO_DEVELOP = {"test", "quality-gate", "scan", "code-review", "build"}


@dataclass(frozen=True)
class StationTemplate:
    """One entry of a phase box's "Add station" list."""

    id: str  # "type:" + type id, "scan:" + check, custom, or <pack>/<station>
    title: str
    hint: str
    kind: Literal["agent", "check"]
    phase: str | None  # None: the custom step, offered in every phase
    base_id: str  # the station id it suggests
    station: dict[str, Any]  # WorkflowStation fields, without id (and phase for the custom step)
    source: str = "built-in"  # built-in | <pack>@<version>
    works_on: frozenset[str] = frozenset({"factory", "repo"})
    control: bool = False  # a mandatory control (test, secret scan, change-risk)
    agent: AgentSpec | None = None  # a pack template ships its agent
    # (title, hint) per works_on where a type's implementation reads differently (handover / PR)
    labels: dict[str, tuple[str, str]] = field(default_factory=dict)

    def available_for(self, works_on: str) -> bool:
        return works_on in self.works_on

    def title_for(self, works_on: str) -> tuple[str, str]:
        return self.labels.get(works_on, (self.title, self.hint))


def builtin_templates() -> list[StationTemplate]:
    """Every built-in type as a template (scan once per check), in the registry's column order."""
    out: list[StationTemplate] = []
    for t in contract.STATION_TYPES.values():
        if t.id == "scan":
            for check, (title, hint) in SCAN_CHECKS.items():
                out.append(
                    StationTemplate(
                        id=f"scan:{check}",
                        title=title,
                        hint=hint,
                        kind="check",
                        phase=t.column,
                        base_id=f"{check}-scan",
                        station={"type": "scan", "check": check, "fail_on": "high"},
                        works_on=frozenset(w for w, h in t.implementations.items() if h),
                        control=check == "secrets",
                    )
                )
            continue
        handlers = [h for h in t.implementations.values() if h]
        info = HANDLER_INFO.get(handlers[0])
        labels = {
            w: (HANDLER_INFO[h].label, HANDLER_INFO[h].hint)
            for w, h in t.implementations.items()
            if h and h in HANDLER_INFO
        }
        out.append(
            StationTemplate(
                id=f"type:{t.id}",
                title=info.label if info else t.id,
                hint=info.hint if info else "",
                kind="agent" if any(h in AGENT_HANDLERS for h in handlers) else "check",
                phase=t.column,
                base_id=t.id,
                station={"type": t.id, **({"only_on_fail": True} if t.id == "deploy-repair" else {})},
                works_on=frozenset(w for w, h in t.implementations.items() if h),
                control=bool(t.floor),
                labels=labels,
            )
        )
    out.append(
        StationTemplate(
            id=CUSTOM_ID,
            title="Custom agent step",
            hint="your own agent: it reads the code and reports, and gets more only if you grant it",
            kind="agent",
            phase=None,
            base_id="custom-step",
            station={"type": contract.CUSTOM},
        )
    )
    order = contract.COLUMN_IDS
    return sorted(out, key=lambda t: order.index(t.phase) if t.phase in order else len(order))


BUILTIN_TEMPLATES = {t.id: t for t in builtin_templates()}


# ----------------------------------------------------------------- presets ---
@dataclass(frozen=True)
class ScopePreset:
    """A phase-scoped lane (ADR-0039 §5): which phases it covers, what it starts from beyond the
    request and the code, and the stations it adds when the lane has none of their kind."""

    id: str
    title: str
    hint: str
    scope: tuple[str, ...]  # (first, last); empty: whatever the lane's stations cover
    entry: tuple[str, ...] = ()  # beyond the request and the branch
    adds: tuple[str, ...] = ()  # template ids


SCOPE_PRESETS: list[ScopePreset] = [
    ScopePreset(
        "design-only",
        "Design only",
        "Turn a request into a spec and a design. Building happens in your own tools.",
        ("design", "plan"),
        adds=("type:requirements", "type:design"),
    ),
    ScopePreset(
        "development-only",
        "Development only",
        "Write the change and verify it. Your own pipeline delivers it.",
        ("develop", "verify"),
        adds=("type:implement", "type:test", "scan:secrets", "type:change-risk"),
    ),
    ScopePreset(
        "test-and-secure",
        "Test & secure",
        "Test, scan and risk-check code that was written elsewhere.",
        ("verify", "verify"),
        adds=("type:test", "scan:secrets", "type:change-risk"),
    ),
    ScopePreset(
        "delivery-only",
        "Delivery only",
        "Ship code your own tools built and tested. They provide the test and change-risk reports.",
        ("deliver", "deliver"),
        entry=("report.test@1", "report.risk@1"),
        adds=("type:build", "type:deploy", "type:handover"),
    ),
    ScopePreset(
        "full",
        "Full package",
        "The factory's whole lane again, every phase from the request to delivery, plus your own steps.",
        (),
    ),
]
PRESETS = {p.id: p for p in SCOPE_PRESETS}


def preset_of(doc: WorkflowDoc, standard: WorkflowDoc | None = None) -> str | None:
    """The preset a lane's scope and entry match, if any. Full package also needs every station of
    the factory default lane (when known): a lane with stations removed is no longer the full one."""
    for p in SCOPE_PRESETS:
        entry = [*DEFAULT_ENTRY, *p.entry]
        if list(doc.scope) == list(p.scope) and sorted(doc.entry) == sorted(entry):
            if p.id == "full" and standard is not None:
                ids = {s.id for s in doc.stations}
                if any(s.id not in ids for s in standard.stations):
                    return None
            return p.id
    return None


# --------------------------------------------------------------- artifacts ---
ARTIFACT_TITLES: dict[str, str] = {
    "request": "The request",
    "branch": "The code",
    "spec": "Spec",
    "design": "Design",
    "stories": "Stories",
    "image": "Container image",
    "url": "Running app",
    "pr": "Pull request",
    "report.test": "Test report",
    "report.risk": "Change-risk report",
    "report.scan": "Scan report",
    "report.review": "Code review report",
    "report.acceptance": "Acceptance report",
}
# what a lane may start from beyond the request and the code (the builder's "Starts from" list)
ENTRY_CHOICES = ["spec@1", "design@1", "stories@1", "image@1", "url@1", "report.test@1", "report.risk@1"]


def artifact_title(key: str) -> str:
    if key in ARTIFACT_TITLES:
        return ARTIFACT_TITLES[key]
    if key.startswith("report."):
        return f"{key.split('.', 1)[1].replace('-', ' ').capitalize()} report"
    return key.capitalize()


_ZERO = "0" * 64


def example_manifest(ref: contract.ArtifactRef) -> dict[str, Any]:
    """A manifest of the artifact's schema with placeholder values, for the requester to edit.
    `tests/test_builder.py` checks each one is valid."""
    if ref.type == "report":
        reports: dict[str, dict[str, Any]] = {
            "test": {"ok": True, "command": "make test"},
            "risk": {"digest": "sha256-of-the-diff", "holds": 0},
            "scan": {"check": "secrets", "passed": True},
            "review": {"passed": True},
            "readiness": {"level": 3},
            "change": {"commit": "0123abc"},
        }
        data = reports.get(ref.kind or "", {})
        return {
            "kind": ref.kind,
            "station": "your-ci",
            "summary": f"what your tool found ({artifact_title(ref.key).lower()})",
            "evidence": [{"path": f"{ref.kind}-report.json", "sha256": _ZERO}],
            "data": data,
        }
    others: dict[str, dict[str, Any]] = {
        "spec": {
            "path": "docs/spec.md",
            "requirements": [{"id": "R1", "title": "what the first requirement asks"}],
            "acceptance_scenarios": 1,
            "holdout": {"scenarios": 0, "sha256": None},
        },
        "design": {"files": [{"path": "docs/design.md", "sha256": _ZERO}]},
        "stories": {"stories": [{"id": "S1", "title": "a story", "acceptance": ["what done looks like"]}]},
        "image": {"ref": "registry.example.com/app@sha256:" + _ZERO, "sbom": None, "provenance": None},
        "url": {"url": "https://app.example.com"},
        "pr": {"via": "github", "branch": "feature/x"},
    }
    return others.get(ref.type, {})


# --------------------------------------------------------------- placement ---
def insert_position(doc: WorkflowDoc, phase: str, order: Sequence[str]) -> int:
    """The index at the end of `phase`'s box: after every station in it or an earlier phase."""
    col = doc.columns()
    target = order.index(phase) if phase in order else len(order)
    pos = 0
    for i, s in enumerate(doc.stations):
        c = col.get(s.id, phase)
        if (order.index(c) if c in order else len(order)) <= target:
            pos = i + 1
    return pos


def unique_id(base: str, taken: Collection[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]", "-", base.lower()).strip("-_") or "step"
    base = base if re.match(r"^[a-z]", base) else f"s-{base}"
    base = base[:36]
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _develop_station(doc: WorkflowDoc) -> str | None:
    """The forward station that writes the code (where failures of the controls go back to)."""
    for s in doc.forward_stations():
        if any(g.type == "branch" for g in s.gives_refs()) and s.column() == "develop":
            return s.id
    return None


def _first_in(doc: WorkflowDoc, column: str) -> str | None:
    col = doc.columns()
    return next((s.id for s in doc.forward_stations() if col[s.id] == column), None)


def _fits(doc: WorkflowDoc, station: WorkflowStation, spec: AgentSpec) -> bool:
    """The agent can do the handler's work with only the tools its station will be granted."""
    handler = station.handler_for(doc.works_on) or station.resolved_handler()
    if handler == "scan":
        return spec.observe_only
    column = station.column()
    allows = BUILTIN_REGISTRY.get(column).allows if column in BUILTIN_REGISTRY else contract.OBSERVE
    grant = contract.narrow(station.uses_caps(), allows)
    return not _role_errors(station.id, handler, spec) and not tools_within(spec.effective_tools(), grant)[1]


def _agent_for(
    doc: WorkflowDoc, station: WorkflowStation, library: AgentLibrary | None = None
) -> tuple[str | None, AgentSpec | None]:
    """The agent a new station starts with, and the spec to add when it comes from the library:
    the one another station of its type uses, else the one the factory default lanes give this
    type (an intake agent for requirements, not any observer that happens to fit), else an agent
    of the lane that fits."""
    tid = station.type_id()
    for s in doc.stations:
        if s.agent in doc.agents and s.type_id() == tid and tid != contract.CUSTOM:
            return s.agent, None
    spec = (library.get(f"{doc.works_on}:{tid}") or library.get(tid)) if library else None
    if spec is not None:
        return spec.id, (None if spec.id in doc.agents else spec)
    for aid, own in doc.agents.items():
        if _fits(doc, station, own):
            return aid, None
    return None, None


@dataclass
class AgentLibrary:
    """The agents the factory default lanes give each station type (`<works_on>:<type>` and
    `<type>`) and the reference docs they read: a lane without an agent for a new station borrows
    one, with its docs."""

    agents: dict[str, AgentSpec] = field(default_factory=dict)
    docs: dict[str, RefDoc] = field(default_factory=dict)

    def get(self, key: str) -> AgentSpec | None:
        return self.agents.get(key)


def agent_library(lane_files: Iterable[Path]) -> AgentLibrary:
    out = AgentLibrary()
    for path in lane_files:
        lane = load_workflow_dir(path)
        out.docs.update(lane.docs)
        for st in lane.stations:
            if st.agent and st.agent in lane.agents:
                spec = lane.agents[st.agent]
                out.agents.setdefault(f"{lane.works_on}:{st.type_id()}", spec)
                out.agents.setdefault(st.type_id(), spec)
    return out


def instantiate(
    doc: WorkflowDoc,
    template: StationTemplate,
    phase: str,
    station_id: str | None = None,
    library: AgentLibrary | None = None,
) -> tuple[WorkflowStation, AgentSpec | None]:
    """The station a template adds in a phase, with sensible routes and agent, plus the agent spec
    to add with it (a custom step gets its own observe-only agent to write; a pack template brings
    its agent unless the lane already has one with that id)."""
    taken = [s.id for s in doc.stations]
    sid = station_id or unique_id(template.base_id, taken)
    data: dict[str, Any] = {"id": sid, **template.station}
    if template.phase is None:
        data["phase"] = phase
    elif template.source != "built-in":
        data["phase"] = template.phase
    station = WorkflowStation.model_validate(data)
    new_agent: AgentSpec | None = None
    if template.agent is not None:
        if template.agent.id not in doc.agents:
            new_agent = template.agent
        station.agent = template.agent.id
    elif template.id == CUSTOM_ID:
        aid = unique_id(sid, list(doc.agents))
        new_agent = AgentSpec(id=aid, description=f"the {sid} step", tools="observer", prompt=CUSTOM_PROMPT)
        station.agent = aid
    elif station.kind == "agent" or station.resolved_handler() == "scan":
        station.agent, new_agent = _agent_for(doc, station, library)
    if station.resolved_handler() in _ROUTE_TO_DEVELOP:
        station.on_fail = _develop_station(doc)
    if station.only_on_fail:
        station.next = _first_in(doc, "verify")
    return station, new_agent


def with_station(
    doc: WorkflowDoc,
    template: StationTemplate,
    phase: str,
    order: Sequence[str],
    station_id: str | None = None,
    library: AgentLibrary | None = None,
) -> tuple[WorkflowDoc, str]:
    """A copy of the lane with the template added at the end of `phase`, and the new station's id."""
    out = doc.model_copy(deep=True)
    station, agent = instantiate(out, template, phase, station_id, library)
    if agent is not None:
        out.agents[agent.id] = agent
        for d in agent.context_docs:  # a borrowed agent brings the reference docs it reads
            if d not in out.docs and library and d in library.docs:
                out.docs[d] = library.docs[d]
    out.stations.insert(insert_position(out, phase, order), station)
    return out, station.id


def offered(template: StationTemplate, phase: str, doc: WorkflowDoc) -> bool:
    """Whether a phase box offers the template: its own phase (the custom step in every phase),
    for what the lane works on."""
    return template.available_for(doc.works_on) and template.phase in (None, phase)


def added_problems(
    doc: WorkflowDoc,
    template: StationTemplate,
    phase: str,
    order: Sequence[str],
    validate: Callable[[WorkflowDoc], list[str]],
    before: Iterable[str] | None = None,
    library: AgentLibrary | None = None,
) -> list[str]:
    """The drop-time check: what adding the template now would break that isn't broken already,
    in the publish checks' own words (a station it needs to its left, a control it would skip)."""
    known = set(before if before is not None else validate(doc))
    after, _ = with_station(doc, template, phase, order, library=library)
    return [p for p in validate(after) if p not in known]


# ------------------------------------------------------------ scope changes ---
@dataclass
class PresetResult:
    doc: WorkflowDoc
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


def _has_kind(doc: WorkflowDoc, template: StationTemplate) -> bool:
    if template.id == "scan:secrets":
        return any("secrets" in s.floor_controls() for s in doc.stations)
    tid = template.station.get("type")
    if template.control:
        floor = contract.STATION_TYPES[tid].floor if tid in contract.STATION_TYPES else frozenset()
        return any(floor & s.floor_controls() for s in doc.stations)
    return any(s.type_id() == tid for s in doc.stations)


def _cannot_run(doc: WorkflowDoc, span: Collection[str]) -> list[str]:
    """Stations a scope leaves unable to run: one that changes the code without Verify in the
    scope, one whose needs nothing gives before it, a repair with nowhere to continue."""
    out: list[str] = []
    have = set(doc.entry_refs())
    for s in doc.stations:
        if "verify" not in span and any(g.type == "branch" for g in s.gives_refs()):
            out.append(s.id)
            continue
        if s.only_on_fail:
            continue
        if not all(contract.satisfied(n, have) for n in s.needs_refs()):
            out.append(s.id)
            continue
        have |= set(s.gives_refs())
    forward = {s.id for s in doc.forward_stations()} - set(out)
    out += [s.id for s in doc.stations if s.only_on_fail and s.id not in out and s.next not in forward]
    return out


def _drop(doc: WorkflowDoc, ids: Collection[str]) -> None:
    doc.stations = [s for s in doc.stations if s.id not in ids]
    for s in doc.stations:
        if s.on_fail in ids:
            s.on_fail = None
        if s.next in ids:
            s.next = None


def apply_preset(
    doc: WorkflowDoc,
    preset: ScopePreset,
    order: Sequence[str],
    templates: dict[str, StationTemplate] | None = None,
    library: AgentLibrary | None = None,
    standard: WorkflowDoc | None = None,
) -> PresetResult:
    """Set the lane's scope and entry to the preset's, drop the stations outside it or unable to
    run in it (routes to them are cleared) and add the preset's stations the lane has none of.
    `standard` is the factory default lane the workflow comes from: Full package brings back every
    station of it the lane lacks, and a preset re-adds a standard station as it is there (its id,
    agent and routes) rather than a fresh one from the template."""
    templates = templates or BUILTIN_TEMPLATES
    out = doc.model_copy(deep=True)
    out.scope = list(preset.scope)
    out.entry = [*DEFAULT_ENTRY, *preset.entry]
    result = PresetResult(out)
    if not preset.scope and standard is not None:
        result.doc, result.added = restore_standard(out, standard, order)
        return result
    if preset.scope:
        first, last = order.index(preset.scope[0]), order.index(preset.scope[-1])
        span = set(order[first : last + 1])
        col = out.columns()
        result.removed = [s.id for s in out.stations if col[s.id] not in span]
        _drop(out, result.removed)
        while gone := _cannot_run(out, span):
            result.removed += gone
            _drop(out, gone)
    for tid in preset.adds:
        t = templates.get(tid)
        if t is None or not t.available_for(out.works_on) or _has_kind(out, t) or t.phase is None:
            continue
        from_standard = _standard_station(standard, t, out) if standard else None
        if from_standard is not None:
            out = _with_standard(out, standard, from_standard, order)  # type: ignore[arg-type]
            result.added.append(from_standard.id)
            continue
        out, sid = with_station(out, t, t.phase, order, library=library)
        result.added.append(sid)
    result.doc = out
    return result


def _standard_station(standard: WorkflowDoc, t: StationTemplate, doc: WorkflowDoc) -> WorkflowStation | None:
    """The station of the factory default lane a template stands for, when its id is free."""
    taken = {s.id for s in doc.stations}
    for s in standard.stations:
        if s.id in taken:
            continue
        if t.id.startswith("scan:") and s.resolved_handler() == "scan" and s.check_name() == t.id[5:]:
            return s
        if t.id.startswith("type:") and s.type_id() == t.station.get("type"):
            return s
    return None


def _bring_agent(out: WorkflowDoc, standard: WorkflowDoc, station: WorkflowStation) -> None:
    if station.agent and station.agent not in out.agents and station.agent in standard.agents:
        spec = standard.agents[station.agent]
        out.agents[spec.id] = spec
        for d in spec.context_docs:
            if d not in out.docs and d in standard.docs:
                out.docs[d] = standard.docs[d]


def _clean_routes(out: WorkflowDoc) -> None:
    ids = {s.id for s in out.stations}
    for s in out.stations:
        if s.on_fail not in ids:
            s.on_fail = None
        if s.next not in ids:
            s.next = None


def _with_standard(
    out: WorkflowDoc, standard: WorkflowDoc, station: WorkflowStation, order: Sequence[str]
) -> WorkflowDoc:
    out = out.model_copy(deep=True)
    st = station.model_copy(deep=True)
    _bring_agent(out, standard, st)
    out.stations.insert(insert_position(out, st.column(), order), st)
    _clean_routes(out)
    return out


def restore_standard(doc: WorkflowDoc, standard: WorkflowDoc, order: Sequence[str]) -> tuple[WorkflowDoc, list[str]]:
    """The lane as its factory default lane, in the standard's order, plus your own steps (custom and
    pack stations) at the end of their phase. Stations it shares with the standard keep the lane's
    settings, and routes a narrower scope cleared come back. Returns the restored ids."""
    out = doc.model_copy(deep=True)
    mine = {s.id: s for s in out.stations}
    restored = [s.id for s in standard.stations if s.id not in mine]
    stations = [mine.get(s.id) or s.model_copy(deep=True) for s in standard.stations]
    out.stations = stations
    for s in stations:
        if s.id in restored:
            _bring_agent(out, standard, s)
    col = doc.columns()
    by_std = {s.id: s for s in standard.stations}
    for s in stations:  # a route a narrower scope cleared comes back with its station
        std = by_std[s.id]
        s.on_fail = s.on_fail or std.on_fail
        s.next = s.next or std.next
    for own in doc.stations:
        # your own steps stay (custom and pack stations); built-in stations the standard doesn't
        # have (a narrower preset's additions) go, so Full package is the factory's whole lane
        if own.id not in by_std and own.station_type() is None:
            copy = own.model_copy(deep=True)
            if copy.station_type() is None:
                copy.phase = copy.phase or col[own.id]  # keep its phase wherever it lands
            out.stations.insert(insert_position(out, col[own.id], order), copy)
    _clean_routes(out)
    return out, restored
