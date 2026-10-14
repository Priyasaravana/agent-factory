"""The product work ledger (ADR-0040, pure): what was found about a product, across iterations.

Every assessment reports issues, risks, test gaps, recommendations and coverage gaps. The
ledger keeps one item per thing found, recognised again in later assessments by its
fingerprint, and follows it through its life:

    open → in_progress (a change addresses it) → fixed (a later assessment no longer finds it)
                                               → open (still present after the change)
    open → wont_do (a person decided, with a reason) | accepted_risk (until a date)
    fixed → open (reappeared)

Only what was really checked can be verified: an issue is fixed only when the check that
found it ran again with a real tool (or the factory rules) and didn't find it. A risk, test gap
or recommendation is the assessor's judgement, so its absence counts only after a change
addressed it, and the history says so. Nothing here does IO or calls a model.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from agent_factory.models import WorkItem, WorkItemEvent
from agent_factory.rollup import rank

MEASURED = {"issue", "coverage_gap"}  # verified by a check; the rest is the assessor's judgement
OPEN = {"open", "in_progress"}
_SEV = {"high": "high", "medium": "medium", "low": "low", "critical": "critical", "info": "info"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) > 2}


def fingerprint(kind: str, title: str, key: str = "") -> str:
    return f"{kind}:{_norm(key) or _norm(title)}"


def item_id(product_id: str, fp: str) -> str:
    return "wi_" + hashlib.sha256(f"{product_id}|{fp}".encode()).hexdigest()[:10]


def found_items(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    """What an assessment (assessment.json) reports, as ledger candidates."""
    out: list[dict[str, Any]] = []
    issue_fp: dict[str, str] = {}
    for i in assessment.get("issues") or []:
        fp = fingerprint("issue", i["title"])
        issue_fp[i["id"]] = fp
        kind = "bug" if i.get("category") in ("secrets", "code") else "upkeep"
        out.append(
            {
                "kind": "issue",
                "fingerprint": fp,
                "title": i["title"],
                "severity": i["severity"],
                "category": i.get("category", ""),
                "detail": i.get("detail", ""),
                "at": i.get("at", []),
                "sources": i.get("sources", []),
                "change_kind": kind,
            }
        )
    for r in assessment.get("risks") or []:
        out.append(
            {
                "kind": "risk",
                "fingerprint": fingerprint("risk", r.get("title", "")),
                "title": r.get("title", ""),
                "severity": _SEV.get(str(r.get("severity")), "medium"),
                "category": r.get("pillar", ""),
                "detail": r.get("detail", ""),
                "at": list(r.get("files") or []),
                "sources": [{"station": "assess", "via": "assessor", "rule": ""}],
                "change_kind": "bug",
            }
        )
    for g in assessment.get("test_gaps") or []:
        out.append(
            {
                "kind": "test_gap",
                "fingerprint": fingerprint("test_gap", g.get("area", "")),
                "title": f"Test gap: {g.get('area', '')}",
                "severity": "medium",
                "category": "tests",
                "detail": g.get("why", ""),
                "at": list(g.get("files") or []),
                "sources": [{"station": "assess", "via": "assessor", "rule": ""}],
                "change_kind": "upkeep",
            }
        )
    for r in assessment.get("recommendations") or []:
        related = [issue_fp[x] for x in r.get("issues") or [] if x in issue_fp]
        out.append(
            {
                "kind": "recommendation",
                "fingerprint": fingerprint("recommendation", r.get("title", "")),
                "title": r.get("title", ""),
                "severity": _SEV.get(str(r.get("severity")), "medium"),
                "category": r.get("basis", ""),
                "detail": r.get("why", ""),
                "at": [],
                "sources": [{"station": "report", "via": r.get("source", "assessor"), "rule": ""}],
                "change_kind": r.get("kind", "upkeep"),
                "effort": r.get("effort", ""),
                "related_fps": related,
            }
        )
    for c in (assessment.get("coverage") or {}).get("checks") or []:
        if c.get("status") != "tool":
            out.append(
                {
                    "kind": "coverage_gap",
                    "fingerprint": fingerprint("coverage_gap", "", c.get("station", "")),
                    "title": f"{c.get('title')}: {c.get('status')}",
                    "severity": "medium",
                    "category": c.get("check", ""),
                    "detail": c.get("note", ""),
                    "at": [],
                    "sources": [{"station": c.get("station", ""), "via": c.get("via", ""), "rule": ""}],
                    "change_kind": "upkeep",
                }
            )
    return out


def checked(assessment: dict[str, Any]) -> set[str]:
    """Issue categories this assessment really checked: the factory rules always, and each scan
    check answered by a tool (not simulated, not the agent, not "nothing to scan")."""
    done = {"rules:*"}
    for c in (assessment.get("coverage") or {}).get("checks") or []:
        if c.get("status") == "tool":
            done.add(str(c.get("check")))
    return done


def _is_checked(item: WorkItem, done: set[str]) -> bool:
    if item.kind == "coverage_gap":
        return True
    if item.category.startswith("rules:"):
        return "rules:*" in done
    return item.category in done


def _similar(a: str, b: str) -> bool:
    """The assessor rewords its judgement between runs: same item if most words match."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


def _match(items: list[WorkItem], cand: dict[str, Any], taken: set[str]) -> WorkItem | None:
    for it in items:
        if it.id not in taken and it.fingerprint == cand["fingerprint"]:
            return it
    if cand["kind"] in MEASURED:
        return None
    for it in items:
        if it.id not in taken and it.kind == cand["kind"] and _similar(it.title, cand["title"]):
            return it
    return None


def _log(
    item: WorkItem,
    now: datetime,
    event: str,
    note: str = "",
    *,
    iteration: int | None = None,
    change_id: str | None = None,
    by: str | None = None,
) -> None:
    item.history.append(WorkItemEvent(at=now, iteration=iteration, change_id=change_id, event=event, note=note, by=by))
    item.updated_at = now


def reconcile(
    product_id: str,
    items: list[WorkItem],
    assessment: dict[str, Any],
    *,
    change_id: str,
    iteration: int,
    now: datetime,
    delivered: Iterable[str] = (),
) -> list[WorkItem]:
    """Fold one assessment into the ledger. `delivered` lists the changes that finished (their
    PR opened) before this assessment: an item they addressed and that is gone is fixed; one
    still found is open again."""
    done_changes = set(delivered)
    done = checked(assessment)
    by_id = {it.id: it for it in items}
    taken: set[str] = set()
    fp_to_id: dict[str, str] = {}
    related_fps: dict[str, list[str]] = {}
    for cand in found_items(assessment):
        it = _match(items, cand, taken)
        if it is None:
            it = WorkItem(
                id=item_id(product_id, cand["fingerprint"]),
                product_id=product_id,
                kind=cand["kind"],
                fingerprint=cand["fingerprint"],
                title=cand["title"],
                first_iteration=iteration,
                created_at=now,
                updated_at=now,
            )
            while it.id in by_id:  # two fingerprints with one hash prefix: keep both
                it.id = it.id + "x"
            by_id[it.id] = it
            items.append(it)
            _log(it, now, "found", iteration=iteration, change_id=change_id)
        else:
            addressed = [c for c in it.claimed_by if c in done_changes]
            if it.status == "fixed":
                it.status = "open"
                it.resolved_by = None
                _log(
                    it,
                    now,
                    "reappeared",
                    f"found again in iteration {iteration}",
                    iteration=iteration,
                    change_id=change_id,
                )
            elif it.status == "in_progress" and addressed:
                it.status = "open"
                _log(
                    it,
                    now,
                    "still-present",
                    f"still found after change {', '.join(addressed)}",
                    iteration=iteration,
                    change_id=change_id,
                )
            elif it.status == "accepted_risk" and it.accepted_until and it.accepted_until < now:
                it.status = "open"
                _log(it, now, "reopened", "risk acceptance expired", iteration=iteration, change_id=change_id)
            else:
                _log(it, now, "seen", iteration=iteration, change_id=change_id)
        taken.add(it.id)
        fp_to_id[cand["fingerprint"]] = it.id
        it.title = cand["title"] or it.title
        it.severity, it.category, it.detail = cand["severity"], cand["category"], cand["detail"]
        it.at, it.sources = list(cand["at"])[:20], list(cand["sources"])
        it.change_kind = cand.get("change_kind", it.change_kind)
        it.effort = cand.get("effort", it.effort)
        it.last_iteration = iteration
        if iteration not in it.seen_in:
            it.seen_in.append(iteration)
        if "related_fps" in cand:
            related_fps[it.id] = cand["related_fps"]
    for it in items:  # recommendations point at the issue items behind them
        if it.id in related_fps:
            it.related = [fp_to_id[f] for f in related_fps[it.id] if f in fp_to_id]
    for it in items:
        if it.id in taken or it.status not in OPEN:
            continue
        addressed = [c for c in it.claimed_by if c in done_changes]
        if it.kind in MEASURED and _is_checked(it, done):
            it.status, it.resolved_by = "fixed", change_id
            why = f"after change {', '.join(addressed)}" if addressed else "no longer found"
            _log(
                it,
                now,
                "fixed",
                f"not found in iteration {iteration} ({why})",
                iteration=iteration,
                change_id=change_id,
            )
        elif it.kind not in MEASURED and addressed:
            it.status, it.resolved_by = "fixed", change_id
            _log(
                it,
                now,
                "fixed",
                f"not reported again by the assessor after change {', '.join(addressed)}",
                iteration=iteration,
                change_id=change_id,
            )
    return sorted(items, key=sort_key)


def sort_key(it: WorkItem) -> tuple[int, int, int, str]:
    status = {"open": 0, "in_progress": 1, "accepted_risk": 2, "wont_do": 3, "fixed": 4}[it.status]
    kind = ["issue", "risk", "recommendation", "test_gap", "coverage_gap"].index(it.kind)
    return (status, rank(it.severity), kind, it.title)


def claim(
    items: list[WorkItem], ids: list[str], *, change_id: str, iteration: int, now: datetime, by: str | None
) -> list[WorkItem]:
    """A change addresses these items: they are in progress until verified. Raises ValueError
    for unknown or closed items."""
    by_id = {it.id: it for it in items}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError(f"unknown work items: {', '.join(unknown)}")
    closed = [i for i in ids if by_id[i].status in ("fixed", "wont_do")]
    if closed:
        raise ValueError(f"reopen these first: {', '.join(closed)}")
    out = []
    for i in dict.fromkeys(ids):
        it = by_id[i]
        it.status = "in_progress"
        if change_id not in it.claimed_by:
            it.claimed_by.append(change_id)
        _log(it, now, "claimed", f"addressed by change {change_id}", iteration=iteration, change_id=change_id, by=by)
        out.append(it)
    return out


def release(items: list[WorkItem], *, change_id: str, status: str, now: datetime, pr_url: str | None) -> list[WorkItem]:
    """A change that addressed items stopped: a delivered one leaves them in progress until the
    next assessment verifies them; a failed or cancelled one returns them to open."""
    changed = []
    for it in items:
        if change_id not in it.claimed_by or it.status != "in_progress":
            continue
        if status in ("awaiting_feedback", "delivered"):
            _log(
                it,
                now,
                "proposed",
                f"pull request: {pr_url}" if pr_url else "change delivered; assess again to verify",
                change_id=change_id,
            )
        else:
            it.status = "open"
            it.claimed_by.remove(change_id)
            _log(it, now, "released", f"change {change_id} stopped ({status})", change_id=change_id)
        changed.append(it)
    return changed


def decide(
    items: list[WorkItem],
    ids: list[str],
    decision: str,
    *,
    reason: str,
    by: str | None,
    now: datetime,
    until: datetime | None = None,
) -> list[WorkItem]:
    """A person's decision: won't do, accept the risk (until a date), or reopen."""
    by_id = {it.id: it for it in items}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError(f"unknown work items: {', '.join(unknown)}")
    out = []
    for i in dict.fromkeys(ids):
        it = by_id[i]
        if decision == "reopen":
            it.status, it.decision_reason, it.decision_by, it.accepted_until = "open", "", None, None
            _log(it, now, "reopened", reason, by=by)
        else:
            if it.status == "in_progress":
                raise ValueError(f"{i} is being addressed by a change ({', '.join(it.claimed_by)})")
            it.status = decision  # type: ignore[assignment]
            it.decision_reason, it.decision_by = reason.strip(), by
            it.accepted_until = until if decision == "accepted_risk" else None
            note = reason.strip() + (f" (until {until:%Y-%m-%d})" if until and decision == "accepted_risk" else "")
            _log(it, now, "wont-do" if decision == "wont_do" else "accepted", note, by=by)
        out.append(it)
    return out


def request_text(text: str, items: list[WorkItem]) -> str:
    """The change request with the items it addresses spelled out for the developer and the PR."""
    lines = [text.strip(), "", "Work items to address:"]
    for it in items:
        where = f" ({', '.join(it.at[:5])})" if it.at else ""
        lines.append(f"- [{it.id}] {it.severity} · {it.title}{where}")
        if it.detail:
            lines.append(f"  {it.detail[:400]}")
    return "\n".join(lines)


def counts(items: list[WorkItem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it.status] = out.get(it.status, 0) + 1
    return out
