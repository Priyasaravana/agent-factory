"""Run and maintain a delivered product (roadmap J4), the pure part.

After delivery the factory keeps watching a product: its liveness path and its request counters
are read on a schedule, the readings are judged against the product's **availability target**, and
a breach is the **alert** that opens an **incident** in the product's work ledger. Its data is
**backed up** on a schedule and the backup is proved by a **restore drill**: a backup that has
never been restored doesn't count. Dependencies and the live image are checked again on a schedule
(**upkeep**), so a new CVE or a dependency that fell behind becomes a work item, and a change.

Everything here decides and nothing here acts: `engine/operations.py` probes, runs the pack's
commands and writes the ledger. The engine judges; no agent is asked whether a product is healthy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# ------------------------------------------------------------ availability --
MIN_SUCCESS, MAX_SUCCESS = 0.5, 0.9999


@dataclass(frozen=True)
class Target:
    """What "available" means for a product: at least `success` of the liveness checks in the last
    `window_minutes` answer, judged once `min_samples` were taken. `max_error_rate`: the share of
    requests the app itself answered with a server error in that window (None: not judged)."""

    success: float = 0.99
    window_minutes: int = 30
    min_samples: int = 5
    max_error_rate: float | None = 0.05

    def text(self) -> str:
        t = f"{share(self.success)} of health checks over {self.window_minutes} minutes"
        if self.max_error_rate is not None:
            t += f", at most {self.max_error_rate:.0%} of requests failing"
        return t


def share(x: float) -> str:
    """A share as a person writes it: 99%, 99.5%, 99.95%. Pure."""
    return f"{x * 100:.2f}".rstrip("0").rstrip(".") + "%"


def tighten(default: Target, success: float | None, max_error_rate: float | None) -> tuple[Target, list[str]]:
    """The product's own target: it may tighten the pack's default, never loosen it. Returns the
    target and why a value was refused. Pure."""
    problems: list[str] = []
    out = default
    if success is not None:
        if not MIN_SUCCESS <= success <= MAX_SUCCESS:
            problems.append(f"the target is a share between {MIN_SUCCESS} and {MAX_SUCCESS}")
        elif success < default.success:
            problems.append(f"a product may tighten its stack's target ({default.success}), not loosen it")
        else:
            out = Target(success, out.window_minutes, out.min_samples, out.max_error_rate)
    if max_error_rate is not None:
        if not 0 <= max_error_rate <= 1:
            problems.append("the error rate is a share between 0 and 1")
        elif default.max_error_rate is not None and max_error_rate > default.max_error_rate:
            problems.append(f"a product may tighten its stack's error rate ({default.max_error_rate}), not loosen it")
        else:
            out = Target(out.success, out.window_minutes, out.min_samples, max_error_rate)
    return out, problems


@dataclass(frozen=True)
class Sample:
    """One reading of a live product: did the liveness path answer, and its request counters."""

    at: datetime
    ok: bool
    detail: str = ""  # why it failed, shortened; never a response body
    requests: float | None = None  # the app's request counter (None: metrics not read)
    errors: float | None = None  # of those, answered with a server error


@dataclass
class Availability:
    """What the readings in the window say. `state`: `unknown` (too few readings to judge),
    `ok`, or `breached` (with `reasons`)."""

    state: str
    samples: int = 0
    success: float | None = None
    error_rate: float | None = None
    reasons: list[str] = field(default_factory=list)
    since: datetime | None = None  # the first failed reading of the run of failures that ends the window
    last_at: datetime | None = None

    def as_data(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "samples": self.samples,
            "success": self.success,
            "error_rate": self.error_rate,
            "reasons": self.reasons,
            "since": self.since.isoformat() if self.since else None,
            "last_at": self.last_at.isoformat() if self.last_at else None,
        }


def error_rate(samples: list[Sample]) -> float | None:
    """Server errors over requests across the readings that carried counters, summed between
    neighbours so an app restart anywhere in the window is seen: when either counter went down
    between two readings the app restarted, and the new reading's counts are what happened since
    (what was served between the earlier reading and the restart is unknown and not claimed). None:
    fewer than two such readings, or no requests counted. Pure."""
    counted = [s for s in samples if s.requests is not None and s.errors is not None]
    if len(counted) < 2:  # noqa: PLR2004 - a rate needs two readings
        return None
    requests = errors = 0.0
    for before, after in zip(counted, counted[1:], strict=False):
        r0, e0, r1, e1 = before.requests or 0, before.errors or 0, after.requests or 0, after.errors or 0
        if r1 < r0 or e1 < e0:  # restarted between the two: count from zero
            requests, errors = requests + r1, errors + e1
        else:
            requests, errors = requests + r1 - r0, errors + e1 - e0
    if requests <= 0:
        return None
    return min(1.0, errors / requests)


def evaluate(samples: Iterable[Sample], target: Target, now: datetime) -> Availability:
    """Judge the readings of the last window against the target. Too few readings is `unknown`:
    never a breach, and never proof of health. Pure."""
    start = now - timedelta(minutes=target.window_minutes)
    window = sorted((s for s in samples if start <= s.at <= now), key=lambda s: s.at)
    if not window:
        return Availability("unknown")
    last_at = window[-1].at
    if len(window) < target.min_samples:
        return Availability("unknown", samples=len(window), last_at=last_at)
    success = sum(1 for s in window if s.ok) / len(window)
    rate = error_rate(window)
    reasons: list[str] = []
    if success < target.success:
        failed = [s for s in window if not s.ok]
        why = f": {failed[-1].detail}" if failed and failed[-1].detail else ""
        reasons.append(
            f"{success:.1%} of {len(window)} health checks answered in the last {target.window_minutes} minutes "
            f"(target {share(target.success)}){why}"
        )
    if target.max_error_rate is not None and rate is not None and rate > target.max_error_rate:
        reasons.append(
            f"{rate:.1%} of requests failed with a server error in the last {target.window_minutes} minutes "
            f"(at most {target.max_error_rate:.0%})"
        )
    since = None
    for s in reversed(window):  # the run of failures the window ends with
        if s.ok:
            break
        since = s.at
    return Availability(
        "breached" if reasons else "ok",
        samples=len(window),
        success=success,
        error_rate=rate,
        reasons=reasons,
        since=since or (window[0].at if reasons else None),
        last_at=last_at,
    )


_METRIC = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(\{[^}]*\})?\s+([0-9.eE+-]+|NaN|[+-]?Inf)\s*(?:\d+)?$")
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')
MAX_METRICS_BYTES = 2_000_000


def request_counts(text: str, counter: str, status_label: str = "status") -> tuple[float, float] | None:
    """(requests, server errors) from a Prometheus text exposition: the sum of `counter` over every
    label set, and the part whose `status_label` starts with 5 ("500", "5xx"). None: the counter is
    not there, or the text is too large to be a metrics page. Pure; nothing but numbers is kept."""
    if len(text) > MAX_METRICS_BYTES:
        return None
    total = errors = 0.0
    found = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _METRIC.match(line)
        if not m or m.group(1) != counter:
            continue
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        if value != value or value in (float("inf"), float("-inf")):  # NaN / Inf say nothing
            continue
        found = True
        total += value
        labels = dict(_LABEL.findall(m.group(2) or ""))
        if labels.get(status_label, "").startswith("5"):
            errors += value
    return (total, errors) if found else None


# ---------------------------------------------------------------- incident --
INCIDENT = "incident"  # ledger category of an availability breach


def incident_candidate(a: Availability, target: Target, url: str | None) -> dict[str, Any] | None:
    """What a breach files in the work ledger; None when the product is within its target (or not
    judged yet). One open incident per product: a breach that goes on is seen again, not filed twice."""
    if a.state != "breached":
        return None
    where = f" at {url}" if url else ""
    lines = [f"The product{where} is outside its availability target ({target.text()}).", *a.reasons]
    if a.since:
        lines.append(f"Failing since {a.since:%Y-%m-%d %H:%M} UTC.")
    lines.append(
        "Find the cause from the app's logs and health endpoints, fix it with a failing test first, and redeploy."
    )
    return {"title": "Incident: the product is outside its availability target", "detail": "\n".join(lines)}


# ------------------------------------------------------------------ backup --
@dataclass(frozen=True)
class BackupRecord:
    id: str
    at: datetime
    ok: bool
    size: int = 0
    sha256: str = ""
    fingerprint: str = ""  # what the data looked like when it was taken (the pack's fingerprint command)
    note: str = ""


def backup_due(last_ok: datetime | None, every_hours: float, now: datetime) -> bool:
    return last_ok is None or now - last_ok >= timedelta(hours=every_hours)


def to_prune(backups: list[BackupRecord], keep: int) -> list[BackupRecord]:
    """Backups beyond the newest `keep` good ones; a failed one is always pruned. Pure."""
    good = sorted((b for b in backups if b.ok), key=lambda b: b.at, reverse=True)
    return [b for b in backups if not b.ok] + good[keep:]


def judge_backup(returncode: int, size: int, fingerprint: str | None) -> tuple[bool, str]:
    """A backup counts when the command succeeded, wrote something, and the data could be
    fingerprinted at that moment (so a restore has something to be compared with). Pure."""
    if returncode != 0:
        return False, f"the backup command failed (exit {returncode})"
    if size <= 0:
        return False, "the backup command wrote nothing"
    if not (fingerprint or "").strip():
        return False, "the data couldn't be fingerprinted, so a restore of this backup couldn't be checked"
    return True, f"{size} bytes"


def normal_fingerprint(text: str) -> str:
    """The fingerprint command's output, lines trimmed and sorted, blanks dropped. Pure."""
    return "\n".join(sorted(line.strip() for line in text.splitlines() if line.strip()))


def judge_restore(expected: str, restored: str, ready: bool) -> tuple[bool, str]:
    """A restore is proven when the restored data has the fingerprint recorded with the backup and,
    where the app ran on it, the app reported ready. An empty fingerprint proves nothing. Pure."""
    want, got = normal_fingerprint(expected), normal_fingerprint(restored)
    if not want:
        return False, "the backup has no fingerprint to compare the restore with"
    if not got:
        return False, "the restored data couldn't be read back"
    if want != got:
        a, b = set(want.splitlines()), set(got.splitlines())
        diff = sorted(a ^ b)[:6]
        return False, "the restored data differs from the backup: " + "; ".join(diff)
    if not ready:
        return False, "the data matches, but the app didn't report ready on it"
    return True, f"restored data matches the backup ({len(want.splitlines())} fingerprint lines)"


def restore_due(last_ok: datetime | None, last_backup: datetime | None, every_days: float, now: datetime) -> bool:
    """A drill is due when there is a backup and none was restored within `every_days`."""
    if last_backup is None:
        return False
    return last_ok is None or now - last_ok >= timedelta(days=every_days)


# ------------------------------------------------------------------ upkeep --
UPKEEP = "upkeep"  # ledger category of "dependencies fell behind"
MAX_LISTED = 40


@dataclass(frozen=True)
class Outdated:
    name: str
    current: str
    latest: str
    major: bool  # the newest release is a new major version


def _major(version: str) -> str:
    m = re.match(r"^[^0-9]*([0-9]+)", version or "")
    return m.group(1) if m else ""


def _row(name: Any, current: Any, latest: Any) -> Outdated | None:
    name, current, latest = str(name or "").strip(), str(current or "").strip(), str(latest or "").strip()
    if not name or not latest or current == latest or len(name) > 200:  # noqa: PLR2004 - no package is longer
        return None
    return Outdated(name, current or "?", latest, bool(_major(current)) and _major(current) != _major(latest))


def parse_npm_outdated(text: str) -> list[Outdated] | None:
    """`npm outdated --json`: {name: {current, wanted, latest}}. None: not that format, or no output
    at all (no answer is never "nothing is behind"). Pure."""
    if not (text or "").strip():
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict) or "error" in doc:
        return None
    rows = []
    for name, v in doc.items():
        if not isinstance(v, dict):
            return None
        row = _row(name, v.get("current") or v.get("wanted"), v.get("latest"))
        if row:
            rows.append(row)
    return sorted(rows, key=lambda r: r.name)


def parse_pip_outdated(text: str) -> list[Outdated] | None:
    """`pip list --outdated --format json` / `uv pip list --outdated --format json`:
    [{name, version, latest_version}]. None: not that format, or no output at all. Pure."""
    if not (text or "").strip():
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, list):
        return None
    rows = []
    for v in doc:
        if not isinstance(v, dict):
            return None
        row = _row(v.get("name"), v.get("version"), v.get("latest_version"))
        if row:
            rows.append(row)
    return sorted(rows, key=lambda r: r.name)


OUTDATED_PARSERS = {"npm": parse_npm_outdated, "pip": parse_pip_outdated}


def json_part(text: str) -> str:
    """The JSON document in a command's output: from its first `{` or `[` to the matching end, so a
    tool's own notices before or after it don't spoil the read. Pure."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return text.strip()
    start = min(starts)
    close = "}" if text[start] == "{" else "]"
    end = text.rfind(close)
    return text[start : end + 1] if end > start else text.strip()


def upkeep_candidate(rows: list[Outdated]) -> dict[str, Any] | None:
    """One work item for all dependencies that fell behind (not one each: a product would drown in
    them). None: nothing is behind. Pure."""
    if not rows:
        return None
    majors = [r for r in rows if r.major]
    title = f"{len(rows)} dependenc{'y is' if len(rows) == 1 else 'ies are'} behind"
    if majors:
        title += f" ({len(majors)} by a major version)"
    listed = sorted(rows, key=lambda r: (not r.major, r.name))[:MAX_LISTED]
    lines = [f"{r.name}: {r.current} -> {r.latest}" + (" (major)" if r.major else "") for r in listed]
    if len(rows) > len(listed):
        lines.append(f"and {len(rows) - len(listed)} more")
    lines.append("Update within the current major versions first; take a major version only with its tests passing.")
    return {
        "title": title,
        "detail": "\n".join(lines),
        "severity": "medium" if majors else "low",
        "at": [r.name for r in listed],
    }


def auto_upkeep_pick(candidates: Iterable[Any], scheduled: set[str]) -> tuple[list[Any], list[Any]]:
    """`upkeep_start: auto` (J4): which open items the factory may start a change for by itself, and
    which it leaves for the owner. An item a scheduled change already addressed, and the check still
    finds, had its automatic try: trying again on every upkeep cycle would spend model budget and
    redeploy production for the same failure, without end. `scheduled`: ids of the product's changes
    the factory started on a schedule. Items are anything with `claimed_by`. Pure."""
    picked: list[Any] = []
    left: list[Any] = []
    for it in candidates:
        (left if set(getattr(it, "claimed_by", None) or []) & scheduled else picked).append(it)
    return picked, left


def upkeep_due(last: datetime | None, every_hours: float, now: datetime) -> bool:
    return last is None or now - last >= timedelta(hours=every_hours)


# ------------------------------------------------------------- production --
def production_proof(
    target: str | None, watched: bool, alert_proven: str | None, restore: str | None
) -> tuple[list[str], list[str]]:
    """What the production tier has and still needs (`ready_for`): a declared availability target,
    an alert on it that is running, and a restore that was proven. Pure. Returns (proven, needs)."""
    proven: list[str] = []
    needs: list[str] = []
    if target:
        proven.append(f"availability target: {target}")
    else:
        needs.append("an availability target for the product")
    if target and watched:
        proven.append("alert on the target: " + (alert_proven or "watching, no breach yet"))
    else:
        needs.append("an alert when it is missed (the factory isn't watching this product)")
    if restore:
        proven.append(f"restore proven ({restore})")
    else:
        needs.append("a tested restore of its data")
    return proven, needs


def fresh(last_at: datetime | None, probe_seconds: float, now: datetime) -> bool:
    """The watch is running for a product: its last reading is no older than three probe intervals
    (and never judged on less than two minutes). Pure."""
    return last_at is not None and (now - last_at).total_seconds() <= max(120.0, 3 * probe_seconds)


def summary_counts(rows: Mapping[str, int]) -> str:
    return ", ".join(f"{n} {k}" for k, n in rows.items() if n) or "nothing changed"
