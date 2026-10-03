"""The state store's one SQLite connection is shared by the event loop (a change
writing events) and the API's worker threads (GET /api/changes/{id}, events, …).
Rows used to be read after the lock was released, so concurrent requests
interleaved on the connection: "bad parameter or other API misuse", rows from
another query, and intermittent 500s in CI e2e."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from agent_factory.models import Change, ChangeStatus, EventKind
from agent_factory.state.sqlite import SqliteStateStore


def test_readers_and_a_writer_share_the_connection_safely(tmp_path):
    st = SqliteStateStore(tmp_path / "factory.db")
    now = datetime.now(UTC)
    changes = [
        st.create_change(
            Change(
                id=f"r{i}", product_id="o1", iteration=1, status=ChangeStatus.running, created_at=now, updated_at=now
            )
        )
        for i in range(4)
    ]
    errors: list[str] = []
    stop = threading.Event()
    written = [0]

    def writer() -> None:
        try:
            while not stop.is_set():
                ev = st.add_event(changes[written[0] % 4].id, EventKind.log, f"event {written[0]}")
                if ev.id <= 0:
                    errors.append("event without an id")
                written[0] += 1
        except Exception as exc:  # noqa: BLE001 - a dead writer must fail the test, not warn
            errors.append(f"writer: {type(exc).__name__}: {exc}")

    def reader() -> None:
        for i in range(300):
            try:
                change = st.get_change(changes[i % 4].id)
                assert change is not None and change.id == changes[i % 4].id, "got another row"
                events = st.list_events(change.id)
                assert all(e.change_id == change.id for e in events), "events of another run"
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                errors.append(f"{type(exc).__name__}: {exc}")

    w = threading.Thread(target=writer)
    readers = [threading.Thread(target=reader) for _ in range(12)]
    w.start()
    for t in readers:
        t.start()
    for t in readers:
        t.join()
    stop.set()
    w.join()
    assert not errors, f"{len(errors)} failures, e.g. {sorted(set(errors))[:3]}"
    assert written[0] > 0, "the writer must have run alongside the readers"
