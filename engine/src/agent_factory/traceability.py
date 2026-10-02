"""Requirement traceability (ADR-0017): requirement -> scenarios -> tests -> live acceptance.

Sources in a generated repo:
    docs/requirements.yaml             [{id: R1, title, detail}]  written by Intake
    tests/acceptance/scenarios.yaml    [{id, given, when, then, covers: [R1]}]
    tests/**/*.py                      @pytest.mark.req("R1", ...) on each test
    <data>/holdout/<slug>/scenarios.yaml   hidden scenarios, also with `covers`
Everything here is a pure function of files, so the Quality gate station, the spec
view and the acceptance evidence all agree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

REQ_ID = re.compile(r"^R\d+$")
_REQ_CALL = re.compile(r"\breq\(([^)]*)\)")
_ID_IN_CALL = re.compile(r"""["'](R\d+)["']""")


def _load_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or []
    except yaml.YAMLError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def requirements(root: Path) -> list[dict[str, str]]:
    """Each requirement; `no_live_check` (why no hidden scenario can check it) only when set."""
    out = []
    for r in _load_list(root / "docs" / "requirements.yaml"):
        if not REQ_ID.match(str(r.get("id", ""))):
            continue
        req = {"id": str(r.get("id")), "title": str(r.get("title", "")), "detail": str(r.get("detail", ""))}
        if str(r.get("no_live_check") or "").strip():
            req["no_live_check"] = str(r["no_live_check"])
        out.append(req)
    return out


def scenarios(path: Path) -> list[dict[str, Any]]:
    out = []
    for s in _load_list(path):
        covers = s.get("covers") or []
        out.append(
            {
                "id": str(s.get("id", "")),
                "given": str(s.get("given", "")),
                "when": str(s.get("when", "")),
                "then": str(s.get("then", "")),
                "covers": [str(c) for c in covers] if isinstance(covers, list) else [],
            }
        )
    return out


def acceptance_scenarios(root: Path) -> list[dict[str, Any]]:
    return scenarios(root / "tests" / "acceptance" / "scenarios.yaml")


def test_tags(root: Path) -> dict[str, int]:
    """Requirement id -> number of @pytest.mark.req(...) tags found in tests/."""
    counts: dict[str, int] = {}
    tests = root / "tests"
    if not tests.is_dir():
        return counts
    for f in sorted(tests.rglob("*.py")):
        for call in _REQ_CALL.findall(f.read_text(errors="replace")):
            for rid in _ID_IN_CALL.findall(call):
                counts[rid] = counts.get(rid, 0) + 1
    return counts


def coverage_problems(reqs: Iterable[dict[str, Any]], scens: Iterable[dict[str, Any]], kind: str) -> list[str]:
    """Every requirement covered by at least one scenario; no scenario cites an unknown id."""
    ids = [r["id"] for r in reqs]
    problems = [f"duplicate requirement id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    problems += [f"requirement id '{i}' must look like R1, R2, …" for i in ids if not REQ_ID.match(i)]
    covered: set[str] = set()
    for s in scens:
        for c in s.get("covers", []):
            if c not in ids:
                problems.append(f"{kind} scenario {s.get('id')} covers unknown requirement {c}")
            covered.add(c)
    problems += [f"requirement {i} has no {kind} scenario" for i in ids if i not in covered]
    return problems


def diff(old: list[dict[str, str]], new: list[dict[str, str]]) -> dict[str, list[str]]:
    before = {r["id"]: (r["title"], r.get("detail", "")) for r in old}
    after = {r["id"]: (r["title"], r.get("detail", "")) for r in new}
    return {
        "added": sorted((i for i in after if i not in before), key=_num),
        "changed": sorted((i for i in after if i in before and after[i] != before[i]), key=_num),
        "removed": sorted((i for i in before if i not in after), key=_num),
    }


def matrix(
    root: Path, holdout: list[dict[str, Any]] | None = None, verdict: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """One row per requirement: its acceptance scenarios, tagged tests and, when a
    verdict is given, the hidden scenarios it was checked by live (pass/fail)."""
    tags = test_tags(root)
    acc = acceptance_scenarios(root)
    results: dict[str, bool] = {}
    for r in verdict or []:
        text = str(r.get("scenario", ""))
        for s in holdout or []:
            if s["id"] and re.search(rf"\b{re.escape(s['id'])}\b", text):
                results[s["id"]] = bool(r.get("passed"))
    rows = []
    for req in requirements(root):
        rid = req["id"]
        rows.append(
            {
                "id": rid,
                "title": req["title"],
                "scenarios": [s["id"] for s in acc if rid in s["covers"]],
                "tests": tags.get(rid, 0),
                "holdout": [
                    {"scenario": s["id"], "passed": results.get(s["id"])} for s in holdout or [] if rid in s["covers"]
                ],
                **({"no_live_check": req["no_live_check"]} if req.get("no_live_check") else {}),
            }
        )
    return rows


def untraced(root: Path) -> list[str]:
    """Requirements without an acceptance scenario or a tagged test (readiness signal)."""
    reqs = requirements(root)
    if not reqs:
        return ["docs/requirements.yaml missing or empty"]
    tags, acc = test_tags(root), acceptance_scenarios(root)
    gaps = []
    for r in reqs:
        if not any(r["id"] in s["covers"] for s in acc):
            gaps.append(f"{r['id']} has no acceptance scenario")
        if not tags.get(r["id"]):
            gaps.append(f'{r["id"]} has no test tagged @pytest.mark.req("{r["id"]}")')
    return gaps


def _num(rid: str) -> int:
    return int(rid[1:]) if REQ_ID.match(rid) else 10**9
