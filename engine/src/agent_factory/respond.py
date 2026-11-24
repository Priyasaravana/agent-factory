"""Incident response, slice 0 (ADR-0049 §4, amended 2026-11-22): restore service first, the pure part.

An availability breach opens an incident in the work ledger (`ledger.fold_watch`). Before slice 0 the only
way to close it was a full change (design → … → deploy). Restoring service and fixing a root cause are now
separate: the engine **responds** to an open incident like it backs data up, outside any change and with no
model on the path:

    confirm → snapshot first → restore (one step of the ladder) → verify → classify

**The ladder** (each step at most once per incident, at most `max_actions_per_hour` actions per product,
never while a change deploys to it or it is being retired, every action through ADR-0046's fail-closed
checks):

1. the live state differs from the recorded release (`Product.release`: image, digest, replicas) →
   **re-apply** the recorded release (`helm rollback` to its revision, verified like a rollback);
2. the state matches but the app crashes, isn't ready or doesn't answer → **restart** it once;
3. anything further (rollback to an earlier release, a data restore, a dependency that is down) is not in
   slice 0: the incident is **held** for its owner with what was tried.

**Never undo a newer release.** The factory may have been restored from a backup older than the last deploy,
so its record can be stale. A live Helm revision that is neither the recorded one nor the factory's own
re-apply of it ("Rollback to <recorded>") holds the incident as `stale record`, and nothing is applied over it.

**States**, derived from the product's append-only operation records (nothing else is stored):
`open → mitigating → restored → resolved`, and `reopened` when the same breach comes back before it was
resolved (the ladder then goes on from the step it reached). **Restored**: `restored_after` healthy readings in
a row since the last action (the availability window since delivery still holds the outage's readings, so it
can't say). **Resolved**: restored and no recurrence for `resolve_after_hours`, or the window found the
product within its target with nothing applied (it recovered by itself), or the fix change was delivered.

**DORA**: time to detect = first bad reading → alert; time to restore = first bad reading → restored. An
incident is linked to the change whose release was live when it began, if that release was delivered within
`incident_change_hours` before the first bad reading (it counts against that change).

Everything here decides and nothing here acts: `engine/operations.py` reads the live state, runs the step and
appends the records.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from agent_factory import rollback
from agent_factory.models import Release

Mode = Literal["auto", "ask", "off"]
Action = Literal["reapply", "restart"]
LADDER: tuple[Action, ...] = ("reapply", "restart")
STATES = ("open", "mitigating", "restored", "resolved", "reopened")

# operation record kinds this module reads (the watch's `alert` and `recovered` are J4's)
ALERT, RESPOND, RESTORED, RESOLVED, RECOVERED = "alert", "respond", "restored", "resolved", "recovered"

ACTION_TEXT: dict[str, str] = {
    "reapply": "re-apply the recorded release",
    "restart": "restart the app",
}


# --------------------------------------------------------------- the mode --
def effective_mode(configured: Mode, drill_passed: bool, reconciled: bool) -> tuple[Mode, str]:
    """The mode responses run in, and why it isn't the configured one. Pure.

    `auto` is taken only on an install whose factory restore drill has passed (stations-and-teams plan,
    "Factory continuity": tested before automation), and, after the factory was restored from a backup, only
    for a product whose recorded release a reading has matched with the live one since."""
    if configured != "auto":
        return configured, ""
    if not drill_passed:
        return "ask", "automatic response waits until a factory restore drill has passed on this install"
    if not reconciled:
        return "ask", (
            "the factory was restored from a backup: its record of this product's release hasn't been "
            "matched with the live one yet"
        )
    return "auto", ""


# ------------------------------------------------------------- live state --
@dataclass(frozen=True)
class Workload:
    """A Deployment or StatefulSet of the release: the replicas its spec asks for and how many are ready."""

    name: str  # Kind/name
    replicas: int | None
    ready: int


@dataclass(frozen=True)
class Live:
    """What the cluster says the product's release runs now, read-only (`HelmRelease.state`). `error`: it
    couldn't be read, so nothing is decided from it (fail closed)."""

    revision: int | None = None  # Helm's newest revision
    description: str = ""  # of the newest revision ("Upgrade complete", "Rollback to 3")
    status: str = ""  # of the newest revision
    image: str | None = None  # repository:tag the release's values name now
    digests: list[str | None] = field(default_factory=list)  # one per running container of the image
    workloads: dict[str, Workload] = field(default_factory=dict)
    expected: dict[str, int] = field(default_factory=dict)  # replicas the release's manifest asks for
    pods: int = 0  # the release's pods that are not being deleted
    not_ready: int = 0  # of those, with a container that isn't ready
    crashing: list[str] = field(default_factory=list)  # waiting reasons (CrashLoopBackOff, ImagePullBackOff)
    restarts: int = 0  # container restarts across the release's pods
    error: str = ""

    def as_data(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "description": self.description,
            "status": self.status,
            "image": self.image,
            "digests": sorted({d or "none" for d in self.digests}),
            "workloads": {k: {"replicas": w.replicas, "ready": w.ready} for k, w in self.workloads.items()},
            "expected": self.expected,
            "pods": self.pods,
            "not_ready": self.not_ready,
            "crashing": self.crashing,
            "restarts": self.restarts,
            "error": self.error,
        }


_KINDS = ("Deployment", "StatefulSet")
_DOC_SPLIT = re.compile(r"^---\s*$", re.MULTILINE)
_KIND = re.compile(r"^kind:\s*(\w+)\s*$", re.MULTILINE)
_NAME = re.compile(r"^metadata:\s*\n(?:[ \t]+.*\n)*?[ \t]+name:\s*['\"]?([\w.-]+)['\"]?\s*$", re.MULTILINE)
_REPLICAS = re.compile(r"^spec:\s*\n(?:[ \t]+.*\n)*?[ \t]{2}replicas:\s*(\d+)\s*$", re.MULTILINE)


def manifest_replicas(manifest: str) -> dict[str, int]:
    """`Kind/name` → replicas for every Deployment and StatefulSet in `helm get manifest` output that states
    them (a workload whose replicas an autoscaler owns states none, and is not compared). Pure."""
    out: dict[str, int] = {}
    for doc in _DOC_SPLIT.split(manifest or ""):
        kind = _KIND.search(doc)
        if not kind or kind.group(1) not in _KINDS:
            continue
        name, replicas = _NAME.search(doc), _REPLICAS.search(doc + "\n")
        if name and replicas:
            out[f"{kind.group(1)}/{name.group(1)}"] = int(replicas.group(1))
    return out


def workloads(text: str) -> dict[str, Workload]:
    """Lines of `Kind/name=<spec.replicas>=<status.readyReplicas>` (the provider's jsonpath). Pure."""
    out: dict[str, Workload] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split("=")
        if len(parts) != 3 or "/" not in parts[0]:  # noqa: PLR2004 - name, spec, ready
            continue
        name, spec, ready = parts
        out[name] = Workload(name, int(spec) if spec.isdigit() else None, int(ready) if ready.isdigit() else 0)
    return out


# per pod: deletion timestamp|each container as ready=restartCount=waitingReason
POD_HEALTH_JSONPATH = (
    "{range .items[*]}{.metadata.deletionTimestamp}|"
    "{range .status.containerStatuses[*]}{.ready}={.restartCount}={.state.waiting.reason} {end}{'\\n'}{end}"
)
WORKLOADS_JSONPATH = "{range .items[*]}{.kind}/{.metadata.name}={.spec.replicas}={.status.readyReplicas}{'\\n'}{end}"
CRASH_REASONS = frozenset(
    {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "RunContainerError"}
)


def pod_health(text: str) -> tuple[int, int, list[str], int]:
    """(pods, not ready, crash reasons, restarts) from lines of `<deletion>|ready=restarts=reason …`. Pods being
    deleted are skipped. Pure."""
    pods = not_ready = restarts = 0
    reasons: list[str] = []
    for line in (text or "").splitlines():
        if "|" not in line:
            continue
        deleting, containers = line.split("|", 1)
        if deleting.strip():
            continue
        pods += 1
        bad = False
        for entry in containers.split():
            ready, _, rest = entry.partition("=")
            count, _, reason = rest.partition("=")
            restarts += int(count) if count.isdigit() else 0
            if ready != "true":
                bad = True
            if reason in CRASH_REASONS and reason not in reasons:
                reasons.append(reason)
        not_ready += bad
    return pods, not_ready, reasons, restarts


# ---------------------------------------------------------------- compare --
def stale_record(recorded: Release, live: Live) -> str:
    """Why the factory's record of the release can't be trusted against the live one, or "". Pure.

    Helm's newest revision must be the recorded one, or the factory's own re-apply of it ("Rollback to <n>").
    Anything else (a newer upgrade, a release installed again with a fresh history) means someone, or a newer
    factory, released something the record doesn't know: re-applying would undo it."""
    if live.revision is None:
        return "the live release history couldn't be read"
    if live.revision == recorded.revision:
        return ""
    if live.revision > (recorded.revision or 0) and live.description == f"Rollback to {recorded.revision}":
        return ""
    return (
        f"the live release is at revision {live.revision} ('{live.description or live.status}'), "
        f"which the factory's record (revision {recorded.revision}, {recorded.image}) doesn't know"
    )


def drift(recorded: Release, live: Live) -> list[str]:
    """How the live state differs from the recorded release: image, digest, replicas. [] when it matches. Pure."""
    out: list[str] = []
    if live.image != recorded.image:
        out.append(f"the release names image {live.image or 'none'}, not the recorded {recorded.image}")
    running = {d for d in live.digests}
    if not live.digests:
        out.append(f"no container of {rollback.repository(recorded.image)} is running")
    elif running != {recorded.digest}:
        found = ", ".join(sorted(d or "no digest" for d in running))
        out.append(f"running containers report {found}, not the recorded {recorded.digest}")
    for name, want in sorted(live.expected.items()):
        got = live.workloads.get(name)
        if got is None:
            out.append(f"{name} is missing")
        elif got.replicas != want:
            out.append(f"{name} runs {got.replicas if got.replicas is not None else 'no'} replicas, not {want}")
    return out


def unhealthy(live: Live) -> list[str]:
    """What looks wrong with pods that do run the recorded release (for the snapshot and the record). Pure."""
    out: list[str] = []
    if live.crashing:
        out.append("containers waiting: " + ", ".join(live.crashing))
    if live.not_ready:
        out.append(f"{live.not_ready} of {live.pods} pods not ready")
    return out


# ----------------------------------------------------------------- ladder --
@dataclass(frozen=True)
class Step:
    """What to do for an open incident now. `action`: the ladder's step to run (or propose, in ask mode);
    None with `hold`: nothing automatic is left or allowed, a person decides; None without: wait."""

    action: Action | None
    reason: str
    hold: str = ""  # stale_record | ladder_used | rate_limited | unreadable | ""
    ask: bool = False  # the step is proposed to the owner instead of run


def next_step(  # noqa: PLR0911, PLR0913 - one rung per branch; the inputs the plan names
    recorded: Release | None,
    live: Live,
    tried: Iterable[str],
    actions_last_hour: int,
    max_per_hour: int,
    mode: Mode,
) -> Step:
    """The ladder's next step for an open incident. Pure."""
    if mode == "off":
        return Step(None, "incident response is off (operate.respond: off)")
    if recorded is None:
        return Step(None, "the product has no recorded release to restore", hold="unreadable")
    if live.error:
        return Step(None, f"the live state couldn't be read, so nothing is applied: {live.error}", hold="unreadable")
    if stale := stale_record(recorded, live):
        return Step(None, stale + "; nothing is applied over it, the owner decides", hold="stale_record")
    done = set(tried)
    if actions_last_hour >= max_per_hour:
        return Step(None, f"{actions_last_hour} actions on this product in the last hour (at most {max_per_hour})",
                    hold="rate_limited")  # fmt: skip
    differs = drift(recorded, live)
    if differs and "reapply" not in done:
        return Step("reapply", "; ".join(differs), ask=mode == "ask")
    if "restart" not in done:
        why = "; ".join(differs) if differs else ("; ".join(unhealthy(live)) or "the app doesn't answer")
        prefix = "re-applying didn't restore it" if "reapply" in done else "the live state matches the recorded release"
        return Step("restart", f"{prefix}: {why}", ask=mode == "ask")
    tried_text = " and ".join(ACTION_TEXT[t] for t in LADDER if t in done)
    skipped = (
        ""
        if "reapply" in done
        else " (the live state matches the recorded release, so re-applying it would change nothing)"
    )
    return Step(
        None,
        f"{tried_text[0].upper()}{tried_text[1:]}: tried, and service isn't restored{skipped}; rolling back to an "
        "earlier release, a data restore or a fix change is the owner's call",
        hold="ladder_used",
    )


# -------------------------------------------------------------- the state --
@dataclass
class Episode:
    """One incident's response, from its operation records since it was last resolved."""

    incident: str
    state: str = "open"
    opened_at: datetime | None = None  # the first alert of the episode
    first_bad_at: datetime | None = None
    tried: list[str] = field(default_factory=list)  # ladder steps run, in order
    failed: list[str] = field(default_factory=list)  # steps that ran and reported failure
    last_result: str = ""  # the newest step's summary
    last_ok: bool = True  # the newest step reported success
    last_detail: str = ""  # what its command said when it failed (redacted)
    last_action_at: datetime | None = None
    restored_at: datetime | None = None
    restored_by: str = ""  # the step whose effect restored it
    resolved_at: datetime | None = None
    held: str = ""  # the latest hold reason while nothing could be applied
    proposed: str = ""  # the step the owner is asked to run (ask mode)
    caused_by: str | None = None  # the change it counts against
    reopened: int = 0

    def timings(self) -> dict[str, float | None]:
        """DORA: seconds from the first bad reading to the alert, and to restored service. Pure."""
        start = self.first_bad_at or self.opened_at
        detect = (self.opened_at - start).total_seconds() if start and self.opened_at else None
        restore = (self.restored_at - start).total_seconds() if start and self.restored_at else None
        return {"time_to_detect_s": detect, "time_to_restore_s": restore}


def _incident_of(rec: Any) -> str:
    return str((rec.data or {}).get("incident") or "")


def episode(records: Iterable[Any], incident: str, carry_hours: float | None = None) -> Episode | None:
    """The current episode of `incident` from operation records (any order; each has kind, at, ok, data), or
    None when no alert opened it. A `resolved` record ends an episode; the next alert starts a new one, unless it
    comes within `carry_hours` of the resolution: then the same breach is back, the episode is `reopened` and the
    ladder goes on from the step it reached. Pure."""
    # records written in one pass share a time: the alert comes before the step it led to
    order = {ALERT: 0, RESPOND: 1, RESTORED: 2, RESOLVED: 3, RECOVERED: 3}
    mine = sorted((r for r in records if _incident_of(r) == incident), key=lambda r: (r.at, order.get(r.kind, 1)))
    current: Episode | None = None
    for r in mine:
        data = r.data or {}
        if r.kind == ALERT:
            back = (
                current is not None
                and current.state == "resolved"
                and carry_hours is not None
                and current.resolved_at is not None
                and r.at - current.resolved_at <= timedelta(hours=carry_hours)
            )
            if back and current is not None:
                current.state, current.reopened = "reopened", current.reopened + 1
                current.restored_at, current.resolved_at, current.restored_by = None, None, ""
                current.held, current.proposed = "", ""
            elif current is None or current.state == "resolved":
                current = Episode(incident, opened_at=r.at)
                current.first_bad_at = _when(data.get("first_bad_at")) or r.at
                current.caused_by = data.get("caused_by") or None
            elif current.state in ("restored", "mitigating"):
                current.state, current.reopened = "reopened", current.reopened + 1
                current.restored_at, current.restored_by, current.held, current.proposed = None, "", "", ""
            continue
        if current is None or current.state == "resolved":
            continue
        if r.kind == RESPOND:
            step = str(data.get("step") or "")
            if data.get("ran") and step:
                current.tried.append(step)
                if not r.ok:
                    current.failed.append(step)
                current.last_action_at, current.last_result = r.at, str(getattr(r, "summary", "") or "")
                current.last_ok, current.last_detail = bool(r.ok), str(data.get("detail") or "")
                current.state, current.held, current.proposed = "mitigating", "", ""
            elif data.get("hold"):
                current.held, current.proposed = str(data.get("reason") or data["hold"]), ""
            elif data.get("ask") and step:
                current.proposed = step
        elif r.kind == RESTORED:
            current.state, current.restored_at = "restored", r.at
            current.restored_by = str(data["by_step"]) if "by_step" in data else credited(current)
        elif r.kind in (RESOLVED, RECOVERED):
            current.state, current.resolved_at = "resolved", r.at
    return current


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def credited(ep: Episode) -> str:
    """The step that restored service: the newest one, if it reported success. A failed step that service
    came back after is not credited (a person, or Kubernetes, may have restored it). Pure."""
    if ep.tried and ep.last_ok:
        return ep.tried[-1]
    return ""


def restored_at(samples: Iterable[Any], since: datetime, k: int) -> datetime | None:
    """The reading that made `k` healthy readings in a row after `since` (the last action), or None. A failed
    reading starts the count again. Pure."""
    run = 0
    for s in sorted((s for s in samples if s.at > since), key=lambda s: s.at):
        run = run + 1 if s.ok else 0
        if run >= k:
            return s.at
    return None


def resolve_due(ep: Episode, now: datetime, quiet_hours: float) -> bool:
    """Restored and no recurrence for `quiet_hours`: the incident is resolved (operational). Pure."""
    return (
        ep.state == "restored" and ep.restored_at is not None and now - ep.restored_at >= timedelta(hours=quiet_hours)
    )


def classify(ep: Episode) -> str:
    """What restored service says about the cause (slice 0: the engine proves, nothing is guessed). Pure.

    Re-applying the recorded release restored it: the live state had drifted (operational). A restart restored
    it: the release itself was unchanged (operational, transient). Proving a code defect needs a restart that
    didn't hold and a rollback that did, which the next slice adds."""
    if ep.restored_by == "reapply":
        return "operational: the live state had drifted from the recorded release; re-applying it restored service"
    if ep.restored_by == "restart":
        return "operational: the release was unchanged and a restart restored service"
    if ep.tried:
        return (
            "service came back after the factory's last step failed: something outside the factory restored it "
            "(a person, or Kubernetes); check the snapshot and the step's output"
        )
    return "recovered without an action"


def caused_by(release: Release | None, first_bad: datetime | None, window_hours: float) -> str | None:
    """The change the incident counts against: the one whose release was live when it began, delivered within
    `window_hours` before the first bad reading. Pure."""
    if release is None or first_bad is None:
        return None
    if release.at <= first_bad <= release.at + timedelta(hours=window_hours):
        return release.change_id
    return None


def actions_since(records: Iterable[Any], since: datetime) -> int:
    """Ladder steps the factory ran on a product since `since` (every incident). Pure."""
    return sum(1 for r in records if r.kind == RESPOND and (r.data or {}).get("ran") and r.at >= since)


def view(ep: Episode | None) -> Mapping[str, Any]:
    """An episode for the product page. Pure."""
    if ep is None:
        return {}
    return {
        "incident": ep.incident,
        "state": ep.state,
        "opened_at": ep.opened_at,
        "first_bad_at": ep.first_bad_at,
        "tried": list(ep.tried),
        "failed": list(ep.failed),
        "last_result": ep.last_result,
        "last_ok": ep.last_ok,
        "last_detail": ep.last_detail,
        "last_action_at": ep.last_action_at,
        "restored_at": ep.restored_at,
        "restored_by": ep.restored_by,
        "resolved_at": ep.resolved_at,
        "held": ep.held,
        "proposed": ep.proposed,
        "caused_by": ep.caused_by,
        "reopened": ep.reopened,
        "classification": classify(ep) if ep.state in ("restored", "resolved") else "",
        **ep.timings(),
    }
