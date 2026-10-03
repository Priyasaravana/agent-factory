"""Host sleep is not agent time: detection, recording on active runs, the Outcomes bucket."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import PRODUCT, wait_run
from test_outcomes import NOW, T0, _facts

from agent_factory.hostclock import SuspendWatcher, detect, merge, overlap_s
from agent_factory.models import ChangeStatus
from agent_factory.outcomes import compute

E = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)


def test_detect():
    t = E.timestamp()
    assert detect(t, 100.0, t + 5, 105.0) is None, "awake: both clocks moved alike"
    assert detect(t, 100.0, t + 25, 105.0) is None, "20s gap: under the threshold (jitter, a stall)"
    assert detect(t, 100.0, t - 3600, 105.0) is None, "wall clock set back"
    start, end = detect(t, 100.0, t + 1000, 105.0)  # slept ~995s inside a 5s tick
    assert start == E + timedelta(seconds=5) and end == E + timedelta(seconds=1000)


def test_overlap_and_merge():
    p = merge([(E, E + timedelta(minutes=10)), (E + timedelta(minutes=5), E + timedelta(minutes=12))])
    assert p == [(E, E + timedelta(minutes=12))]
    assert overlap_s(E - timedelta(minutes=1), E + timedelta(minutes=2), p) == 120.0
    assert overlap_s(E + timedelta(hours=1), E + timedelta(hours=2), p) == 0.0


async def test_the_watcher_reports_a_jump():
    ticks = iter([(0.0, 0.0), (5.0, 5.0), (1000.0, 10.0)])
    last = (1000.0, 10.0)
    seen = []
    w = SuspendWatcher(lambda s, e: seen.append((e - s).total_seconds()), interval=0, clock=lambda: next(ticks, last))
    w.start()
    await asyncio.sleep(0.05)
    await w.stop()
    assert seen == [990.0]


def test_outcomes_moves_host_sleep_out_of_agent_time():
    f = _facts()
    base = compute(f).time_split
    # the host slept from +100s to +400s (see test_outcomes for each change's timeline)
    f.pauses = [(T0 + timedelta(seconds=100), T0 + timedelta(seconds=400))]
    s = compute(f).time_split
    # running then: A 300s, C 100s (until +200), D 300s, E 200s (until +300)
    # B waited on answers for all 300s; E waited for the usage window from +300 (100s)
    assert s.agents_s == base.agents_s - (300 + 100 + 300 + 200)
    assert s.person_s == base.person_s - 300, "sleep is moved out of whichever bucket it fell in"
    assert s.system_s == base.system_s - 100
    assert s.suspended_s == 1300
    assert NOW > T0


async def test_active_runs_are_told_and_the_pause_is_recorded(make_factory):
    f = make_factory()
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    f.manager.host_suspended(E, E + timedelta(minutes=16))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    [ev] = [e for e in f.store.list_events(change.id) if "host_suspended" in e.data]
    assert ev.message.startswith("factory host was asleep for 0:16:00") and ev.data["host_suspended"]["seconds"] == 960
    assert f.store.host_pauses() == [(E, E + timedelta(minutes=16))]
    assert f.store.host_pauses(E + timedelta(hours=1)) == []
    f.manager.host_suspended(E + timedelta(hours=2), E + timedelta(hours=3))  # nothing active: recorded only
    assert len(f.store.host_pauses()) == 2
    assert len([e for e in f.store.list_events(change.id) if "host_suspended" in e.data]) == 1
