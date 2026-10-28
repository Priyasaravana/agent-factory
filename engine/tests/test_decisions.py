"""Design decisions (ADR-0044 §1, §5): the pack's catalogue, the decision sheet the engine judges,
the ADRs rendered from it, the pillar roadmap folded into the work ledger, autonomous and ask mode on
the review page, and the discussion with the architect."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import yaml
from conftest import PRODUCT, REPO, wait_run
from test_api import _client

from agent_factory import decisions, ledger
from agent_factory.agents.fake import _ROADMAP
from agent_factory.decisions import Catalogue, CatalogueError, Sheet
from agent_factory.engine.pipeline import FactoryError, InvalidRequestError
from agent_factory.models import ChangeStatus, DecisionAnswer, EventKind
from agent_factory.packs import PackError, load_pack

CATALOGUE = REPO / "packs" / "nextjs-web" / "decisions.yaml"
WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})


def _cat() -> Catalogue:
    return decisions.load_catalogue(yaml.safe_load(CATALOGUE.read_text()), "test")


def _sheet(**choices: str) -> Sheet:
    """Every question answered with its first option, unless a choice is given."""
    answers = []
    for q in _cat().questions:
        pick = choices.get(q.id.replace("-", "_"), q.options[0].id)
        answers.append(
            {"question": q.id, "choice": pick, "recommended": pick, "rationale": f"serves {q.pillar} for v0"}
        )
    return Sheet.model_validate({"answers": answers})


# -------------------------------------------------------------- catalogue --
def test_the_next_js_pack_ships_its_design_questions():
    pack = load_pack(REPO / "packs" / "nextjs-web")
    assert pack.catalogue is not None
    assert [q.id for q in pack.catalogue.questions] == [
        "authentication",
        "sign-up",
        "authorisation",
        "data-store",
        "look",
    ]
    assert load_pack(REPO / "packs" / "fastapi-service").catalogue is None, "FastAPI asks none yet (deferred)"


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (lambda q: q["questions"][0].update(pillar="speed"), "pillar 'speed'"),
        (lambda q: q["questions"][0]["options"].append(dict(q["questions"][0]["options"][0])), "listed twice"),
        (lambda q: q["questions"].append(dict(q["questions"][0])), "question ids listed twice"),
        (lambda q: q["questions"][0]["options"][0].update(id="other"), "reserved"),
        (lambda q: q["questions"][0].update(options=q["questions"][0]["options"][:1]), "at least 2"),
    ],
)
def test_a_broken_catalogue_is_refused(mutate, problem):
    raw = yaml.safe_load(CATALOGUE.read_text())
    mutate(raw)
    with pytest.raises(CatalogueError, match=problem):
        decisions.load_catalogue(raw, "decisions.yaml")


def test_a_pack_with_a_broken_catalogue_does_not_load(tmp_path):
    import shutil

    copy = tmp_path / "nextjs-web"
    shutil.copytree(REPO / "packs" / "nextjs-web", copy, ignore=shutil.ignore_patterns("node_modules", ".next"))
    (copy / "decisions.yaml").write_text("questions: []\n")
    with pytest.raises(PackError, match="decisions.yaml"):
        load_pack(copy)


def test_lane_constraints_must_name_questions_and_options_a_pack_asks():
    cat = _cat()
    assert decisions.constraint_errors([cat], {"authentication": ["local"], "look": ["warm", "bold"]}) == []
    errors = decisions.constraint_errors([cat], {"caching": ["redis"], "authentication": ["oidc"], "look": []})
    assert "no installed pack asks 'caching'" in errors[1]
    assert any("'oidc' is not an option" in e for e in errors)
    assert any("look: list at least one" in e for e in errors)


# ------------------------------------------------------------------ judge --
def test_a_complete_sheet_passes_in_both_modes():
    assert decisions.judge(_cat(), _sheet(), {}, "autonomous") == []
    assert decisions.judge(_cat(), _sheet(), {}, "ask") == []


def test_the_engine_finds_what_is_missing_or_wrong():
    sheet = _sheet()
    sheet.answers = [a for a in sheet.answers if a.question != "look"]
    sheet.answers[0].rationale = "ok"
    sheet.answers[1].choice = "maybe"
    sheet.answers[2].recommended = "nobody"
    sheet.answers.append(sheet.answers[3].model_copy(update={"question": "caching"}))
    errors = decisions.judge(_cat(), sheet, {}, "ask")
    assert "look: not answered (What should the product look like?)" in errors
    assert any(e.startswith("authentication: give a one-line rationale") for e in errors)
    assert any(e.startswith("sign-up: 'maybe' is not an option") for e in errors)
    assert any(e.startswith("authorisation: recommended 'nobody'") for e in errors)
    assert "caching: not a question in this stack's catalogue" in errors


def test_autonomous_mode_stays_inside_the_lane_and_never_answers_other():
    cons = {"authentication": ["local"]}
    # the architect recommends no sign-in, the lane only allows local accounts: it chooses local
    sheet = _sheet(authentication="local")
    sheet.answer("authentication").recommended = "none"
    assert decisions.judge(_cat(), sheet, cons, "autonomous") == []
    stamped = decisions.stamp(sheet, cons, _cat())
    assert stamped.answer("authentication").decided_by == "architect (constrained)"
    assert stamped.answer("look").decided_by == "architect (recommended)"
    # outside the constraints, or not taking an allowed recommendation, or other: refused
    assert "outside this lane's constraints" in decisions.judge(_cat(), _sheet(), cons, "autonomous")[0]
    off = _sheet(look="warm")
    off.answer("look").recommended = "bold"
    assert "takes the recommendation 'bold'" in decisions.judge(_cat(), off, {}, "autonomous")[0]
    other = _sheet()
    other.answer("data-store").choice, other.answer("data-store").other = "other", "SQLite on a volume"
    assert "'other' needs a person" in decisions.judge(_cat(), other, {}, "autonomous")[0]
    assert decisions.judge(_cat(), other, {}, "ask") == []
    other.answer("data-store").other = " "
    assert "needs the concrete choice" in decisions.judge(_cat(), other, {}, "ask")[0]


def test_a_person_answers_over_the_architects_sheet():
    sheet = decisions.stamp(_sheet(), {}, _cat())
    answers = [
        DecisionAnswer(question="look", choice="warm", reason="a community product"),
        DecisionAnswer(question="authentication", choice="none"),  # unchanged, no reason: still the architect's
        DecisionAnswer(question="data-store", choice="other", other="Neon serverless Postgres"),
    ]
    new = decisions.apply_answers(sheet, answers, "saravana")
    assert (new.answer("look").choice, new.answer("look").decided_by) == ("warm", "saravana")
    assert new.answer("look").reason == "a community product"
    assert new.answer("authentication").decided_by == "architect (recommended)"
    assert (new.answer("data-store").other, new.answer("data-store").decided_by) == (
        "Neon serverless Postgres",
        "saravana",
    )
    assert decisions.diff(sheet, new) == {"added": [], "changed": ["data-store", "look"], "removed": []}
    assert decisions.diff(None, new)["added"] == sorted(q.id for q in _cat().questions)


def test_an_iteration_keeps_what_a_person_decided_in_autonomous_mode():
    """Book lending, 2026-10-27: iteration 2 (autonomous) turned the person's `warm` look back into the
    recommended `neutral`, because the engine made autonomous mode take the recommendation."""
    first = decisions.stamp(_sheet(look="neutral"), {}, _cat())
    agreed = decisions.apply_answers(
        first, [DecisionAnswer(question="look", choice="warm", reason="friendly")], "admin"
    )
    agreed.answer("data-store").choice, agreed.answer("data-store").other = "other", "SQLite on a volume"
    agreed.answer("data-store").decided_by = "admin"
    # the architect keeps both: the person's choices stand, "other" included
    kept = agreed.model_copy(deep=True)
    assert decisions.judge(_cat(), kept, {}, "autonomous", earlier=agreed) == []
    stamped = decisions.stamp(kept, {}, _cat(), earlier=agreed)
    assert (stamped.answer("look").decided_by, stamped.answer("look").reason) == ("admin", "friendly")
    assert stamped.answer("data-store").decided_by == "admin"
    assert stamped.answer("authentication").decided_by == "architect (recommended)"
    # without the earlier sheet, the old rule applies: that's what overwrote `warm`
    errors = decisions.judge(_cat(), kept, {}, "autonomous")
    assert any("look: autonomous mode takes the recommendation 'neutral'" in e for e in errors)
    assert any("data-store: 'other' needs a person" in e for e in errors)
    # the lane's constraints still win over a person's earlier answer
    assert (
        "outside this lane's constraints"
        in decisions.judge(_cat(), kept, {"look": ["neutral"]}, "autonomous", earlier=agreed)[0]
    )


def test_the_architect_cannot_claim_a_person_decided():
    sheet = _sheet(look="warm")
    sheet.answer("look").recommended = "neutral"
    sheet.answer("look").decided_by = "admin"  # written by the architect, no person said so
    assert "takes the recommendation 'neutral'" in decisions.judge(_cat(), sheet, {}, "autonomous")[0]
    assert decisions.stamp(sheet, {}, _cat()).answer("look").decided_by == "architect (recommended)"
    # a changed choice is the architect's, even where a person had answered before
    earlier = _sheet(look="bold")
    earlier.answer("look").decided_by = "admin"
    assert "takes the recommendation 'neutral'" in decisions.judge(_cat(), sheet, {}, "autonomous", earlier=earlier)[0]


def test_the_sheet_round_trips_and_bad_files_say_why():
    sheet = _sheet()
    again, errors = decisions.parse_sheet(decisions.dump_sheet(sheet))
    assert errors == [] and again == sheet
    assert decisions.parse_sheet(None)[1] == ["docs/decisions.yaml is missing"]
    assert "not valid YAML" in decisions.parse_sheet("answers: [")[1][0]
    assert "must be a mapping" in decisions.parse_sheet("just text")[1][0]
    bare, _ = decisions.parse_sheet(yaml.safe_dump([a.model_dump() for a in sheet.answers]))
    assert bare == sheet, "a bare list is read as the answers"


def test_one_adr_per_answer_numbered_by_the_catalogue():
    sheet = decisions.apply_answers(
        decisions.stamp(_sheet(authentication="local"), {}, _cat()),
        [
            DecisionAnswer(
                question="data-store", choice="other", other="Neon serverless Postgres", reason="team uses it"
            )
        ],
        "saravana",
    )
    sheet.answer("authentication").requirements = ["R2"]
    adrs = decisions.render_adrs(_cat(), sheet)
    assert sorted(adrs) == [
        "docs/adr/0001-authentication.md",
        "docs/adr/0002-sign-up.md",
        "docs/adr/0003-authorisation.md",
        "docs/adr/0004-data-store.md",
        "docs/adr/0005-look.md",
    ]
    auth = adrs["docs/adr/0001-authentication.md"]
    assert auth.startswith("# 0001. Who can sign in, and how?")
    assert "architect (recommended) · pillar: Security" in auth and "Local accounts with sessions." in auth
    assert "**Local accounts with sessions** (chosen, recommended)" in auth and "Serves: R2" in auth
    data = adrs["docs/adr/0004-data-store.md"]
    assert "Neon serverless Postgres" in data and "Reason given by saravana: team uses it" in data
    assert "**Other** (chosen)" in data


# ---------------------------------------------------------------- roadmap --
def test_the_roadmap_has_one_row_per_pillar_and_deferrals_say_why_and_when():
    rows, errors = decisions.parse_roadmap(_ROADMAP)
    assert errors == [] and len(rows) == 10
    assert [r.pillar for r in rows if r.defers] == ["performance"]
    assert decisions.roadmap_candidates(rows) == [
        {
            "pillar": "performance",
            "key": "performance:load test",
            "title": "Performance & scalability: load test",
            "detail": "Why deferred: no traffic yet\nAdd it when: first real users",
        }
    ]
    broken = _ROADMAP.replace("| cost | basics | — |", "| cost | basics | budgets |").replace(
        "| compliance |", "| speed |"
    )
    _, errors = decisions.parse_roadmap(broken)
    assert "docs/roadmap.md: cost defers 'budgets' without a why and a trigger" in errors
    assert "docs/roadmap.md: 'speed' is not a quality pillar" in errors
    assert "docs/roadmap.md: no row for compliance" in errors
    assert decisions.parse_roadmap("# nothing here")[1] == [
        "docs/roadmap.md: no table with the columns pillar, now, deferred, why, trigger"
    ]
    titled = _ROADMAP.replace("| performance |", "| **Performance & scalability** |")
    assert decisions.parse_roadmap(titled)[1] == [], "pillar titles are read as their ids"


def test_deferred_rows_become_ledger_items_and_close_when_taken_in():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    items: list = []
    cands = decisions.roadmap_candidates(decisions.parse_roadmap(_ROADMAP)[0])
    changed, stats = ledger.fold_roadmap("p1", items, cands, change_id="c1", iteration=1, now=now)
    assert stats == {"new": 1, "fixed": 0, "seen": 0}
    item = changed[0]
    assert (item.kind, item.category, item.change_kind, item.status) == ("recommendation", "roadmap", "feature", "open")
    assert item.detail.startswith("Why deferred: no traffic yet")
    _, stats = ledger.fold_roadmap("p1", items, cands, change_id="c1", iteration=1, now=now)
    assert stats == {"new": 0, "fixed": 0, "seen": 0}, "the same roadmap again records nothing"
    _, stats = ledger.fold_roadmap("p1", items, cands, change_id="c2", iteration=2, now=now)
    assert stats["seen"] == 1 and items[0].seen_in == [1, 2]
    changed, stats = ledger.fold_roadmap("p1", items, [], change_id="c3", iteration=3, now=now)
    assert stats["fixed"] == 1 and changed[0].status == "fixed" and changed[0].resolved_by == "c3"


# ------------------------------------------------------------ in the lane --
def _lane(f, mode: str = "autonomous", spec_review: str = "off", constraints: dict | None = None) -> None:
    w = f.workflows["new-product"]
    w.draft.set_decisions(mode, constraints or {})
    w.draft.set_spec_review(spec_review)
    w.draft.publish(f"decisions {mode}")


def _sheet_in(f, change_id: str) -> Sheet:
    sheet, _ = decisions.parse_sheet((f.manager.ws.change_dir(change_id) / decisions.SHEET).read_text())
    assert sheet is not None
    return sheet


async def test_autonomous_mode_decides_records_adrs_and_ledger_items(make_factory):
    f = make_factory()
    _lane(f, constraints={"authentication": ["local"]})
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    sheet = _sheet_in(f, change.id)
    assert sheet.answer("authentication").choice == "local", "the lane's constraint"
    assert {a.decided_by for a in sheet.answers} == {"architect (recommended)"}
    wt = f.manager.ws.change_dir(change.id)
    assert (wt / "docs" / "adr" / "0001-authentication.md").is_file()
    assert (wt / "docs" / "roadmap.md").is_file()
    messages = [e.message for e in f.store.list_events(change.id)]
    assert "decision sheet: 5 answers, 5 new or changed; decided (autonomous)" in messages
    items = [i for i in f.store.list_work_items(product.id) if i.category == "roadmap"]
    assert [i.title for i in items] == ["Performance & scalability: load test"]
    evidence = f.manager.ws.data_dir / "artifacts" / change.id / "decisions.json"
    assert evidence.is_file() and '"mode": "autonomous"' in evidence.read_text()


async def test_a_pack_without_questions_still_gets_a_roadmap(make_factory):
    f = make_factory()
    product = f.manager.create_product(PRODUCT)  # fastapi-service
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    wt = f.manager.ws.change_dir(change.id)
    assert not (wt / decisions.SHEET).exists() and (wt / decisions.ROADMAP).is_file()
    messages = [e.message for e in f.store.list_events(change.id)]
    assert any(m.startswith("no decision sheet: fastapi-service") for m in messages)


async def test_ask_mode_waits_and_aligns_everything_in_one_turn(make_factory):
    f = make_factory()
    _lane(f, "ask")
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    held = f.store.get_change(change.id)
    assert held.decisions_pending and held.summary == "design decisions ready for your review"

    # choices are judged as a whole before anything goes to the architect
    with pytest.raises(InvalidRequestError, match="not an option"):
        await f.manager.align(change.id, "saravana", [DecisionAnswer(question="look", choice="pink")], [], "")
    with pytest.raises(InvalidRequestError, match="not design questions"):
        await f.manager.align(change.id, "saravana", [DecisionAnswer(question="caching", choice="xx")], [], "")
    with pytest.raises(InvalidRequestError, match="nothing to send"):
        await f.manager.align(change.id, "saravana", [DecisionAnswer(question="look", choice="neutral")], [], " ")

    # choices, an Other… answer and a comment: saved, then one architect turn with all of it
    await f.manager.align(
        change.id,
        "saravana",
        [
            DecisionAnswer(question="look", choice="warm", reason="community product"),
            DecisionAnswer(question="data-store", choice="other", other="Neon serverless Postgres"),
        ],
        [],
        "Why admin-created accounts and not open sign-up?",
    )
    with pytest.raises(FactoryError, match="still answering"):
        await f.manager.align(change.id, "saravana", [], [], "and another thing")
    sheet = _sheet_in(f, change.id)
    assert sheet.answer("look").decided_by == "saravana"
    adr = f.manager.ws.change_dir(change.id) / "docs/adr/0004-data-store.md"
    assert "Neon serverless Postgres" in adr.read_text()
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    after = f.store.get_change(change.id)
    assert [t.role for t in after.discussion] == ["person", "architect"]
    sent = after.discussion[0].text
    assert sent.startswith("I decided:\n- Decision data-store: Neon serverless Postgres\n- Decision look: Warm")
    assert "(reason: community product)" in sent and sent.endswith("Why admin-created accounts and not open sign-up?")
    assert after.discussion[1].text == "[dry-run] the design now follows your decisions"
    assert after.discussion[1].files == ["docs/design.md"]
    assert after.discuss_pending is None and after.attempts.get("design") == 1

    # a comment that asks the architect to change a choice
    await f.manager.align(change.id, "saravana", [], [], "Please set authentication to local")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    assert _sheet_in(f, change.id).answer("authentication").choice == "local"

    f.manager.approve_spec(change.id, "saravana")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    events = f.store.list_events(change.id)
    assert any(e.message == "saravana sent their input to the architect (decided data-store, look)" for e in events)
    assert any(
        e.kind == EventKind.decision and e.message == "architect replied on the review page; changed docs/design.md"
        for e in events
    )
    assert (f.manager.ws.data_dir / "artifacts" / change.id / "discussion.json").is_file()


async def test_open_questions_go_to_the_architect_not_back_to_the_requester(make_factory):
    from agent_factory.agents import FakeAgentRunner

    agents = FakeAgentRunner(open_questions=["Should books have a cover image?", "Can two users share a shelf?"])
    f = make_factory(agents=agents)
    _lane(f, "ask")
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval, "no needs_input: intake didn't stop"
    sheet = _sheet_in(f, change.id)
    assert [(q.id, q.question, q.decided_by) for q in sheet.open_questions] == [
        ("Q1", "Should books have a cover image?", "architect (proposed)"),
        ("Q2", "Can two users share a shelf?", "architect (proposed)"),
    ]
    wt = f.manager.ws.change_dir(change.id)
    record = (wt / "docs/adr/0006-open-questions.md").read_text()
    assert "## Q1. Should books have a cover image?" in record and "[dry-run] default for Q1" in record

    # the person answers one open question in their own words, with a comment, in one send
    with pytest.raises(InvalidRequestError, match="not open questions"):
        await f.manager.align(change.id, "saravana", [], [("Q9", "yes")], "")
    await f.manager.align(change.id, "saravana", [], [("Q2", "No, one shelf per user")], "Keep it simple.")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    q2 = _sheet_in(f, change.id).open_answer("Q2")
    assert (q2.answer, q2.decided_by) == ("No, one shelf per user", "saravana")
    first = f.store.get_change(change.id).discussion[0].text
    assert "- Open question Q2 (Can two users share a shelf?): No, one shelf per user" in first
    f.manager.approve_spec(change.id, "saravana")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback


async def test_autonomous_mode_answers_open_questions_without_waiting(make_factory):
    from agent_factory.agents import FakeAgentRunner

    f = make_factory(agents=FakeAgentRunner(open_questions=["Should books have a cover image?"]))
    change = f.manager.start_change(f.manager.create_product(PRODUCT))  # FastAPI: no design questions
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    sheet = _sheet_in(f, change.id)
    assert sheet.answers == [] and sheet.open_questions[0].answer == "[dry-run] default for Q1"
    messages = [e.message for e in f.store.list_events(change.id)]
    assert "decision sheet: 0 answers, 0 new or changed, 1 open questions answered; decided (autonomous)" in messages


async def test_a_requirements_change_agreed_in_alignment_goes_back_to_the_specification(make_factory):
    from agent_factory.agents import FakeAgentRunner

    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    _lane(f, "ask")
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    await f.manager.align(change.id, "saravana", [], [], "Searching by title matters: change the requirement")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    after = f.store.get_change(change.id)
    assert after.review_notes == ["R2 also filters by title (agreed with saravana)"]
    assert after.rework_requirements is None and after.current_station == "implement"
    assert "the specification is being updated first: R2 also filters by title" in after.discussion[1].text
    started = [e.station for e in f.store.list_events(change.id) if e.kind == EventKind.station_started]
    assert started == ["requirements", "design", "design", "requirements", "design"]
    intake = [c for c in agents.calls if c.role == "intake"][-1]
    assert "R2 also filters by title (agreed with saravana)" in intake.prompt
    architect = [c for c in agents.calls if c.role == "architect"][-1]
    assert "The alignment with the person so far" in architect.prompt and "change the requirement" in architect.prompt


async def test_approval_waits_for_a_complete_sheet(make_factory):
    f = make_factory()
    _lane(f, "ask")
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    (f.manager.ws.change_dir(change.id) / decisions.SHEET).write_text("answers: []\n")
    with pytest.raises(FactoryError, match="answer every design decision first: authentication: not answered"):
        f.manager.approve_spec(change.id, "saravana")


async def test_ask_mode_and_spec_review_share_one_gate(make_factory):
    f = make_factory()
    _lane(f, "ask", spec_review="first")
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    assert f.store.get_change(change.id).summary == "spec and design decisions ready for your review"
    f.manager.approve_spec(change.id, "saravana")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback


async def test_a_fastapi_product_on_an_ask_lane_does_not_wait(make_factory):
    f = make_factory()
    _lane(f, "ask")
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback, "its pack asks no questions"


async def test_publish_refuses_constraints_no_pack_can_answer(make_factory):
    from agent_factory.engine.workflows import WorkflowError

    f = make_factory()
    w = f.workflows["new-product"]
    w.draft.set_decisions("autonomous", {"authentication": ["oidc"]})
    with pytest.raises(WorkflowError, match="'oidc' is not an option"):
        w.draft.publish("bad constraint")


async def test_the_review_page_api(make_factory):
    f = make_factory()
    _lane(f, "ask", constraints={"data-store": ["postgres-chart"]})
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    _, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/changes/{change.id}/decisions")).json()
        assert (view["mode"], view["pending"], view["asked"]) == ("ask", True, True)
        assert [q["id"] for q in view["questions"]][:2] == ["authentication", "sign-up"]
        data = next(q for q in view["questions"] if q["id"] == "data-store")
        assert [(o["id"], o["allowed"]) for o in data["options"]] == [
            ("postgres-chart", True),
            ("postgres-external", False),
        ]
        assert len(view["answers"]) == 5 and len(view["roadmap"]) == 10 and view["problems"] == []
        assert view["open_questions"] == []
        assert sorted(view["changed"]) == sorted(q["id"] for q in view["questions"])
        bad = await c.post(
            f"/api/changes/{change.id}/align",
            json={"answers": [{"question": "data-store", "choice": "postgres-external"}]},
        )
        assert bad.status_code == 422 and "outside this lane's constraints" in bad.text
        r = await c.post(
            f"/api/changes/{change.id}/align",
            json={"answers": [{"question": "look", "choice": "bold"}], "message": "Why Postgres?"},
        )
        assert r.status_code == 200 and r.json()["discuss_pending"].endswith("Why Postgres?")
        assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
        view = (await c.get(f"/api/changes/{change.id}/decisions")).json()
        assert any(a["choice"] == "bold" for a in view["answers"])
        assert [t["role"] for t in view["discussion"]] == ["person", "architect"]
        assert view["discussion"][1]["text"] == "[dry-run] the design now follows your decisions"
    await ctx.__aexit__(None, None, None)


async def test_a_discussion_changes_only_the_design_documents(make_factory):
    f = make_factory()
    _lane(f, "ask")
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    wt = f.manager.ws.change_dir(change.id)
    agents_md = (wt / "AGENTS.md").read_text()
    await f.manager.align(change.id, "saravana", [], [], "please also edit the code, and set look to bold")
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_approval
    assert not (wt / "src" / "stray.ts").exists() and (wt / "AGENTS.md").read_text() == agents_md
    reply = f.store.get_change(change.id).discussion[-1]
    assert reply.files == ["docs/decisions.yaml"]
    assert "undid changes outside the design documents: AGENTS.md, src/stray.ts" in reply.text
    assert _sheet_in(f, change.id).answer("look").choice == "bold"
