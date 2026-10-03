"""Workflow services: one versioned workflow per blueprint, seeded from a
template, edited through a draft, published as immutable versions."""

from __future__ import annotations

from pathlib import Path

from agent_factory.skills import SkillLibrary
from agent_factory.state.base import StateStore
from agent_factory.workflow import (
    AGENT_HANDLERS,
    CHECK_HANDLERS,
    MAX_LEARNINGS_CHARS,
    PHASE_IDS,
    STATION_ID,
    AgentSpec,
    EvalCase,
    RefDoc,
    WorkflowDoc,
    WorkflowStation,
    WorkflowVersionInfo,
    canonical_handler,
    load_workflow_dir,
    validate_workflow,
    workflow_file,
    workflow_warnings,
)


class WorkflowError(Exception):
    """An invalid workflow, template or unknown version (surfaced as HTTP 409/404)."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message + (": " + "; ".join(problems) if problems else ""))
        self.problems = problems or []


class WorkflowService:
    def __init__(
        self,
        store: StateStore,
        workflow_id: str,
        template_dir: Path,
        skills_dir: Path,
        library: SkillLibrary | None = None,
    ) -> None:
        self.store = store
        self.workflow_id = workflow_id
        self.template_dir = template_dir
        self.skills_dir = skills_dir
        self.library = library
        self._cache: dict[int, WorkflowDoc] = {}
        self.draft = Draft(self)

    # ---------------------------------------------------------------- read --
    def active_version(self) -> int:
        v = self.store.active_workflow_version(self.workflow_id)
        if v is None:
            raise WorkflowError(f"workflow '{self.workflow_id}' has no active version (not seeded)")
        return v

    def get(self, version: int | None = None) -> WorkflowDoc:
        v = version or self.active_version()
        if v not in self._cache:  # versions are immutable, so caching is safe
            doc = self.store.get_workflow_version(self.workflow_id, v)
            if doc is None:
                raise WorkflowError(f"workflow '{self.workflow_id}' version {v} not found")
            self._cache[v] = doc
        return self._cache[v]

    def versions(self) -> list[WorkflowVersionInfo]:
        return self.store.list_workflow_versions(self.workflow_id)

    def template_update_available(self) -> bool:
        """True when the shipped template differs from every version of this workflow."""
        if not workflow_file(self.template_dir).exists():
            return False
        latest = load_workflow_dir(self.template_dir).template_hash
        seen = {self.get(i.version).template_hash for i in self.versions()}
        return latest not in seen

    # --------------------------------------------------------------- write --
    def ensure_seeded(self) -> int | None:
        """First start: copy the template into the DB as version 1. Never overwrites."""
        if self.store.active_workflow_version(self.workflow_id) is not None:
            return None
        return self.import_dir(self.template_dir, f"seeded from template '{self.template_dir.name}'")

    def import_dir(self, path: Path, note: str, activate: bool = True) -> int:
        return self.create_version(load_workflow_dir(path), note, activate)

    def imported_skills(self) -> set[str]:
        return self.library.imported_names() if self.library else set()

    def validate(self, doc: WorkflowDoc) -> list[str]:
        return validate_workflow(doc, self.skills_dir, self.imported_skills())

    def create_version(self, doc: WorkflowDoc, note: str, activate: bool = True) -> int:
        problems = self.validate(doc)
        if problems:
            raise WorkflowError("workflow is not valid", problems)
        if self.library:  # fix the commit of every imported skill this version uses
            doc = doc.model_copy(update={"skill_pins": self.library.pins_for(doc)})
        version = self.store.create_workflow_version(self.workflow_id, doc, note)
        if activate:
            self.store.set_active_workflow_version(self.workflow_id, version)
        return version

    def activate(self, version: int) -> WorkflowVersionInfo:
        self.get(version)  # raises if missing
        self.store.set_active_workflow_version(self.workflow_id, version)
        return next(i for i in self.versions() if i.version == version)


class WorkflowRegistry:
    """One WorkflowService per blueprint. The workflow id is the blueprint id."""

    def __init__(
        self,
        store: StateStore,
        blueprints: dict[str, Path],
        templates_dir: Path,
        skills_dir: Path,
        library: SkillLibrary | None = None,
    ) -> None:
        self.store = store
        self.templates_dir = templates_dir
        self.skills_dir = skills_dir
        self.library = library
        self._services = {
            pl: WorkflowService(store, pl, template, skills_dir, library) for pl, template in blueprints.items()
        }

    def skill_users(self, name: str) -> list[str]:
        """Workflows whose active version or draft uses a skill."""
        users = []
        for svc in self:
            docs = [svc.get(), svc.draft.get()[1]]
            if any(name in a.skills for d in docs for a in d.agents.values()):
                users.append(svc.workflow_id)
        return users

    def __getitem__(self, workflow_id: str) -> WorkflowService:
        try:
            return self._services[workflow_id]
        except KeyError as exc:
            raise WorkflowError(f"workflow '{workflow_id}' not found") from exc

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._services.values())

    def ids(self) -> list[str]:
        return list(self._services)

    def ensure_seeded(self) -> None:
        # Before workflows existed there was one line called "default": it becomes the
        # workflow of the first blueprint, so existing history is kept.
        first = next(iter(self._services), None)
        if (
            first
            and self.store.active_workflow_version(first) is None
            and self.store.active_workflow_version("default")
        ):
            self.store.rename_workflow("default", first)
        for svc in self._services.values():
            svc.ensure_seeded()

    # ------------------------------------------------------------ templates --
    def templates(self) -> list[tuple[str, WorkflowDoc]]:
        out = []
        for d in sorted(p for p in self.templates_dir.iterdir() if p.is_dir()):
            if workflow_file(d).exists():
                out.append((d.name, load_workflow_dir(d)))
        return out

    def template(self, name: str) -> WorkflowDoc:
        path = self.templates_dir / name
        if "/" in name or not workflow_file(path).exists():
            raise WorkflowError(f"template '{name}' not found")
        return load_workflow_dir(path)

    def load_fetched(self, root: Path, label: str) -> WorkflowDoc:
        """Load an imported template folder (validated later, at publish)."""
        if not workflow_file(root).exists():
            raise WorkflowError(f"{label} has no workflow.yaml")
        try:
            doc = load_workflow_dir(root)
        except ValueError as exc:
            raise WorkflowError(f"{label} is not a valid workflow template: {exc}") from exc
        return doc.model_copy(update={"template": label})


class Draft:
    """The single editable working copy of a workflow. It never runs; publishing
    validates it and stores it as the next immutable version."""

    def __init__(self, service: WorkflowService) -> None:
        self.s = service

    # ----------------------------------------------------------- lifecycle --
    def get(self) -> tuple[int, WorkflowDoc, str | None]:
        row = self.s.store.get_workflow_draft(self.s.workflow_id)
        if row:
            return row
        base = self.s.active_version()
        return base, self.s.get(base).model_copy(deep=True), None

    def _save(self, doc: WorkflowDoc) -> None:
        base, _, _ = self.get()
        WorkflowDoc.model_validate(doc.model_dump())  # structural check only; full validation at publish
        self.s.store.save_workflow_draft(self.s.workflow_id, base, doc)

    def discard(self) -> None:
        self.s.store.delete_workflow_draft(self.s.workflow_id)

    def dirty(self) -> bool:
        base, doc, stamp = self.get()
        return stamp is not None and doc.model_dump() != self.s.get(base).model_dump()

    def problems(self) -> list[str]:
        return self.s.validate(self.get()[1])

    def skill_updates(self) -> dict[str, str]:
        """Imported skills with a newer installed commit than the draft's base pins."""
        return self.s.library.pending_updates(self.get()[1]) if self.s.library else {}

    def warnings(self) -> list[str]:
        return workflow_warnings(self.get()[1])

    def replace_with(self, doc: WorkflowDoc) -> None:
        """Start the draft from a template (nothing changes until it is published)."""
        base, _, _ = self.get()
        WorkflowDoc.model_validate(doc.model_dump())
        self.s.store.save_workflow_draft(self.s.workflow_id, base, doc)

    def publish(self, note: str, activate: bool = True) -> WorkflowVersionInfo:
        """Store the draft as the next version. With `activate=False` (an evaluation gate,
        ADR-0025) the version is a candidate and the draft is kept, so a failed
        candidate can be fixed and published again; it is discarded on activation."""
        _, doc, stamp = self.get()
        if not self.dirty() and not self.skill_updates():
            raise WorkflowError("nothing to publish: the draft has no changes")
        version = self.s.create_version(doc, note, activate=activate)  # raises with problems
        if activate:
            self.discard()
        return next(i for i in self.s.versions() if i.version == version)

    def discard_if_published_as(self, version: int) -> None:
        """Drop the draft once `version` (published from it) is active, unless it changed since."""
        _, doc, stamp = self.get()
        if stamp is not None and doc.model_dump(exclude={"skill_pins"}) == self.s.get(version).model_dump(
            exclude={"skill_pins"}
        ):
            self.discard()

    def set_eval_gate(self, gate: str) -> None:
        _, doc, _ = self.get()
        doc.eval_gate = gate  # type: ignore[assignment]
        self._save(doc)

    def set_evals(self, cases: list[EvalCase]) -> None:
        _, doc, _ = self.get()
        doc.evals = list(cases)
        self._save(doc)

    def set_spec_review(self, mode: str) -> None:
        _, doc, _ = self.get()
        doc.spec_review = mode  # type: ignore[assignment]
        self._save(doc)

    def set_learn_from_runs(self, on: bool) -> None:
        _, doc, _ = self.get()
        doc.learn_from_runs = on
        self._save(doc)

    def add_learning(self, agent_id: str, lesson: str) -> None:
        """Append an accepted lesson to an agent's learnings in the draft (ADR-0021)."""
        _, doc, _ = self.get()
        spec = doc.agents.get(agent_id)
        if spec is None:
            raise WorkflowError(f"agent '{agent_id}' is not in this workflow's draft")
        line = f"- {lesson.strip()}"
        if line in spec.learnings.splitlines():
            return
        text = (spec.learnings.rstrip() + "\n" + line).strip()
        if len(text) > MAX_LEARNINGS_CHARS:
            raise WorkflowError(
                f"agent '{agent_id}' learnings would exceed {MAX_LEARNINGS_CHARS} characters: "
                "condense its learnings in the editor first"
            )
        spec.learnings = text
        self._save(doc)

    # -------------------------------------------------------------- agents --
    def upsert_agent(self, spec: AgentSpec) -> None:
        _, doc, _ = self.get()
        doc.agents[spec.id] = spec
        self._save(doc)

    def duplicate_agent(self, source: str, new_id: str) -> AgentSpec:
        _, doc, _ = self.get()
        if source not in doc.agents:
            raise WorkflowError(f"agent '{source}' not found")
        if new_id in doc.agents:
            raise WorkflowError(f"agent '{new_id}' already exists")
        copy = doc.agents[source].model_copy(update={"id": new_id})
        AgentSpec.model_validate(copy.model_dump())  # re-validate the new id
        doc.agents[new_id] = copy
        self._save(doc)
        return copy

    def delete_agent(self, agent_id: str) -> None:
        _, doc, _ = self.get()
        if agent_id not in doc.agents:
            raise WorkflowError(f"agent '{agent_id}' not found")
        used = doc.stations_using(agent_id)
        if used:
            raise WorkflowError(f"agent '{agent_id}' is used by stations {used}; assign another agent first")
        del doc.agents[agent_id]
        self._save(doc)

    def set_station_agent(self, station_id: str, agent_id: str) -> None:
        _, doc, _ = self.get()
        try:
            st = doc.station(station_id)
        except KeyError as exc:
            raise WorkflowError(f"station '{station_id}' not found") from exc
        if st.kind != "agent":
            raise WorkflowError(f"station '{station_id}' is a deterministic check and has no agent")
        if agent_id not in doc.agents:
            raise WorkflowError(f"agent '{agent_id}' not found")
        st.agent = agent_id
        self._save(doc)

    # ------------------------------------------------------------ stations --
    def _station(self, doc: WorkflowDoc, station_id: str) -> WorkflowStation:
        try:
            return doc.station(station_id)
        except KeyError as exc:
            raise WorkflowError(f"station '{station_id}' not found") from exc

    def add_station(self, station: WorkflowStation, position: int | None = None) -> None:
        """Insert a station (at the end when `position` is None). Routes are not
        changed; the forward order is the list order."""
        _, doc, _ = self.get()
        if not STATION_ID.match(station.id):
            raise WorkflowError(f"station id '{station.id}' must be 2-41 chars: lowercase letters, digits, '-' or '_'")
        if any(s.id == station.id for s in doc.stations):
            raise WorkflowError(f"station '{station.id}' already exists")
        if station.kind == "check" and station.resolved_handler() not in CHECK_HANDLERS:
            raise WorkflowError(f"check stations need a handler from {sorted(CHECK_HANDLERS)}")
        if station.kind == "agent" and station.agent and station.agent not in doc.agents:
            raise WorkflowError(f"agent '{station.agent}' not found")
        pos = len(doc.stations) if position is None else max(0, min(position, len(doc.stations)))
        doc.stations.insert(pos, station)
        self._save(doc)

    def remove_station(self, station_id: str) -> list[str]:
        """Remove a station and clear routes that pointed at it. Returns the
        routes that were cleared, so the UI can say what changed."""
        _, doc, _ = self.get()
        self._station(doc, station_id)
        if len(doc.stations) == 1:
            raise WorkflowError("a workflow needs at least one station")
        doc.stations = [s for s in doc.stations if s.id != station_id]
        cleared = []
        for s in doc.stations:
            if s.on_fail == station_id:
                s.on_fail = None
                cleared.append(f"{s.id}.on_fail")
            if s.next == station_id:
                s.next = None
                cleared.append(f"{s.id}.next")
        self._save(doc)
        return cleared

    def reorder_stations(self, order: list[str]) -> None:
        _, doc, _ = self.get()
        if sorted(order) != sorted(s.id for s in doc.stations):
            raise WorkflowError("the new order must list every station exactly once")
        by_id = {s.id: s for s in doc.stations}
        doc.stations = [by_id[i] for i in order]
        self._save(doc)

    def update_station(self, station_id: str, patch: dict[str, object]) -> None:
        """Change routes, the repair flag, the handler or the agent. Ids and kinds
        are fixed; remove and add instead."""
        _, doc, _ = self.get()
        st = self._station(doc, station_id)
        allowed = {"on_fail", "next", "only_on_fail", "handler", "agent", "phase"}
        unknown = set(patch) - allowed
        if unknown:
            raise WorkflowError(f"cannot change {sorted(unknown)} (allowed: {sorted(allowed)})")
        ids = {s.id for s in doc.stations}
        for key in ("on_fail", "next"):
            if key in patch:
                target = patch[key] or None
                if target is not None and target not in ids:
                    raise WorkflowError(f"{key} must name an existing station, not '{target}'")
                if target == station_id:
                    raise WorkflowError(f"a station cannot route to itself ({key})")
                setattr(st, key, target)
        if "only_on_fail" in patch:
            st.only_on_fail = bool(patch["only_on_fail"])
        if "handler" in patch:
            h = canonical_handler(st.kind, str(patch["handler"])) if patch["handler"] else None
            known = AGENT_HANDLERS if st.kind == "agent" else CHECK_HANDLERS
            if h is not None and h not in known:
                raise WorkflowError(f"unknown {st.kind} handler '{h}' (choose from {sorted(known)})")
            st.handler = h
        if "phase" in patch:
            if patch["phase"] and patch["phase"] not in PHASE_IDS:
                raise WorkflowError(f"phase must be one of {PHASE_IDS}, not '{patch['phase']}'")
            st.phase = patch["phase"] or None  # type: ignore[assignment]
        if "agent" in patch:
            if st.kind != "agent":
                raise WorkflowError(f"station '{station_id}' is a deterministic check and has no agent")
            if patch["agent"] not in doc.agents:
                raise WorkflowError(f"agent '{patch['agent']}' not found")
            st.agent = str(patch["agent"])
        self._save(doc)

    # ---------------------------------------------------------------- docs --
    def upsert_doc(self, ref: RefDoc) -> None:
        _, doc, _ = self.get()
        doc.docs[ref.id] = ref
        self._save(doc)

    def delete_doc(self, doc_id: str) -> None:
        _, doc, _ = self.get()
        if doc_id not in doc.docs:
            raise WorkflowError(f"doc '{doc_id}' not found")
        users = [a.id for a in doc.agents.values() if doc_id in a.context_docs]
        if users:
            raise WorkflowError(f"doc '{doc_id}' is attached to agents {users}; detach it first")
        del doc.docs[doc_id]
        self._save(doc)
