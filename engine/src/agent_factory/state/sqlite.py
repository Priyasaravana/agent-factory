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
"""


def _now() -> datetime:
    return datetime.now(UTC)


class SqliteStateStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

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
