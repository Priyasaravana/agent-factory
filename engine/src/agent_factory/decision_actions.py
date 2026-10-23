"""Design decisions on the review page (ADR-0044 §1): read the sheet, and align with the architect
(choices, answers to open questions and comments, sent together). Registered with the other actions
(`actions.REGISTRY`) when `actions` is imported.

No `from __future__ import annotations` here: FastAPI resolves the endpoints' annotations from the
`actions` module's globals, so these must be real types, not strings.
"""

import json
from typing import TYPE_CHECKING

from agent_factory import decisions
from agent_factory.actions import _may_steer, action
from agent_factory.engine.pipeline import FactoryError
from agent_factory.identity import current_identity
from agent_factory.models import (
    AlignInput,
    Change,
    ChangeStatus,
    DecisionAnswerView,
    DecisionOptionView,
    DecisionQuestionView,
    DecisionsView,
    OpenQuestionView,
    RoadmapRowView,
)

if TYPE_CHECKING:
    from agent_factory.app_factory import Factory


def _change(f: "Factory", change_id: str) -> Change:
    change = f.store.get_change(change_id)
    if not change:
        raise FactoryError("change not found")
    return change


@action(
    "get_change_decisions",
    "The change's design decisions: the stack's questions, the answers and who decided them, what "
    "changed this iteration, the pillar roadmap and the discussion with the architect",
    "GET",
    "/api/changes/{change_id}/decisions",
)
def get_change_decisions(f: "Factory", change_id: str) -> DecisionsView:
    change = _change(f, change_id)
    flow, cat, wt = f.manager._design_context(change)
    sheet_file, roadmap_file = wt / decisions.SHEET, wt / decisions.ROADMAP
    sheet, _ = decisions.parse_sheet(sheet_file.read_text() if sheet_file.is_file() else None)
    evidence = f.manager.ws.data_dir / "artifacts" / change.id / "decisions.json"
    recorded: dict = {}
    if evidence.is_file():
        try:
            recorded = json.loads(evidence.read_text())
        except ValueError:
            recorded = {}
    if sheet is None and recorded.get("answers"):  # the worktree is gone: the sealed record
        sheet = decisions.Sheet.model_validate({"answers": recorded["answers"]})
    rows, _ = decisions.parse_roadmap(roadmap_file.read_text() if roadmap_file.is_file() else None)
    diff = recorded.get("diff") or {}
    from agent_factory.engine.design_station import open_questions

    asked = open_questions(wt)
    reviewing = change.status == ChangeStatus.awaiting_approval or bool(change.discuss_pending)
    problems = (
        decisions.judge(cat, sheet, flow.decision_constraints, "ask", asked)
        if sheet and (cat or asked) and reviewing and not change.discuss_pending
        else []
    )
    return DecisionsView(
        mode=flow.decisions,
        pending=reviewing,
        asked=cat is not None or bool(asked),
        questions=[
            DecisionQuestionView(
                id=q.id,
                title=q.title,
                pillar=q.pillar,
                hint=q.hint,
                options=[
                    DecisionOptionView(
                        id=o.id,
                        title=o.title,
                        consequences=o.consequences,
                        allowed=o.id in decisions.allowed_options(q, flow.decision_constraints),
                    )
                    for o in q.options
                ],
            )
            for q in (cat.questions if cat else [])
        ],
        answers=[DecisionAnswerView(**a.model_dump()) for a in (sheet.answers if sheet else [])],
        open_questions=[
            OpenQuestionView(**(oq.model_dump() if (oq := sheet.open_answer(q["id"]) if sheet else None) else q))
            for q in asked
        ],
        changed=sorted(set(diff.get("added", [])) | set(diff.get("changed", []))),
        problems=problems,
        roadmap=[RoadmapRowView(**r.model_dump()) for r in rows],
        discussion=list(change.discussion),
        discuss_pending=bool(change.discuss_pending),
    )


@action(
    "align_with_architect",
    "Review page: send the architect your design choices, answers to open questions and comments together; "
    "choices and answers are saved first (within the lane's constraints), then it replies and updates the "
    "spec, design and plan, or sends a requirements change back to the specification",
    "POST",
    "/api/changes/{change_id}/align",
)
async def align_with_architect(f: "Factory", change_id: str, body: AlignInput) -> Change:
    change = _change(f, change_id)
    _may_steer(f, change.product_id)
    return await f.manager.align(
        change_id,
        current_identity().user,
        body.answers,
        [(a.id, a.answer) for a in body.open_answers],
        body.message,
    )
