"""Look and feel (ADR-0044 §4, nextjs-web 1.4.0): the designer in the design station, route
screenshots kept as evidence, and the UI review that reports only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import PRODUCT, REPO, wait_run
from test_api import _client

from agent_factory import ui_review
from agent_factory.agents import FakeAgentRunner
from agent_factory.agents.runner import AgentRequest
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.packs import PackError, load_pack

PACK = REPO / "packs" / "nextjs-web"
WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
SHOTS = ["home-desktop-light.png", "home-phone-dark.png", "sign-in-desktop-light.png"]


# ------------------------------------------------------------------ rubric --
def test_the_pack_ships_a_rubric_and_a_designer_skill():
    pack = load_pack(PACK)
    assert pack.stack is not None and pack.stack.ui is not None
    assert pack.stack.ui.screenshots == "e2e-screenshots"
    assert (PACK / pack.stack.ui.skill / "SKILL.md").is_file()
    rubric = ui_review.parse_rubric(yaml.safe_load((PACK / pack.stack.ui.rubric).read_text()))
    assert rubric.ids == ["hierarchy", "spacing", "consistency", "states", "responsive"]


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({}, "non-empty list"),
        ({"criteria": [{"id": "x1", "title": "t"}]}, "needs id, title and look_for"),
        ({"criteria": [{"id": "Bad Id", "title": "t", "look_for": "l"}]}, "lowercase slug"),
        ({"criteria": [{"id": "ab", "title": "t", "look_for": "l"}] * 2}, "listed twice"),
    ],
)
def test_a_broken_rubric_is_refused(raw, error):
    with pytest.raises(ui_review.RubricError, match=error):
        ui_review.parse_rubric(raw)


def test_a_pack_whose_ui_section_points_nowhere_is_refused(tmp_path):
    import shutil

    from agent_factory.packs import IGNORED

    copy = tmp_path / "pack"
    shutil.copytree(PACK, copy, ignore=shutil.ignore_patterns(*IGNORED))
    raw = yaml.safe_load((copy / "pack.yaml").read_text())
    raw["stack"]["ui"]["rubric"] = "missing.yaml"
    (copy / "pack.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(PackError, match="stack.ui.rubric"):
        load_pack(copy)


# ----------------------------------------------------------------- collect --
def test_collect_keeps_named_pngs_only_and_replaces_the_last_run(tmp_path):
    src, dest = tmp_path / "shots", tmp_path / "evidence"
    src.mkdir()
    for name in SHOTS:
        (src / name).write_bytes(PNG)
    (src / "notes.txt").write_text("x")
    (src / "-flag.png").write_bytes(PNG)  # could read as an option: skipped
    (src / "Space Name.png").write_bytes(PNG)  # not a route-size-scheme name: skipped
    (src / "big.png").write_bytes(b"0" * (ui_review.MAX_SHOT_BYTES + 1))
    (src / "link.png").symlink_to(src / SHOTS[0])
    (dest).mkdir()
    (dest / "stale.png").write_bytes(PNG)
    kept = ui_review.collect(src, dest)
    assert kept == SHOTS
    assert sorted(p.name for p in dest.iterdir()) == kept  # the stale picture is gone
    assert ui_review.collect(tmp_path / "none", dest) == [] and not dest.exists()


def test_collect_stops_at_the_cap(tmp_path):
    src = tmp_path / "shots"
    src.mkdir()
    for i in range(ui_review.MAX_SHOTS + 5):
        (src / f"r{i:03d}-desktop-light.png").write_bytes(PNG)
    assert len(ui_review.collect(src, tmp_path / "out")) == ui_review.MAX_SHOTS


# ------------------------------------------------------------------- judge --
RUBRIC = ui_review.parse_rubric(
    {"criteria": [{"id": i, "title": i, "look_for": "x"} for i in ("hierarchy", "spacing", "states")]}
)


def test_a_complete_report_is_summarised_and_never_blocks():
    report = {
        "summary": "clean",
        "scores": [
            {"criterion": "hierarchy", "score": 4, "why": "clear title"},
            {"criterion": "spacing", "score": 2, "why": "cramped table"},
            {"criterion": "states", "score": 5, "why": "good empty state"},
        ],
        "findings": [
            {"screenshot": "e2e-screenshots/home-phone-dark.png", "issue": "header wraps"},
            {"screenshot": "imagined.png", "issue": "x"},
        ],
    }
    r = ui_review.judge(RUBRIC, report, SHOTS)
    assert r.complete and r.mean == 3.67
    assert r.headline() == "UI review 3.67/5 (reported only); weak: spacing"
    assert [f["screenshot"] for f in r.findings] == ["home-phone-dark.png", ""]
    assert [f["severity"] for f in r.findings] == ["minor", "minor"], "an older report without severities"
    assert r.as_data()["blocking"] is False and r.as_data()["serious"] == 0


def test_serious_defects_are_named_on_a_screenshot_and_still_never_block():
    """UX-04: a page 576 px wide at 390 px is serious; it is counted and headlined, never a failure.
    A serious finding on no real screenshot can't be shown or checked again: it is kept as minor."""
    report = {
        "summary": "the phone layout breaks",
        "scores": [{"criterion": c, "score": 3, "why": ""} for c in ("hierarchy", "spacing", "states")],
        "findings": [
            {
                "screenshot": "home-phone-dark.png",
                "issue": "Page is 576 px wide; Approve is off-screen.",
                "severity": "serious",
            },
            {"screenshot": "imagined.png", "issue": "broken", "severity": "serious"},
            {"screenshot": "home-desktop-light.png", "issue": "gaps uneven", "severity": "cosmetic"},
        ],
    }
    r = ui_review.judge(RUBRIC, report, SHOTS)
    assert [f["severity"] for f in r.findings] == ["serious", "minor", "minor"]
    assert r.headline() == "UI review 3.0/5 (reported only); 1 serious defect"
    assert r.as_data()["serious"] == 1 and r.as_data()["blocking"] is False
    assert ui_review.schema(RUBRIC)["properties"]["findings"]["items"]["properties"]["severity"]["enum"] == [
        "serious",
        "minor",
    ]


def test_an_incomplete_report_says_what_is_missing():
    report = {
        "scores": [
            {"criterion": "hierarchy", "score": 9, "why": ""},
            {"criterion": "spacing", "score": 3},
            {"criterion": "spacing", "score": 3},
            {"criterion": "colour", "score": 3},
        ]
    }
    r = ui_review.judge(RUBRIC, report, SHOTS)
    assert not r.complete
    assert r.problems == [
        "'hierarchy' score must be 1–5",
        "'spacing' scored twice",
        "unknown criterion 'colour'",
        "not scored: states",
    ]
    assert r.headline().startswith("UI review incomplete (reported only)")
    assert not ui_review.judge(RUBRIC, None, SHOTS).complete


# --------------------------------------------------------------- in the lane --
@dataclass
class ShootingExecutor(FakeExecutor):
    """The e2e suite saves its route screenshots into the repo, as the template's spec does."""

    async def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 600,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        res = await super().run(command, cwd, timeout, env)
        if "make e2e" in command and cwd is not None:
            shots = Path(cwd) / "e2e-screenshots"
            shots.mkdir(exist_ok=True)
            for name in SHOTS:
                (shots / name).write_bytes(PNG)
        return res


class StrayDesigner(FakeAgentRunner):
    """A designer that also edits code and the architect's design: both are undone."""

    def _write_artifacts(self, req: AgentRequest) -> None:
        super()._write_artifacts(req)
        if req.role == "designer":
            (req.cwd / "src" / "stray.ts").parent.mkdir(exist_ok=True)
            (req.cwd / "src" / "stray.ts").write_text("export const x = 1;\n")
            (req.cwd / "docs" / "design.md").write_text("# rewritten by the designer\n")


async def test_a_web_product_gets_a_designer_screenshots_and_a_ui_review(make_factory):
    agents = StrayDesigner()
    f = make_factory(ShootingExecutor(), agents)
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback

    roles = [(c.role, c.station) for c in agents.calls]
    designer = next(c for c in agents.calls if c.role == "designer")
    assert roles.index(("architect", "design")) < roles.index(("designer", "design"))
    assert "Skill: nextjs-ui-design" in designer.system_prompt
    assert "Skill: nextjs-golden-path" in designer.system_prompt
    assert designer.produces == ["docs/ui.md"]

    events = f.store.list_events(change.id)
    design = next(e for e in events if e.message.startswith("designer wrote docs/ui.md"))
    assert set(design.data["ui_design"]["undone"]) == {"src/stray.ts", "docs/design.md"}
    ws = f.manager.ws
    worktree = ws.data_dir / "products" / product.slug
    # the architect's design survived the designer; the stray file never reached the product
    shown = await ws.git("show HEAD:docs/design.md", worktree)
    assert "rewritten by the designer" not in shown.output
    assert not (await ws.git("cat-file -e HEAD:src/stray.ts", worktree)).ok

    kept = [e.data["screenshots"]["run"] for e in events if "screenshots" in e.data]
    assert kept == ["preview", "live"]
    review = next(e for e in events if "ui_review" in e.data)
    assert review.message == "UI review 4.0/5 (reported only)"

    app, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/changes/{change.id}/screenshots")).json()
        assert [(r["run"], r["files"]) for r in view["runs"]] == [("preview", SHOTS), ("live", SHOTS)]
        assert view["review"]["mean"] == 4.0 and [k["id"] for k in view["review"]["criteria"]][:2] == [
            "hierarchy",
            "spacing",
        ]
        png = await c.get(f"/api/changes/{change.id}/screenshots/live/{SHOTS[0]}")
        assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content == PNG
        for bad in ("live/..%2Fui-review.json", "nope/home.png", "live/missing.png"):
            assert (await c.get(f"/api/changes/{change.id}/screenshots/{bad}")).status_code in (400, 404)
    await ctx.__aexit__(None, None, None)


class PhoneBreakingReviewer(FakeAgentRunner):
    """A UI reviewer that finds the phone layout broken (UX-04, Expense claims' Manage page)."""

    serious = True

    def _structured(self, req: AgentRequest) -> Any:
        out = super()._structured(req)
        if req.role == "ui-reviewer" or "scores" in (req.output_schema or {}).get("properties", {}):
            out = {**out, "findings": list(out["findings"])}
            if self.serious:
                out["findings"].append(
                    {
                        "screenshot": "home-phone-dark.png",
                        "issue": "The page is 576 px wide on a phone: Approve and Reject are off-screen.",
                        "severity": "serious",
                    }
                )
        return out


async def test_a_serious_ui_defect_shows_at_delivery_and_is_offered_for_the_next_iteration(make_factory):
    """UX-04: delivered with a serious defect, the change is still delivered (the review never blocks),
    the defect is an open item on the next iteration's card, and a later delivery whose review looked at
    the same screen and no longer reports it fixes the item."""
    agents = PhoneBreakingReviewer()
    f = make_factory(ShootingExecutor(), agents)
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    events = f.store.list_events(change.id)
    assert (
        next(e for e in events if "ui_review" in e.data).message == "UI review 4.0/5 (reported only); 1 serious defect"
    )
    folded = next(e for e in events if "work_ledger" in e.data and "ui" in e.data["work_ledger"])
    assert folded.data["work_ledger"]["ui"] == {"new": 1, "seen": 0, "fixed": 0, "reopened": 0}

    app, ctx, c = await _client(f)
    async with c:
        view = (await c.get(f"/api/changes/{change.id}/screenshots")).json()
        assert view["review"]["serious"] == 1
        assert [x["severity"] for x in view["review"]["findings"]] == ["minor", "serious"]
        card = (await c.get(f"/api/products/{product.id}/next-iteration")).json()
        [item] = card["ui"]
        assert item["title"] == "The page is 576 px wide on a phone: Approve and Reject are off-screen"
        assert item["at"] == ["home-phone-dark.png"] and item["change_kind"] == "bug"
        assert item["severity"] == "high" and item["category"] == "ui"
    await ctx.__aexit__(None, None, None)

    agents.serious = False
    nxt = f.manager.start_change(f.store.get_product(product.id), "fix the phone layout")
    assert await wait_run(f, nxt.id, 60) == ChangeStatus.awaiting_feedback
    [it] = [i for i in f.store.list_work_items(product.id) if i.category == "ui"]
    assert it.status == "fixed" and it.resolved_by == nxt.id
    assert "looked at home-phone-dark.png and no longer reports it" in it.history[-1].note


async def test_a_fastapi_product_has_no_designer_and_no_ui_review(make_factory):
    agents = FakeAgentRunner()
    f = make_factory(ShootingExecutor(), agents)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert "designer" not in {c.role for c in agents.calls}
    assert not any("ui_review" in e.data or "screenshots" in e.data for e in f.store.list_events(change.id))
