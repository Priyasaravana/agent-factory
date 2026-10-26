"""The plan lives in docs/planning/ and nowhere else (docs/planning/README.md, rules 1 and 6).

These checks keep it from drifting: links resolve, ADRs it names exist, no page claims to
mirror another, every page is reachable from the README, and nothing points back at the
chat-project copies the plan was moved out of on 2026-10-26."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLANNING = REPO / "docs" / "planning"
ADRS = REPO / "docs" / "adr"
PAGES = sorted(PLANNING.rglob("*.md"))
LINK = re.compile(r"\]\(([^)\s]+)\)")
ADR_REF = re.compile(r"\bADR-(\d{4})\b")
OLD_PROJECT_PATH = re.compile(r"(?<![\w./-])claude/[\w.-]+\.md")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def _links(page: Path) -> list[str]:
    targets = []
    for target in LINK.findall(page.read_text()):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue  # web links and in-page anchors
        targets.append(target.split("#", 1)[0])
    return targets


def _reserved() -> set[str]:
    text = (PLANNING / "README.md").read_text()
    section = text.split("## Reserved ADR numbers", 1)[1].split("\n## ", 1)[0]
    return set(ADR_REF.findall(section))


def test_the_planning_folder_exists_with_its_entry_pages():
    for name in ("README.md", "roadmap.md", "handoff.md", "master-plan.md"):
        assert (PLANNING / name).is_file(), name


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_relative_links_resolve(page: Path):
    missing = [t for t in _links(page) if not (page.parent / t).exists()]
    assert not missing, f"{_rel(page)} links to missing paths: {missing}"


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_every_adr_named_exists_or_is_reserved(page: Path):
    written = {p.name[:4] for p in ADRS.glob("[0-9][0-9][0-9][0-9]-*.md")}
    unknown = sorted(set(ADR_REF.findall(page.read_text())) - written - _reserved())
    assert not unknown, f"{_rel(page)} names ADRs that don't exist: {unknown}"


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_no_page_mirrors_another(page: Path):
    assert "mirror of" not in page.read_text().lower(), f"{_rel(page)}: one copy only (README rule 1)"


def test_every_page_is_reachable_from_the_readme():
    seen: set[Path] = set()
    todo = [PLANNING / "README.md"]
    while todo:
        page = todo.pop()
        if page in seen or page.suffix != ".md" or not page.is_file():
            continue
        seen.add(page)
        for target in _links(page):
            path = (page.parent / target).resolve()
            if path.is_dir():
                todo.extend(path.glob("*.md"))
            elif path.is_relative_to(PLANNING):
                todo.append(path)
    orphans = [_rel(p) for p in PAGES if p.resolve() not in seen]
    assert not orphans, f"pages no one links to (link them or delete them): {orphans}"


@pytest.mark.parametrize("name", ["roadmap.md", "handoff.md"])
def test_living_pages_carry_their_date(name: str):
    head = "\n".join((PLANNING / name).read_text().splitlines()[:4])
    assert re.search(r"[Uu]pdated \d{4}-\d{2}-\d{2}", head), f"{name}: say when it was last updated"


def test_nothing_points_at_the_old_chat_project_copies():
    docs = [REPO / "AGENTS.md", REPO / "README.md", *sorted((REPO / "docs").rglob("*.md"))]
    hits = [f"{_rel(p)}: {m}" for p in docs for m in OLD_PROJECT_PATH.findall(p.read_text())]
    assert not hits, "the plan lives in docs/planning/ now:\n" + "\n".join(hits)


def test_the_drift_rules_catch_what_they_should():
    """The patterns above, on known-good and known-bad lines."""
    assert OLD_PROJECT_PATH.findall("see `claude/roadmap.md`") == ["claude/roadmap.md"]
    assert not OLD_PROJECT_PATH.findall("mirrored in `.claude/skills/x.md`")
    assert not OLD_PROJECT_PATH.findall("docs/planning/roadmap.md")
    assert ADR_REF.findall("ADR-0041 and ADR-0038, not ADR-41") == ["0041", "0038"]
    assert LINK.findall("[a](b.md) [c](https://x.y)") == ["b.md", "https://x.y"]
