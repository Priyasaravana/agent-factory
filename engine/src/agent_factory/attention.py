"""What a change is waiting for, as actions a person can take (pure).

The product and change pages show these at the top ("Needs you"), so nobody has to scroll to find
what is blocking a change. Each action names who acts (the product's creator, an admin, or the
factory's operator for its own failures, ADR-0043; the creator gives way to the product's named
owner, J4), the panel on the change page that does it, and
whether it blocks the change. The engine decides; the page only shows what it is given.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from agent_factory.models import (
    Change,
    ChangeStatus,
    IncidentResponse,
    PendingAction,
    Product,
    ProductTarget,
    WorkItem,
)
from agent_factory.pr_loop import CiFix, FeedbackFix

SYSTEM = "system"
ADMIN = "an admin"


def _creator(product: Product | None) -> str:
    """Who acts on a product: its named owner (J4), else its creator, else an admin."""
    return ((product.owner or product.created_by) if product else None) or ADMIN


def product_actions(
    product: Product,
    incidents: list[WorkItem],
    upkeep: list[WorkItem],
    can_start: bool,
    now: datetime,
    responses: Mapping[str, IncidentResponse] | None = None,
) -> list[PendingAction]:
    """What a delivered product itself is waiting for, outside any change (J4, J5): an incident to
    fix, upkeep that is due, a retirement that stopped half-way. They go to the product's owner.
    `incidents` and `upkeep` are its open ledger items; `can_start`: no change is running on it;
    `responses`: the engine's response to each incident, by item id (ADR-0049 §4, slice 0)."""
    if product.archived_at and not (product.retirement and not product.retirement.done):
        return []
    owner = _creator(product)
    out: list[PendingAction] = []

    def act(kind: str, title: str, detail: str, panel: str, since: datetime, blocking: bool) -> None:
        out.append(
            PendingAction.model_validate(
                {
                    "kind": kind,
                    "title": title,
                    "detail": detail,
                    "owner": owner,
                    "panel": panel,
                    "blocking": blocking,
                    "since": since,
                }
            )
        )

    r = product.retirement
    if r is not None and not r.done:
        failed = next((s for s in r.steps if s.status == "failed"), None)
        act(
            "retire",
            "Retirement stopped half-way: continue it",
            (f"{failed.title} failed: {failed.detail}. " if failed else "")
            + "The steps already done are kept; continuing picks up at the one that failed.",
            "operations",
            r.started_at,
            True,
        )
        return out
    for it in incidents:
        resp = (responses or {}).get(it.id)
        if resp is not None and _response_action(resp, it, act):
            continue
        waiting = it.status == "open"
        act(
            "incident",
            "Incident: the product is outside its availability target" + ("" if waiting else " (a fix is in progress)"),
            it.detail.split("\n", 1)[0]
            + (
                " Make this change to start a fix."
                if waiting and can_start
                else " A fix can start when the running change stops."
                if waiting
                else ""
            ),
            "next-iteration",
            it.updated_at,
            waiting,
        )
    for it in upkeep:
        if it.status == "open":
            act("upkeep", f"Upkeep: {it.title}", "Tick it on the Next iteration card to start an upkeep change.",
                "next-iteration", it.created_at, False)  # fmt: skip
    return out


STEP_TEXT = {"reapply": "re-apply the recorded release", "restart": "restart the app"}


def _response_action(r: IncidentResponse, it: WorkItem, act: object) -> bool:
    """The action for an incident the engine is responding to, or False: none, show the plain incident.
    Proposed or held: the owner acts on the Operations panel. Mitigating or restored: optional, the factory
    is on it."""
    if r.state == "resolved":
        return False
    since = r.last_action_at or r.opened_at or it.updated_at
    if r.proposed:
        act("incident", f"Incident: restore service ({STEP_TEXT.get(r.proposed, r.proposed)})",  # type: ignore[operator]
            "The factory proposes this step and waits for you to run it (incident response is in ask mode).",
            "operations", since, True)  # fmt: skip
        return True
    if r.held:
        act("incident", "Incident: the factory's response is held for you", r.held,  # type: ignore[operator]
            "operations", since, True)  # fmt: skip
        return True
    if r.state in ("mitigating", "reopened") and r.tried:
        act("incident", "Incident: the factory is restoring service",  # type: ignore[operator]
            "Tried: " + ", ".join(STEP_TEXT.get(t, t) for t in r.tried) + ". Nothing to do unless it is held.",
            "operations", since, False)  # fmt: skip
        return True
    if r.state == "restored":
        act("incident", "Incident: service restored, watching for a recurrence",  # type: ignore[operator]
            r.classification or "Restored without a change.", "operations", r.restored_at or since, False)  # fmt: skip
        return True
    return False


def pending_actions(
    product: Product | None,
    change: Change,
    since: datetime,
    ci_fix: CiFix | None = None,
    feedback: FeedbackFix | None = None,
) -> list[PendingAction]:
    """The actions `change` is waiting for, most urgent first. `since` is when it entered its state.
    `ci_fix`: what `pr_loop.ci_fix` decided for its pull request's red CI (J3), when it opened one;
    `feedback`: what `pr_loop.feedback_fix` decided for the team's feedback on it (J3 slice 3)."""
    if product is not None and product.archived_at:
        return []
    owner = _creator(product)
    station = change.current_station
    st = change.status

    def act(
        kind: str, title: str, detail: str, panel: str, *, who: str = owner, blocking: bool = True
    ) -> PendingAction:
        return PendingAction.model_validate(
            {
                "kind": kind,
                "title": title,
                "detail": detail,
                "station": station,
                "owner": who,
                "panel": panel,
                "blocking": blocking,
                "since": since,
            }
        )

    if st == ChangeStatus.needs_input:
        n = len(change.questions)
        detail = f"Intake can't specify this change without {n} answer{'s' if n != 1 else ''}."
        if change.switch_to:
            detail += " It can also switch to a stack that builds what was asked."
        return [act("answer_questions", "Answer the intake questions", detail, "questions")]

    if st == ChangeStatus.awaiting_approval:
        if change.discuss_pending:
            return [
                act(
                    "wait",
                    "The architect is replying",
                    "Your choices and comments were sent; the review page comes back when it has answered.",
                    "decisions",
                    who=SYSTEM,
                    blocking=False,
                )
            ]
        what = "the design decisions and the spec" if change.decisions_pending else "the spec"
        return [
            act(
                "align",
                "Review and approve the design",
                f"Building waits until a person approves {what}. Change choices or ask the architect first if needed.",
                "decisions",
            )
        ]

    if st == ChangeStatus.awaiting_risk_approval:
        n = change.risk_hold.findings if change.risk_hold else 0
        return [
            act(
                "approve_risk",
                "Approve or send back a risky change",
                f"The diff has {n} change-risk finding{'s' if n != 1 else ''}; "
                "an admin other than the requester decides.",
                "risk",
                who=ADMIN,
            )
        ]

    if st == ChangeStatus.held:
        in_place_kinds = ("approve_schema", "schema_in_place", "colors_in_place")
        if change.required_action is not None and change.required_action.kind in in_place_kinds:
            kind = change.required_action.kind
            title = {
                "approve_schema": "Approve the schema's compatibility, or release in place",
                "colors_in_place": "Fix the cause and resume, or release in place",
            }.get(kind, "Release this change in place")
            return [
                act(
                    "approve_schema",
                    title,
                    change.summary or change.required_action.text,
                    "hold",
                )
            ]
        if change.required_action is not None:
            return [
                act(
                    "accept_risk",
                    "Decide on the held risk, then resume",
                    change.required_action.text,
                    "work-ledger",
                )
            ]
        if change.hold_class == "infra":
            return [
                act(
                    "factory_hold",
                    "Fix the factory, then resume",
                    "The factory failed here, not the product: retries didn't cure it and nothing went to the "
                    "developer. " + (change.summary or ""),
                    "hold",
                    who=SYSTEM,
                )
            ]
        return [
            act(
                "resume",
                "Read the evidence, then resume or change the request",
                change.summary or "The change stopped and needs a person.",
                "hold",
            )
        ]

    if st == ChangeStatus.interrupted:
        return [act("resume", "Resume the change", "The factory restarted while it was running.", "hold")]

    if st == ChangeStatus.paused_limits:
        p = change.pause
        if p is not None and p.kind == "throttle":
            title = "Nothing to do: the model provider is rate limiting"
            when = f"about {p.resume_at:%H:%M} (estimate)"
        else:
            title = "Nothing to do: waiting for the model usage window"
            when = (
                "when the window resets"
                if p is None
                else f"at {p.resume_at:%H:%M} (from the provider)"
                if p.known
                else f"at about {p.resume_at:%H:%M} (estimate: the provider gave no time)"
            )
        return [
            act(
                "wait",
                title,
                f"The change resumes the paused step by itself {when}.",
                "hold",
                who=SYSTEM,
                blocking=False,
            )
        ]

    if st == ChangeStatus.awaiting_feedback:
        if change.kind == "assess":
            return [
                act(
                    "feedback",
                    "Read the assessment and pick what to change",
                    "Tick items in the work ledger and make a change from them.",
                    "work-ledger",
                    blocking=False,
                )
            ]
        on_pr: list[PendingAction] = []
        if ci_fix is not None and ci_fix.auto:
            on_pr.append(
                act(
                    "wait",
                    "CI failed on the pull request: the lane starts a fix by itself",
                    ci_fix.reason + ". The fix continues the same pull request.",
                    "pull-request",
                    who=SYSTEM,
                    blocking=False,
                )
            )
        elif ci_fix is not None and ci_fix.offer:
            on_pr.append(
                act(
                    "fix_ci",
                    "CI failed on the pull request: start a fix",
                    ci_fix.reason + ". A fix continues the same pull request on its branch; nothing is forced.",
                    "pull-request",
                )
            )
        if feedback is not None and feedback.auto:
            on_pr.append(
                act(
                    "wait",
                    "Feedback on the pull request: the lane answers it by itself",
                    feedback.reason + ". Each piece is answered or addressed on the same pull request.",
                    "pull-request",
                    who=SYSTEM,
                    blocking=False,
                )
            )
        elif feedback is not None and feedback.offer:
            on_pr.append(
                act(
                    "address_feedback",
                    "Feedback on the pull request: answer it",
                    feedback.reason + ". A change answers or addresses each piece on the same pull request "
                    "and replies to it there.",
                    "pull-request",
                )
            )
        running = (ci_fix.running if ci_fix else None) or (feedback.running if feedback else None)
        if not on_pr and running:
            on_pr.append(
                act(
                    "wait",
                    f"A change continuing the pull request is in progress (change {running})",
                    (ci_fix.reason if ci_fix and ci_fix.running else feedback.reason if feedback else "") + ".",
                    "pull-request",
                    who=SYSTEM,
                    blocking=False,
                )
            )
        if on_pr:
            return on_pr
        if product is not None and product.target == ProductTarget.repo:
            return [
                act(
                    "feedback",
                    "Try the app, then ask for the next change",
                    "Delivered. Nothing waits on you; ask for a feature, a bug fix or upkeep when you're ready.",
                    "feedback",
                    blocking=False,
                )
            ]
        # a factory product picks its next iteration on the "Next iteration" card (roadmap J2)
        return [
            act(
                "feedback",
                "Try the app, then pick what the next iteration does",
                "Delivered. Nothing waits on you; tick suggested features, quality work or security fixes, "
                "or describe a change, when you're ready.",
                "next-iteration",
                blocking=False,
            )
        ]
    return []
