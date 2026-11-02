"""Stack cards and "switch to <pack>" (ADR-0041 step 2): the create form offers one card per
installed stack; a request that requires another installed stack may switch to it at intake, but
only before the first build, while the product repo is still only its template."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from conftest import PRODUCT, REPO, wait_run

from agent_factory import stack
from agent_factory.actions import get_config
from agent_factory.agents import FakeAgentRunner
from agent_factory.config import Blueprint
from agent_factory.engine.pipeline import FactoryError, InvalidRequestError
from agent_factory.models import ChangeStatus, CreateProductInput

NODE = [{"technology": "Node.js", "quote": "build it in Node.js"}]
TODO = CreateProductInput(title="Todo in Node", requirements="A to-do API; build it in Node.js with Express.")


def _node_pack(tmp_path: Path) -> Path:
    """A second stack pack: the FastAPI pack's files under another id and stack."""
    dst = tmp_path / "packs" / "node-web"
    shutil.copytree(REPO / "packs" / "fastapi-service", dst, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    meta = yaml.safe_load((dst / "pack.yaml").read_text())
    meta.update(id="node-web", title="Node web app")
    meta["stack"].update(
        builds="web-app",
        summary="Node.js + TypeScript web app, one container on Kubernetes",
        technologies=["typescript", "node.js", "docker", "helm", "kubernetes"],
        skill=None,
    )
    (dst / "pack.yaml").write_text(yaml.safe_dump(meta))
    return dst


def _only_fastapi(cfg):
    """The shipped config without the Next.js stack, so each test chooses its second stack."""
    cfg.packs = [p for p in cfg.packs if not p.endswith("nextjs-web")]
    cfg.blueprints.pop("nextjs-web", None)
    return cfg


@pytest.fixture
def two_stacks(cfg, tmp_path):
    _only_fastapi(cfg)
    cfg.packs.append(str(_node_pack(tmp_path)))  # absolute: joined onto the factory home as is
    api = cfg.blueprints["fastapi-service"]
    cfg.blueprints["node-web"] = Blueprint(
        description="Node web app",
        pack="node-web",
        node_ports=[30150],
        secret_scan_command=api.secret_scan_command,
        scan_command=api.scan_command,
        sbom_command=api.sbom_command,
    )
    return cfg


def _messages(f, change_id: str) -> list[str]:
    return [e.message for e in f.store.list_events(change_id)]


# ------------------------------------------------------------------ pure --
def test_switch_options_are_the_stacks_that_build_everything_required():
    stacks = {"api": ["python", "fastapi"], "web": ["typescript", "node.js"], "bare": []}
    assert stack.switch_options(["Node.js"], stacks) == ["web"]
    assert stack.switch_options(["NodeJS", "TypeScript"], stacks) == ["web"], "canonical names"
    assert stack.switch_options(["node.js", "mongodb"], stacks) == [], "must build every requirement"
    assert stack.switch_options(["elm"], {"bare": []}) == [], "a blueprint without a stack promises nothing"


def test_the_question_offers_the_switch_only_when_there_is_one():
    q = stack.question(["node.js"], {}, ["python"], "api", [("web", "Node web app")])
    assert "switch this product to a stack that builds it: Node web app (web)" in q
    assert "cancel this change" not in q
    assert "cancel this change" in stack.question(["node.js"], {}, ["python"], "api")


# ------------------------------------------------------------ stack cards --
def test_the_create_form_gets_one_card_per_stack(two_stacks, make_factory):
    f = make_factory()
    cards = {c.blueprint: c for c in get_config(f).stacks}
    assert set(cards) == {"fastapi-service", "node-web"}, "blueprints for existing repos are not stacks"
    api = cards["fastapi-service"]
    assert (api.title, api.builds, api.pack) == ("FastAPI service", "service", "fastapi-service@1.1.0")
    assert api.summary.startswith("Python FastAPI REST service") and "fastapi" in api.technologies
    web = cards["node-web"]
    assert (web.title, web.builds, web.pack) == ("Node web app", "web-app", "node-web@1.1.0")


def test_a_product_records_the_pack_it_builds_with(make_factory):
    f = make_factory()
    assert f.manager.create_product(PRODUCT).pack == "fastapi-service@1.1.0"


# ------------------------------------------------------------- switching --
async def test_a_conflict_before_the_first_build_may_switch_to_the_stack_that_builds_it(two_stacks, make_factory):
    agents = FakeAgentRunner(stack_required=NODE)
    f = make_factory(agents=agents)
    product = f.manager.create_product(TODO)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id) == ChangeStatus.needs_input
    got = f.store.get_change(change.id)
    assert got.switch_to == ["node-web"]
    assert "switch this product to a stack that builds it: Node web app (node-web)" in got.questions[0]
    assert any(m.endswith("may switch to node-web") for m in _messages(f, change.id))

    await f.manager.switch_stack(change.id, "node-web", "ann")
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    product = f.store.get_product(product.id)
    assert (product.blueprint, product.pack, product.node_port) == ("node-web", "node-web@1.1.0", 30150)
    events = f.store.list_events(change.id)
    switched = [e.data["stack_switch"] for e in events if "stack_switch" in e.data]
    assert switched == [
        {
            "from": "fastapi-service",
            "to": "node-web",
            "from_pack": "fastapi-service@1.1.0",
            "pack": "node-web@1.1.0",
            "by": "ann",
        }
    ]
    packs = [e.data["stack_pack"]["id"] for e in events if "stack_pack" in e.data]
    assert packs == ["fastapi-service", "node-web"], "the evidence names the pack it was built with"
    assert not any("answered by a person" in m for m in _messages(f, change.id)), "the new stack has no conflict"
    log = (await f.manager.ws.git("log --format=%s main", f.manager.ws.product_dir(product.slug))).output
    assert "packs/node-web/template" in log and "fastapi-service" not in log, "scaffolded again from the new stack"


async def test_only_an_offered_stack_and_only_while_waiting(two_stacks, make_factory):
    f = make_factory(agents=FakeAgentRunner(stack_required=NODE))
    change = f.manager.start_change(f.manager.create_product(TODO))
    assert await wait_run(f, change.id) == ChangeStatus.needs_input
    with pytest.raises(InvalidRequestError, match="not a stack this change may switch to"):
        await f.manager.switch_stack(change.id, "existing-repo", "ann")
    f.manager.answer(change.id, ["build it with python"])
    assert f.store.get_change(change.id).switch_to == [], "answering keeps the stack"
    assert await wait_run(f, change.id) == ChangeStatus.awaiting_feedback
    with pytest.raises(FactoryError, match="not waiting for answers"):
        await f.manager.switch_stack(change.id, "node-web", "ann")


async def test_after_the_first_build_the_stack_no_longer_switches(two_stacks, make_factory):
    agents = FakeAgentRunner()
    f = make_factory(agents=agents)
    product = f.manager.create_product(PRODUCT)
    first = f.manager.start_change(product)
    assert await wait_run(f, first.id) == ChangeStatus.awaiting_feedback
    agents.stack_required = NODE
    second = f.manager.feedback(product.id, "Rewrite it in Node.js please")
    assert await wait_run(f, second.id) == ChangeStatus.needs_input
    got = f.store.get_change(second.id)
    assert got.switch_to == [] and "cancel this change" in got.questions[0]
    with pytest.raises(InvalidRequestError, match="not a stack this change may switch to"):
        await f.manager.switch_stack(second.id, "node-web", "ann")


def test_without_another_stack_nothing_is_offered(cfg, make_factory):
    _only_fastapi(cfg)
    assert [c.blueprint for c in get_config(make_factory()).stacks] == ["fastapi-service"]


def test_the_shipped_stacks_offer_next_js_for_a_node_request(make_factory):
    """ADR-0041 step 3: with the nextjs-web pack installed, a request that needs Node.js or Next.js
    is offered that stack instead of only "build it in Python or cancel"."""
    cards = {c.blueprint: c for c in get_config(make_factory()).stacks}
    assert list(cards) == ["fastapi-service", "nextjs-web"]
    web = cards["nextjs-web"]
    assert (web.title, web.builds, web.pack) == ("Next.js web app", "web-app", "nextjs-web@1.5.0")
    techs = {c.blueprint: c.technologies for c in cards.values()}
    assert stack.switch_options(["Next.js"], techs) == stack.switch_options(["TypeScript"], techs) == ["nextjs-web"]
    assert stack.switch_options(["FastAPI"], techs) == ["fastapi-service"]
