"""Learning from runs that needed help (ADR-0021).

After a run is delivered, if it needed help (a fix loop, a review that sent it
back, a hold, blocking intake questions), an observe-only "retro" agent reads the
recorded evidence and suggests at most three short lessons, each for the agent
whose work caused the problem. The engine vets them (`vet`) and stores the
survivors as *proposals*. Nothing changes until an admin accepts one: it is then
added to that agent's learnings in the workflow **draft**, and takes effect only
when the draft is published as a new, versioned workflow.

`signals` and `vet` are pure; `run_retro` does the I/O.
"""

from __future__ import annotations

import difflib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_factory.agents.runner import AgentRequest
from agent_factory.models import Event, EventKind, LearningProposal, Order, Run
from agent_factory.observe import AgentCall
from agent_factory.workflow import MAX_LEARNINGS_CHARS, WorkflowDoc

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import RunManager

MAX_PROPOSALS = 3
MIN_LESSON, MAX_LESSON = 20, 300
SIMILAR = 0.85  # difflib ratio at which a lesson counts as a duplicate
# a lesson must never talk an agent out of a check: that is what the checks are for
WEAKENS = re.compile(
    r"\b(skip|disable|bypass|ignore|remove|turn off|comment out|xfail|mark .* skip)\b.{0,40}"
    r"\b(tests?|checks?|reviews?|guardrails?|hooks?|verif\w*|scans?|readiness|acceptance|lint\w*)\b",
    re.IGNORECASE,
)

RETRO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "maxItems": MAX_PROPOSALS,
            "items": {
                "type": "object",
                "required": ["agent", "lesson", "why", "evidence"],
                "properties": {
                    "agent": {"type": "string"},
                    "lesson": {"type": "string"},
                    "why": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        }
    },
}


@dataclass
class Signals:
    routed: list[dict[str, Any]] = field(default_factory=list)  # {from, to, evidence}: a fix loop
    held: list[str] = field(default_factory=list)  # evidence of holds a person resolved
    questions: list[str] = field(default_factory=list)  # blocking intake questions a person answered
    denied: list[dict[str, Any]] = field(default_factory=list)  # actions a guardrail refused (ADR-0022)
    calls: list[dict[str, Any]] = field(default_factory=list)  # every agent call: turns, tools, cost

    def needs_retro(self) -> bool:
        # call stats alone never trigger a retro: they are context for the lessons
        return bool(self.routed or self.held or self.questions or self.denied)


def signals(run: Run, events: list[Event]) -> Signals:
    s = Signals()
    for e in events:
        if isinstance(e.data.get("denied"), dict):
            s.denied.append(e.data["denied"])
        elif isinstance(e.data.get("agent_call"), dict) and e.data["agent_call"].get("station") != "retro":
            s.calls.append(e.data["agent_call"])
        elif isinstance(e.data.get("routed"), dict):
            s.routed.append(e.data["routed"])
        elif e.kind == EventKind.status and e.message.startswith("held at"):
            s.held.append(f"{e.message}\n{e.data.get('evidence', '')}".strip())
    if run.answers:
        s.questions = list(run.questions)
    return s


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


_STOP = set(
    "a an the to of and or for in on with by as is are be it its this that these those do not no per each "
    "your you use using from at into via since then than when only one any all".split()
)
SAME_IDEA = 0.4  # share of the shorter lesson's content words found in the other


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z][a-z0-9-]+", text.lower()):
        if w in _STOP:
            continue
        out.add(re.sub(r"(es|s)$", "", w) if len(w) > 4 else w)
    return out


def _duplicate(lesson: str, existing: list[str]) -> bool:
    """Same text, nearly the same text, or the same idea in other words (most of the
    shorter lesson's content words appear in the other)."""
    n, words = _norm(lesson), _words(lesson)

    def same(x: str) -> bool:
        if n == _norm(x) or difflib.SequenceMatcher(None, n, _norm(x)).ratio() >= SIMILAR:
            return True
        other = _words(x)
        small = min(len(words), len(other))
        return small >= 4 and len(words & other) / small >= SAME_IDEA

    return any(same(x) for x in existing)


def vet(
    proposals: list[dict[str, Any]], doc: WorkflowDoc, pending: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep proposals that are well-formed, new, and do not weaken a check.
    `pending` maps agent id -> lessons already waiting for a decision."""
    kept: list[dict[str, Any]] = []
    rejected: list[str] = []
    for p in proposals[: MAX_PROPOSALS * 2]:
        agent, lesson = str(p.get("agent", "")), " ".join(str(p.get("lesson", "")).split())
        why, evidence = str(p.get("why", "")).strip(), str(p.get("evidence", "")).strip()
        spec = doc.agents.get(agent)
        if spec is None:
            rejected.append(f"unknown agent '{agent}'")
        elif not MIN_LESSON <= len(lesson) <= MAX_LESSON:
            rejected.append(f"lesson for '{agent}' must be {MIN_LESSON}-{MAX_LESSON} characters")
        elif not evidence or not why:
            rejected.append(f"lesson for '{agent}' cites no evidence")
        elif WEAKENS.search(lesson):
            rejected.append(f"lesson for '{agent}' would weaken a check: {lesson[:80]}")
        elif _duplicate(
            lesson,
            [ln.lstrip("- ").strip() for ln in spec.learnings.splitlines() if ln.strip()]
            + pending.get(agent, [])
            + [k["lesson"] for k in kept if k["agent"] == agent],
        ):
            rejected.append(f"lesson for '{agent}' repeats an existing or pending one")
        elif len(spec.learnings) + len(lesson) + 3 > MAX_LEARNINGS_CHARS:
            rejected.append(f"agent '{agent}' learnings are full; condense them in the editor")
        else:
            kept.append({"agent": agent, "lesson": lesson, "why": why[:600], "evidence": evidence[:1500]})
        if len(kept) == MAX_PROPOSALS:
            break
    considered = min(len(proposals), MAX_PROPOSALS * 2)
    if len(proposals) > considered:
        rejected.append(f"{len(proposals) - considered} further suggestion(s) ignored (limit {MAX_PROPOSALS * 2})")
    return kept, rejected


def prompt(order: Order, run: Run, sig: Signals, doc: WorkflowDoc, pending: dict[str, list[str]] | None = None) -> str:
    roster = "\n".join(
        f"- `{a.id}`: {a.description or '(no description)'}"
        + (f"\n  current learnings:\n  {a.learnings.strip().replace(chr(10), chr(10) + '  ')}" if a.learnings else "")
        for a in doc.agents.values()
    )
    routed = "\n\n".join(
        f"### {r.get('from')} failed, routed to {r.get('to')}\n{str(r.get('evidence', ''))[-1500:]}" for r in sig.routed
    )
    held = "\n\n".join(h[-1500:] for h in sig.held)
    questions = "\n".join(f"- {q}" for q in sig.questions)
    denied = "\n".join(
        f"- {d.get('role')} at {d.get('station')}: {d.get('tool')} `{d.get('input')}` -> {d.get('reason')}"
        for d in sig.denied
    )
    waiting = "\n".join(f"- `{a}`: {lesson}" for a, ls in (pending or {}).items() for lesson in ls)
    calls = "\n".join(
        f"- {c.get('station')} / {c.get('role')}: {c.get('turns')} turns, {c.get('duration_s')}s, "
        f"{c.get('tool_calls')} tool calls {c.get('tools')}" + (" FAILED" if not c.get("ok", True) else "")
        for c in sig.calls
    )
    return f"""You run the retrospective for a delivered run of the software factory. The run
needed help. Suggest at most {MAX_PROPOSALS} lessons that would have prevented that help being
needed, each for the ONE agent whose work caused the problem. A person will review every
lesson before any agent sees it.

A good lesson:
- applies to FUTURE orders of this product line, not just this app ("validate query
  parameters with Pydantic and return 422", not "fix the tag filter in app/main.py");
- is one actionable sentence, {MIN_LESSON}-{MAX_LESSON} characters;
- is not already in that agent's current learnings;
- never tells an agent to skip, weaken or work around a test, check, review or guardrail.
Suggest nothing (an empty list) if the problems were one-off or already covered.

## Order: {order.title}
{order.requirements[:1500]}
Iteration {run.iteration}; fix loops: {run.loops}.

## Agents in this workflow (use these ids)
{roster}

## Lessons already waiting for a person's decision (never suggest these again, even reworded)
{waiting or "none"}

## Fix loops (a station failed and the run was routed back)
{routed or "none"}

## Holds a person had to resolve
{held or "none"}

## Blocking questions intake had to ask
{questions or "none"}

## Actions a guardrail refused (the agent tried something it must not do)
{denied or "none"}

## Agent calls in this run (spot wasted effort, e.g. many turns spent searching)
{calls or "none"}

For each lesson give `agent`, `lesson`, `why` (the pattern it prevents) and `evidence`
(quote the evidence above that shows it)."""


async def run_retro(mgr: RunManager, run: Run, order: Order) -> list[LearningProposal]:
    """Suggest learnings for a delivered run that needed help. Never raises into the run."""
    wf_id = run.workflow_id or order.product_line
    service = mgr.workflows[wf_id]
    doc = service.get(run.workflow_version)
    if not doc.learn_from_runs:
        return []
    events = mgr.store.list_events(run.id)
    sig = signals(run, events)
    if not sig.needs_retro():
        return []
    pending: dict[str, list[str]] = {}
    for p in mgr.store.list_proposals(wf_id, "pending"):
        pending.setdefault(p.agent, []).append(p.lesson)
    req = AgentRequest(
        run_id=run.id,
        station="retro",
        role="retro",
        prompt=prompt(order, run, sig, doc, pending),
        cwd=mgr.ws.run_dir(run.id),
        model=mgr.cfg.models.resolve("judgment"),
        tools=["Read", "Glob", "Grep"],
        observe_only=True,
        max_turns=6,
        output_schema=RETRO_SCHEMA,
        protected_paths=[str(mgr.ws.data_dir / "holdout")],
        subagent_model=mgr.cfg.models.fast,
    )

    async def quiet(kind: str, data: dict[str, Any]) -> None:  # the retro's chatter is not run evidence
        return None

    async def emit(kind: EventKind, message: str, data: dict[str, Any]) -> None:
        mgr.store.add_event(run.id, kind, message, station="retro", data=data)

    call = AgentCall(mgr.ws.data_dir / "artifacts" / run.id, "retro", "retro", req.model, emit)
    res = await mgr.agents.run(req, call.wrap(quiet))
    run.cost_usd += res.cost_usd
    await call.finish(res)
    if not res.ok or not isinstance(res.structured, dict):
        mgr.store.add_event(run.id, EventKind.log, "retro: no suggestions (the retro agent gave no answer)")
        return []
    kept, rejected = vet(list(res.structured.get("proposals", [])), doc, pending)
    now = datetime.now(UTC)
    stored = [
        mgr.store.add_proposal(
            LearningProposal(
                id=uuid.uuid4().hex[:12],
                workflow_id=wf_id,
                workflow_version=run.workflow_version,
                run_id=run.id,
                order_id=order.id,
                created_at=now,
                **k,
            )
        )
        for k in kept
    ]
    mgr.store.add_event(
        run.id,
        EventKind.decision,
        f"retro: {len(stored)} learning(s) suggested for an admin to review"
        + (f" ({len(rejected)} discarded)" if rejected else ""),
        data={"retro": {"proposals": [p.id for p in stored], "discarded": rejected}},
    )
    return stored
