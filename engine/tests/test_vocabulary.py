"""Product → Product, Run → Change, blueprint → Blueprint (ADR-0029): a factory
upgraded in place keeps every record, and old config and documents still load."""

from __future__ import annotations

import json
import sqlite3

from agent_factory.config import FactoryConfig
from agent_factory.models import Change, ChangeStatus, LearningProposal, Product
from agent_factory.state.sqlite import SqliteStateStore

NOW = "2026-10-01T10:00:00+00:00"
OLD_ORDER = {
    "id": "o1",
    "title": "Bookmarks",
    "requirements": "Save bookmarks.",
    "product_line": "fastapi-service",
    "product_slug": "bookmarks",
    "created_at": NOW,
    "latest_run_id": "r1",
    "latest_status": "awaiting_feedback",
}
OLD_RUN = {
    "id": "r1",
    "order_id": "o1",
    "iteration": 1,
    "status": "awaiting_feedback",
    "created_at": NOW,
    "updated_at": NOW,
}


def _old_database(path) -> None:  # noqa: ANN001
    """The tables and documents as a factory from before the rename wrote them."""
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE orders (id TEXT PRIMARY KEY, created_at TEXT, doc TEXT NOT NULL);
        CREATE TABLE runs (id TEXT PRIMARY KEY, order_id TEXT NOT NULL, status TEXT NOT NULL,
                           created_at TEXT, doc TEXT NOT NULL);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL,
                             station TEXT, kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE run_transitions (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL,
                                      from_status TEXT, to_status TEXT NOT NULL);
        CREATE TABLE feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, run_id TEXT,
                               text TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    db.execute("INSERT INTO orders VALUES (?,?,?)", ("o1", NOW, json.dumps(OLD_ORDER)))
    db.execute("INSERT INTO runs VALUES (?,?,?,?,?)", ("r1", "o1", "awaiting_feedback", NOW, json.dumps(OLD_RUN)))
    db.execute(
        "INSERT INTO events(run_id, ts, kind, message, data) VALUES ('r1', ?, 'status', 'run queued', '{}')", (NOW,)
    )
    db.execute("INSERT INTO run_transitions(run_id, ts, to_status) VALUES ('r1', ?, 'awaiting_feedback')", (NOW,))
    db.execute("INSERT INTO feedback(order_id, run_id, text, created_at) VALUES ('o1', 'r1', 'add tags', ?)", (NOW,))
    db.commit()
    db.close()


def test_an_old_database_is_renamed_in_place_and_keeps_everything(tmp_path) -> None:
    path = tmp_path / "factory.db"
    _old_database(path)
    store = SqliteStateStore(path)

    product = store.get_product("o1")
    assert product and (product.blueprint, product.slug, product.latest_change_id) == (
        "fastapi-service",
        "bookmarks",
        "r1",
    )
    change = store.get_change("r1")
    assert change and change.product_id == "o1" and change.status == ChangeStatus.awaiting_feedback
    assert [c.id for c in store.list_changes("o1")] == ["r1"]
    assert [e.message for e in store.list_events("r1")] == ["run queued"]
    assert [t.to_status for t in store.transitions(["r1"])["r1"]] == [ChangeStatus.awaiting_feedback]
    assert [(fb.product_id, fb.change_id, fb.text) for fb in store.list_feedback("o1")] == [("o1", "r1", "add tags")]

    tables = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"products", "changes", "change_transitions"} <= tables and not {
        "orders",
        "runs",
        "run_transitions",
    } & tables

    store.save_product(product)  # written back with the new names
    raw = json.loads(sqlite3.connect(path).execute("SELECT doc FROM products").fetchone()[0])
    assert "blueprint" in raw and "product_line" not in raw
    SqliteStateStore(path)  # opening again is a no-op


def test_old_documents_and_config_keys_still_read() -> None:
    assert Product.model_validate(OLD_ORDER).blueprint == "fastapi-service"
    assert Change.model_validate(OLD_RUN).product_id == "o1"
    proposal = LearningProposal.model_validate(
        {
            "id": "p1",
            "workflow_id": "w",
            "workflow_version": 1,
            "run_id": "r1",
            "order_id": "o1",
            "agent": "developer",
            "lesson": "a lesson long enough",
            "why": "x",
            "evidence": "y",
            "created_at": NOW,
        }
    )
    assert (proposal.change_id, proposal.product_id) == ("r1", "o1")
    cfg = FactoryConfig.model_validate(
        {
            "factory": {"max_concurrent_runs": 2},
            "product_lines": {"svc": {"template": "fastapi-service"}},
            "budgets": {"max_loops_per_run": 4, "run_wall_clock_minutes": 30},
        }
    )
    assert list(cfg.blueprints) == ["svc"] and cfg.factory.max_concurrent_changes == 2
    assert (cfg.budgets.max_loops_per_change, cfg.budgets.change_wall_clock_minutes) == (4, 30)
