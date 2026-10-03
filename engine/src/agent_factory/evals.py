"""Evaluation harness (ADR-0025): a workflow version is measured on a fixed suite of
products before it serves real ones.

- **The suite** is part of the workflow (`WorkflowDoc.evals`), versioned with it.
- **An evaluation** runs every case as a hidden order pinned to the candidate
  version and, when no earlier result of the same suite exists for it, to the
  baseline version too. Human gates are handled by the engine: the spec gate is
  approved "by evaluation" (a planned touch) and blocking questions get the case's
  canned answers (an unplanned touch, counted against autonomy) or end the case.
- **The verdict** is a pure function (`judge`) of the two summaries. Regressions
  block a gated activation; warnings never do.
- **The gate** (`eval_gate`): `block` keeps a published version as a candidate
  until its evaluation passes, then activates it; `warn` activates at once and
  reports; `off` does nothing. Rolling back to an older version is never gated.

Evaluation products are archived when the evaluation finishes (their app ports are
freed) and never appear in the products list or on Outcomes.
"""

from __future__ import annotations

import asyncio
import statistics
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_factory.models import (
    Change,
    ChangeStatus,
    CreateProductInput,
    EvalCaseResult,
    EvalRun,
    EvalSide,
    EvalSummary,
    EvalVerdict,
    EventKind,
    Product,
)

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import ChangeManager

# Regression thresholds: small suites are noisy, so cost and time need a margin.
COST_REGRESSION = 0.25  # cost per delivery up by more than 25% …
COST_FLOOR_USD = 0.25  # … and by more than this in absolute terms
LOOPS_REGRESSION = 0.5  # fix loops per case up by more than this
VERIFIED_REGRESSION = 0.10  # requirements verified live: share down by more than this
LEAD_TIME_WARNING = 0.5  # lead time up by more than 50%: warning only

FINAL = {
    ChangeStatus.awaiting_feedback: "delivered",
    ChangeStatus.failed: "failed",
    ChangeStatus.held: "held",
    ChangeStatus.cancelled: "cancelled",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _ratio(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


# ------------------------------------------------------------------ pure --
def summarize(results: list[EvalCaseResult]) -> EvalSummary:
    delivered = [r for r in results if r.status == "delivered"]
    cost = round(sum(r.cost_usd for r in results), 4)
    leads = [r.lead_time_s for r in delivered if r.lead_time_s is not None]
    reqs = sum(r.requirements for r in delivered)
    return EvalSummary(
        cases=len(results),
        delivered=len(delivered),
        pass_rate=_ratio(len(delivered), len(results)) or 0.0,
        autonomy=_ratio(sum(1 for r in delivered if not r.unplanned_touch), len(delivered)),
        cost_usd=cost,
        cost_per_delivery_usd=_ratio(cost, len(delivered)),
        fix_loops_per_case=_ratio(sum(r.fix_loops for r in results), len(results)) or 0.0,
        lead_time_median_s=round(statistics.median(leads), 1) if leads else None,
        level3_share=_ratio(sum(1 for r in delivered if (r.readiness_level or 0) >= 3), len(delivered)),
        verified_live_share=_ratio(sum(r.requirements_verified for r in delivered), reqs),
    )


def judge(base: EvalSummary | None, cand: EvalSummary, base_version: int | None = None) -> EvalVerdict:
    """Regressions block a gated activation; warnings are reported only."""
    if base is None:
        return EvalVerdict(passed=True, warnings=["no baseline: this result is the baseline for the next version"])
    reg: list[str] = []
    warn: list[str] = []

    def pct(x: float) -> str:
        return f"{round(100 * x)}%"

    if cand.pass_rate < base.pass_rate:
        reg.append(f"pass rate fell from {pct(base.pass_rate)} to {pct(cand.pass_rate)}")
    if base.autonomy is not None and cand.autonomy is not None and cand.autonomy < base.autonomy:
        reg.append(f"autonomy fell from {pct(base.autonomy)} to {pct(cand.autonomy)}")
    if base.level3_share is not None and cand.level3_share is not None and cand.level3_share < base.level3_share:
        reg.append(f"Level 3 share fell from {pct(base.level3_share)} to {pct(cand.level3_share)}")
    b, c = base.verified_live_share, cand.verified_live_share
    if b is not None and c is not None and b - c > VERIFIED_REGRESSION:
        reg.append(f"requirements verified live fell from {pct(b)} to {pct(c)}")
    b, c = base.cost_per_delivery_usd, cand.cost_per_delivery_usd
    if b is not None and c is not None and c > b:
        if c > b * (1 + COST_REGRESSION) and c - b > COST_FLOOR_USD:
            reg.append(f"cost per delivery rose from ${b:.2f} to ${c:.2f}")
        elif c > b * 1.10:
            warn.append(f"cost per delivery rose from ${b:.2f} to ${c:.2f} (within the margin)")
    if cand.fix_loops_per_case - base.fix_loops_per_case > LOOPS_REGRESSION:
        reg.append(f"fix loops per case rose from {base.fix_loops_per_case} to {cand.fix_loops_per_case}")
    b, c = base.lead_time_median_s, cand.lead_time_median_s
    if b and c and c > b * (1 + LEAD_TIME_WARNING):
        warn.append(f"median lead time rose from {round(b)}s to {round(c)}s")
    if cand.delivered == 0 and base.delivered == 0:
        warn.append("neither version delivered any case: the comparison says little")
    return EvalVerdict(passed=not reg, compared_to=base_version, regressions=reg, warnings=warn)


# ------------------------------------------------------------- the engine --
def _results(e: EvalRun) -> list[tuple[EvalSide, EvalCaseResult]]:
    sides = [e.candidate, *([e.baseline] if e.baseline else [])]
    return [(s, r) for s in sides for r in s.results]


def _previous_summary(mgr: ChangeManager, workflow_id: str, suite: str, version: int) -> tuple[str, EvalSummary] | None:
    """The latest finished result of this suite on `version` (as candidate or baseline)."""
    for e in mgr.store.list_evals(workflow_id):
        if e.status != "done" or e.suite_hash != suite:
            continue
        for side in (e.candidate, e.baseline):
            if side and side.version == version and side.summary:
                return e.id, side.summary
    return None


def start(
    mgr: ChangeManager, workflow_id: str, version: int, by: str, trigger: str = "manual", baseline: int | None = None
) -> EvalRun:
    from agent_factory.engine.pipeline import FactoryError

    if workflow_id not in mgr.cfg.blueprints:
        raise FactoryError(f"workflow '{workflow_id}' has no blueprint to evaluate on")
    svc = mgr.workflows[workflow_id]
    doc = svc.get(version)
    if not doc.evals:
        raise FactoryError("this workflow version has no evaluation cases")
    if any(e.status == "running" for e in mgr.store.list_evals(workflow_id)):
        raise FactoryError("an evaluation of this workflow is already running")
    suite = doc.eval_suite_hash()
    base_v = baseline if baseline is not None else svc.active_version()
    e = EvalRun(
        id=uuid.uuid4().hex[:12],
        workflow_id=workflow_id,
        suite_hash=suite,
        trigger=trigger,  # type: ignore[arg-type]
        started_by=by,
        created_at=_now(),
        candidate=EvalSide(version=version),
    )
    sides = [e.candidate]
    if base_v != version:
        if prev := _previous_summary(mgr, workflow_id, suite, base_v):
            e.baseline_from = prev[0]
            e.baseline = EvalSide(version=base_v, summary=prev[1])
        else:
            e.baseline = EvalSide(version=base_v)
            sides.append(e.baseline)
    need = len(doc.evals) * len(sides)
    usable = mgr.usable_node_ports(workflow_id) if mgr.uses_node_ports(workflow_id) else list(range(need))
    if len(usable) < need:
        mapped = mgr.preflight.mapped_node_ports
        hint = (
            f"; the cluster maps only {len(mapped)} app ports: `make reset-cluster` maps all of them"
            if mapped is not None and len(mapped) < len(mgr.cfg.blueprints[workflow_id].node_ports)
            else ""
        )
        raise FactoryError(
            f"an evaluation needs {need} free app ports and {len(usable)} are usable: archive orders you no "
            f"longer need{hint}"
        )
    created: list[tuple[Product, Change]] = []
    try:
        for side in sides:
            for case in doc.evals:
                product = mgr.create_product(
                    CreateProductInput(
                        title=f"[eval {e.id[:6]} v{side.version}] {case.title}"[:120],
                        requirements=case.requirements,
                        blueprint=workflow_id,
                    )
                )
                product.eval_run_id, product.created_by = e.id, by
                mgr.store.save_product(product)
                change = mgr.start_change(product, version=side.version)
                created.append((product, change))
                side.results.append(
                    EvalCaseResult(
                        case_id=case.id, title=case.title, product_id=product.id, change_id=change.id, status="running"
                    )
                )
    except Exception:
        # all or nothing: runs were only scheduled (no await yet), so nothing was built or deployed
        for product, change in created:
            mgr.cancel(change.id)
            product.archived_at, product.archived_by = _now(), "evaluation"
            mgr.store.save_product(product)
        raise
    return mgr.store.save_eval(e)  # no await since the first start_run: no run has stopped yet


def on_change_stopped(mgr: ChangeManager, change: Change, product: Product) -> None:
    """Called by the engine whenever an evaluation run stops. Synchronous on purpose:
    the read-modify-write of the evaluation cannot interleave with another case."""
    if not product.eval_run_id:
        return
    e = mgr.store.get_eval(product.eval_run_id)
    if e is None or e.status != "running":
        return
    found = next(((s, r) for s, r in _results(e) if r.change_id == change.id), None)
    if found is None or found[1].final:
        return
    side, res = found
    if change.status == ChangeStatus.awaiting_approval:
        mgr.approve_spec(change.id, by="evaluation")  # the spec gate is a planned touch
        return
    if change.status == ChangeStatus.awaiting_risk_approval:
        # never approved automatically: a fixed case that trips the change-risk rules is a finding
        _final(mgr, res, change, "held", "risky changes held for an admin (change risk, ADR-0027)")
        return
    if change.status == ChangeStatus.needs_input:
        case = next(
            (c for c in mgr.workflows[e.workflow_id].get(e.candidate.version).evals if c.id == res.case_id), None
        )
        if case and case.answers and not change.answers:
            res.unplanned_touch = True
            mgr.answer(change.id, case.answers, by="evaluation")
            mgr.store.save_eval(e)
            return
        _final(mgr, res, change, "needed_input", "intake asked blocking questions the case has no answers for")
    elif change.status in FINAL:
        _final(mgr, res, change, FINAL[change.status])
    else:
        return  # paused for the usage window, or still going: not final yet
    mgr.store.save_eval(e)
    if all(r.final for _, r in _results(e)):
        mgr._tasks[f"eval-{e.id}"] = asyncio.get_running_loop().create_task(finish(mgr, e.id), name=f"eval-{e.id}")


def _final(mgr: ChangeManager, res: EvalCaseResult, change: Change, status: str, note: str | None = None) -> None:
    res.status, res.final, res.note = status, True, note or change.summary
    res.cost_usd, res.fix_loops = round(change.cost_usd, 4), change.loops
    trans = mgr.store.transitions([change.id]).get(change.id, [])
    done = next((t.ts for t in reversed(trans) if t.to_status == ChangeStatus.awaiting_feedback), None)
    res.lead_time_s = round((done - change.created_at).total_seconds(), 1) if done else None
    if got := mgr.store.last_event_data(change.id, "readiness"):
        res.readiness_level = int(got[1].get("level", 0))
    if got := mgr.store.last_event_data(change.id, "traceability"):
        rows: list[dict[str, Any]] = list(got[1])
        res.requirements = len(rows)
        res.requirements_verified = sum(
            1 for r in rows if r.get("holdout") and all(h.get("passed") is True for h in r["holdout"])
        )


async def finish(mgr: ChangeManager, eval_id: str) -> EvalRun | None:
    """All cases are final: wait for their post-run work, judge, free the ports, apply the gate."""
    e = mgr.store.get_eval(eval_id)
    if e is None:
        return None
    me = asyncio.current_task()
    ids = {r.change_id for _, r in _results(e)}
    pending = [t for rid, t in mgr._tasks.items() if rid in ids and t is not me]
    await asyncio.gather(*pending, return_exceptions=True)  # retro and seal of the last cases
    for side in (e.candidate, e.baseline):
        if side and side.results:
            side.summary = summarize(side.results)
    base = e.baseline.summary if e.baseline else None
    assert e.candidate.summary is not None
    e.verdict = judge(base, e.candidate.summary, e.baseline.version if e.baseline else None)
    for _, r in _results(e):
        if r.product_id:
            try:
                await mgr.archive(r.product_id, "evaluation")
            except Exception as exc:  # noqa: BLE001 - a port that stays taken is reported, never fatal
                r.note = f"{r.note or ''} (archive failed: {exc})".strip()
    svc = mgr.workflows[e.workflow_id]
    doc = svc.get(e.candidate.version)
    if (
        e.trigger == "publish"
        and doc.eval_gate == "block"
        and e.verdict.passed
        and e.candidate.version > svc.active_version()
    ):
        svc.activate(e.candidate.version)
        svc.draft.discard_if_published_as(e.candidate.version)
        e.activated = True
    e.status, e.finished_at = "done", _now()
    return mgr.store.save_eval(e)


def gate_activation(mgr: ChangeManager, workflow_id: str, version: int, by: str, override: str | None) -> None:
    """Refuse to activate a newer `block`-gated version without a passing evaluation.
    An admin may override a failed evaluation with a recorded reason. Rollback is free."""
    from agent_factory.engine.pipeline import FactoryError
    from agent_factory.models import EvalOverride

    svc = mgr.workflows[workflow_id]
    doc = svc.get(version)
    if doc.eval_gate != "block" or version <= svc.active_version():
        return
    evals = [e for e in mgr.store.list_evals(workflow_id) if e.candidate.version == version]
    if any(e.status == "done" and e.verdict and e.verdict.passed for e in evals):
        return
    failed = next((e for e in evals if e.status == "done"), None)
    if failed is None:
        raise FactoryError(f"version {version} has no finished evaluation yet: its gate is 'block'")
    if not override:
        why = "; ".join(failed.verdict.regressions if failed.verdict else [])
        raise FactoryError(
            f"version {version} failed its evaluation ({why}): give an override reason to activate it anyway"
        )
    failed.override = EvalOverride(by=by, reason=override, at=_now())
    mgr.store.save_eval(failed)


def record_not_started(mgr: ChangeManager, workflow_id: str, version: int, by: str, error: str) -> EvalRun:
    """A gated publish whose evaluation could not start: kept, so the reason stays visible."""
    return mgr.store.save_eval(
        EvalRun(
            id=uuid.uuid4().hex[:12],
            workflow_id=workflow_id,
            suite_hash=mgr.workflows[workflow_id].get(version).eval_suite_hash(),
            trigger="publish",
            started_by=by,
            created_at=_now(),
            finished_at=_now(),
            status="not_started",
            error=error[:500],
            candidate=EvalSide(version=version),
        )
    )


def version_states(mgr: ChangeManager, workflow_id: str) -> dict[int, str]:
    """One line per evaluated version for the versions list, from its latest evaluation."""
    out: dict[int, str] = {}
    svc = mgr.workflows[workflow_id]
    active = svc.active_version()
    for e in mgr.store.list_evals(workflow_id):  # newest first: the first one per version wins
        v = e.candidate.version
        if v in out:
            continue
        if e.status == "running":
            out[v] = "evaluating"
        elif e.status == "not_started":
            out[v] = f"evaluation not started: {e.error}"
        elif e.status == "cancelled":
            out[v] = "evaluation cancelled"
        elif e.verdict and e.verdict.passed:
            out[v] = "evaluation passed"
        elif e.override:
            out[v] = f"evaluation failed; activated by {e.override.by} with a reason"
        else:
            out[v] = "evaluation failed"
    for info in svc.versions():
        if info.version > active and info.version not in out and svc.get(info.version).eval_gate == "block":
            out[info.version] = "candidate: not evaluated"
    return out


async def cancel(mgr: ChangeManager, e: EvalRun) -> EvalRun:
    from agent_factory.engine.pipeline import FactoryError

    if e.status != "running":
        raise FactoryError("this evaluation is not running")
    e.status, e.finished_at = "cancelled", _now()
    mgr.store.save_eval(e)  # first: stopping runs must not report to it any more
    ids = [r.change_id for _, r in _results(e) if r.change_id]
    for rid in ids:
        change = mgr.store.get_change(rid)
        if change and change.status not in FINAL:
            mgr.cancel(rid)
    await asyncio.gather(*[t for rid, t in list(mgr._tasks.items()) if rid in ids], return_exceptions=True)
    for _, r in _results(e):
        if r.product_id:
            try:
                await mgr.archive(r.product_id, "evaluation")
            except Exception as exc:  # noqa: BLE001
                r.note = f"archive failed: {exc}"
    return mgr.store.save_eval(e)


async def recover_on_startup(mgr: ChangeManager) -> list[str]:
    """An evaluation cut short by a restart can't be judged fairly: cancel it, free its ports.
    Evaluation products whose evaluation is missing or over (left by a failed start) are
    cancelled and archived too."""
    out = []
    for product in mgr.store.list_products():
        if not product.eval_run_id or product.archived_at:
            continue
        e = mgr.store.get_eval(product.eval_run_id)
        if e is not None and e.status == "running":
            continue
        for change in mgr.store.list_changes(product.id):
            if change.status not in FINAL:
                mgr.cancel(change.id)
        try:
            await mgr.archive(product.id, "evaluation")
        except Exception as exc:  # noqa: BLE001 - archived next time; never blocks startup
            if product.latest_change_id:
                mgr.store.add_event(product.latest_change_id, EventKind.log, f"evaluation cleanup failed: {exc}")
            continue
        out.append(product.id)
    for wf in mgr.workflows.ids():
        for e in mgr.store.list_evals(wf):
            if e.status == "running":
                for _, r in _results(e):
                    r.note = r.note or "factory restarted during the evaluation"
                await cancel(mgr, e)
                out.append(e.id)
    return out
