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
    next assessment verifies them (a suggested feature is done: the change's acceptance verified it);
    a failed or cancelled one returns them to open."""
    changed = []
    for it in items:
        if change_id not in it.claimed_by or it.status != "in_progress":
            continue
        if status in ("awaiting_feedback", "delivered") and it.category == FEATURE:
            # a feature is verified by the change's own acceptance (hidden scenarios on the live app),
            # not by a later assessment
            it.status, it.resolved_by = "fixed", change_id
            _log(it, now, "fixed", "delivered and accepted by change " + change_id, change_id=change_id)
        elif status in ("awaiting_feedback", "delivered"):
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


IMAGE = "image"  # category of the Build station's image findings (ADR-0042)


def image_fingerprint(key: str) -> str:
    return fingerprint("issue", "", key)


def accepted_image_keys(items: list[WorkItem], keys: Iterable[str], now: datetime) -> set[str]:
    """Finding keys whose risk a person accepted and that haven't expired (ADR-0042)."""
    ok = {
        it.fingerprint
        for it in items
        if it.category == IMAGE
        and it.status == "accepted_risk"
        and (it.accepted_until is None or it.accepted_until > now)
    }
    return {k for k in keys if image_fingerprint(k) in ok}


def adopt_factory_decisions(
    items: list[WorkItem], covered: dict[str, Any], *, now: datetime, change_id: str = ""
) -> list[WorkItem]:
    """Image items a factory-wide decision covers (ADR-0045 §3): `covered` maps a finding key to
    its decision (with `until`, `reason`, `by`). An open item becomes an accepted risk that says so;
    returns the items that changed."""
    changed = []
    by_fp = {image_fingerprint(k): d for k, d in covered.items()}
    for it in items:
        d = by_fp.get(it.fingerprint)
        if it.category != IMAGE or d is None or it.status not in OPEN:
            continue
        it.status, it.accepted_until = "accepted_risk", d.until
        it.decision_reason = f"factory decision for this base image: {d.reason}".strip()
        it.decision_by, it.updated_at = d.by, now
        _log(
            it,
            now,
            "accepted",
            "covered by a factory decision for this base image",
            change_id=change_id or None,
            by=d.by,
        )
        changed.append(it)
    return changed


BASE_IMAGE_UPKEEP = "base-image"  # category of "make this approved image the default" (ADR-0045 §2)


def propose_base_image(
    product_id: str,
    items: list[WorkItem],
    *,
    image: str,
    default: str,
    why: str,
    change_id: str,
    iteration: int,
    now: datetime,
) -> list[WorkItem]:
    """An upkeep item proposing that the pack default to the approved image a build fell back to.
    One per image: seen again, it is updated; returns the items that changed."""
    fp = fingerprint("recommendation", BASE_IMAGE_UPKEEP, image)
    it = next((x for x in items if x.fingerprint == fp), None)
    detail = (
        f"Build fell back from {default} to the approved {image} ({why}). Make {image} the pack's default "
        "runtime (a new pack version), or wait for the default's publisher to ship the fix: later builds "
        "try the default first."
    )
    if it is None:
        it = WorkItem(
            id=item_id(product_id, fp),
            product_id=product_id,
            kind="recommendation",
            fingerprint=fp,
            title=f"pack upkeep: make {image} the default runtime",
            severity="medium",
            category=BASE_IMAGE_UPKEEP,
            change_kind="upkeep",
            first_iteration=iteration,
            created_at=now,
            updated_at=now,
        )
        items.append(it)
        _log(it, now, "found", "a build used this approved fallback", iteration=iteration, change_id=change_id)
    elif it.status not in OPEN:
        it.status = "open"
        _log(
            it, now, "reappeared", "a build used this approved fallback again", iteration=iteration, change_id=change_id
        )
    it.detail, it.last_iteration, it.updated_at = detail, iteration, now
    it.sources = [{"station": "build", "via": "base images", "rule": image}]
    if iteration not in it.seen_in:
        it.seen_in.append(iteration)
    return [it]


def fold_image_scan(
    product_id: str,
    items: list[WorkItem],
    findings: list[dict[str, Any]],
    *,
    change_id: str,
    iteration: int,
    now: datetime,
) -> tuple[list[WorkItem], dict[str, int]]:
    """Fold one image scan into the ledger (ADR-0042). `findings` are candidates with `key`,
    `title`, `severity`, `detail`, `at`, `rule`. The scan is a real check of the whole image, so an
    image item it no longer finds is fixed. Returns the items that changed and what happened."""
    changed: dict[str, WorkItem] = {}
    stats = {"new": 0, "fixed": 0, "reopened": 0}
    by_fp = {it.fingerprint: it for it in items if it.category == IMAGE}
    ids = {it.id for it in items}
    seen: set[str] = set()
    for f in findings:
        fp = image_fingerprint(f["key"])
        seen.add(fp)
        it = by_fp.get(fp)
        if it is None:
            it = WorkItem(
                id=item_id(product_id, fp),
                product_id=product_id,
                kind="issue",
                fingerprint=fp,
                title=f["title"],
                category=IMAGE,
                change_kind="upkeep",
                first_iteration=iteration,
                created_at=now,
                updated_at=now,
            )
            while it.id in ids:
                it.id = it.id + "x"
            ids.add(it.id)
            by_fp[fp] = it
            items.append(it)
            stats["new"] += 1
            _log(it, now, "found", "in the built image", iteration=iteration, change_id=change_id)
        elif it.status == "fixed":
            it.status, it.resolved_by = "open", None
            stats["reopened"] += 1
            _log(it, now, "reappeared", "found again in the built image", iteration=iteration, change_id=change_id)
        elif it.status == "accepted_risk" and it.accepted_until and it.accepted_until <= now:
            it.status = "open"
            stats["reopened"] += 1
            _log(it, now, "reopened", "risk acceptance expired", iteration=iteration, change_id=change_id)
        elif it.status == "in_progress" and change_id in it.claimed_by:
            it.status = "open"  # the change that addresses it built an image that still has it
            stats["reopened"] += 1
            _log(it, now, "still-present", f"still in the image built by change {change_id}", change_id=change_id)
        elif iteration not in it.seen_in:
            _log(it, now, "seen", iteration=iteration, change_id=change_id)
        else:
            if (it.severity, it.detail) == (f["severity"], f["detail"]):
                continue  # the same scan again within this iteration: nothing new to record
        it.title, it.severity, it.detail = f["title"], f["severity"], f["detail"]
        it.at = list(f.get("at") or [])[:20]
        it.sources = [
            {"station": "build", "via": "image scan", "rule": f.get("rule", "")}
            # which image and who can fix it (ADR-0045): a base-image risk is decided factory-wide
            | {k: str(f[k]) for k in ("base_image", "origin", "key") if f.get(k)}
        ]
        it.last_iteration = iteration
        if iteration not in it.seen_in:
            it.seen_in.append(iteration)
        it.updated_at = now
        changed[it.id] = it
    for it in by_fp.values():
        if it.fingerprint in seen or it.status not in (*OPEN, "accepted_risk"):
            continue
        it.status, it.resolved_by = "fixed", change_id
        stats["fixed"] += 1
        _log(
            it,
            now,
            "fixed",
            f"no longer in the image built by change {change_id}",
            iteration=iteration,
            change_id=change_id,
        )
        changed[it.id] = it
    return sorted(changed.values(), key=sort_key), stats


ROADMAP = "roadmap"  # deferred pillar work from a product's docs/roadmap.md (ADR-0044 §5)


def fold_roadmap(
    product_id: str,
    items: list[WorkItem],
    candidates: list[dict[str, str]],
    *,
    change_id: str,
    iteration: int,
    now: datetime,
) -> tuple[list[WorkItem], dict[str, int]]:
    """Fold the deferred rows of a product's pillar roadmap into the ledger (ADR-0044 §5).
    `candidates` come from `decisions.roadmap_candidates` (`key`, `title`, `detail`, `pillar`).
    A row the architect no longer defers was taken into the product: its item is fixed.
    Returns the items that changed and what happened."""
    changed: dict[str, WorkItem] = {}
    stats = {"new": 0, "fixed": 0, "seen": 0}
    by_fp = {it.fingerprint: it for it in items if it.category == ROADMAP}
    ids = {it.id for it in items}
    seen: set[str] = set()
    for c in candidates:
        fp = fingerprint("recommendation", "", f"roadmap:{c['key']}")
        seen.add(fp)
        it = by_fp.get(fp)
        if it is None:
            it = WorkItem(
                id=item_id(product_id, fp),
                product_id=product_id,
                kind="recommendation",
                fingerprint=fp,
                title=c["title"],
                severity="low",
                category=ROADMAP,
                change_kind="feature",
                first_iteration=iteration,
                created_at=now,
                updated_at=now,
            )
            while it.id in ids:
                it.id = it.id + "x"
            ids.add(it.id)
            by_fp[fp] = it
            items.append(it)
            stats["new"] += 1
            _log(it, now, "found", "deferred in the product roadmap", iteration=iteration, change_id=change_id)
        elif iteration in it.seen_in and it.detail == c["detail"]:
            continue
        else:
            stats["seen"] += 1
            _log(it, now, "seen", iteration=iteration, change_id=change_id)
        it.title, it.detail = c["title"], c["detail"]
        it.sources = [{"station": "design", "via": "product roadmap", "rule": c.get("pillar", "")}]
        it.last_iteration = iteration
        if iteration not in it.seen_in:
            it.seen_in.append(iteration)
        it.updated_at = now
        changed[it.id] = it
    for it in by_fp.values():
        if it.fingerprint in seen or it.status not in OPEN:
            continue
        it.status, it.resolved_by = "fixed", change_id
        stats["fixed"] += 1
        _log(it, now, "fixed", "no longer deferred in the product roadmap", iteration=iteration, change_id=change_id)
        changed[it.id] = it
    return sorted(changed.values(), key=sort_key), stats


FEATURE = "feature"  # suggested next features for a delivered product (roadmap J2)
SIZES = ("S", "M", "L")
VALUES = {"high": "high", "medium": "medium", "low": "low"}
MAX_SUGGESTIONS = 5


def parse_suggestions(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Next-feature suggestions from the architect or the designer, checked: 1 to 5 entries, each with a
    title, what it does for the people using the product (`detail`), a `value` (high | medium | low), a
    `size` (S | M | L) and the requirements it would add. Returns the clean suggestions and the problems."""
    if not isinstance(raw, list) or not raw:
        return [], ["give 1 to 5 suggestions as a list"]
    if len(raw) > MAX_SUGGESTIONS:
        return [], [f"at most {MAX_SUGGESTIONS} suggestions, ranked by value: drop the weakest"]
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    titles: set[str] = set()
    for n, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            errors.append(f"suggestion {n}: not a mapping")
            continue
        title = " ".join(str(s.get("title") or "").split())[:120]
        detail = str(s.get("detail") or "").strip()[:1000]
        value = str(s.get("value") or "").strip().lower()
        size = str(s.get("size") or "").strip().upper()
        reqs = s.get("requirements") or []
        by = str(s.get("by") or "architect").strip() or "architect"
        where = f"suggestion {n} ({title or 'untitled'})"
        if len(title) < 3:
            errors.append(f"suggestion {n}: needs a title")
        elif _norm(title) in titles:
            errors.append(f"{where}: suggested twice")
        if len(detail) < 10:
            errors.append(f"{where}: say what it does for the people using the product")
        if value not in VALUES:
            errors.append(f"{where}: value is one of high, medium, low")
        if size not in SIZES:
            errors.append(f"{where}: size is one of S, M, L")
        if not isinstance(reqs, list) or not all(isinstance(r, str) and r.strip() for r in reqs):
            errors.append(f"{where}: requirements is a list of the requirements it would add")
            reqs = []
        titles.add(_norm(title))
        out.append(
            {
                "title": title,
                "detail": detail,
                "value": VALUES.get(value, "medium"),
                "size": size if size in SIZES else "M",
                "requirements": [" ".join(r.split())[:300] for r in reqs][:10],
                "by": by[:40],
            }
        )
    return (out, []) if not errors else ([], errors)


def fold_features(
    product_id: str,
    items: list[WorkItem],
    suggestions: list[dict[str, Any]],
    *,
    change_id: str,
    iteration: int,
    now: datetime,
) -> tuple[list[WorkItem], dict[str, int]]:
    """Fold the next-feature suggestions made at a delivery into the ledger (roadmap J2), from
    `parse_suggestions`. A new title is a new open item; one suggested before is seen again (a person's
    decision on it stands: a rejected feature isn't reopened by being suggested again). A suggestion that
    isn't repeated stays as it is: it was the agent's judgement, not a check. Returns the items that
    changed and what happened."""
    changed: dict[str, WorkItem] = {}
    stats = {"new": 0, "seen": 0}
    by_fp = {it.fingerprint: it for it in items if it.category == FEATURE}
    ids = {it.id for it in items}
    for s in suggestions:
        fp = fingerprint("recommendation", "", f"feature:{s['title']}")
        it = by_fp.get(fp)
        if it is None:
            it = WorkItem(
                id=item_id(product_id, fp),
                product_id=product_id,
                kind="recommendation",
                fingerprint=fp,
                title=s["title"],
                severity=s["value"],
                category=FEATURE,
                change_kind="feature",
                effort=s["size"],
                first_iteration=iteration,
                created_at=now,
                updated_at=now,
            )
            while it.id in ids:
                it.id = it.id + "x"
            ids.add(it.id)
            by_fp[fp] = it
            items.append(it)
            stats["new"] += 1
            _log(it, now, "found", f"suggested by the {s['by']}", iteration=iteration, change_id=change_id)
        elif iteration in it.seen_in:
            continue
        else:
            stats["seen"] += 1
            _log(it, now, "seen", f"suggested again by the {s['by']}", iteration=iteration, change_id=change_id)
        if it.status in OPEN:  # a decided item keeps what the person saw when they decided
            it.severity, it.effort = s["value"], s["size"]
            it.detail = s["detail"] + ("\nWould add: " + "; ".join(s["requirements"]) if s["requirements"] else "")
        it.sources = [{"station": "handover", "via": f"next-feature suggestions ({s['by']})", "rule": ""}]
        it.last_iteration = iteration
        if iteration not in it.seen_in:
            it.seen_in.append(iteration)
        it.updated_at = now
        changed[it.id] = it
    return sorted(changed.values(), key=sort_key), stats


def has_fix(it: WorkItem) -> bool:
    """An image finding with a released fix (its detail starts "fixed in", `image_scan.ledger_detail`)."""
    return it.category == IMAGE and it.detail.startswith("fixed in")


def next_iteration(items: list[WorkItem]) -> dict[str, list[WorkItem]]:
    """What a person can pick for a delivered product's next iteration (roadmap J2, the "Next
    iteration" card): suggested **features**, deferred **quality** work from the pillar roadmap, and
    **security** fixes (image vulnerabilities with a released fix). Open items only, best first;
    everything else stays on the work ledger."""
    open_ = sorted((it for it in items if it.status == "open"), key=sort_key)
    return {
        "features": [it for it in open_ if it.category == FEATURE],
        "quality": [it for it in open_ if it.category == ROADMAP],
        "security": [it for it in open_ if has_fix(it)],
    }


def pick_kind(items: list[WorkItem]) -> str:
    """The kind of change that addresses these items (roadmap J2): any new behaviour makes it a feature
    (carrying the fixes with it), otherwise a bug fix if any item is a bug, otherwise upkeep (security
    fixes alone keep behaviour). No items: a feature, as free text asks for new behaviour."""
    kinds = {it.change_kind for it in items}
    if not items or "feature" in kinds:
        return "feature"
    return "bug" if "bug" in kinds else "upkeep"


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
