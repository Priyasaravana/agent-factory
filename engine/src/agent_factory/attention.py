"""What a change is waiting for, as actions a person can take (pure).

The product and change pages show these at the top ("Needs you"), so nobody has to scroll to find
what is blocking a change. Each action names who acts (the product's creator, an admin, or the
factory's operator for its own failures, ADR-0043), the panel on the change page that does it, and
whether it blocks the change. The engine decides; the page only shows what it is given.
"""

from __future__ import annotations

from datetime import datetime

from agent_factory.models import Change, ChangeStatus, PendingAction, Product, ProductTarget

SYSTEM = "system"
ADMIN = "an admin"


def _creator(product: Product | None) -> str:
    return (product.created_by if product and product.created_by else None) or ADMIN


def pending_actions(product: Product | None, change: Change, since: datetime) -> list[PendingAction]:
    """The actions `change` is waiting for, most urgent first. `since` is when it entered its state."""
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
        return [
            act(
                "wait",
                "Nothing to do: waiting for the model usage window",
                "The change resumes by itself when the window resets.",
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
