"""The workflow builder (ADR-0039 1c): phase boxes offer only their phase's templates, a new station
lands at the end of its phase with sensible routes, presets scope a lane, packs ship templates, and
a change brings what its lane starts from."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import default_skill_names

from agent_factory import artifacts, contract
from agent_factory import station_templates as tpl
from agent_factory.app_factory import create_app
from agent_factory.config import FactoryConfig
from agent_factory.contract import BUILTIN_REGISTRY
from agent_factory.engine.workflows import WorkflowError
from agent_factory.phases import PackError, load_phases
from agent_factory.workflow import CUSTOM_PROMPT, WorkflowDoc, load_workflow_dir, validate_workflow

REPO = Path(__file__).resolve().parents[2]
LANES = REPO / "workflow-templates" / "standard" / "lanes"
SKILLS = REPO / "plugin" / "skills"
ORDER = BUILTIN_REGISTRY.ids
T = tpl.BUILTIN_TEMPLATES
LIBRARY = tpl.agent_library(sorted(LANES.glob("*.yaml")))


def _lane(name: str = "new-product") -> WorkflowDoc:
    return load_workflow_dir(LANES / f"{name}.yaml")


def _errors(doc: WorkflowDoc) -> list[str]:
    return validate_workflow(doc, SKILLS, default_skill_names(), tuple(contract.BUILTIN_ROLES))


def _ids(doc: WorkflowDoc) -> list[str]:
    return [s.id for s in doc.stations]


# ------------------------------------------------------------- templates --
def test_every_builtin_type_is_a_template_in_its_own_phase() -> None:
    for t in contract.STATION_TYPES.values():
        if t.id == "scan":
            assert {T[f"scan:{c}"].phase for c in tpl.SCAN_CHECKS} == {"verify"}
        else:
            assert T[f"type:{t.id}"].phase == t.column
    assert T["custom"].phase is None  # offered in every phase


def test_a_phase_box_offers_only_its_own_templates_for_the_lane() -> None:
    factory, repo = _lane("new-product"), _lane("repo-change")
    develop = [t.id for t in T.values() if tpl.offered(t, "develop", factory)]
    assert develop == ["type:implement", "custom"]
    deliver = {t.id for t in T.values() if tpl.offered(t, "deliver", repo)}
    assert "type:build" not in deliver and "type:handover" in deliver  # a repo change leaves as a PR


def test_controls_are_marked() -> None:
    controls = {t.id for t in T.values() if t.control}
    assert {"type:test", "scan:secrets", "type:change-risk", "type:quality-gate"} <= controls
    assert "scan:code" not in controls


# ------------------------------------------------------------- placement --
def test_a_new_station_lands_at_the_end_of_its_phase_with_routes() -> None:
    doc = _lane("new-product")
    out, sid = tpl.with_station(doc, T["scan:dependencies"], "verify", ORDER)
    col = out.columns()
    i = _ids(out).index(sid)
    assert col[out.stations[i - 1].id] == "verify" and col[out.stations[i + 1].id] == "deliver"
    st = out.station(sid)
    assert (sid, st.check, st.fail_on) == ("dependencies-scan", "dependencies", "high")
    assert st.on_fail == "implement"  # a failed control goes back to the station that wrote the code
    assert st.agent == "checker"  # observe-only fallback agent for scans
    assert _errors(out) == []


def test_a_new_station_gets_the_agent_its_kind_already_uses() -> None:
    doc = _lane("new-product")
    out, sid = tpl.with_station(doc, T["type:code-review"], "verify", ORDER)
    assert (sid, out.station(sid).agent) == ("code-review-2", "reviewer")


def test_ids_stay_unique() -> None:
    doc = _lane("new-product")
    out, first = tpl.with_station(doc, T["scan:code"], "verify", ORDER)
    out, second = tpl.with_station(out, T["scan:code"], "verify", ORDER)
    assert (first, second) == ("code-scan-2", "code-scan-3")  # the lane has a code-scan already
    assert tpl.unique_id("Rollout Review!", []) == "rollout-review"


def test_a_custom_step_gets_its_own_agent_that_must_be_written_before_publishing() -> None:
    doc = _lane("new-product")
    out, sid = tpl.with_station(doc, T["custom"], "design", ORDER)
    st = out.station(sid)
    assert (st.phase, st.type, st.uses) == ("design", "agent", None)  # observe-only until granted
    assert out.columns()[sid] == "design"
    agent = out.agents[st.agent or ""]
    assert agent.tools == "observer" and agent.prompt == CUSTOM_PROMPT
    assert any("still has the starter prompt" in e for e in _errors(out))
    agent.prompt = "Check the spec names an owner for every requirement and report the ones that don't."
    assert _errors(out) == []


def test_the_drop_time_check_says_what_adding_a_station_would_break() -> None:
    doc = _lane("product-change")
    doc.stations = [s for s in doc.stations if s.resolved_handler() not in ("build", "deploy", "acceptance", "promote")]
    doc.stations = [s for s in doc.stations if s.id != "deploy-repair"]
    for s in doc.stations:
        s.on_fail = s.on_fail if s.on_fail in _ids(doc) else None
    before = _errors(doc)
    assert before == []
    deploy = tpl.added_problems(doc, T["type:deploy"], "deliver", ORDER, _errors)
    assert any("needs image@1, which no station to its left gives" in p for p in deploy)
    assert tpl.added_problems(doc, T["type:build"], "deliver", ORDER, _errors) == []


def test_the_draft_refuses_a_template_outside_its_phase(make_factory) -> None:
    f = make_factory()
    draft = f.workflows["new-product"].draft
    with pytest.raises(WorkflowError, match="belongs to Deliver, not Develop"):
        draft.add_template(T["type:build"], "develop")
    with pytest.raises(WorkflowError, match="does not work on"):
        draft.add_template(T["type:onboard"], "discover")  # repo-only, on a new-build lane
    sid = draft.add_template(T["scan:config"], "verify")
    assert sid in [s.id for s in draft.get()[1].stations]


def test_moving_a_custom_step_to_another_phase_box_puts_it_at_the_end_of_that_box(make_factory) -> None:
    f = make_factory()
    draft = f.workflows["new-product"].draft
    sid = draft.add_template(T["custom"], "design")
    draft.update_station(sid, {"phase": "verify"})
    doc = draft.get()[1]
    col = doc.columns()
    i = _ids(doc).index(sid)
    assert col[sid] == "verify" and col[doc.stations[i + 1].id] == "deliver"


# ---------------------------------------------------------------- presets --
@pytest.mark.parametrize("lane", ["new-product", "product-change", "repo-change"])
@pytest.mark.parametrize("preset", [p.id for p in tpl.SCOPE_PRESETS])
def test_every_preset_gives_a_lane_that_publishes(lane: str, preset: str) -> None:
    doc = _lane(lane)
    if not tpl.PRESETS[preset].applies(doc):
        pytest.skip("the preset doesn't fit this lane")
    result = tpl.apply_preset(doc, tpl.PRESETS[preset], ORDER, library=LIBRARY)
    out = result.doc
    if not out.stations:  # nothing of this lane fits (e.g. delivery only on a repo lane keeps the PR)
        pytest.skip("no station")
    assert _errors(out) == [], (preset, _ids(out))
    assert tpl.preset_of(out) == preset
    span = out.scope_phases()
    assert all(out.columns()[s.id] in span for s in out.stations)


def test_test_and_secure_keeps_only_the_controls_and_routes_to_nowhere() -> None:
    result = tpl.apply_preset(_lane("product-change"), tpl.PRESETS["test-and-secure"], ORDER)
    out = result.doc
    assert "implement" in result.removed and "build" in result.removed
    assert all(s.on_fail is None or s.on_fail in _ids(out) for s in out.stations)
    controls = set().union(*(s.floor_controls() for s in out.stations))
    assert controls >= {"test", "secrets", "risk"}


def test_a_borrowed_agent_comes_with_its_station() -> None:
    doc = _lane("repo-change")
    out, sid = tpl.with_station(doc, T["type:design"], "design", ORDER, library=LIBRARY)
    assert out.station(sid).agent == "architect" and "architect" in out.agents
    assert _errors(tpl.apply_preset(doc, tpl.PRESETS["design-only"], ORDER, library=LIBRARY).doc) == []


def test_delivery_only_starts_from_your_reports() -> None:
    result = tpl.apply_preset(_lane("product-change"), tpl.PRESETS["delivery-only"], ORDER)
    # it can't change the code (no Verify) nor accept against a spec it doesn't have
    assert {"deploy-repair", "acceptance"} <= set(result.removed)
    keys = {r.key for r in result.doc.entry_refs()}
    assert keys == {"request", "branch", "report.test", "report.risk"}
    assert not any(g.type == "branch" for s in result.doc.stations for g in s.gives_refs())


def test_a_preset_adds_missing_controls_once() -> None:
    doc = _lane("new-product")
    doc.stations = [s for s in doc.stations if s.resolved_handler() != "change-risk"]
    result = tpl.apply_preset(doc, tpl.PRESETS["development-only"], ORDER)
    assert result.added == ["change-risk"]  # test and a secret scan (quality gate) were there


@pytest.mark.parametrize("lane", ["new-product", "product-change", "repo-change", "repo-assessment"])
@pytest.mark.parametrize("narrow", ["design-only", "test-and-secure", "delivery-only", "development-only"])
def test_full_package_brings_the_whole_lane_back(lane: str, narrow: str) -> None:
    """Reported 2026-10-16: Full package after Design only kept only the design stations."""
    standard = _lane(lane)
    if not tpl.PRESETS[narrow].applies(standard):
        pytest.skip("the preset doesn't fit this lane")
    scoped = tpl.apply_preset(standard, tpl.PRESETS[narrow], ORDER, library=LIBRARY, standard=standard).doc
    back = tpl.apply_preset(scoped, tpl.PRESETS["full"], ORDER, library=LIBRARY, standard=standard)
    assert _ids(back.doc) == _ids(standard)
    assert {s.id: (s.agent, s.on_fail, s.next) for s in back.doc.stations} == {
        s.id: (s.agent, s.on_fail, s.next) for s in standard.stations
    }
    assert _errors(back.doc) == []
    assert tpl.preset_of(back.doc) == "full"


def test_a_lane_with_stations_removed_is_not_the_full_package() -> None:
    standard = _lane("new-product")
    doc = standard.model_copy(deep=True)
    assert tpl.preset_of(doc, standard) == "full"
    doc.stations = [s for s in doc.stations if s.id != "code-review"]
    assert tpl.preset_of(doc, standard) is None
    assert tpl.preset_of(doc) == "full"  # without the standard, scope and entry decide


def test_full_package_keeps_your_own_stations_in_their_phase() -> None:
    standard = _lane("product-change")
    doc, sid = tpl.with_station(standard, T["custom"], "verify", ORDER)
    scoped = tpl.apply_preset(doc, tpl.PRESETS["test-and-secure"], ORDER, standard=standard).doc
    back = tpl.apply_preset(scoped, tpl.PRESETS["full"], ORDER, standard=standard).doc
    assert sid in _ids(back) and back.columns()[sid] == "verify"
    assert len(back.stations) == len(standard.stations) + 1


def test_a_preset_re_adds_the_standard_station_with_its_agent() -> None:
    standard = _lane("product-change")
    scoped = tpl.apply_preset(standard, tpl.PRESETS["test-and-secure"], ORDER, standard=standard).doc
    design = tpl.apply_preset(scoped, tpl.PRESETS["design-only"], ORDER, library=LIBRARY, standard=standard)
    assert design.added == ["requirements", "design"]
    assert {s.id: s.agent for s in design.doc.stations} == {"requirements": "intake", "design": "architect"}


def test_a_new_requirements_station_gets_the_intake_agent_not_any_observer() -> None:
    doc = _lane("new-product")
    doc.stations = [s for s in doc.stations if s.id != "requirements"]
    out, sid = tpl.with_station(doc, T["type:requirements"], "design", ORDER, library=LIBRARY)
    assert out.station(sid).agent == "intake"


def test_presets_fit_the_lane() -> None:
    """New builds can stop early but not start late; assessments have a fixed shape (2026-10-16)."""
    ids = {
        lane: [p.id for p in tpl.presets_for(_lane(lane))]
        for lane in ("new-product", "product-change", "repo-change", "repo-assessment")
    }
    assert ids["new-product"] == ["design-only", "development-only", "full"]
    assert ids["product-change"] == ids["repo-change"] == [p.id for p in tpl.SCOPE_PRESETS]
    assert ids["repo-assessment"] == []


def test_the_evaluation_gate_is_only_for_lanes_that_build_new_products() -> None:
    doc = _lane("product-change")
    assert doc.eval_gate == "off" and _errors(doc) == []
    doc.eval_gate = "block"
    assert any("doesn't build new products" in e for e in _errors(doc))
    assert _lane("new-product").eval_gate == "block"


def test_full_package_keeps_every_station() -> None:
    doc = _lane("new-product")
    result = tpl.apply_preset(doc, tpl.PRESETS["full"], ORDER)
    assert (result.removed, result.added, _ids(result.doc)) == ([], [], _ids(doc))


# ------------------------------------------------------------------ packs --
def _pack_cfg(*packs: str) -> FactoryConfig:
    return FactoryConfig.model_validate({"blueprints": {}, "phases": {"packs": list(packs)}})


def test_the_ops_pack_ships_station_templates() -> None:
    registry, templates = load_phases(_pack_cfg("packs/ops-pack"), REPO)
    by_id = {t.id: t for t in templates}
    review = by_id["ops-pack/rollout-review"]
    assert (review.phase, review.source, review.agent and review.agent.id) == (
        "observe-act",
        "ops-pack@1.0.0",
        "rollout-reviewer",
    )
    check = by_id["ops-pack/runbook-check"]
    doc = _lane("new-product")
    out, sid = tpl.with_station(doc, check, "verify", registry.ids)
    st = out.station(sid)
    assert (st.phase, st.uses, st.agent) == ("verify", ["read-repo"], "runbook-checker")
    assert "runbook-checker" in out.agents
    assert validate_workflow(out, SKILLS, default_skill_names(), tuple(contract.BUILTIN_ROLES), registry) == []


def _write_pack(root: Path, station: str, agent_tools: str = "observer") -> Path:
    pack = root / "p"
    (pack / "agents").mkdir(parents=True)
    (pack / "agents" / "a.md").write_text(f"---\nid: pack-agent\ntools: {agent_tools}\n---\nDo the thing.\n")
    (pack / "pack.yaml").write_text(f"id: my-pack\nversion: '1.0'\nstations:\n{station}")
    return pack


def test_a_pack_template_outside_its_phase_is_refused(tmp_path: Path) -> None:
    _write_pack(tmp_path, "  - {id: img, title: Build it, phase: develop, uses: [push-image], agent: agents/a.md}\n")
    with pytest.raises(PackError, match="uses push-image, which Develop does not allow"):
        load_phases(_pack_cfg("p"), tmp_path)


def test_a_pack_agent_with_tools_beyond_uses_is_refused(tmp_path: Path) -> None:
    _write_pack(
        tmp_path,
        "  - {id: fix, title: Fix it, phase: verify, uses: [read-repo], agent: agents/a.md}\n",
        agent_tools="builder",
    )
    with pytest.raises(PackError, match="its agent uses"):
        load_phases(_pack_cfg("p"), tmp_path)


def test_a_pack_agent_outside_the_pack_is_refused(tmp_path: Path) -> None:
    _write_pack(tmp_path, "  - {id: xx, title: Escape, phase: verify, agent: ../../etc/passwd}\n")
    with pytest.raises(PackError, match="is not in the pack"):
        load_phases(_pack_cfg("p"), tmp_path)


# -------------------------------------------------------- provided entries --
@pytest.mark.parametrize("ref", tpl.ENTRY_CHOICES)
def test_example_manifests_are_valid(ref: str) -> None:
    r = contract.parse_ref(ref)
    assert artifacts.problems(r, tpl.example_manifest(r)) == []


# -------------------------------------------------------------------- API --
async def _client(f):
    app = create_app(f)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    return ctx, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_builder_over_http(make_factory) -> None:
    ctx, c = await _client(make_factory())
    async with c:
        cat = (await c.get("/api/catalog")).json()
        assert [p["id"] for p in cat["scope_presets"]][:2] == ["design-only", "development-only"]
        assert {e["key"] for e in cat["entry_choices"]} >= {"report.test", "image"}

        draft = (await c.get("/api/workflows/new-product/draft")).json()
        assert draft["works_on"] == "factory" and draft["preset"] == "full"
        assert [e["by"] for e in draft["entry"]] == ["factory", "factory"]
        verify = next(s for s in draft["stations"] if s["type"] == "test")
        assert verify["control"] and not verify["custom"]

        boxes = (await c.get("/api/workflows/new-product/draft/templates")).json()["phases"]
        assert [t["id"] for t in boxes["develop"]] == ["type:implement", "custom"]
        assert next(t for t in boxes["develop"] if t["id"] == "type:implement")["present"]

        r = await c.post(
            "/api/workflows/new-product/draft/stations/from-template",
            json={"template": "type:build", "phase": "develop"},
        )
        assert r.status_code == 409 and "belongs to Deliver" in r.text
        r = await c.post(
            "/api/workflows/new-product/draft/stations/from-template",
            json={"template": "scan:code", "phase": "verify"},
        )
        assert r.status_code == 200 and any(s["id"] == "code-scan" for s in r.json()["stations"])

        assert draft["presets"] == ["design-only", "development-only", "full"] and draft["evaluable"]
        r = await c.post("/api/workflows/new-product/draft/preset", json={"preset": "delivery-only"})
        assert r.status_code == 409 and "can't start later" in r.text
        r = await c.post("/api/workflows/product-change/draft/preset", json={"preset": "delivery-only"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "implement" in body["removed"] and body["draft"]["preset"] == "delivery-only"
        assert body["draft"]["span"] == ["deliver"]
        assert [e["by"] for e in body["draft"]["entry"]].count("your tools") == 2

        assert not body["draft"]["evaluable"]
        r = await c.patch(
            "/api/workflows/new-product/draft/settings", json={"scope": ["verify", "develop"], "entry": []}
        )
        assert r.json()["scope"] == ["develop", "verify"]  # given backwards, stored in order
        assert [e["key"] for e in r.json()["entry"]] == ["request", "branch"]
    await ctx.__aexit__(None, None, None)


async def test_a_product_says_what_its_next_change_must_bring(make_factory) -> None:
    f = make_factory()
    ctx, c = await _client(f)
    async with c:
        r = await c.post(
            "/api/products",
            json={"title": "Bookmarks service", "requirements": "Save and tag bookmarks, filter by tag."},
        )
        pid = r.json()["product"]["id"]
        entry = (await c.get(f"/api/products/{pid}/entry")).json()
        assert (entry["workflow_id"], entry["kind"], entry["takes"]) == ("product-change", "feature", [])
        draft = f.workflows["product-change"].draft
        draft.apply_preset("delivery-only", f.workflows.station_templates)
        f.workflows["product-change"].draft.publish("delivery only")
        entry = (await c.get(f"/api/products/{pid}/entry?kind=feature")).json()
        assert [t["key"] for t in entry["takes"]] == ["report.test", "report.risk"]
        assert entry["takes"][0]["title"] == "Test report"
    await ctx.__aexit__(None, None, None)
