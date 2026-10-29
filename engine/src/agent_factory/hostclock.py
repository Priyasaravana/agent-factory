"""Host suspension detection (ADR-0019 amendment, 2026-10-01).

When the machine running the factory sleeps (a laptop lid closed, Docker Desktop's
VM paused), nothing runs, but wall-clock time keeps going. Without this, Outcomes
counted that time as "agents working" and lead times grew for no visible reason.

The monotonic clock stops while the host is suspended; the wall clock jumps
forward when it wakes. A watcher compares the two every few seconds: a gap larger
than `THRESHOLD_S` is a suspension, recorded with its start and end. Outcomes moves
overlapping run time into its own "host asleep" bucket, and every change active at
the time gets an event saying so.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

INTERVAL_S = 5.0
THRESHOLD_S = 30.0  # scheduling jitter and short stalls stay well below this


def detect(
    wall_prev: float, mono_prev: float, wall_now: float, mono_now: float, threshold: float = THRESHOLD_S
) -> tuple[datetime, datetime] | None:
    """(start, end) of a suspension between two ticks, or None. Pure."""
    gap = (wall_now - wall_prev) - (mono_now - mono_prev)
    if gap <= threshold:
        return None  # includes a wall clock set backwards (negative gap)
    start = wall_prev + (mono_now - mono_prev)  # the part of the interval that really ran came first
    return datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(wall_now, UTC)


class SuspendWatcher:
    def __init__(
        self,
        on_suspend: Callable[[datetime, datetime], None],
        interval: float = INTERVAL_S,
        clock: Callable[[], tuple[float, float]] | None = None,
    ) -> None:
        self.on_suspend = on_suspend
        self.interval = interval
        self.clock = clock or (lambda: (time.time(), time.monotonic()))
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="host-suspend-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        prev = self.clock()
        while True:
            await asyncio.sleep(self.interval)
            now = self.clock()
            if got := detect(prev[0], prev[1], now[0], now[1]):
                try:
                    self.on_suspend(*got)
                except Exception:  # noqa: BLE001, S110 - the watcher must keep watching
                    pass
            prev = now


def now_pair() -> tuple[float, float]:
    """(wall, monotonic) now: take one before and one after a step, and `asleep_s` says whether the
    host slept during it."""
    return time.time(), time.monotonic()


def asleep_s(before: tuple[float, float], after: tuple[float, float], threshold: float = THRESHOLD_S) -> float:
    """Seconds the host was suspended between two `now_pair` readings, 0 if none. Pure. It needs no
    watcher and has no race with it: the step's own clocks show the gap (Kudos board, 2026-10-29: the
    preview build was cut off by a 1h04 sleep and sent to the developer as a code failure)."""
    got = detect(before[0], before[1], after[0], after[1], threshold)
    return 0.0 if got is None else (got[1] - got[0]).total_seconds()


def overlap_s(a: datetime, b: datetime, pauses: list[tuple[datetime, datetime]]) -> float:
    """Seconds of [a, b] covered by the (non-overlapping) pauses. Pure."""
    total = 0.0
    for s, e in pauses:
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def merge(pauses: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(pauses):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def human(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds)))
