"""Incident response, slice 0 (ADR-0049 §4), the pure part: the mode, reading the live state, the ladder,
the never-undo-a-newer-release rule, the incident's states and its DORA timings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_factory import respond
from agent_factory.models import Release

T0 = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64


def release(**kw: Any) -> Release:
    base = {
        "change_id": "c1", "iteration": 1, "image": "bookmarks:c1", "environment": "local", "target": "local",
        "cluster": "kind:factory", "namespace": "app-bookmarks", "helm_release": "bookmarks", "revision": 3,
        "digest": DIGEST, "at": T0 - timedelta(hours=2),
    }  # fmt: skip
    return Release.model_validate({**base, **kw})


def live(**kw: Any) -> respond.Live:
    base: dict[str, Any] = {
        "revision": 3, "description": "Upgrade complete", "status": "deployed", "image": "bookmarks:c1",
        "digests": [DIGEST], "workloads": {"Deployment/bookmarks": respond.Workload("Deployment/bookmarks", 1, 1)},
        "expected": {"Deployment/bookmarks": 1}, "pods": 1,
    }  # fmt: skip
    return respond.Live(**{**base, **kw})


@dataclass
class Rec:
    kind: str
    at: datetime
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class S:
    at: datetime
    ok: bool


# -------------------------------------------------------------------- mode --
def test_auto_waits_for_a_passed_factory_restore_drill_and_for_reconciliation():
    assert respond.effective_mode("auto", drill_passed=False, reconciled=True)[0] == "ask"
    mode, why = respond.effective_mode("auto", drill_passed=True, reconciled=False)
    assert mode == "ask" and "restored from a backup" in why
    assert respond.effective_mode("auto", drill_passed=True, reconciled=True) == ("auto", "")
    assert respond.effective_mode("ask", drill_passed=True, reconciled=True) == ("ask", "")
    assert respond.effective_mode("off", drill_passed=False, reconciled=False) == ("off", "")


# -------------------------------------------------------------- live state --
MANIFEST = """---
# Source: app/templates/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: bookmarks
spec:
  ports: [{port: 80}]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bookmarks
  labels:
    app.kubernetes.io/instance: bookmarks
spec:
  replicas: 2
  selector:
    matchLabels: {app: x}
  template:
    spec:
      containers:
        - name: api
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: bookmarks-postgres
spec:
  serviceName: bookmarks-postgres
  replicas: 1
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: autoscaled
spec:
  selector: {}
"""


def test_the_manifest_says_how_many_replicas_each_workload_runs():
    assert respond.manifest_replicas(MANIFEST) == {"Deployment/bookmarks": 2, "StatefulSet/bookmarks-postgres": 1}, (
        "services are skipped, and a workload an autoscaler owns states no replicas"
    )
    assert respond.manifest_replicas("") == {}


def test_workloads_and_pod_health_are_read_from_the_jsonpath_lines():
    got = respond.workloads("Deployment/bookmarks=2=1\nStatefulSet/bookmarks-postgres=1=\njunk\n")
    assert got["Deployment/bookmarks"] == respond.Workload("Deployment/bookmarks", 2, 1)
    assert got["StatefulSet/bookmarks-postgres"].ready == 0, "no ready replicas prints nothing"
    text = (
        "|true=0= true=0= \n"
        "|false=7=CrashLoopBackOff true=0= \n"
        "2026-11-22T12:00:00Z|false=0= \n"  # being deleted: not counted
    )
    assert respond.pod_health(text) == (2, 1, ["CrashLoopBackOff"], 7)


# ----------------------------------------------------------------- compare --
def test_the_factorys_own_reapply_is_not_a_newer_release():
    rec = release()
    assert respond.stale_record(rec, live()) == ""
    assert respond.stale_record(rec, live(revision=5, description="Rollback to 3")) == ""


@pytest.mark.parametrize(
    ("revision", "description"),
    [(4, "Upgrade complete"), (5, "Rollback to 2"), (1, "Install complete")],
    ids=["someone upgraded", "rolled back elsewhere", "installed again"],
)
def test_any_other_live_revision_means_the_record_is_stale(revision, description):
    why = respond.stale_record(release(), live(revision=revision, description=description))
    assert f"revision {revision}" in why and "doesn't know" in why


def test_drift_is_image_digest_or_replicas_and_nothing_else():
    rec = release()
    assert respond.drift(rec, live()) == []
    assert "not the recorded bookmarks:c1" in respond.drift(rec, live(image="bookmarks:x"))[0]
    assert "not the recorded sha256:111" in respond.drift(rec, live(digests=[OTHER]))[0]
    scaled = live(workloads={"Deployment/bookmarks": respond.Workload("Deployment/bookmarks", 0, 0)})
    assert respond.drift(rec, scaled) == ["Deployment/bookmarks runs 0 replicas, not 1"]
    assert respond.drift(rec, live(workloads={})) == ["Deployment/bookmarks is missing"]
    assert "no container" in respond.drift(rec, live(digests=[]))[0]


# ------------------------------------------------------------------ ladder --
def test_drift_is_reapplied_first_and_a_crash_with_no_drift_restarts():
    step = respond.next_step(release(), live(digests=[OTHER]), [], 0, 3, "auto")
    assert (step.action, step.ask) == ("reapply", False)
    step = respond.next_step(release(), live(crashing=["CrashLoopBackOff"], not_ready=1), [], 0, 3, "auto")
    assert step.action == "restart" and "CrashLoopBackOff" in step.reason


def test_each_step_once_then_the_owner_decides():
    rec, drifted = release(), live(digests=[OTHER])
    assert respond.next_step(rec, drifted, ["reapply"], 1, 3, "auto").action == "restart"
    step = respond.next_step(rec, drifted, ["reapply", "restart"], 2, 3, "auto")
    assert (step.action, step.hold) == (None, "ladder_used")


def test_ask_mode_proposes_the_same_step_and_runs_nothing():
    step = respond.next_step(release(), live(), [], 0, 3, "ask")
    assert (step.action, step.ask) == ("restart", True)


def test_a_stale_record_holds_before_anything_else():
    step = respond.next_step(release(), live(revision=4, digests=[OTHER]), [], 0, 3, "auto")
    assert (step.action, step.hold) == (None, "stale_record") and "nothing is applied over it" in step.reason


def test_an_unreadable_state_or_too_many_actions_hold():
    assert respond.next_step(release(), respond.Live(error="no pods"), [], 0, 3, "auto").hold == "unreadable"
    assert respond.next_step(None, live(), [], 0, 3, "auto").hold == "unreadable"
    assert respond.next_step(release(), live(), [], 3, 3, "auto").hold == "rate_limited"
    assert respond.next_step(release(), live(), [], 0, 3, "off").action is None


# ------------------------------------------------------------------- state --
def _alert(at: datetime, **data: Any) -> Rec:
    return Rec("alert", at, False, {"incident": "i1", "first_bad_at": (at - timedelta(minutes=3)).isoformat(), **data})


def test_the_episode_follows_the_records():
    recs = [
        _alert(T0, caused_by="c1"),
        Rec("respond", T0 + timedelta(seconds=10), data={"incident": "i1", "step": "reapply", "ran": True}),
        Rec("respond", T0, data={"incident": "other", "step": "restart", "ran": True}),  # another incident
    ]
    ep = respond.episode(recs, "i1")
    assert ep is not None
    assert (ep.state, ep.tried, ep.caused_by) == ("mitigating", ["reapply"], "c1")
    recs.append(Rec("restored", T0 + timedelta(minutes=1), data={"incident": "i1", "by_step": "reapply"}))
    ep = respond.episode(recs, "i1")
    assert ep is not None and (ep.state, ep.restored_by) == ("restored", "reapply")
    assert ep.timings() == {"time_to_detect_s": 180.0, "time_to_restore_s": 240.0}, "both from the first bad reading"
    assert respond.classify(ep).startswith("operational: the live state had drifted")


def test_a_recurrence_before_it_is_resolved_reopens_and_keeps_what_was_tried():
    recs = [
        _alert(T0),
        Rec("respond", T0, data={"incident": "i1", "step": "reapply", "ran": True}),
        Rec("restored", T0 + timedelta(minutes=1), data={"incident": "i1"}),
        _alert(T0 + timedelta(hours=2), reopened=True),
    ]
    ep = respond.episode(recs, "i1")
    assert ep is not None and (ep.state, ep.tried, ep.reopened, ep.restored_at) == ("reopened", ["reapply"], 1, None)


def test_after_resolved_the_next_alert_starts_a_new_episode():
    recs = [
        _alert(T0),
        Rec("respond", T0, data={"incident": "i1", "step": "restart", "ran": True}),
        Rec("resolved", T0 + timedelta(days=1), data={"incident": "i1"}),
        _alert(T0 + timedelta(days=2)),
    ]
    ep = respond.episode(recs, "i1")
    assert ep is not None and (ep.state, ep.tried) == ("open", [])


def test_holds_and_proposals_are_remembered_until_a_step_runs():
    recs = [_alert(T0), Rec("respond", T0, data={"incident": "i1", "ask": True, "step": "restart"})]
    ep = respond.episode(recs, "i1")
    assert ep is not None and ep.proposed == "restart"
    recs.append(Rec("respond", T0 + timedelta(seconds=5), data={"incident": "i1", "step": "restart", "ran": True}))
    ep = respond.episode(recs, "i1")
    assert ep is not None and (ep.proposed, ep.state) == ("", "mitigating")
    assert respond.episode([Rec("respond", T0, data={"incident": "i1"})], "i1") is None, "no alert, no episode"


def test_restored_needs_k_healthy_readings_in_a_row_after_the_action():
    since = T0
    rows = [S(T0 - timedelta(seconds=5), True), S(T0 + timedelta(seconds=10), True),
            S(T0 + timedelta(seconds=20), False), S(T0 + timedelta(seconds=30), True),
            S(T0 + timedelta(seconds=40), True), S(T0 + timedelta(seconds=50), True)]  # fmt: skip
    assert respond.restored_at(rows, since, 3) == T0 + timedelta(seconds=50)
    assert respond.restored_at(rows[:5], since, 3) is None


def test_resolved_after_the_quiet_period_with_no_recurrence():
    ep = respond.Episode("i1", state="restored", restored_at=T0)
    assert not respond.resolve_due(ep, T0 + timedelta(hours=23), 24)
    assert respond.resolve_due(ep, T0 + timedelta(hours=24), 24)
    assert not respond.resolve_due(respond.Episode("i1", state="mitigating"), T0 + timedelta(days=9), 24)


def test_an_incident_counts_against_the_change_whose_release_had_just_gone_live():
    rec = release(at=T0)
    assert respond.caused_by(rec, T0 + timedelta(hours=3), 24) == "c1"
    assert respond.caused_by(rec, T0 + timedelta(hours=25), 24) is None
    assert respond.caused_by(rec, T0 - timedelta(minutes=1), 24) is None, "it began before that release"
    assert respond.caused_by(None, T0, 24) is None


def test_actions_are_counted_across_incidents_in_the_last_hour():
    recs = [
        Rec("respond", T0 - timedelta(minutes=70), data={"ran": True}),
        Rec("respond", T0 - timedelta(minutes=30), data={"ran": True, "incident": "a"}),
        Rec("respond", T0 - timedelta(minutes=10), data={"ran": True, "incident": "b"}),
        Rec("respond", T0 - timedelta(minutes=5), data={"ask": True}),  # proposed, not run
    ]
    assert respond.actions_since(recs, T0 - timedelta(hours=1)) == 2
