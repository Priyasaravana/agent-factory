"""Suggested next features for a delivered product (roadmap J2, "plan the next iteration").

After a factory product's change is delivered, two observe-only agents look ahead: the **architect**
proposes next features (from what the spec left out of scope, open questions answered "later" and
the obvious next needs of the people using it) and, for a product with route screenshots, the
**designer** proposes UX improvements from them. The engine vets what they suggest (`vet`) and folds
the survivors into the product's work ledger in the `feature` category (`ledger.fold_features`), where
a person accepts, rejects (with a reason) or leaves each one for later.

Like the retro (ADR-0021), this runs after the delivery: it never delays or fails it. `vet` and the
prompts are pure; `run_suggestions` does the I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory import ledger
from agent_factory.agents.runner import AgentRequest
from agent_factory.models import Change, ChangeKind, ChangeStatus, EventKind, Product, ProductTarget, WorkItem
from agent_factory.observe import AgentCall

if TYPE_CHECKING:
    from agent_factory.engine.pipeline import ChangeManager

MAX_BY = {"architect": 4, "designer": 2}  # 3–5 in all after vetting (ledger.MAX_SUGGESTIONS)
VALUE_RANK = {"high": 0, "medium": 1, "low": 2}
SCREENSHOT_RUNS = ("live", "preview")  # the deployed app's screenshots first

SUGGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["suggestions"],
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "detail", "value", "size", "requirements"],
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "value": {"type": "string", "enum": ["high", "medium", "low"]},
                    "size": {"type": "string", "enum": ["S", "M", "L"]},
                    "requirements": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def wanted(change: Change, product: Product) -> bool:
    """A delivered change of a product the factory owns (not a team repo, J3; not an evaluation product)."""
    return (
        change.status == ChangeStatus.awaiting_feedback
        and change.kind != ChangeKind.assess
        and product.target != ProductTarget.repo
        and not product.eval_run_id
    )


def vet(raw: dict[str, list[Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """What each role suggested (`{"architect": [...], "designer": [...]}`) → the suggestions kept,
    best value first and at most `ledger.MAX_SUGGESTIONS`, and why the others were dropped. Each
    suggestion is checked on its own (`ledger.parse_suggestions`), so one bad entry doesn't lose the
    rest; a title suggested twice is kept once (the first role's); each role keeps at most its share
    (`MAX_BY`) of valid ones. Pure."""
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for role, items in raw.items():
        cap, taken = MAX_BY.get(role, 0), 0
        for s in items if isinstance(items, list) else []:
            if taken >= cap:
                dropped.append(f"{role}: more than {cap} suggestions, the rest were dropped")
                break
            clean, errors = ledger.parse_suggestions([{**s, "by": role} if isinstance(s, dict) else s])
            if errors:
                dropped.append(f"{role}: {errors[0]}")
                continue
            key = ledger.fingerprint("recommendation", "", f"feature:{clean[0]['title']}")
            if key in seen:
                dropped.append(f"{role}: '{clean[0]['title']}' was already suggested")
                continue
            seen.add(key)
            kept.append(clean[0])
            taken += 1
    kept.sort(key=lambda s: VALUE_RANK[s["value"]])  # stable: architect before designer at the same value
    for s in kept[ledger.MAX_SUGGESTIONS :]:
        dropped.append(f"{s['by']}: '{s['title']}' ranked below the top {ledger.MAX_SUGGESTIONS}")
    return kept[: ledger.MAX_SUGGESTIONS], dropped


def _ledger_view(items: list[WorkItem]) -> str:
    """The product's earlier feature suggestions, so an agent repeats an open one under the same title
    and never suggests a rejected or delivered one again."""
    lines = []
    for it in sorted((i for i in items if i.category == ledger.FEATURE), key=ledger.sort_key):
        why = f" (rejected: {it.decision_reason})" if it.status == "wont_do" and it.decision_reason else ""
        state = {"fixed": "delivered", "wont_do": "rejected", "in_progress": "being built"}.get(it.status, it.status)
        lines.append(f"- [{state}] {it.title}{why}")
    return "\n".join(lines) or "none yet"


def _common(product: Product, change: Change, items: list[WorkItem], cap: int) -> str:
    return f"""## Product: {product.title}
{product.requirements[:2000]}

Iteration {change.iteration} was just delivered. What it asked for:
{(change.change_request or product.requirements)[:1500]}

## Features suggested before for this product
{_ledger_view(items)}
Never suggest a rejected or delivered one again. To keep an open one, repeat its title exactly.

Give at most {cap} suggestions (fewer is fine, none if nothing is worth it). For each:
- `title`: a few words, the feature as its users would name it;
- `detail`: one or two sentences on what it lets the people using the product do, and why it matters;
- `value`: high | medium | low, for those people;
- `size`: S (an hour or two of building) | M (a day) | L (several days);
- `requirements`: the numbered-requirement style sentences it would add ("Members can …").
Read only; change nothing."""


def architect_prompt(product: Product, change: Change, items: list[WorkItem]) -> str:
    return f"""You are the architect of this product, looking at its next iteration. Read docs/spec.md,
docs/requirements.yaml, docs/design.md, docs/roadmap.md and docs/open-questions.yaml (if present).

Suggest the next FEATURES worth building, best value first, from:
- what the spec leaves out of scope that its users would now want;
- open questions answered "later" or "not in v0";
- the obvious next needs of the people using it, given what it does today.
Not quality work: the roadmap's deferred pillar rows (performance, security hardening, …) are already
on the product's work ledger. Not bugs.

{_common(product, change, items, MAX_BY["architect"])}"""


def designer_prompt(product: Product, change: Change, items: list[WorkItem], shots: list[str]) -> str:
    listed = "\n".join(f"- {s}" for s in shots[:24])
    return f"""You are the product's designer, looking at its next iteration. Look at the route screenshots
below (phone and desktop, light and dark) and read docs/ui.md (if present).

Suggest UX IMPROVEMENTS worth building, best value first: what makes the product easier, clearer or
more pleasant to use for its people (navigation, feedback, empty and error states, phone layout,
accessibility). Each one should be visible in a screenshot: say which in `detail`.

Screenshots:
{listed}

{_common(product, change, items, MAX_BY["designer"])}"""


def screenshots(artifacts: Path) -> list[str]:
    """The change's kept route screenshots (live first), as absolute paths."""
    for run in SCREENSHOT_RUNS:
        d = artifacts / "screenshots" / run
        if d.is_dir():
            files = sorted(str(p) for p in d.glob("*.png"))
            if files:
                return files
    return []


async def run_suggestions(mgr: ChangeManager, change: Change, product: Product) -> list[WorkItem]:
    """Ask for the next features of a delivered product and fold them into its ledger. Never raises
    into the change (the caller logs a failure)."""
    if not wanted(change, product):
        return []
    items = mgr.store.list_work_items(product.id)
    artifacts = mgr.ws.data_dir / "artifacts" / change.id
    shots = screenshots(artifacts)
    asks = {"architect": architect_prompt(product, change, items)}
    if shots:
        asks["designer"] = designer_prompt(product, change, items, shots)

    async def quiet(kind: str, data: dict[str, Any]) -> None:  # the agents' chatter is not run evidence
        return None

    async def emit(kind: EventKind, message: str, data: dict[str, Any]) -> None:
        mgr.store.add_event(change.id, kind, message, station="suggest", data=data)

    raw: dict[str, list[Any]] = {}
    for role, text in asks.items():
        req = AgentRequest(
            change_id=change.id,
            station="suggest",
            role=role,
            prompt=text,
            cwd=mgr.ws.change_dir(change.id),
            model=mgr.cfg.models.resolve("judgment"),
            tools=["Read", "Glob", "Grep"],
            observe_only=True,
            max_turns=8,
            output_schema=SUGGEST_SCHEMA,
            protected_paths=[str(mgr.ws.data_dir / "holdout")],
            subagent_model=mgr.cfg.models.fast,
        )
        call = AgentCall(artifacts, "suggest", role, req.model, emit)
        res = await mgr.agents.run(req, call.wrap(quiet))
        change.cost_usd += res.cost_usd
        await call.finish(res)
        if res.ok and isinstance(res.structured, dict):
            raw[role] = list(res.structured.get("suggestions") or [])
    kept, dropped = vet(raw)
    if not kept:
        mgr.store.add_event(
            change.id,
            EventKind.log,
            "next features: none suggested" + (f" ({len(dropped)} dropped)" if dropped else ""),
            station="suggest",
            data={"suggestions": {"kept": [], "dropped": dropped}},
        )
        return []
    changed, stats = ledger.fold_features(
        product.id, items, kept, change_id=change.id, iteration=change.iteration, now=datetime.now(UTC)
    )
    mgr.store.save_work_items(changed)
    by: dict[str, int] = {}
    for s in kept:
        by[s["by"]] = by.get(s["by"], 0) + 1
    mgr.store.add_event(
        change.id,
        EventKind.decision,
        f"next features: {len(kept)} suggested ({', '.join(f'{r} {n}' for r, n in by.items())}), "
        f"{stats['new']} new on the work ledger",
        station="suggest",
        data={
            "suggestions": {
                "kept": [{k: s[k] for k in ("title", "value", "size", "by")} for s in kept],
                "dropped": dropped,
                "items": [i.id for i in changed],
                **stats,
            }
        },
    )
    return changed
