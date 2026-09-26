"""Line service: seeds, versions and serves the instance's line from the DB."""

from __future__ import annotations

from pathlib import Path

from agent_factory.line import LineDoc, LineVersionInfo, load_line_dir, validate_line
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
