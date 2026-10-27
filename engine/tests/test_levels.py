"""Event levels (`levels.level_of`): one level per event, decided by the engine, never by an agent."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_factory.levels import EventLevel, level_of
from agent_factory.models import Event, EventKind

L = EventLevel


@pytest.mark.parametrize(
    ("kind", "message", "data", "level"),
    [
        # stations: failed is an error, a stop for a person a warning, passed is info
        ("station_finished", "test: failed — 2 tests failed", {"outcome": "failed"}, L.error),
        ("station_finished", "build: held — base image", {"outcome": "held"}, L.warn),
        ("station_finished", "change-risk: needs_approval", {"outcome": "needs_approval"}, L.warn),
        ("station_finished", "intake: needs_input", {"outcome": "needs_input"}, L.warn),
        ("station_finished", "test: passed — 12 tests", {"outcome": "passed"}, L.info),
        ("station_started", "test started", {}, L.info),
        # commands: a non-zero exit or a timeout is an error; a clean run is info
        ("command", "✗ make e2e", {"returncode": 1, "output": "getaddrinfo ENOTFOUND dind"}, L.error),
        ("command", "✗ make verify", {"returncode": 0, "timed_out": True}, L.error),
        ("command", "✓ make verify", {"returncode": 0, "output": "ok"}, L.info),
        # status: an engine failure is an error; holds and waits for a person are warnings
        ("status", "engine error: boom", {"traceback": "..."}, L.error),
        ("status", "held at acceptance: e2e failed", {"evidence": "..."}, L.warn),
        ("status", "review gate: waiting for a person to approve the spec before build", {}, L.warn),
        ("status", "change risk: waiting for an admin to approve", {}, L.warn),
        ("status", "engine restarted: change interrupted", {}, L.warn),
        ("status", "delivered — feedback gate open", {}, L.info),
        ("status", "change queued (iteration 1)", {}, L.info),
        # decisions: infra retries and guardrail denials are warnings; ordinary decisions are info
        ("decision", "retrying agent call in 20s (1/3): 401 Expired Key", {"retry": {"attempt": 1}}, L.warn),
        (
            "decision",
            "no progress: acceptance failed the same way again and implement changed no code",
            {"no_progress": {"station": "acceptance", "repair": "implement"}},
            L.warn,
        ),
        ("decision", "guardrail denied Bash for reviewer: write", {"denied": {"tool": "Bash"}}, L.warn),
        ("decision", "denied secret env://X", {"denied": "push-image"}, L.warn),
        ("decision", "held for infra", {"failure": {"class": "infra"}}, L.warn),
        ("decision", "work failure routed to implement", {"failure": {"class": "work"}}, L.info),
        ("decision", "work item: feature from ui, asked by priya", {}, L.info),
        # agent detail is debug: useful when digging, noise otherwise
        ("agent", "Let me read the spec first.", {}, L.debug),
        ("log", "Read: docs/spec.md", {"tool": "Read"}, L.debug),
        ("feedback", "Add search", {"kind": "feature"}, L.info),
    ],
)
def test_level_of(kind: str, message: str, data: dict[str, object], level: EventLevel) -> None:
    assert level_of(kind, message, data) == level


def test_unknown_kinds_and_missing_data_are_info() -> None:
    assert level_of("something-new", "x", None) == L.info
    assert level_of("station_finished", "x", None) == L.info


def test_every_event_carries_its_level_in_the_api() -> None:
    ev = Event(
        id=1,
        change_id="c",
        ts=datetime(2026, 10, 26, tzinfo=UTC),
        kind=EventKind.command,
        message="✗ make e2e",
        data={"returncode": 2},
    )
    assert ev.level == L.error
    assert ev.model_dump(mode="json")["level"] == "error"
    assert '"level":"error"' in ev.model_dump_json()
