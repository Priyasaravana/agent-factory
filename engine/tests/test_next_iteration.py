"""Planning a delivered product's next iteration (roadmap J2): suggested features in the work ledger,
the "Next iteration" groups and the change kind the engine picks. Pure: no API, no agents."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_factory import ledger
from agent_factory.models import WorkItem

T0 = datetime(2026, 10, 28, 19, tzinfo=UTC)
P = "p1"


def _suggestion(title: str, value: str = "medium", size: str = "M", **more) -> dict:
    return {
        "title": title,
        "detail": f"{title}: people using the product can do more",
        "value": value,
        "size": size,
        "requirements": [f"R9: {title}"],
        **more,
    }


def _fold(items: list[WorkItem], raw: list[dict], iteration: int = 1, change: str = "c1") -> dict[str, int]:
    clean, errors = ledger.parse_suggestions(raw)
    assert errors == []
    return ledger.fold_features(P, items, clean, change_id=change, iteration=iteration, now=T0)[1]


def _image(title: str, detail: str) -> WorkItem:
    fp = ledger.image_fingerprint(title)
    return WorkItem(
        id=ledger.item_id(P, fp),
        product_id=P,
        kind="issue",
        fingerprint=fp,
        title=title,
        severity="high",
        category=ledger.IMAGE,
        detail=detail,
        created_at=T0,
        updated_at=T0,
    )


def test_suggestions_are_checked_before_they_reach_the_ledger():
    clean, errors = ledger.parse_suggestions([_suggestion("Search books", value="HIGH", size="s")])
    assert errors == [] and (clean[0]["value"], clean[0]["size"], clean[0]["by"]) == ("high", "S", "architect")
    assert ledger.parse_suggestions([])[1] == ["give 1 to 5 suggestions as a list"]
    assert "at most 5" in ledger.parse_suggestions([_suggestion(f"Feature {n}") for n in range(6)])[1][0]
    _, errors = ledger.parse_suggestions(
        [
            {"title": "x", "detail": "short", "value": "urgent", "size": "XL", "requirements": "R1"},
            _suggestion("Search books"),
            _suggestion("search  BOOKS"),
        ]
    )
    assert errors == [
        "suggestion 1: needs a title",
        "suggestion 1 (x): say what it does for the people using the product",
        "suggestion 1 (x): value is one of high, medium, low",
        "suggestion 1 (x): size is one of S, M, L",
        "suggestion 1 (x): requirements is a list of the requirements it would add",
        "suggestion 3 (search BOOKS): suggested twice",
    ]


def test_suggested_features_become_ledger_items_and_are_recognised_again():
    items: list[WorkItem] = []
    assert _fold(items, [_suggestion("Search books", "high", "S"), _suggestion("Due dates", by="designer")]) == {
        "new": 2,
        "seen": 0,
    }
    search = next(it for it in items if it.title == "Search books")
    assert (search.kind, search.category, search.change_kind, search.severity, search.effort) == (
        "recommendation",
        "feature",
        "feature",
        "high",
        "S",
    )
    assert search.detail.endswith("Would add: R9: Search books")
    assert "suggested by the designer" in next(it for it in items if it.title == "Due dates").history[0].note
    # the next delivery suggests it again, reworded in case and spacing only: the same item
    assert _fold(items, [_suggestion("search   Books", "medium", "M")], iteration=2, change="c2") == {
        "new": 0,
        "seen": 1,
    }
    assert len(items) == 2 and (search.severity, search.effort, search.seen_in) == ("medium", "M", [1, 2])
    # not suggested again: still open (a judgement, not a check)
    assert next(it for it in items if it.title == "Due dates").status == "open"


def test_a_rejected_feature_stays_rejected_when_suggested_again():
    items: list[WorkItem] = []
    _fold(items, [_suggestion("Search books", "high")])
    ledger.decide(items, [items[0].id], "wont_do", reason="search is out of scope for v1", now=T0, by="admin")
    _fold(items, [_suggestion("Search books", "low")], iteration=2, change="c2")
    assert (items[0].status, items[0].severity) == ("wont_do", "high")
    assert [e.event for e in items[0].history][-1] == "seen"


def test_a_delivered_feature_is_done_and_a_failed_one_goes_back():
    items: list[WorkItem] = []
    _fold(items, [_suggestion("Search books"), _suggestion("Due dates")])
    roadmap = ledger.fold_roadmap(
        P,
        items,
        [{"key": "perf", "title": "Cache pages", "detail": "later", "pillar": "performance"}],
        change_id="c1",
        iteration=1,
        now=T0,
    )[0][0]
    ids = [it.id for it in items]
    ledger.claim(items, ids, change_id="c2", iteration=2, now=T0, by="admin")
    ledger.release(items, change_id="c2", status="awaiting_feedback", now=T0, pr_url=None)
    features = [it for it in items if it.category == ledger.FEATURE]
    assert {(it.status, it.resolved_by) for it in features} == {("fixed", "c2")}
    assert roadmap.status == "in_progress"  # deferred pillar work is verified by a later assessment
    items2: list[WorkItem] = []
    _fold(items2, [_suggestion("Search books")])
    ledger.claim(items2, [items2[0].id], change_id="c3", iteration=2, now=T0, by="admin")
    ledger.release(items2, change_id="c3", status="failed", now=T0, pr_url=None)
    assert items2[0].status == "open"


def test_the_next_iteration_card_groups_open_items():
    items: list[WorkItem] = []
    _fold(items, [_suggestion("Due dates", "low"), _suggestion("Search books", "high")])
    ledger.fold_roadmap(
        P,
        items,
        [{"key": "perf", "title": "Cache pages", "detail": "later", "pillar": "performance"}],
        change_id="c1",
        iteration=1,
        now=T0,
    )
    fixable = _image("CVE-2026-1 in openssl 3.0.1 (base image)", "fixed in 3.0.2. Buffer overflow")
    stuck = _image("CVE-2026-2 in zlib 1.2 (base image)", "no fix released yet. Crash")
    items += [fixable, stuck]
    ledger.decide(
        items,
        [next(it.id for it in items if it.title == "Due dates")],
        "wont_do",
        reason="not needed by this team",
        now=T0,
        by="admin",
    )
    groups = ledger.next_iteration(items)
    assert [it.title for it in groups["features"]] == ["Search books"]  # decided items leave the card
    assert [it.title for it in groups["quality"]] == ["Cache pages"]
    assert [it.title for it in groups["security"]] == [fixable.title]  # no fix released: nothing to pick


def test_the_engine_picks_the_change_kind():
    items: list[WorkItem] = []
    _fold(items, [_suggestion("Search books")])
    feature = items[0]
    cve = _image("CVE-2026-1 in openssl 3.0.1 (base image)", "fixed in 3.0.2")
    bug = cve.model_copy(update={"change_kind": "bug", "category": "rules:code"})
    assert ledger.pick_kind([cve]) == "upkeep"  # security fixes alone keep behaviour
    assert ledger.pick_kind([cve, feature]) == "feature"  # a feature carries the fixes
    assert ledger.pick_kind([cve, bug]) == "bug"
    assert ledger.pick_kind([]) == "feature"  # free text only asks for new behaviour


def test_a_ticked_suggestion_reaches_intake_whole():
    """UX-16: the CSV export picked on the card reached intake cut off mid-sentence ("with dat")."""
    reqs = [f"R{n}: the CSV export includes column {n} with data from every claim in the period" for n in range(10)]
    detail = "Export the claims list as CSV so finance can reconcile it in their own tools. " * 8
    items: list[WorkItem] = []
    _fold(items, [_suggestion("CSV export", detail=detail.strip(), requirements=reqs)])
    text = ledger.request_text("Next iteration", items)
    assert detail.strip() in text.replace("\n  ", "\n")
    for r in reqs:
        assert r in text


def test_a_suggestion_too_long_to_carry_goes_back_rather_than_being_cut():
    eleven = [f"R{n}: something" for n in range(11)]
    _, errors = ledger.parse_suggestions([_suggestion("CSV export", requirements=eleven)])
    assert errors == ["suggestion 1 (CSV export): at most 10 requirements: split it into two suggestions"]
    _, errors = ledger.parse_suggestions([_suggestion("CSV export", detail="d" * 1001)])
    assert errors == ["suggestion 1 (CSV export): say what it does in at most 1000 characters"]
    _, errors = ledger.parse_suggestions([_suggestion("CSV export", requirements=["R1: " + "x" * 300])])
    assert errors == ["suggestion 1 (CSV export): each requirement in at most 300 characters"]


# ------------------------------------------------- serious UI defects (UX-04) --
PHONE = "manage-phone-light.png"
WIDE = "The Manage page is 576 px wide at 390 px; the Approve and Reject buttons are off-screen."


def _review(*findings: tuple[str, str, str], shots: tuple[str, ...] = (PHONE, "home-phone-dark.png"), complete=True):
    return {
        "complete": complete,
        "screenshots": list(shots),
        "findings": [{"screenshot": s, "issue": i, "severity": sev} for s, i, sev in findings],
    }


def _fold_ui(items: list[WorkItem], review: dict, iteration: int = 1, change: str = "c1") -> dict[str, int]:
    return ledger.fold_ui_review(P, items, review, change_id=change, iteration=iteration, now=T0)[1]


def test_serious_ui_defects_become_open_items_and_minor_ones_stay_in_the_review():
    items: list[WorkItem] = []
    stats = _fold_ui(items, _review((PHONE, WIDE, "serious"), ("home-phone-dark.png", "gaps uneven", "minor")))
    assert stats == {"new": 1, "seen": 0, "fixed": 0, "reopened": 0}
    [it] = items
    assert (it.title, it.detail, it.at) == ("The Manage page is 576 px wide at 390 px", WIDE, [PHONE])
    assert (it.kind, it.category, it.severity, it.change_kind, it.status) == ("issue", "ui", "high", "bug", "open")
    assert it.history[-1].note == f"serious in the UI review ({PHONE})"
    assert ledger.next_iteration(items)["ui"] == [it]
    assert ledger.pick_kind([it]) == "bug"
    # what the developer gets for it names the screen and the whole defect (UX-16)
    assert f"[{it.id}] high · {it.title} ({PHONE})" in ledger.request_text("fix", [it])
    assert WIDE in ledger.request_text("fix", [it])


def test_a_reworded_defect_on_the_same_screen_is_the_same_item_and_an_incomplete_review_changes_nothing():
    items: list[WorkItem] = []
    _fold_ui(items, _review((PHONE, WIDE, "serious")))
    reworded = "At 390 px the Manage page is 576 px wide, so the Approve and Reject buttons are off-screen."
    assert _fold_ui(items, _review((PHONE, reworded, "serious")), iteration=2, change="c2")["seen"] == 1
    assert len(items) == 1 and items[0].detail == reworded and items[0].seen_in == [1, 2]
    # a different screen is a different defect, even worded alike
    _fold_ui(
        items,
        _review(("home-phone-dark.png", WIDE, "serious"), shots=("home-phone-dark.png",)),
        iteration=3,
        change="c3",
    )
    assert len(items) == 2
    assert _fold_ui(items, _review(complete=False), iteration=4, change="c4") == {
        "new": 0,
        "seen": 0,
        "fixed": 0,
        "reopened": 0,
    }
    assert all(it.status == "open" for it in items), "an incomplete review fixes nothing"


def test_a_ui_defect_is_fixed_only_when_a_later_review_looked_at_its_screen():
    items: list[WorkItem] = []
    _fold_ui(items, _review((PHONE, WIDE, "serious")))
    # the next review didn't look at the Manage page (a route removed from the run, a capped set): open
    assert _fold_ui(items, _review(shots=("home-phone-dark.png",)), iteration=2, change="c2")["fixed"] == 0
    assert items[0].status == "open"
    assert _fold_ui(items, _review(), iteration=3, change="c3")["fixed"] == 1
    assert (items[0].status, items[0].resolved_by) == ("fixed", "c3")
    assert ledger.next_iteration(items)["ui"] == []
    # back again later: open again, with its history
    assert _fold_ui(items, _review((PHONE, WIDE, "serious")), iteration=4, change="c4")["reopened"] == 1
    assert items[0].status == "open" and [h.event for h in items[0].history][-2:] == ["fixed", "reappeared"]


def test_a_person_s_decision_on_a_ui_defect_stands():
    items: list[WorkItem] = []
    _fold_ui(items, _review((PHONE, WIDE, "serious")))
    ledger.decide(items, [items[0].id], "wont_do", reason="admin page, desktop only", now=T0, by="admin")
    _fold_ui(items, _review((PHONE, "Manage page wider than the phone.", "serious")), iteration=2, change="c2")
    _fold_ui(items, _review(), iteration=3, change="c3")
    assert [it.status for it in items if it.at == [PHONE]][0] == "wont_do"
    assert ledger.next_iteration(items)["ui"] == [it for it in items if it.status == "open"]
