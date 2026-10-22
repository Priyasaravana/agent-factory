"""Failure classes in the pipeline (ADR-0043): infra failures are retried in place and never reach
the developer; what retries can't cure, decisions and time limits hold the change."""

from __future__ import annotations

from conftest import PRODUCT, wait_run
from test_failures import GUESTBOOK_401

from agent_factory.agents import FakeAgentRunner
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus, EventKind


def _quick(f, **budgets):
    b = f.manager.cfg.budgets
    b.infra_backoff_seconds = [0.0]
    for k, v in budgets.items():
        setattr(b, k, v)
    return f


def _retries(f, change_id):
    return [e.data["retry"] for e in f.store.list_events(change_id) if "retry" in (e.data or {})]


def _stations(f, change_id):
    return [e.station for e in f.store.list_events(change_id) if e.kind == EventKind.station_finished]


async def test_guestbook_expired_keys_are_retried_not_sent_to_the_developer(make_factory):
    """Guestbook (change df3bdaaeb306): the reviewer's key expired twice; both 401s went to the
    developer as code failures and used up implement's attempts. Now: two retries, then delivered."""
    agents = FakeAgentRunner(infra_fail={"reviewer": [GUESTBOOK_401, GUESTBOOK_401]})
    f = _quick(make_factory(agents=agents))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback

    assert [c.role for c in agents.calls].count("developer") == 1, "the 401s never reached the developer"
    assert "implement" not in _stations(f, change.id)[3:], "no fix loop"
    retries = _retries(f, change.id)
    assert [(r["rule"], r["attempt"]) for r in retries] == [("key_expired", 1), ("key_expired", 2)]
    assert retries[0]["wait_s"] == 0, "a fresh key is tried at once"
    retried = [c for c in agents.calls if c.role == "reviewer"]
    assert len(retried) == 3 and "The factory retried this call" in retried[1].prompt
    assert f.store.get_change(change.id).loops == 0


async def test_infra_that_retries_cannot_cure_holds_for_the_factory(make_factory):
    agents = FakeAgentRunner(infra_fail={"reviewer": [GUESTBOOK_401] * 4})
    f = _quick(make_factory(agents=agents), infra_retries=3)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held

    held = f.store.get_change(change.id)
    assert held.hold_class == "infra" and held.current_station == "code-review"
    assert "infrastructure: the model key expired during the call after 3 retries" in held.summary
    assert [c.role for c in agents.calls].count("developer") == 1
    assert held.attempts.get("code-review", 0) == 0, "the factory's failure is not an attempt"

    f.manager.resume(change.id)
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert f.store.get_change(change.id).hold_class is None


async def test_cluster_unreachable_is_retried_not_repaired(make_factory):
    ex = FakeExecutor(
        fail_on=["helm upgrade"],
        fail_with={"helm upgrade": "Error: Kubernetes cluster unreachable: dial tcp 10.0.0.1:6443: connection refused"},
    )
    f = _quick(make_factory(ex))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert "deploy-repair" not in _stations(f, change.id)
    assert [r["rule"] for r in _retries(f, change.id)] == ["cluster_unreachable"]


async def test_a_real_deploy_failure_still_goes_to_repair(make_factory):
    """The classifier only takes wrong routes away: an unrecognised failure is still work."""
    f = _quick(make_factory(FakeExecutor(fail_on=["helm upgrade"])))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert "deploy-repair" in _stations(f, change.id) and not _retries(f, change.id)


async def test_model_budget_is_a_decision_held_at_once(make_factory):
    agents = FakeAgentRunner(
        infra_fail={"architect": ["model gateway: model budget for this change reached ($5 of $5)"]}
    )
    f = _quick(make_factory(agents=agents))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert held.hold_class == "decision" and "model budget" in held.summary
    assert not _retries(f, change.id)


async def test_a_stalled_call_is_cancelled_and_retried(make_factory):
    agents = FakeAgentRunner(hang={"architect": 5.0})
    f = _quick(make_factory(agents=agents), agent_stall_minutes=0.003)  # ~0.2 s
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert [r["rule"] for r in _retries(f, change.id)] == ["stalled"]


async def test_a_call_past_its_limit_holds(make_factory):
    agents = FakeAgentRunner(hang={"architect": 5.0})
    f = _quick(make_factory(agents=agents), agent_call_minutes=0.003, agent_stall_minutes=10)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert held.current_station == "design" and "ran past its 0.003-minute limit" in held.summary
    assert held.hold_class == "work" and not _retries(f, change.id)


async def test_the_time_budget_holds_inside_a_station(make_factory):
    """Before ADR-0043 the budget was only checked between stations: one long call ran on."""
    agents = FakeAgentRunner(hang={"intake": 5.0})
    f = _quick(make_factory(agents=agents), change_wall_clock_minutes=0.004)  # ~0.24 s
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, timeout=10) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert "time budget (during the intake agent call)" in held.summary


TRIVY_DB_CUT = (
    "2026-10-22T15:19:21Z\tFATAL\tFatal error\trun error: init error: DB error: failed to download vulnerability "
    "DB: OCI artifact error: failed to download artifact from mirror.gcr.io/aquasec/trivy-db:2: oci download "
    "error: copy error: unexpected EOF"
)


async def test_a_cut_off_scanner_db_download_is_retried_not_sent_to_the_developer(make_factory):
    """Book list (change 43be36b68aaf, 2026-10-22): the Mac slept during the image scan, Trivy's
    DB download ended in "unexpected EOF", the build went to implement as a code failure and the
    change held on implement's attempt budget. The scan now goes through ctx.cmd: retried in place."""
    needle = "aquasec/trivy:latest image"
    ex = FakeExecutor(fail_on=[needle], fail_with={needle: TRIVY_DB_CUT})
    f = _quick(make_factory(ex))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    assert "implement" not in _stations(f, change.id)[3:], "no fix loop"
    assert [r["rule"] for r in _retries(f, change.id)] == ["scanner_db"]
    scans = [e for e in f.store.list_events(change.id) if e.kind == EventKind.command and needle in e.message]
    assert len(scans) == 1 and scans[0].message.startswith("✓"), "one summary, the retried scan's"
    assert not any("> " in e.message and needle in e.message for e in scans), "no report path or raw output"
