"""Line service: seeds, versions and serves the instance's line from the DB."""

from __future__ import annotations

from pathlib import Path

from agent_factory.line import AgentSpec, LineDoc, LineVersionInfo, RefDoc, load_line_dir, validate_line
from agent_factory.state.base import StateStore


class LineError(Exception):
    """An invalid line or an unknown version (surfaced as HTTP 409/404)."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message + (": " + "; ".join(problems) if problems else ""))
        self.problems = problems or []


class LineService:
    def __init__(self, store: StateStore, line_id: str, blueprint_dir: Path, skills_dir: Path) -> None:
        self.store = store
        self.line_id = line_id
        self.blueprint_dir = blueprint_dir
        self.skills_dir = skills_dir
        self._cache: dict[int, LineDoc] = {}
        self.draft = Draft(self)

    # ---------------------------------------------------------------- read --
    def active_version(self) -> int:
        v = self.store.active_line_version(self.line_id)
        if v is None:
            raise LineError("no active line version (not seeded)")
        return v

    def get(self, version: int | None = None) -> LineDoc:
        v = version or self.active_version()
        if v not in self._cache:  # versions are immutable, so caching is safe
            doc = self.store.get_line_version(self.line_id, v)
            if doc is None:
                raise LineError(f"line version {v} not found")
            self._cache[v] = doc
        return self._cache[v]

    def versions(self) -> list[LineVersionInfo]:
        return self.store.list_line_versions(self.line_id)

    def blueprint_update_available(self) -> bool:
        """True when the shipped blueprint differs from what this instance was seeded from."""
        if not (self.blueprint_dir / "line.yaml").exists():
            return False
        latest = load_line_dir(self.blueprint_dir).blueprint_hash
        seeded = {d.blueprint_hash for d in (self.get(i.version) for i in self.versions())}
        return latest not in seeded

    # --------------------------------------------------------------- write --
    def ensure_seeded(self) -> int | None:
        """First start: copy the blueprint into the DB as version 1. Never overwrites."""
        if self.store.active_line_version(self.line_id) is not None:
            return None
        return self.import_dir(self.blueprint_dir, f"seeded from blueprint '{self.blueprint_dir.name}'")

    def import_dir(self, path: Path, note: str, activate: bool = True) -> int:
        return self.create_version(load_line_dir(path), note, activate)

    def create_version(self, doc: LineDoc, note: str, activate: bool = True) -> int:
        problems = validate_line(doc, self.skills_dir)
        if problems:
            raise LineError("line is not valid", problems)
        version = self.store.create_line_version(self.line_id, doc, note)
        if activate:
            self.store.set_active_line_version(self.line_id, version)
        return version

    def activate(self, version: int) -> LineVersionInfo:
        self.get(version)  # raises if missing
        self.store.set_active_line_version(self.line_id, version)
        return next(i for i in self.versions() if i.version == version)


class Draft:
    """The single editable working copy of a line. It never runs; publishing
    validates it and stores it as the next immutable version."""

    def __init__(self, service: LineService) -> None:
        self.s = service

    # ----------------------------------------------------------- lifecycle --
    def get(self) -> tuple[int, LineDoc, str | None]:
        row = self.s.store.get_line_draft(self.s.line_id)
        if row:
            return row
        base = self.s.active_version()
        return base, self.s.get(base).model_copy(deep=True), None

    def _save(self, doc: LineDoc) -> None:
        base, _, _ = self.get()
        LineDoc.model_validate(doc.model_dump())  # structural check only; full validation at publish
        self.s.store.save_line_draft(self.s.line_id, base, doc)

    def discard(self) -> None:
        self.s.store.delete_line_draft(self.s.line_id)

    def dirty(self) -> bool:
        base, doc, stamp = self.get()
        return stamp is not None and doc.model_dump() != self.s.get(base).model_dump()

    def problems(self) -> list[str]:
        return validate_line(self.get()[1], self.s.skills_dir)

    def publish(self, note: str) -> LineVersionInfo:
        _, doc, stamp = self.get()
        if stamp is None or not self.dirty():
            raise LineError("nothing to publish: the draft has no changes")
        version = self.s.create_version(doc, note, activate=True)  # raises with problems
        self.discard()
        return next(i for i in self.s.versions() if i.version == version)

    # -------------------------------------------------------------- agents --
    def upsert_agent(self, spec: AgentSpec) -> None:
        _, doc, _ = self.get()
        doc.agents[spec.id] = spec
        self._save(doc)

    def duplicate_agent(self, source: str, new_id: str) -> AgentSpec:
        _, doc, _ = self.get()
        if source not in doc.agents:
            raise LineError(f"agent '{source}' not found")
        if new_id in doc.agents:
            raise LineError(f"agent '{new_id}' already exists")
        copy = doc.agents[source].model_copy(update={"id": new_id})
        AgentSpec.model_validate(copy.model_dump())  # re-validate the new id
        doc.agents[new_id] = copy
        self._save(doc)
        return copy

    def delete_agent(self, agent_id: str) -> None:
        _, doc, _ = self.get()
        if agent_id not in doc.agents:
            raise LineError(f"agent '{agent_id}' not found")
        used = doc.stations_using(agent_id)
        if used:
            raise LineError(f"agent '{agent_id}' is used by stations {used}; assign another agent first")
        del doc.agents[agent_id]
        self._save(doc)

    def set_station_agent(self, station_id: str, agent_id: str) -> None:
        _, doc, _ = self.get()
        try:
            st = doc.station(station_id)
        except KeyError as exc:
            raise LineError(f"station '{station_id}' not found") from exc
        if st.kind != "agent":
            raise LineError(f"station '{station_id}' is a deterministic check and has no agent")
        if agent_id not in doc.agents:
            raise LineError(f"agent '{agent_id}' not found")
        st.agent = agent_id
        self._save(doc)

    # ---------------------------------------------------------------- docs --
    def upsert_doc(self, ref: RefDoc) -> None:
        _, doc, _ = self.get()
        doc.docs[ref.id] = ref
        self._save(doc)

    def delete_doc(self, doc_id: str) -> None:
        _, doc, _ = self.get()
        if doc_id not in doc.docs:
            raise LineError(f"doc '{doc_id}' not found")
        users = [a.id for a in doc.agents.values() if doc_id in a.context_docs]
        if users:
            raise LineError(f"doc '{doc_id}' is attached to agents {users}; detach it first")
        del doc.docs[doc_id]
        self._save(doc)
