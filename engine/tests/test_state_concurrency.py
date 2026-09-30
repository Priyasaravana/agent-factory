"""The state store's one SQLite connection is shared by the event loop (a run
writing events) and the API's worker threads (GET /api/runs/{id}, events, …).
Rows used to be read after the lock was released, so concurrent requests
interleaved on the connection: "bad parameter or other API misuse", rows from
another query, and intermittent 500s in CI e2e."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from agent_factory.models import EventKind, Run, RunStatus
from agent_factory.state.sqlite import SqliteStateStore


def test_readers_and_a_writer_share_the_connection_safely(tmp_path):
    st = SqliteStateStore(tmp_path / "factory.db")
    now = datetime.now(UTC)
    runs = [
        st.create_run(
            Run(id=f"r{i}", order_id="o1", iteration=1, status=RunStatus.running, created_at=now, updated_at=now)
        )
        for i in range(4)
    ]
    errors: list[str] = []
    stop = threading.Event()

    def writer() -> None:
        n = 0
        while not stop.is_set():
            ev = st.add_event(runs[n % 4].id, EventKind.info, f"event {n}")
            if ev.id <= 0:
                errors.append("event without an id")
            n += 1

    def reader() -> None:
        for i in range(300):
            try:
                run = st.get_run(runs[i % 4].id)
                assert run is not None and run.id == runs[i % 4].id, "got another row"
                events = st.list_events(run.id)
                assert all(e.run_id == run.id for e in events), "events of another run"
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
