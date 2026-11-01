"""How much an event matters to a person reading the activity log (pure).

Every event gets one level, decided here from what the engine recorded, never by an agent:

- ``error``: the product or the factory failed at something (a station failed, a command exited
  non-zero, the engine itself failed);
- ``warn``: something needs attention or slowed the change down (a hold, a wait for a person, an
  infra retry, a guardrail denial, a refused secret);
- ``info``: what happened (stations, decisions, status, feedback);
- ``debug``: detail an agent produced on the way (its text, tool calls, sandbox and gateway notes).

The rules are a short table on the event's kind and data, so the same event always gets the same
level. Anything they don't recognise is ``info``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class EventLevel(StrEnum):
    error = "error"
    warn = "warn"
    info = "info"
    debug = "debug"


# messages of status events that mean a person must act, or the change stopped short
_WAITING = ("review gate:", "change risk: waiting", "intake needs", "paused", "held at", "interrupted")
_FAILED_STATUS = ("engine error", "failed")


def level_of(kind: str, message: str, data: Mapping[str, Any] | None) -> EventLevel:
    d = data or {}
    msg = (message or "").lower()
    if kind == "station_finished":
        outcome = str(d.get("outcome", "passed"))
        if outcome == "failed":
            return EventLevel.error
        if outcome in ("held", "needs_input", "needs_approval", "paused_limits"):
            return EventLevel.warn
        return EventLevel.info
    if kind == "command":
        rc = d.get("returncode")
        if d.get("timed_out") or (isinstance(rc, int) and rc != 0):
            return EventLevel.error
        return EventLevel.info
    if kind == "status":
        if "traceback" in d or msg.startswith(_FAILED_STATUS):
            return EventLevel.error
        if "evidence" in d or any(w in msg for w in _WAITING):
            return EventLevel.warn
        return EventLevel.info
    if kind == "decision":
        rb = d.get("rollback")
        if isinstance(rb, Mapping):  # production rolled back (ADR-0046): attention; a failed one is an error
            return EventLevel.error if rb.get("ok") is False else EventLevel.warn
        removed = d.get("validation_removed")
        if isinstance(removed, Mapping) and removed.get("ok") is False:  # a temporary environment left behind
            return EventLevel.warn
        if "retry" in d or "denied" in d or "no_progress" in d:
            return EventLevel.warn
        failure = d.get("failure")
        if isinstance(failure, Mapping) and failure.get("class") in ("infra", "decision"):
            return EventLevel.warn
        return EventLevel.info
    if kind in ("agent", "log"):
        return EventLevel.debug
    return EventLevel.info
