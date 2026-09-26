"""SQLite StateStore. Records are stored as JSON documents keyed by id, which
keeps the schema migration-free for v0 and trivially portable to Postgres."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_factory.models import Event, EventKind, Feedback, Order, Run, RunStatus
from agent_factory.skills import SkillRecord
from agent_factory.workflow import WorkflowDoc, WorkflowVersionInfo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, created_at TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_order ON runs(order_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL,
  station TEXT, kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, run_id TEXT,
  text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_versions (
  workflow_id TEXT NOT NULL, version INTEGER NOT NULL, doc TEXT NOT NULL, note TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY (workflow_id, version));
CREATE TABLE IF NOT EXISTS workflow_active (workflow_id TEXT PRIMARY KEY, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_drafts (
  workflow_id TEXT PRIMARY KEY, base_version INTEGER NOT NULL, doc TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skill_versions (
  name TEXT NOT NULL, sha TEXT NOT NULL, doc TEXT NOT NULL, installed_at TEXT NOT NULL, PRIMARY KEY (name, sha));
CREATE TABLE IF NOT EXISTS skill_current (name TEXT PRIMARY KEY, sha TEXT NOT NULL);
"""


def _now() -> datetime:
    return datetime.now(UTC)


class SqliteStateStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._migrate_legacy_tables()
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def _migrate_legacy_tables(self) -> None:
        """'line_*' tables from before the rename become 'workflow_*' (data kept)."""
        existing = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for old, new in (
            ("line_versions", "workflow_versions"),
            ("line_active", "workflow_active"),
            ("line_drafts", "workflow_drafts"),
        ):
            if old in existing and new not in existing:
                self._db.execute(f"ALTER TABLE {old} RENAME TO {new}")
                self._db.execute(f"ALTER TABLE {new} RENAME COLUMN line_id TO workflow_id")

    def _exec(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    # -- orders --------------------------------------------------------------
    def create_order(self, order: Order) -> Order:
        self._exec(
            "INSERT INTO orders(id, created_at, doc) VALUES (?,?,?)",
            (order.id, order.created_at.isoformat(), order.model_dump_json()),
        )
        return order

    def get_order(self, order_id: str) -> Order | None:
        row = self._exec("SELECT doc FROM orders WHERE id=?", (order_id,)).fetchone()
        return Order.model_validate_json(row[0]) if row else None

    def list_orders(self) -> list[Order]:
        rows = self._exec("SELECT doc FROM orders ORDER BY created_at DESC").fetchall()
        return [Order.model_validate_json(r[0]) for r in rows]

    def save_order(self, order: Order) -> None:
        self._exec("UPDATE orders SET doc=? WHERE id=?", (order.model_dump_json(), order.id))

    # -- runs ----------------------------------------------------------------
    def create_run(self, run: Run) -> Run:
        self._exec(
            "INSERT INTO runs(id, order_id, status, created_at, doc) VALUES (?,?,?,?,?)",
            (run.id, run.order_id, run.status, run.created_at.isoformat(), run.model_dump_json()),
        )
        return run

    def get_run(self, run_id: str) -> Run | None:
        row = self._exec("SELECT doc FROM runs WHERE id=?", (run_id,)).fetchone()
        return Run.model_validate_json(row[0]) if row else None

    def save_run(self, run: Run) -> None:
        run.updated_at = _now()
        self._exec(
            "UPDATE runs SET status=?, doc=? WHERE id=?",
            (run.status, run.model_dump_json(), run.id),
        )

    def list_runs(self, order_id: str) -> list[Run]:
        rows = self._exec("SELECT doc FROM runs WHERE order_id=? ORDER BY created_at DESC", (order_id,)).fetchall()
        return [Run.model_validate_json(r[0]) for r in rows]

    def runs_with_status(self, statuses: set[RunStatus]) -> list[Run]:
        marks = ",".join("?" * len(statuses))
        rows = self._exec(
            f"SELECT doc FROM runs WHERE status IN ({marks})",  # noqa: S608 - placeholders only
            tuple(s.value for s in statuses),
        ).fetchall()
        return [Run.model_validate_json(r[0]) for r in rows]

    # -- events --------------------------------------------------------------
    def add_event(
        self,
        run_id: str,
        kind: EventKind,
        message: str,
        station: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        ts = _now()
        cur = self._exec(
            "INSERT INTO events(run_id, ts, station, kind, message, data) VALUES (?,?,?,?,?,?)",
            (run_id, ts.isoformat(), station, kind.value, message, json.dumps(data or {})),
        )
        return Event(
            id=int(cur.lastrowid or 0),
            run_id=run_id,
            ts=ts,
            station=station,
            kind=kind,
            message=message,
            data=data or {},
        )

    def list_events(self, run_id: str, after_id: int = 0) -> list[Event]:
        rows = self._exec(
            "SELECT id, run_id, ts, station, kind, message, data FROM events WHERE run_id=? AND id>? ORDER BY id",
            (run_id, after_id),
        ).fetchall()
        return [
            Event(
                id=r[0],
                run_id=r[1],
                ts=datetime.fromisoformat(r[2]),
                station=r[3],
                kind=EventKind(r[4]),
                message=r[5],
                data=json.loads(r[6]),
            )
            for r in rows
        ]

    # -- feedback ------------------------------------------------------------
    def add_feedback(self, order_id: str, run_id: str | None, text: str) -> Feedback:
        ts = _now()
        cur = self._exec(
            "INSERT INTO feedback(order_id, run_id, text, created_at) VALUES (?,?,?,?)",
            (order_id, run_id, text, ts.isoformat()),
        )
        return Feedback(id=int(cur.lastrowid or 0), order_id=order_id, run_id=run_id, text=text, created_at=ts)

    def list_feedback(self, order_id: str) -> list[Feedback]:
        rows = self._exec(
            "SELECT id, order_id, run_id, text, created_at FROM feedback WHERE order_id=? ORDER BY id",
            (order_id,),
        ).fetchall()
        return [
            Feedback(
                id=r[0],
                order_id=r[1],
                run_id=r[2],
                text=r[3],
                created_at=datetime.fromisoformat(r[4]),
            )
            for r in rows
        ]

    # -- versioned workflows--------------------------------------------------
    def create_workflow_version(self, workflow_id: str, doc: WorkflowDoc, note: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM workflow_versions WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
            version = int(row[0]) + 1
            self._db.execute(
                "INSERT INTO workflow_versions(workflow_id, version, doc, note, created_at) VALUES (?,?,?,?,?)",
                (workflow_id, version, doc.model_dump_json(), note, _now().isoformat()),
            )
        return version

    def get_workflow_version(self, workflow_id: str, version: int) -> WorkflowDoc | None:
        row = self._exec(
            "SELECT doc FROM workflow_versions WHERE workflow_id=? AND version=?", (workflow_id, version)
        ).fetchone()
        return WorkflowDoc.model_validate_json(row[0]) if row else None

    def active_workflow_version(self, workflow_id: str) -> int | None:
        row = self._exec("SELECT version FROM workflow_active WHERE workflow_id=?", (workflow_id,)).fetchone()
        return int(row[0]) if row else None

    def set_active_workflow_version(self, workflow_id: str, version: int) -> None:
        self._exec(
            "INSERT INTO workflow_active(workflow_id, version) VALUES (?,?) "
            "ON CONFLICT(workflow_id) DO UPDATE SET version=excluded.version",
            (workflow_id, version),
        )

    def list_workflow_versions(self, workflow_id: str) -> list[WorkflowVersionInfo]:
        active = self.active_workflow_version(workflow_id)
        rows = self._exec(
            "SELECT version, doc, note, created_at FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC",
            (workflow_id,),
        ).fetchall()
        out = []
        for version, doc, note, created in rows:
            d = WorkflowDoc.model_validate_json(doc)
            out.append(
                WorkflowVersionInfo(
                    workflow_id=workflow_id,
                    version=version,
                    note=note,
                    created_at=created,
                    active=version == active,
                    stations=len(d.stations),
                    agents=len(d.agents),
                )
            )
        return out

    # -- workflow drafts------------------------------------------------------
    def get_workflow_draft(self, workflow_id: str) -> tuple[int, WorkflowDoc, str] | None:
        row = self._exec(
            "SELECT base_version, doc, updated_at FROM workflow_drafts WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        return (int(row[0]), WorkflowDoc.model_validate_json(row[1]), row[2]) if row else None

    def save_workflow_draft(self, workflow_id: str, base_version: int, doc: WorkflowDoc) -> str:
        ts = _now().isoformat()
        self._exec(
            "INSERT INTO workflow_drafts(workflow_id, base_version, doc, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(workflow_id) DO UPDATE SET base_version=excluded.base_version, doc=excluded.doc, "
            "updated_at=excluded.updated_at",
            (workflow_id, base_version, doc.model_dump_json(), ts),
        )
        return ts

    def delete_workflow_draft(self, workflow_id: str) -> None:
        self._exec("DELETE FROM workflow_drafts WHERE workflow_id=?", (workflow_id,))

    def rename_workflow(self, old_id: str, new_id: str) -> None:
        with self._lock:
            for table in ("workflow_versions", "workflow_active", "workflow_drafts"):
                self._db.execute(f"UPDATE {table} SET workflow_id=? WHERE workflow_id=?", (new_id, old_id))  # noqa: S608

    # ------------------------------------------------------ imported skills --
    def save_skill_version(self, rec: SkillRecord) -> None:
        self._exec(
            "INSERT INTO skill_versions(name, sha, doc, installed_at) VALUES (?,?,?,?) "
            "ON CONFLICT(name, sha) DO UPDATE SET doc=excluded.doc, installed_at=excluded.installed_at",
            (rec.name, rec.sha, rec.model_dump_json(), rec.installed_at),
        )

    def get_skill_version(self, name: str, sha: str) -> SkillRecord | None:
        row = self._exec("SELECT doc FROM skill_versions WHERE name=? AND sha=?", (name, sha)).fetchone()
        return SkillRecord.model_validate_json(row[0]) if row else None

    def skill_versions(self, name: str) -> list[SkillRecord]:
        rows = self._exec("SELECT doc FROM skill_versions WHERE name=? ORDER BY installed_at DESC", (name,))
        return [SkillRecord.model_validate_json(r[0]) for r in rows.fetchall()]

    def set_current_skill(self, name: str, sha: str | None) -> None:
        if sha is None:
            self._exec("DELETE FROM skill_current WHERE name=?", (name,))
        else:
            self._exec(
                "INSERT INTO skill_current(name, sha) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET sha=excluded.sha",
                (name, sha),
            )

    def current_skill(self, name: str) -> SkillRecord | None:
        row = self._exec("SELECT sha FROM skill_current WHERE name=?", (name,)).fetchone()
        return self.get_skill_version(name, row[0]) if row else None

    def current_skills(self) -> list[SkillRecord]:
        rows = self._exec(
            "SELECT v.doc FROM skill_current c JOIN skill_versions v ON v.name=c.name AND v.sha=c.sha ORDER BY c.name"
        ).fetchall()
        return [SkillRecord.model_validate_json(r[0]) for r in rows]
