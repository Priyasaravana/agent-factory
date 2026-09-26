"""Workflow services: one versioned workflow per product line, seeded from a
template, edited through a draft, published as immutable versions."""

from __future__ import annotations

from pathlib import Path

from agent_factory.state.base import StateStore
from agent_factory.workflow import (
    AgentSpec,
    RefDoc,
    WorkflowDoc,
    WorkflowVersionInfo,
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
    def __init__(self, store: StateStore, workflow_id: str, template_dir: Path, skills_dir: Path) -> None:
        self.store = store
        self.workflow_id = workflow_id
        self.template_dir = template_dir
        self.skills_dir = skills_dir
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

    def create_version(self, doc: WorkflowDoc, note: str, activate: bool = True) -> int:
        problems = validate_workflow(doc, self.skills_dir)
        if problems:
            raise WorkflowError("workflow is not valid", problems)
        version = self.store.create_workflow_version(self.workflow_id, doc, note)
        if activate:
            self.store.set_active_workflow_version(self.workflow_id, version)
        return version

    def activate(self, version: int) -> WorkflowVersionInfo:
        self.get(version)  # raises if missing
        self.store.set_active_workflow_version(self.workflow_id, version)
        return next(i for i in self.versions() if i.version == version)


class WorkflowRegistry:
    """One WorkflowService per product line. The workflow id is the product line id."""

    def __init__(
        self, store: StateStore, product_lines: dict[str, Path], templates_dir: Path, skills_dir: Path
    ) -> None:
        self.store = store
        self.templates_dir = templates_dir
        self.skills_dir = skills_dir
        self._services = {
            pl: WorkflowService(store, pl, template, skills_dir) for pl, template in product_lines.items()
        }

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
        # workflow of the first product line, so existing history is kept.
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
        return validate_workflow(self.get()[1], self.s.skills_dir)

    def warnings(self) -> list[str]:
        return workflow_warnings(self.get()[1])

    def replace_with(self, doc: WorkflowDoc) -> None:
        """Start the draft from a template (nothing changes until it is published)."""
        base, _, _ = self.get()
        WorkflowDoc.model_validate(doc.model_dump())
        self.s.store.save_workflow_draft(self.s.workflow_id, base, doc)

    def publish(self, note: str) -> WorkflowVersionInfo:
        _, doc, stamp = self.get()
        if stamp is None or not self.dirty():
            raise WorkflowError("nothing to publish: the draft has no changes")
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
