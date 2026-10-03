"""SQLite StateStore. Records are stored as JSON documents keyed by id, which
keeps the schema migration-free for v0 and trivially portable to Postgres."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_factory.models import (
    Change,
    ChangeStatus,
    EvalRun,
    Event,
    EventKind,
    Feedback,
    LearningProposal,
    Product,
    Transition,
)
from agent_factory.secret_refs import REDACTOR
from agent_factory.skills import SkillRecord
from agent_factory.workflow import WorkflowDoc, WorkflowVersionInfo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, created_at TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS changes (
  id TEXT PRIMARY KEY, product_id TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS changes_product ON changes(product_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, change_id TEXT NOT NULL, ts TEXT NOT NULL,
  station TEXT, kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_change ON events(change_id, id);
-- every change status change, with its time: the basis of the Outcomes page (ADR-0019)
CREATE TABLE IF NOT EXISTS change_transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, change_id TEXT NOT NULL, ts TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS change_transitions_change ON change_transitions(change_id, id);
-- lessons the retro suggests after changes that needed help; an admin decides (ADR-0021)
CREATE TABLE IF NOT EXISTS learning_proposals (
  id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS learning_proposals_wf ON learning_proposals(workflow_id, status);
-- periods the factory host was suspended (the computer slept): not agent time
CREATE TABLE IF NOT EXISTS host_pauses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL, end TEXT NOT NULL, seconds REAL NOT NULL);
-- evaluations of workflow versions on their fixed suite (ADR-0025)
CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, created_at TEXT NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS eval_runs_wf ON eval_runs(workflow_id, created_at);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL, change_id TEXT,
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


class _Result:
    """Rows of a finished statement (see SqliteStateStore._exec)."""

    __slots__ = ("lastrowid", "rowcount", "rows")

    def __init__(self, rows: list[tuple[Any, ...]], rowcount: int, lastrowid: int | None) -> None:
        self.rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self.rows)


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
        """Tables from before a rename keep their data: 'line_*' became 'workflow_*';
        products/runs became products/changes (ADR-0029)."""
        existing = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def columns(table: str) -> set[str]:
            return {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}

        for old, new in (("orders", "products"), ("runs", "changes"), ("run_transitions", "change_transitions")):
            if old in existing and new not in existing:
                self._db.execute(f"ALTER TABLE {old} RENAME TO {new}")
                existing = (existing - {old}) | {new}
        for table, old, new in (
            ("changes", "order_id", "product_id"),
            ("events", "run_id", "change_id"),
            ("change_transitions", "run_id", "change_id"),
            ("feedback", "order_id", "product_id"),
            ("feedback", "run_id", "change_id"),
        ):
            if table in existing and old in columns(table) and new not in columns(table):
                self._db.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        for old, new in (
            ("line_versions", "workflow_versions"),
            ("line_active", "workflow_active"),
            ("line_drafts", "workflow_drafts"),
        ):
            if old in existing and new not in existing:
                self._db.execute(f"ALTER TABLE {old} RENAME TO {new}")
                self._db.execute(f"ALTER TABLE {new} RENAME COLUMN line_id TO workflow_id")

    def _exec(self, sql: str, args: tuple[Any, ...] = ()) -> _Result:
        """Run one statement and read its rows while holding the lock. The
        connection is shared by the event loop (changes writing events) and the API's
        worker threads: a cursor read after the lock is released interleaves with
        other statements ("bad parameter or other API misuse", rows from another
        query), which surfaced as intermittent 500s in CI e2e."""
        with self._lock:
            cur = self._db.execute(sql, args)
            return _Result(cur.fetchall(), cur.rowcount, cur.lastrowid)

    # -- products --------------------------------------------------------------
    def create_product(self, product: Product) -> Product:
        self._exec(
            "INSERT INTO products(id, created_at, doc) VALUES (?,?,?)",
            (product.id, product.created_at.isoformat(), REDACTOR.text(product.model_dump_json())),
        )
        return product

    def get_product(self, product_id: str) -> Product | None:
        row = self._exec("SELECT doc FROM products WHERE id=?", (product_id,)).fetchone()
        return Product.model_validate_json(row[0]) if row else None

    def list_products(self) -> list[Product]:
        rows = self._exec("SELECT doc FROM products ORDER BY created_at DESC").fetchall()
        return [Product.model_validate_json(r[0]) for r in rows]

    def save_product(self, product: Product) -> None:
        self._exec("UPDATE products SET doc=? WHERE id=?", (REDACTOR.text(product.model_dump_json()), product.id))

    # -- changes ----------------------------------------------------------------
    def create_change(self, change: Change) -> Change:
        with self._lock:
            self._db.execute(
                "INSERT INTO changes(id, product_id, status, created_at, doc) VALUES (?,?,?,?,?)",
                (
                    change.id,
                    change.product_id,
                    change.status,
                    change.created_at.isoformat(),
                    REDACTOR.text(change.model_dump_json()),
                ),
            )
            self._record_transition(change.id, None, change.status, change.created_at)
        return change

    def get_change(self, change_id: str) -> Change | None:
        row = self._exec("SELECT doc FROM changes WHERE id=?", (change_id,)).fetchone()
        return Change.model_validate_json(row[0]) if row else None

    def save_change(self, change: Change) -> None:
        """Every status change is recorded here, the one place all changes are saved,
        so no call site can forget it (ADR-0019)."""
        change.updated_at = _now()
        with self._lock:
            row = self._db.execute("SELECT status FROM changes WHERE id=?", (change.id,)).fetchone()
            self._db.execute(
                "UPDATE changes SET status=?, doc=? WHERE id=?",
                (change.status, REDACTOR.text(change.model_dump_json()), change.id),
            )
            if row and row[0] != change.status:
                self._record_transition(change.id, ChangeStatus(row[0]), change.status, change.updated_at)

    def _record_transition(self, change_id: str, old: ChangeStatus | None, new: ChangeStatus, ts: datetime) -> None:
        # caller holds the lock
        self._db.execute(
            "INSERT INTO change_transitions(change_id, ts, from_status, to_status) VALUES (?,?,?,?)",
            (change_id, ts.isoformat(), old.value if old else None, ChangeStatus(new).value),
        )

    def all_changes(self) -> list[Change]:
        return [Change.model_validate_json(r[0]) for r in self._exec("SELECT doc FROM changes ORDER BY created_at")]

    def transitions(self, change_ids: list[str] | None = None) -> dict[str, list[Transition]]:
        """Status changes per run, oldest first (all changes when run_ids is None)."""
        sql, args = "SELECT change_id, ts, from_status, to_status FROM change_transitions", ()
        if change_ids is not None:
            if not change_ids:
                return {}
            sql += f" WHERE change_id IN ({','.join('?' * len(change_ids))})"  # noqa: S608 - placeholders only
            args = tuple(change_ids)
        out: dict[str, list[Transition]] = {}
        for rid, ts, old, new in self._exec(sql + " ORDER BY id", args):
            out.setdefault(rid, []).append(
                Transition(
                    change_id=rid,
                    ts=datetime.fromisoformat(ts),
                    from_status=ChangeStatus(old) if old else None,
                    to_status=ChangeStatus(new),
                )
            )
        return out

    # -- host suspensions ------------------------------------------------------
    def add_host_pause(self, start: datetime, end: datetime) -> None:
        self._exec(
            "INSERT INTO host_pauses(start, end, seconds) VALUES (?,?,?)",
            (start.isoformat(), end.isoformat(), (end - start).total_seconds()),
        )

    def host_pauses(self, since: datetime | None = None) -> list[tuple[datetime, datetime]]:
        rows = self._exec("SELECT start, end FROM host_pauses ORDER BY start")
        out = [(datetime.fromisoformat(s), datetime.fromisoformat(e)) for s, e in rows]
        return [p for p in out if since is None or p[1] >= since]

    # -- evaluations (ADR-0025) -----------------------------------------------
    def save_eval(self, e: EvalRun) -> EvalRun:
        self._exec(
            "INSERT INTO eval_runs(id, workflow_id, created_at, doc) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc",
            (e.id, e.workflow_id, e.created_at.isoformat(), REDACTOR.text(e.model_dump_json())),
        )
        return e

    def get_eval(self, eval_id: str) -> EvalRun | None:
        row = self._exec("SELECT doc FROM eval_runs WHERE id=?", (eval_id,)).fetchone()
        return EvalRun.model_validate_json(row[0]) if row else None

    def list_evals(self, workflow_id: str) -> list[EvalRun]:
        rows = self._exec("SELECT doc FROM eval_runs WHERE workflow_id=? ORDER BY created_at DESC", (workflow_id,))
        return [EvalRun.model_validate_json(r[0]) for r in rows]

    # -- learning proposals (ADR-0021) ---------------------------------------
    def add_proposal(self, p: LearningProposal) -> LearningProposal:
        self._exec(
            "INSERT INTO learning_proposals(id, workflow_id, status, created_at, doc) VALUES (?,?,?,?,?)",
            (p.id, p.workflow_id, p.status, p.created_at.isoformat(), REDACTOR.text(p.model_dump_json())),
        )
        return p

    def save_proposal(self, p: LearningProposal) -> None:
        self._exec(
            "UPDATE learning_proposals SET status=?, doc=? WHERE id=?",
            (p.status, REDACTOR.text(p.model_dump_json()), p.id),
        )

    def get_proposal(self, proposal_id: str) -> LearningProposal | None:
        row = self._exec("SELECT doc FROM learning_proposals WHERE id=?", (proposal_id,)).fetchone()
        return LearningProposal.model_validate_json(row[0]) if row else None

    def list_proposals(self, workflow_id: str, status: str | None = None) -> list[LearningProposal]:
        sql, args = "SELECT doc FROM learning_proposals WHERE workflow_id=?", (workflow_id,)
        if status:
            sql, args = sql + " AND status=?", (workflow_id, status)
        return [LearningProposal.model_validate_json(r[0]) for r in self._exec(sql + " ORDER BY created_at DESC", args)]

    def last_event_data(self, change_id: str, key: str) -> tuple[datetime, Any] | None:
        """(time, data[key]) of the change's latest event whose data carries `key`."""
        for ts, data in self._exec(
            "SELECT ts, data FROM events WHERE change_id=? AND data LIKE ? ORDER BY id DESC LIMIT 20",
            (change_id, f'%"{key}"%'),
        ):
            value = json.loads(data).get(key)
            if value is not None:
                return datetime.fromisoformat(ts), value
        return None

    def events_with_key(self, change_ids: list[str], key: str) -> list[tuple[str, datetime, Any]]:
        """(run id, time, data[key]) of every event in these changes whose data carries `key`, oldest first."""
        out: list[tuple[str, datetime, Any]] = []
        for i in range(0, len(change_ids), 500):
            chunk = change_ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._exec(
                f"SELECT change_id, ts, data FROM events WHERE change_id IN ({marks}) AND data LIKE ? ORDER BY id",  # noqa: S608
                (*chunk, f'%"{key}"%'),
            )
            for rid, ts, data in rows:
                value = json.loads(data).get(key)
                if value is not None:
                    out.append((rid, datetime.fromisoformat(ts), value))
        return out

    def first_event_time(self, change_id: str, message_prefix: str) -> datetime | None:
        row = self._exec(
            "SELECT ts FROM events WHERE change_id=? AND message LIKE ? ORDER BY id LIMIT 1",
            (change_id, message_prefix.replace("%", "") + "%"),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def list_changes(self, product_id: str) -> list[Change]:
        rows = self._exec(
            "SELECT doc FROM changes WHERE product_id=? ORDER BY created_at DESC", (product_id,)
        ).fetchall()
        return [Change.model_validate_json(r[0]) for r in rows]

    def changes_with_status(self, statuses: set[ChangeStatus]) -> list[Change]:
        marks = ",".join("?" * len(statuses))
        rows = self._exec(
            f"SELECT doc FROM changes WHERE status IN ({marks})",  # noqa: S608 - placeholders only
            tuple(s.value for s in statuses),
        ).fetchall()
        return [Change.model_validate_json(r[0]) for r in rows]

    # -- events --------------------------------------------------------------
    def add_event(
        self,
        change_id: str,
        kind: EventKind,
        message: str,
        station: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        ts = _now()
        # every resolved secret value is masked before anything is stored or streamed
        message, data = REDACTOR.text(message), REDACTOR.obj(data or {})
        cur = self._exec(
            "INSERT INTO events(change_id, ts, station, kind, message, data) VALUES (?,?,?,?,?,?)",
            (change_id, ts.isoformat(), station, kind.value, message, json.dumps(data or {})),
        )
        return Event(
            id=int(cur.lastrowid or 0),
            change_id=change_id,
            ts=ts,
            station=station,
            kind=kind,
            message=message,
            data=data or {},
        )

    def list_events(self, change_id: str, after_id: int = 0) -> list[Event]:
        rows = self._exec(
            "SELECT id, change_id, ts, station, kind, message, data FROM events WHERE change_id=? AND id>? ORDER BY id",
            (change_id, after_id),
        ).fetchall()
        return [
            Event(
                id=r[0],
                change_id=r[1],
                ts=datetime.fromisoformat(r[2]),
                station=r[3],
                kind=EventKind(r[4]),
                message=r[5],
                data=json.loads(r[6]),
            )
            for r in rows
        ]

    # -- feedback ------------------------------------------------------------
    def add_feedback(self, product_id: str, change_id: str | None, text: str) -> Feedback:
        ts = _now()
        cur = self._exec(
            "INSERT INTO feedback(product_id, change_id, text, created_at) VALUES (?,?,?,?)",
            (product_id, change_id, text, ts.isoformat()),
        )
        return Feedback(
            id=int(cur.lastrowid or 0), product_id=product_id, change_id=change_id, text=text, created_at=ts
        )

    def list_feedback(self, product_id: str) -> list[Feedback]:
        rows = self._exec(
            "SELECT id, product_id, change_id, text, created_at FROM feedback WHERE product_id=? ORDER BY id",
            (product_id,),
        ).fetchall()
        return [
            Feedback(
                id=r[0],
                product_id=r[1],
                change_id=r[2],
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
