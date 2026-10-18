"""Image findings as evidence, routed by type (ADR-0042).

The Build station's image scan reads Trivy's JSON report, sorts each CVE by where it came from
(an app dependency the developer can bump, or the base image) and whether a fix exists, and then
passes, sends the exact bumps to the developer, or holds for a person. Findings land in the
product's work ledger, where a person can accept a risk until a date."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import PRODUCT, wait_run

from agent_factory import image_scan, ledger
from agent_factory.agents import FakeAgentRunner
from agent_factory.executor import FakeExecutor
from agent_factory.image_scan import ImageFinding, judge
from agent_factory.models import ChangeStatus, EventKind

T0 = datetime(2026, 10, 18, 12, tzinfo=UTC)
SCAN = " image --quiet --format json"


def vuln(cve: str, pkg: str, installed: str, fixed: str = "", severity: str = "CRITICAL", path: str = "") -> dict:
    v = {
        "VulnerabilityID": cve,
        "PkgID": f"{pkg}@{installed}",
        "PkgName": pkg,
        "InstalledVersion": installed,
        "Status": "fixed" if fixed else "affected",
        "Severity": severity,
        "Title": f"{pkg}: something bad",
        "Layer": {"DiffID": "sha256:abc"},
    }
    if fixed:
        v["FixedVersion"] = fixed
    if path:
        v["PkgPath"] = path
    return v


def report(os_vulns: list[dict] = (), lang_vulns: list[dict] = (), lang_type: str = "node-pkg") -> str:
    """A Trivy image report in its real shape (SchemaVersion 2): an OS result and a language result."""
    results = [
        {
            "Target": "guestbook:abc (debian 12.11)",
            "Class": "os-pkgs",
            "Type": "debian",
            "Vulnerabilities": list(os_vulns),
        },
        {"Target": "Node.js", "Class": "lang-pkgs", "Type": lang_type, "Vulnerabilities": list(lang_vulns)},
    ]
    return json.dumps(
        {
            "SchemaVersion": 2,
            "ArtifactName": "guestbook:abc",
            "ArtifactType": "container_image",
            "Metadata": {"OS": {"Family": "debian", "Name": "12.11"}},
            "Results": results,
        }
    )


PERL = vuln("CVE-2026-13221", "perl-base", "5.36.0-7+deb12u2", "5.36.0-7+deb12u3")
NEXT = vuln("CVE-2026-1000", "next", "15.1.0", "15.1.8", path="app/node_modules/next/package.json")
NPM_DEP = vuln(
    "CVE-2026-2000",
    "cross-spawn",
    "7.0.3",
    "7.0.5",
    path="usr/local/lib/node_modules/npm/node_modules/cross-spawn/package.json",
)


def f(key_pkg: str, *, origin: str = "app", fixed: str = "2.0", severity: str = "critical") -> ImageFinding:
    return ImageFinding(f"CVE-1-{key_pkg}", key_pkg, "1.0", fixed, severity, origin, "lang-pkgs", "node-pkg")  # type: ignore[arg-type]


# ------------------------------------------------------------------ parse --
def test_parse_classifies_by_origin_and_keeps_high_and_above():
    text = report(
        [PERL, vuln("CVE-2026-3", "zlib1g", "1.2", severity="MEDIUM")],
        [NEXT, NPM_DEP, vuln("CVE-2026-4", "react", "19.0.0", "19.0.1", severity="HIGH")],
    )
    got = {x.package: x for x in image_scan.parse(text, {"next", "react"})}
    assert set(got) == {"perl-base", "next", "cross-spawn", "react"}, "medium and below are not read"
    assert got["perl-base"].origin == "base", "OS packages come with the base image"
    assert got["next"].origin == "app" and got["next"].fixed == "15.1.8"
    assert got["cross-spawn"].origin == "base", "npm's own dependency, not in the app's lockfile"
    assert got["react"].severity == "high"


def test_parse_without_a_lockfile_judges_by_path():
    got = {x.package: x.origin for x in image_scan.parse(report([], [NEXT, NPM_DEP]))}
    assert got == {"next": "app", "cross-spawn": "base"}


def test_parse_refuses_what_is_not_a_trivy_json_report():
    with pytest.raises(image_scan.NotAnImageReport):
        image_scan.parse("guestbook:abc (debian 12.11)\nTotal: 3 (CRITICAL: 3)")
    with pytest.raises(image_scan.NotAnImageReport):
        image_scan.parse('{"runs": []}')


def test_locked_packages_reads_npm_and_uv_lockfiles():
    npm = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "guestbook"},
                "node_modules/next": {"version": "15.1.0"},
                "node_modules/@next/env": {"version": "15.1.0"},
                "node_modules/next/node_modules/postcss": {"version": "8.4.31"},
            },
        }
    )
    uv = '[[package]]\nname = "fastapi"\nversion = "0.1"\n\n[[package]]\nname = "Typing_Extensions"\n'
    got = image_scan.locked_packages({"package-lock.json": npm, "uv.lock": uv})
    assert {"next", "@next/env", "postcss", "fastapi", "typing-extensions"} <= got


def test_base_image_follows_stage_names():
    dockerfile = (
        "FROM node:22-bookworm-slim AS deps\nRUN npm ci\n"
        "FROM gcr.io/distroless/nodejs22-debian12:nonroot\nCOPY --from=deps /app /app\n"
    )
    assert image_scan.base_image(dockerfile) == "gcr.io/distroless/nodejs22-debian12:nonroot"
    assert image_scan.base_image("FROM python:3.12-slim AS base\nFROM base\n") == "python:3.12-slim"


# ------------------------------------------------------------------ judge --
def test_app_dependency_goes_to_the_developer_with_the_exact_bump():
    v = judge(image_scan.parse(report([], [NEXT]), {"next"}))
    assert v.action == "fix" and [x.package for x in v.app_fixes] == ["next"]
    brief = image_scan.developer_brief(v, ["package-lock.json"])
    assert "next: 15.1.0 → 15.1.8 (CVE-2026-1000)" in brief
    assert ".trivyignore" in brief, "the developer is told not to narrow the scan"


def test_base_image_holds_for_a_person_at_once():
    v = judge(image_scan.parse(report([PERL], [NEXT]), {"next"}))
    assert v.action == "hold", "a base-image CVE never loops the developer"
    brief = image_scan.person_brief(v, "node:22-bookworm-slim")
    assert "node:22-bookworm-slim" in brief and "perl-base" in brief
    assert "accept the risk with an expiry" in brief
    assert "After you resume, the developer bumps" in brief and "next" in brief


def test_no_fix_released_holds_until_a_person_accepts_it():
    unfixed = vuln("CVE-2026-9", "next", "15.1.0", path="app/node_modules/next/package.json")
    v = judge(image_scan.parse(report([], [unfixed]), {"next"}))
    assert v.action == "hold" and [x.cve for x in v.no_fix] == ["CVE-2026-9"]
    assert "No fix is released yet" in image_scan.person_brief(v)


def test_accepted_risks_and_lower_severities_do_not_block():
    findings = [f("a", origin="base"), f("b", fixed=""), f("c", severity="high")]
    v = judge(findings, accepted={findings[0].key, findings[1].key})
    assert v.action == "pass"
    assert len(v.accepted) == 2 and [x.package for x in v.recorded] == ["c"]
    assert "2 accepted risk" in v.headline() and "1 lower recorded" in v.headline()


def test_fail_on_high_blocks_high_too():
    assert judge([f("c", severity="high")], fail_on="high").action == "fix"
    with pytest.raises(ValueError):
        judge([], fail_on="medium")


def test_the_same_findings_after_a_fix_hold_instead_of_looping():
    first = judge([f("a"), f("b")])
    assert first.action == "fix"
    again = judge([f("a"), f("b")], previous_fixes=[x.key for x in first.app_fixes])
    assert again.action == "hold" and again.repeated
    assert "another loop is unlikely to help" in image_scan.person_brief(again)
    progress = judge([f("b")], previous_fixes=[x.key for x in first.app_fixes])
    assert progress.action == "fix", "one bump landed: the developer gets another go"


# ----------------------------------------------------------------- ledger --
def cand(x: ImageFinding) -> dict:
    return {
        "key": x.key,
        "title": image_scan.ledger_title(x),
        "severity": x.severity,
        "detail": image_scan.ledger_detail(x),
        "at": [x.path or x.ecosystem],
        "rule": x.cve,
    }


def test_ledger_records_findings_and_fixes_what_the_image_no_longer_has():
    items: list = []
    changed, stats = ledger.fold_image_scan(
        "p1", items, [cand(f("a")), cand(f("b", severity="high"))], change_id="c1", iteration=1, now=T0
    )
    assert stats["new"] == 2 and {i.category for i in changed} == {"image"}
    assert all(i.kind == "issue" and i.status == "open" for i in items)
    again, _ = ledger.fold_image_scan(
        "p1", items, [cand(f("a")), cand(f("b", severity="high"))], change_id="c1", iteration=1, now=T0
    )
    assert again == [], "the same scan again in one iteration records nothing"
    changed, stats = ledger.fold_image_scan(
        "p1", items, [cand(f("b", severity="high"))], change_id="c2", iteration=2, now=T0
    )
    a = next(i for i in items if "CVE-1-a" in i.title)
    assert stats["fixed"] == 1 and a.status == "fixed" and a.resolved_by == "c2"
    ledger.fold_image_scan("p1", items, [cand(f("a"))], change_id="c3", iteration=3, now=T0)
    assert a.status == "open" and a.history[-1].event == "reappeared"


def test_accepted_risk_counts_until_it_expires():
    items: list = []
    x = f("a", origin="base")
    ledger.fold_image_scan("p1", items, [cand(x)], change_id="c1", iteration=1, now=T0)
    ledger.decide(
        items,
        [items[0].id],
        "accepted_risk",
        reason="no patched distroless yet",
        by="ada",
        now=T0,
        until=T0 + timedelta(days=7),
    )
    assert ledger.accepted_image_keys(items, [x.key], T0 + timedelta(days=1)) == {x.key}
    assert ledger.accepted_image_keys(items, [x.key], T0 + timedelta(days=8)) == set()
    ledger.fold_image_scan("p1", items, [cand(x)], change_id="c2", iteration=2, now=T0 + timedelta(days=8))
    assert items[0].status == "open" and items[0].history[-1].note == "risk acceptance expired"


def test_assessments_leave_image_items_alone():
    items: list = []
    ledger.fold_image_scan("p1", items, [cand(f("a"))], change_id="c1", iteration=1, now=T0)
    ledger.reconcile("p1", items, {"issues": [], "coverage": {"checks": []}}, change_id="c2", iteration=2, now=T0)
    assert items[0].status == "open", "an assessment doesn't scan the image, so it can't call it fixed"


# --------------------------------------------------------------- pipeline --
STARLETTE = vuln(
    "CVE-2026-5000", "starlette", "0.41.0", "0.47.2", path="app/.venv/lib/python3.12/site-packages/starlette"
)
PIP = vuln(
    "CVE-2026-6000", "pip", "24.0", "25.3", path="usr/local/lib/python3.12/site-packages/pip-24.0.dist-info/METADATA"
)
CLEAN = report()


def _image_events(fac, change_id):
    return [e for e in fac.store.list_events(change_id) if e.kind == EventKind.decision and "image_scan" in e.data]


async def test_app_cve_goes_back_to_the_developer_then_delivers(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs[SCAN] = [report([], [STARLETTE], "python-pkg"), CLEAN]
    agents = FakeAgentRunner()
    fac = make_factory(ex, agents)
    product = fac.manager.create_product(PRODUCT)
    change = fac.manager.start_change(product)
    assert await wait_run(fac, change.id) == ChangeStatus.awaiting_feedback
    fix = [c for c in agents.calls if c.role == "developer"][-1].prompt
    assert "starlette: 0.41.0 → 0.47.2 (CVE-2026-5000)" in fix
    first, last = _image_events(fac, change.id)
    assert first.data["image_scan"]["action"] == "fix" and last.data["image_scan"]["action"] == "pass"
    item = next(i for i in fac.store.list_work_items(product.id) if i.category == "image")
    assert item.status == "fixed", "the clean rebuild verified the bump"
    evidence = json.loads((fac.manager.ws.data_dir / "artifacts" / change.id / "image-scan.json").read_text())
    assert evidence["action"] == "pass" and "report" not in evidence, "only normalised findings are kept"


async def test_base_image_cve_holds_then_an_accepted_risk_lets_it_through(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs[SCAN] = report([], [PIP], "python-pkg")
    agents = FakeAgentRunner()
    fac = make_factory(ex, agents)
    product = fac.manager.create_product(PRODUCT)
    change = fac.manager.start_change(product)
    assert await wait_run(fac, change.id) == ChangeStatus.held
    held = fac.store.get_change(change.id)
    assert held.current_station == "build" and "base image" in held.summary
    assert "python:3.12-slim" in held.last_failure, "the brief names the base image"
    assert len([c for c in agents.calls if c.role == "developer"]) == 1, "no fix loop for the base image"
    items = fac.store.list_work_items(product.id)
    pip = next(i for i in items if i.category == "image")
    assert pip.status == "open" and "pip 24.0" in pip.title

    ledger.decide(
        items,
        [pip.id],
        "accepted_risk",
        reason="Debian has no patched pip yet",
        by="ada",
        now=datetime.now(UTC),
        until=datetime.now(UTC) + timedelta(days=14),
    )
    fac.store.save_work_items(items)
    fac.manager.resume(change.id)
    assert await wait_run(fac, change.id) == ChangeStatus.awaiting_feedback
    assert _image_events(fac, change.id)[-1].data["image_scan"]["accepted"] == [
        image_scan.parse(report([], [PIP]))[0].key
    ]


async def test_no_fix_released_holds(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs[SCAN] = report([], [vuln("CVE-2026-7", "starlette", "0.41.0", path="app/x")], "python-pkg")
    fac = make_factory(ex)
    change = fac.manager.start_change(fac.manager.create_product(PRODUCT))
    assert await wait_run(fac, change.id) == ChangeStatus.held
    assert "no fix released" in fac.store.get_change(change.id).summary


async def test_a_fix_that_changes_nothing_holds_after_one_loop(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs[SCAN] = report([], [STARLETTE], "python-pkg")  # every scan finds it
    agents = FakeAgentRunner()
    fac = make_factory(ex, agents)
    change = fac.manager.start_change(fac.manager.create_product(PRODUCT))
    assert await wait_run(fac, change.id) == ChangeStatus.held
    assert len([c for c in agents.calls if c.role == "developer"]) == 2, "one fix loop, then a person"
    assert "another loop is unlikely to help" in fac.store.get_change(change.id).last_failure


async def test_high_cves_are_recorded_without_blocking(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs[SCAN] = report([vuln("CVE-2026-8", "libssl3", "3.0.1", "3.0.2", severity="HIGH")])
    fac = make_factory(ex)
    product = fac.manager.create_product(PRODUCT)
    change = fac.manager.start_change(product)
    assert await wait_run(fac, change.id) == ChangeStatus.awaiting_feedback
    (item,) = [i for i in fac.store.list_work_items(product.id) if i.category == "image"]
    assert item.severity == "high" and item.status == "open"


async def test_an_old_table_scan_command_still_decides_by_exit_code(make_factory):
    ex = FakeExecutor()
    ex.scan_outputs[SCAN] = "guestbook (debian 12)\nTotal: 0 (CRITICAL: 0)"
    fac = make_factory(ex)
    change = fac.manager.start_change(fac.manager.create_product(PRODUCT))
    assert await wait_run(fac, change.id) == ChangeStatus.awaiting_feedback
    notes = [e.message for e in fac.store.list_events(change.id) if "image findings not read" in e.message]
    assert notes, "a scan the factory can't read is said, never silently passed as findings-free"
