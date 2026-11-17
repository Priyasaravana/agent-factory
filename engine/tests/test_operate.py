"""Run and maintain (roadmap J4), the pure rules: availability against a target, the error rate
from a metrics page, backup and restore judgement, upkeep parsers on their real output formats, the
production tier, and the ledger fold of a scheduled check."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_factory import ledger, operate, ready_for
from agent_factory.operate import Sample, Target

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _samples(pattern: str, every: int = 60, end: datetime = NOW) -> list[Sample]:
    """One reading per character, the last one at `end`: `.` answered, `x` didn't."""
    n = len(pattern)
    return [
        Sample(end - timedelta(seconds=every * (n - 1 - i)), ch == ".", "" if ch == "." else "curl: (7) refused")
        for i, ch in enumerate(pattern)
    ]


# ------------------------------------------------------------ availability --
def test_too_few_readings_are_unknown_never_a_breach_and_never_proof():
    a = operate.evaluate(_samples("xxxx"), Target(min_samples=5), NOW)
    assert (a.state, a.samples, a.reasons) == ("unknown", 4, [])
    assert operate.evaluate([], Target(), NOW).state == "unknown"
    assert operate.incident_candidate(a, Target(), "http://app") is None


def test_a_window_within_target_is_ok():
    a = operate.evaluate(_samples("." * 30), Target(success=0.99), NOW)
    assert (a.state, a.success, a.since) == ("ok", 1.0, None)
    assert operate.incident_candidate(a, Target(), None) is None


def test_missed_health_checks_breach_the_target_and_say_since_when():
    a = operate.evaluate(_samples("......xxx"), Target(success=0.99, window_minutes=30, min_samples=5), NOW)
    assert a.state == "breached" and a.success == pytest.approx(6 / 9)
    assert a.since == NOW - timedelta(minutes=2), "the run of failures the window ends with"
    assert "66.7% of 9 health checks answered in the last 30 minutes (target 99%): curl: (7) refused" in a.reasons
    cand = operate.incident_candidate(a, Target(), "http://127.0.0.1:30080")
    assert cand and cand["title"].startswith("Incident:") and "http://127.0.0.1:30080" in cand["detail"]


def test_only_the_window_counts():
    old = _samples("xxxxxxxxxx", end=NOW - timedelta(minutes=45))
    a = operate.evaluate(old + _samples("." * 10), Target(window_minutes=30), NOW)
    assert (a.state, a.samples) == ("ok", 10), "what failed before the window is not this window's breach"


def test_error_rate_is_judged_from_the_request_counters():
    rows = _samples("." * 6)
    counted = [Sample(s.at, True, "", requests=100.0 + 50 * i, errors=0.0 + 10 * i) for i, s in enumerate(rows)]
    a = operate.evaluate(counted, Target(max_error_rate=0.05), NOW)
    assert a.state == "breached" and a.error_rate == pytest.approx(0.2)
    assert a.reasons == ["20.0% of requests failed with a server error in the last 30 minutes (at most 5%)"]
    assert operate.evaluate(counted, Target(max_error_rate=None), NOW).state == "ok", "not judged when not declared"


def test_error_rate_claims_nothing_without_two_counted_readings_or_after_a_restart():
    t = NOW
    assert operate.error_rate([Sample(t, True, requests=10, errors=9)]) is None
    assert operate.error_rate([Sample(t, True, requests=10, errors=0), Sample(t, True, requests=10, errors=0)]) is None
    restarted = [Sample(t, True, requests=500, errors=40), Sample(t, True, requests=20, errors=1)]
    assert operate.error_rate(restarted) == pytest.approx(1 / 20), "after a restart only what was counted since"


def test_a_restart_in_the_middle_of_the_window_is_seen_between_neighbours():
    """Review of #185: (100,0), (0,0), (110,10) read 100% errors when only the ends were compared."""
    t = NOW
    rows = [Sample(t, True, requests=100, errors=0), Sample(t, True, requests=0, errors=0),
            Sample(t, True, requests=110, errors=10)]  # fmt: skip
    assert operate.error_rate(rows) == pytest.approx(10 / 110)
    # the counter came back higher than before the restart: still a restart, the errors are new ones
    rows = [Sample(t, True, requests=1000, errors=1), Sample(t, True, requests=1200, errors=0),
            Sample(t, True, requests=1300, errors=0)]  # fmt: skip
    assert operate.error_rate(rows) == pytest.approx(0 / 1300), "an error count that went down is a restart too"
    steady = [Sample(t, True, requests=100 * i, errors=float(i)) for i in range(1, 5)]
    assert operate.error_rate(steady) == pytest.approx(3 / 300), "no restart: the same as end minus start"


METRICS = """# HELP http_requests_total Requests by route and status.
# TYPE http_requests_total counter
http_requests_total{route="/api/bookmarks",status="200"} 180
http_requests_total{route="/api/bookmarks",status="201"} 12
http_requests_total{route="/api/bookmarks",status="500"} 6
http_requests_total{route="/api/tags",status="503"} 2 1731844800000
http_request_duration_seconds_bucket{le="0.1",status="500"} 99
process_cpu_seconds_total 12.5
"""


def test_request_counts_read_a_prometheus_page():
    assert operate.request_counts(METRICS, "http_requests_total") == (200.0, 8.0)
    grouped = 'http_requests_total{handler="/x",status="2xx"} 40\nhttp_requests_total{handler="/x",status="5xx"} 10\n'
    assert operate.request_counts(grouped, "http_requests_total") == (50.0, 10.0), "2xx/5xx classes count too"
    assert operate.request_counts("up 1\n", "http_requests_total") is None, "the counter is not there"
    assert operate.request_counts("<html>not metrics</html>", "http_requests_total") is None
    assert operate.request_counts("x" * (operate.MAX_METRICS_BYTES + 1), "http_requests_total") is None


def test_shares_are_written_as_a_person_writes_them():
    assert [operate.share(x) for x in (0.99, 0.995, 0.9995, 0.5)] == ["99%", "99.5%", "99.95%", "50%"]


def test_a_product_may_tighten_its_target_never_loosen_it():
    default = Target(success=0.99, max_error_rate=0.05)
    tighter, problems = operate.tighten(default, 0.999, 0.01)
    assert (tighter.success, tighter.max_error_rate, problems) == (0.999, 0.01, [])
    same, problems = operate.tighten(default, 0.95, None)
    assert same == default and problems == ["a product may tighten its stack's target (0.99), not loosen it"]
    _, problems = operate.tighten(default, None, 0.5)
    assert problems == ["a product may tighten its stack's error rate (0.05), not loosen it"]
    assert "99% of health checks over 30 minutes, at most 5% of requests failing" == default.text()


# ----------------------------------------------------------- backup/restore --
def test_a_backup_counts_only_with_data_and_a_fingerprint():
    assert operate.judge_backup(0, 2048, "users=3") == (True, "2048 bytes")
    assert operate.judge_backup(1, 2048, "users=3")[0] is False
    assert operate.judge_backup(0, 0, "users=3") == (False, "the backup command wrote nothing")
    ok, why = operate.judge_backup(0, 2048, "")
    assert not ok and "couldn't be fingerprinted" in why


def test_a_restore_is_proven_by_the_same_fingerprint_and_a_ready_app():
    backup = "users=3\nbookmarks=12\n"
    assert operate.judge_restore(backup, "bookmarks=12\n users=3 \n\n", True)[0], "order and spacing don't matter"
    ok, why = operate.judge_restore(backup, "users=3\nbookmarks=11\n", True)
    assert not ok and "differs from the backup" in why and "bookmarks=11" in why
    assert operate.judge_restore(backup, backup, False) == (
        False,
        "the data matches, but the app didn't report ready on it",
    )
    assert not operate.judge_restore("", "", True)[0], "an empty fingerprint proves nothing"
    assert not operate.judge_restore(backup, "", True)[0]


def test_backup_and_drill_schedules_and_pruning():
    assert operate.backup_due(None, 24, NOW) and operate.backup_due(NOW - timedelta(hours=25), 24, NOW)
    assert not operate.backup_due(NOW - timedelta(hours=2), 24, NOW)
    assert not operate.restore_due(None, None, 7, NOW), "nothing to restore yet"
    assert operate.restore_due(None, NOW, 7, NOW), "a backup that was never restored"
    assert not operate.restore_due(NOW - timedelta(days=2), NOW, 7, NOW)
    rows = [operate.BackupRecord(str(i), NOW - timedelta(days=i), ok=i != 1) for i in range(5)]
    assert [b.id for b in operate.to_prune(rows, keep=2)] == ["1", "3", "4"], "failed ones, then beyond the newest 2"


# ------------------------------------------------------------------- upkeep --
NPM_OUTDATED = json.dumps(
    {
        "next": {"current": "15.0.3", "wanted": "15.0.4", "latest": "16.0.1", "dependent": "app", "location": "/w/n"},
        "zod": {"current": "3.23.8", "wanted": "3.23.8", "latest": "3.24.1", "dependent": "app", "location": "/w/z"},
        "react": {"wanted": "19.0.0", "latest": "19.0.0", "dependent": "app"},
    }
)
PIP_OUTDATED = json.dumps(
    [
        {"name": "fastapi", "version": "0.115.0", "latest_version": "0.116.1", "latest_filetype": "wheel"},
        {"name": "pydantic", "version": "1.10.2", "latest_version": "2.9.0", "latest_filetype": "wheel"},
    ]
)


def test_npm_outdated_is_read_in_its_real_format():
    rows = operate.parse_npm_outdated(NPM_OUTDATED)
    assert rows == [
        operate.Outdated("next", "15.0.3", "16.0.1", True),
        operate.Outdated("zod", "3.23.8", "3.24.1", False),
    ]
    assert operate.parse_npm_outdated("{}") == [], "nothing is behind"
    assert operate.parse_npm_outdated("") is None and operate.parse_npm_outdated("  \n") is None, (
        "no output is no answer, never 'nothing is behind'"
    )
    assert operate.parse_npm_outdated("npm ERR! network") is None
    assert operate.parse_npm_outdated('{"error": {"code": "E404"}}') is None


def test_pip_outdated_is_read_in_its_real_format():
    rows = operate.parse_pip_outdated(PIP_OUTDATED)
    assert rows == [
        operate.Outdated("fastapi", "0.115.0", "0.116.1", False),
        operate.Outdated("pydantic", "1.10.2", "2.9.0", True),
    ]
    assert operate.parse_pip_outdated("[]") == [] and operate.parse_pip_outdated("{}") is None
    assert operate.parse_pip_outdated("") is None, "no output is no answer"


def test_the_json_is_found_inside_a_tools_other_output():
    noisy = f"Resolved 41 packages in 12ms\n{PIP_OUTDATED}\n"
    assert operate.parse_pip_outdated(operate.json_part(noisy)) == operate.parse_pip_outdated(PIP_OUTDATED)
    assert operate.parse_npm_outdated(operate.json_part(f"npm warn old lockfile\n{NPM_OUTDATED}")) is not None


def test_dependencies_behind_are_one_work_item():
    cand = operate.upkeep_candidate(operate.parse_npm_outdated(NPM_OUTDATED) or [])
    assert cand and cand["title"] == "2 dependencies are behind (1 by a major version)"
    assert cand["severity"] == "medium" and cand["detail"].startswith("next: 15.0.3 -> 16.0.1 (major)\nzod:")
    one = operate.upkeep_candidate([operate.Outdated("zod", "3.23.8", "3.24.1", False)])
    assert one and (one["title"], one["severity"]) == ("1 dependency is behind", "low")
    assert operate.upkeep_candidate([]) is None


# --------------------------------------------------------------- production --
def test_production_needs_a_target_a_running_alert_and_a_proven_restore():
    has, needs = operate.production_proof(None, False, None, None)
    assert has == [] and len(needs) == 3
    has, needs = operate.production_proof("99% of health checks", True, None, None)
    assert needs == ["a tested restore of its data"] and "alert on the target: watching, no breach yet" in has
    has, needs = operate.production_proof("99% of health checks", True, "it fired on 2026-11-17", "drill on 2026-11-17")
    assert needs == [] and len(has) == 3


def _pilot(production: dict | None) -> ready_for.Inputs:
    trace = [{"id": "R1", "holdout": [{"passed": True}]}]
    return ready_for.Inputs(True, 3, trace, "priya", "c1", production)


def test_ready_for_production_is_proved_never_assumed():
    r = ready_for.assess(_pilot(None))
    assert (r.tier, r.next_tier) == ("pilot", "production") and len(r.needs) == 3
    watched = {
        "target": "99% of health checks",
        "watched": True,
        "alert_proven": None,
        "restore": None,
        "data_declared": True,
    }
    r = ready_for.assess(_pilot(watched))
    assert (r.tier, r.needs) == ("pilot", ["a tested restore of its data"])
    r = ready_for.assess(_pilot({**watched, "restore": "drill on 2026-11-17"}))
    assert (r.tier, r.next_tier, r.needs) == ("production", None, [])
    assert "restore proven (drill on 2026-11-17)" in r.proven
    r = ready_for.assess(_pilot({**watched, "data_declared": False}))
    assert r.tier == "production" and "restore proven (the stack declares no data to restore)" in r.proven
    r = ready_for.assess(_pilot({**watched, "watched": False, "restore": "drill on 2026-11-17"}))
    assert r.tier == "pilot" and "the factory isn't watching" in r.needs[0], "a target nobody watches is not an alert"


def test_the_watch_is_fresh_for_three_probe_intervals():
    assert operate.fresh(NOW - timedelta(seconds=170), 60, NOW)
    assert not operate.fresh(NOW - timedelta(seconds=200), 60, NOW) and not operate.fresh(None, 60, NOW)


# ------------------------------------------------------------ ledger fold --
INCIDENT = {"title": "Incident: the product is outside its availability target", "detail": "60% answered"}


def _fold(items, candidate, running=(), now=NOW, category=ledger.INCIDENT):
    return ledger.fold_watch("p1", items, category, candidate, now=now, iteration=1, running=running)


def test_a_breach_opens_one_incident_and_a_healthy_window_closes_it():
    items: list = []
    it, what = _fold(items, INCIDENT)
    assert what == "new" and it is not None
    assert (it.category, it.kind, it.severity, it.change_kind, it.status) == (
        "incident",
        "issue",
        "high",
        "bug",
        "open",
    )
    assert _fold(items, INCIDENT) == (None, ""), "the same breach again is not filed twice"
    it2, what = _fold(items, {**INCIDENT, "detail": "40% answered"})
    assert what == "updated" and it2 is it and len(items) == 1
    it3, what = _fold(items, None)
    assert what == "fixed" and it3 is it and it.status == "fixed"
    assert it.history[-1].note == "the check ran again and no longer finds it (no change addressed it)"
    assert _fold(items, None) == (None, "")
    assert ledger.pick_kind([it]) == "bug", "an incident is fixed by a bug-fix change"


def test_an_incident_stays_in_progress_until_its_fix_is_deployed_then_the_check_decides():
    items: list = []
    it, _ = _fold(items, INCIDENT)
    ledger.claim(items, [it.id], change_id="c2", iteration=2, now=NOW, by="priya")
    assert it.status == "in_progress"
    assert _fold(items, None, running={"c2"}) == (None, ""), "healthy before the fix is out proves nothing yet"
    assert _fold(items, INCIDENT, running={"c2"}) == (None, "")
    fixed, what = _fold(items, None)
    assert what == "fixed" and fixed.resolved_by == "c2" and "after change c2" in fixed.history[-1].note


def test_a_fix_that_did_not_hold_reopens_the_incident():
    items: list = []
    it, _ = _fold(items, INCIDENT)
    ledger.claim(items, [it.id], change_id="c2", iteration=2, now=NOW, by="priya")
    back, what = _fold(items, INCIDENT)  # c2 stopped and the check still finds it
    assert what == "reopened" and back.status == "open" and back.history[-1].event == "still-present"


def test_a_new_occurrence_reopens_a_fixed_incident():
    items: list = []
    it, _ = _fold(items, INCIDENT)
    _fold(items, None)
    again, what = _fold(items, INCIDENT, now=NOW + timedelta(days=3))
    assert what == "reopened" and again is it and it.status == "open" and it.claimed_by == []


def test_upkeep_is_folded_the_same_way_and_listed_for_the_next_iteration():
    items: list = []
    cand = operate.upkeep_candidate(operate.parse_npm_outdated(NPM_OUTDATED) or [])
    it, what = _fold(items, cand, category=ledger.UPKEEP)
    assert what == "new" and (it.severity, it.change_kind, it.at) == ("medium", "upkeep", ["next", "zod"])
    _fold(items, INCIDENT)
    groups = ledger.next_iteration(items)
    assert [i.category for i in groups["incidents"]] == ["incident"] and groups["upkeep"] == [it]
    assert ledger.pick_kind(groups["upkeep"]) == "upkeep"
    assert _fold(items, None, category=ledger.UPKEEP)[1] == "fixed"
