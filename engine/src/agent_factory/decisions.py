"""Design decisions (ADR-0044 §1, §5): a pack's catalogue of architecture questions, the decision
sheet the architect answers in `docs/decisions.yaml`, the engine's judgement of that sheet, the
short ADRs rendered from it, and the per-product pillar roadmap in `docs/roadmap.md`.

Everything here is pure: no I/O beyond the text it is given, no model calls. The design station
reads files and calls these; the engine, not the agent, decides whether a sheet is complete.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent_factory import pillars

OTHER = "other"  # "Other…": free text the architect turns into a concrete choice (ask mode only)
SHEET = "docs/decisions.yaml"
ROADMAP = "docs/roadmap.md"
OPEN_QUESTIONS = "docs/open-questions.yaml"  # intake's open questions, for the architect to answer
ADR_DIR = "docs/adr"
Mode = Literal["autonomous", "ask"]
_SLUG = r"^[a-z][a-z0-9-]{1,40}$"


class CatalogueError(ValueError):
    """A pack's decisions.yaml or a lane's constraints that can't be used."""


# --------------------------------------------------------------- catalogue --
class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=_SLUG)
    title: str = Field(min_length=2, max_length=120)
    consequences: str = Field(min_length=10, max_length=600)

    @field_validator("id")
    @classmethod
    def _not_other(cls, v: str) -> str:
        if v == OTHER:
            raise ValueError(f"'{OTHER}' is reserved for the free-text answer")
        return v


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=_SLUG)
    title: str = Field(min_length=5, max_length=200)
    pillar: str
    hint: str = Field(default="", max_length=600)  # how to choose a recommendation
    options: list[Option] = Field(min_length=2, max_length=8)

    @field_validator("pillar")
    @classmethod
    def _known_pillar(cls, v: str) -> str:
        if v not in pillars.IDS:
            raise ValueError(f"pillar '{v}' is not one of {', '.join(pillars.IDS)}")
        return v

    @model_validator(mode="after")
    def _distinct(self) -> Question:
        ids = [o.id for o in self.options]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"question '{self.id}': option ids listed twice: {', '.join(dupes)}")
        return self

    def option(self, oid: str) -> Option | None:
        return next((o for o in self.options if o.id == oid), None)


class Catalogue(BaseModel):
    """The questions every product of a stack answers, in the order they are asked."""

    model_config = ConfigDict(extra="forbid")
    questions: list[Question] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _distinct(self) -> Catalogue:
        ids = [q.id for q in self.questions]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"question ids listed twice: {', '.join(dupes)}")
        return self

    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)


def load_catalogue(raw: Any, where: str) -> Catalogue:
    try:
        return Catalogue.model_validate(raw)
    except ValidationError as exc:
        raise CatalogueError(f"{where}: {_first_error(exc)}") from exc


def _first_error(exc: ValidationError) -> str:
    e = exc.errors()[0]
    loc = ".".join(str(p) for p in e["loc"])
    return f"{loc}: {e['msg']}" if loc else str(e["msg"])


def constraint_errors(catalogues: list[Catalogue], constraints: dict[str, list[str]]) -> list[str]:
    """A lane's `decision_constraints` against every installed pack's catalogue (publish check).
    Lanes are stack-neutral, so a question only one pack asks may be constrained; an option id
    must exist in some catalogue that asks that question."""
    errors: list[str] = []
    for qid, allowed in sorted(constraints.items()):
        asked = [q for c in catalogues for q in c.questions if q.id == qid]
        if not asked:
            errors.append(f"decision_constraints: no installed pack asks '{qid}'")
            continue
        if not allowed:
            errors.append(f"decision_constraints.{qid}: list at least one allowed option")
        known = {o.id for q in asked for o in q.options}
        for oid in allowed:
            if oid not in known:
                errors.append(
                    f"decision_constraints.{qid}: '{oid}' is not an option (options: {', '.join(sorted(known))})"
                )
    return errors


def allowed_options(question: Question, constraints: dict[str, list[str]]) -> list[str]:
    """The option ids a lane allows for a question: all of them unless the lane narrows it."""
    ids = [o.id for o in question.options]
    narrowed = constraints.get(question.id)
    return [i for i in ids if i in narrowed] if narrowed else ids


# ------------------------------------------------------------------- sheet --
class Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str
    choice: str
    other: str | None = None  # the concrete choice when `choice` is "other"
    recommended: str  # the option the architect recommends (may lie outside the lane's constraints)
    rationale: str = ""  # one line, naming the pillar it serves
    requirements: list[str] = Field(default_factory=list)  # R1, R3, …
    decided_by: str = "architect"  # architect (recommended) | architect (constrained) | <member>
    reason: str | None = None  # a person's reason when they changed or confirmed an answer


class OpenAnswer(BaseModel):
    """The answer to one of intake's open questions: the architect proposes it, a person may change it."""

    model_config = ConfigDict(extra="ignore")
    id: str  # Q1, Q2, … as intake numbered them
    question: str = ""
    answer: str = ""
    rationale: str = ""
    decided_by: str = "architect"


class Sheet(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answers: list[Answer] = Field(default_factory=list)
    open_questions: list[OpenAnswer] = Field(default_factory=list)

    def answer(self, qid: str) -> Answer | None:
        return next((a for a in self.answers if a.question == qid), None)

    def open_answer(self, qid: str) -> OpenAnswer | None:
        return next((a for a in self.open_questions if a.id == qid), None)


def parse_open_questions(text: str | None) -> list[dict[str, str]]:
    """Intake's open questions (`docs/open-questions.yaml`): [{id, question}]. Anything unreadable is none."""
    try:
        raw = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return []
    out = []
    for q in raw if isinstance(raw, list) else []:
        if isinstance(q, dict) and str(q.get("id", "")).strip() and str(q.get("question", "")).strip():
            out.append({"id": str(q["id"]).strip(), "question": str(q["question"]).strip()})
    return out


def dump_open_questions(questions: Sequence[str]) -> str:
    return yaml.safe_dump(
        [{"id": f"Q{n}", "question": q.strip()} for n, q in enumerate(questions, start=1) if q.strip()],
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def parse_sheet(text: str | None) -> tuple[Sheet | None, list[str]]:
    """`docs/decisions.yaml` as the architect wrote it: {answers: [...]} or a bare list."""
    if text is None:
        return None, [f"{SHEET} is missing"]
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"{SHEET} is not valid YAML: {str(exc).splitlines()[0]}"]
    if isinstance(raw, list):
        raw = {"answers": raw}
    if not isinstance(raw, dict):
        return None, [f"{SHEET} must be a mapping with `answers:` (a list, one per question)"]
    raw = {**raw, "answers": raw.get("answers") or [], "open_questions": raw.get("open_questions") or []}
    try:
        return Sheet.model_validate(raw), []
    except ValidationError as exc:
        return None, [f"{SHEET}: {_first_error(exc)}"]


def dump_sheet(sheet: Sheet) -> str:
    data = {"answers": [a.model_dump(exclude_none=True, exclude_defaults=False) for a in sheet.answers]}
    for a in data["answers"]:
        if not a.get("requirements"):
            a.pop("requirements", None)
    if sheet.open_questions:
        data["open_questions"] = [q.model_dump() for q in sheet.open_questions]
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def by_person(decided_by: str) -> bool:
    """Whether an answer's attribution names a person rather than the architect."""
    return bool(decided_by.strip()) and not decided_by.startswith("architect")


def _kept(a: Answer, earlier: Sheet | None) -> Answer | None:
    """The earlier answer a person gave to this question, when `a` keeps it unchanged (same choice and
    same `other`). Iterations carry what a person agreed: autonomous mode doesn't overrule it."""
    e = earlier.answer(a.question) if earlier else None
    if e is None or not by_person(e.decided_by):
        return None
    return e if (a.choice, (a.other or "").strip()) == (e.choice, (e.other or "").strip()) else None


def judge(
    cat: Catalogue | None,
    sheet: Sheet,
    constraints: dict[str, list[str]],
    mode: Mode,
    asked: Sequence[dict[str, str]] = (),
    earlier: Sheet | None = None,
) -> list[str]:
    """Whether the sheet is complete and inside the lane's constraints. Empty means it passes.

    - every catalogue question has exactly one answer, and no answer names an unknown question;
    - `recommended` is one of the question's options, and every answer has a rationale;
    - `choice` is an allowed option, or "other" with its text, which only ask mode accepts
      (it leaves the approved options, so a person must see it);
    - in autonomous mode the architect takes its recommendation when the lane allows it, except where
      it keeps an answer a person gave in `earlier` (the sheet the change started from), "other" included;
    - every open question intake `asked` has an answer (ADR-0044 §1: they go to the architect)."""
    errors: list[str] = []
    for oq in asked:
        got = sheet.open_answer(oq["id"])
        if got is None or len(got.answer.strip()) < 2:
            errors.append(f"{oq['id']}: open question not answered ({oq['question'][:120]})")
    if cat is None:
        return errors
    ids = [a.question for a in sheet.answers]
    for qid in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"{qid}: answered more than once")
    for qid in sorted(set(ids) - {q.id for q in cat.questions}):
        errors.append(f"{qid}: not a question in this stack's catalogue")
    for q in cat.questions:
        a = sheet.answer(q.id)
        if a is None:
            errors.append(f"{q.id}: not answered ({q.title})")
            continue
        allowed = allowed_options(q, constraints)
        if q.option(a.recommended) is None:
            errors.append(
                f"{q.id}: recommended '{a.recommended}' is not an option ({', '.join(o.id for o in q.options)})"
            )
        if len(a.rationale.strip()) < 10:
            errors.append(f"{q.id}: give a one-line rationale naming the pillar it serves")
        kept = _kept(a, earlier) is not None
        if a.choice == OTHER:
            if mode != "ask" and not kept:
                errors.append(f"{q.id}: 'other' needs a person (decisions: ask); choose one of {', '.join(allowed)}")
            elif not (a.other or "").strip():
                errors.append(f"{q.id}: 'other' needs the concrete choice in `other`")
        elif a.choice not in allowed:
            if q.option(a.choice) is None:
                errors.append(f"{q.id}: '{a.choice}' is not an option ({', '.join(o.id for o in q.options)})")
            else:
                errors.append(f"{q.id}: '{a.choice}' is outside this lane's constraints ({', '.join(allowed)})")
        elif mode == "autonomous" and a.recommended in allowed and a.choice != a.recommended and not kept:
            errors.append(f"{q.id}: autonomous mode takes the recommendation '{a.recommended}'")
    return errors


def stamp(
    sheet: Sheet,
    constraints: dict[str, list[str]],
    cat: Catalogue | None,
    asked: Sequence[dict[str, str]] = (),
    earlier: Sheet | None = None,
) -> Sheet:
    """Who decided each answer, for the record. The architect's own answers say whether they are its
    recommendation or the lane's constraint. An answer a person gave in `earlier` and the architect kept
    keeps that person's name and reason; the architect can't name a person on an answer it wrote."""
    out = sheet.model_copy(deep=True)
    texts = {q["id"]: q["question"] for q in asked}
    out.open_questions = [q for q in out.open_questions if not texts or q.id in texts]
    for oq in out.open_questions:
        oq.question = texts.get(oq.id, oq.question)
        if oq.decided_by.startswith("architect") or not oq.decided_by.strip():
            oq.decided_by = "architect (proposed)"
    for a in out.answers:
        if cat is None:
            break
        if (e := _kept(a, earlier)) is not None:
            a.decided_by, a.reason = e.decided_by, e.reason
            continue
        q = cat.question(a.question)
        constrained = q is not None and a.recommended not in allowed_options(q, constraints)
        a.decided_by = "architect (constrained)" if constrained else "architect (recommended)"
    return out


class PersonAnswer(Protocol):
    """One answer a person gives on the review page (ADR-0044 §1, ask mode): `DecisionAnswer`."""

    question: str
    choice: str
    other: str | None
    reason: str | None


def apply_answers(sheet: Sheet, answers: Sequence[PersonAnswer], by: str) -> Sheet:
    """A person's answers over the architect's sheet. Unchanged answers a person confirms keep the
    architect's attribution unless they give a reason. Judge the result before saving it."""
    out = sheet.model_copy(deep=True)
    for pa in answers:
        a = out.answer(pa.question)
        if a is None:
            continue  # judge() reports unknown questions on the person's next save
        other = (pa.other or "").strip() or None
        reason = (pa.reason or "").strip() or None
        if pa.choice == a.choice and other == a.other and reason is None:
            continue
        a.choice, a.other, a.reason, a.decided_by = pa.choice, other if pa.choice == OTHER else None, reason, by
    return out


def apply_open_answers(sheet: Sheet, answers: Sequence[tuple[str, str]], by: str) -> tuple[Sheet, list[str]]:
    """A person's own answers to open questions (id, text). Returns the sheet and the ids they changed."""
    out = sheet.model_copy(deep=True)
    changed: list[str] = []
    for qid, text in answers:
        oq = out.open_answer(qid)
        text = text.strip()
        if oq is None or not text or text == oq.answer:
            continue
        oq.answer, oq.decided_by = text, by
        changed.append(qid)
    return out, changed


def diff(old: Sheet | None, new: Sheet) -> dict[str, list[str]]:
    """Which answers this iteration added or changed (by question id), for the review page."""
    before = {a.question: (a.choice, a.other) for a in (old.answers if old else [])}
    after = {a.question: (a.choice, a.other) for a in new.answers}
    return {
        "added": sorted(set(after) - set(before)),
        "changed": sorted(q for q in after if q in before and after[q] != before[q]),
        "removed": sorted(set(before) - set(after)),
    }


def render_adrs(cat: Catalogue | None, sheet: Sheet) -> dict[str, str]:
    """One short ADR per answer, numbered by the catalogue's order, so they always match the sheet;
    the answered open questions follow as one record."""
    out: dict[str, str] = {}
    questions = cat.questions if cat else []
    if sheet.open_questions:
        n = len(questions) + 1
        lines = [f"# {n:04d}. Open questions from the specification", ""]
        for oq in sheet.open_questions:
            lines += [f"## {oq.id}. {oq.question}", "", f"**Answer** ({oq.decided_by}): {oq.answer}", ""]
            if oq.rationale:
                lines += [f"Why: {oq.rationale}", ""]
        out[f"{ADR_DIR}/{n:04d}-open-questions.md"] = "\n".join(lines).rstrip() + "\n"
    for n, q in enumerate(questions, start=1):
        a = sheet.answer(q.id)
        if a is None:
            continue
        chosen = q.option(a.choice)
        title = (a.other or "").strip() if a.choice == OTHER else (chosen.title if chosen else a.choice)
        lines = [
            f"# {n:04d}. {q.title}",
            "",
            f"**Status:** decided · {a.decided_by} · pillar: {pillars.BY_ID[q.pillar].title}",
            "",
            "## Decision",
            f"{title}." if not title.endswith(".") else title,
        ]
        if chosen:
            lines += ["", "## Consequences", chosen.consequences]
        lines += ["", "## Why", a.rationale.strip() or "(no rationale given)"]
        if a.reason:
            lines += ["", f"Reason given by {a.decided_by}: {a.reason}"]
        if a.requirements:
            lines += ["", f"Serves: {', '.join(a.requirements)}"]
        rec = q.option(a.recommended)
        lines += ["", "## Options considered"]
        for o in q.options:
            marks = [m for m, on in (("chosen", o.id == a.choice), ("recommended", rec is o)) if on]
            lines.append(f"- **{o.title}**{' (' + ', '.join(marks) + ')' if marks else ''}: {o.consequences}")
        if a.choice == OTHER:
            lines.append(f"- **Other** (chosen): {title}")
        out[f"{ADR_DIR}/{n:04d}-{q.id}.md"] = "\n".join(lines) + "\n"
    return out


# ----------------------------------------------------------------- roadmap --
class RoadmapRow(BaseModel):
    pillar: str
    now: str
    deferred: str
    why: str
    trigger: str

    @property
    def defers(self) -> bool:
        return _cell(self.deferred) != ""


_EMPTY = {"", "-", "—", "–", "none", "nothing", "n/a", "na"}
_COLUMNS = ("pillar", "now", "deferred", "why", "trigger")


def _cell(text: str) -> str:
    t = text.strip().strip("*_` ").strip()
    return "" if t.lower() in _EMPTY else t


def _pillar_id(text: str) -> str | None:
    t = _cell(text).lower()
    for p in pillars.PILLARS:
        if t in (p.id, p.title.lower()) or t.split(" ")[0].rstrip("&,") == p.id:
            return p.id
    return None


def parse_roadmap(text: str | None) -> tuple[list[RoadmapRow], list[str]]:
    """`docs/roadmap.md`: a markdown table with the columns pillar, now, deferred, why and trigger,
    one row per quality pillar (ADR-0024). A deferred row needs a why and a trigger."""
    if text is None:
        return [], [f"{ROADMAP} is missing"]
    header: list[str] | None = None
    rows: list[RoadmapRow] = []
    errors: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if header is not None and rows:
                break  # the first table is the roadmap
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            names = [_cell(c).lower() for c in cells]
            if all(c in names for c in _COLUMNS):
                header = names
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        cell = dict(zip(header, cells, strict=False))
        pid = _pillar_id(cell.get("pillar", ""))
        if pid is None:
            errors.append(f"{ROADMAP}: '{cell.get('pillar', '')}' is not a quality pillar")
            continue
        row = RoadmapRow(pillar=pid, **{k: cell.get(k, "") for k in _COLUMNS[1:]})
        if row.defers and not (_cell(row.why) and _cell(row.trigger)):
            errors.append(f"{ROADMAP}: {pid} defers '{_cell(row.deferred)}' without a why and a trigger")
        rows.append(row)
    if header is None:
        return [], [f"{ROADMAP}: no table with the columns {', '.join(_COLUMNS)}"]
    seen = [r.pillar for r in rows]
    for pid in sorted({p for p in seen if seen.count(p) > 1}):
        errors.append(f"{ROADMAP}: {pid} has more than one row")
    for pid in pillars.IDS:
        if pid not in seen:
            errors.append(f"{ROADMAP}: no row for {pid}")
    return rows, errors


def roadmap_candidates(rows: list[RoadmapRow]) -> list[dict[str, str]]:
    """Deferred rows as work ledger candidates (category `roadmap`): what to add, why, when."""
    return [
        {
            "pillar": r.pillar,
            "key": f"{r.pillar}:{_cell(r.deferred).lower()}",
            "title": f"{pillars.BY_ID[r.pillar].title}: {_cell(r.deferred)}",
            "detail": f"Why deferred: {_cell(r.why)}\nAdd it when: {_cell(r.trigger)}",
        }
        for r in rows
        if r.defers
    ]
