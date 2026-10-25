"""Approved base images, the fallback at Build and factory-wide image risk decisions (ADR-0045).

A pack lists its approved runtime images in order. When the default holds on its own findings (fixed
upstream, not yet shipped by its publisher), Build tries the next approved image: rebuilt with
`--build-arg RUNTIME=<stage>`, scanned and started once. A person's acceptance of a base-image risk
holds for every product on that image, and closes when a scan no longer finds it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import PRODUCT, wait_run
from test_image_scan import CLEAN, report, vuln

from agent_factory import base_images, image_scan, ledger
from agent_factory.actions import decide_work_items
from agent_factory.agents.fake import FakeAgentRunner
from agent_factory.base_images import BaseImage, ImageRisk
from agent_factory.executor import CommandResult, FakeExecutor
from agent_factory.models import ChangeStatus, WorkItemDecisionInput
from agent_factory.packs import PackError, load_pack
from agent_factory.secret_refs import REDACTOR

REPO = Path(__file__).resolve().parents[2]
WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})
SCAN = " image --quiet --format json"
T0 = datetime(2026, 10, 25, 12, tzinfo=UTC)
LIBSSL = vuln("CVE-2026-31789", "libssl3", "3.0.18-1~deb12u2", "3.0.19-1~deb12u2")
HARDENED = BaseImage(stage="runtime-hardened", image="dhi.io/node:22-alpine3.23", login="dhi")
DISTROLESS = BaseImage(stage="runtime-distroless", image="gcr.io/distroless/nodejs22-debian12:nonroot")
TEMPLATE_DOCKERFILE = (REPO / "packs" / "nextjs-web" / "template" / "Dockerfile").read_text()
TOKEN = "dockerdockerdocker"  # stands for a Docker Hub access token (low entropy: no scan noise)


class RecordingExecutor(FakeExecutor):
    def __init__(self, **kw) -> None:  # noqa: ANN003
        super().__init__(**kw)
        self.envs: list[tuple[str, dict[str, str]]] = []

    async def run(self, command, cwd=None, timeout=600, env=None) -> CommandResult:  # noqa: ANN001
        self.envs.append((command, dict(env or {})))
        return await super().run(command, cwd, timeout, env)


def _verdict(*findings: image_scan.ImageFinding) -> image_scan.Verdict:
    return image_scan.judge(list(findings))


def _f(pkg: str, origin: str, fixed: str = "2.0") -> image_scan.ImageFinding:
    return image_scan.ImageFinding(f"CVE-1-{pkg}", pkg, "1.0", fixed, "critical", origin, "os-pkgs", "debian")  # type: ignore[arg-type]


# ------------------------------------------------------------------- pure --
def test_an_image_is_named_without_its_digest():
    assert base_images.image_name("DHI.io/node:22-alpine3.23@sha256:abc") == "dhi.io/node:22-alpine3.23"
    assert base_images.registry_of("dhi.io/node:22") == "dhi.io"
    assert base_images.registry_of("node:22-bookworm-slim") == "docker.io"
    assert base_images.registry_of("gcr.io/distroless/nodejs22-debian12:nonroot") == "gcr.io"


def test_the_template_dockerfile_offers_every_approved_image():
    assert base_images.candidates([HARDENED, DISTROLESS], TEMPLATE_DOCKERFILE) == [HARDENED, DISTROLESS]
    assert image_scan.base_image(TEMPLATE_DOCKERFILE) == HARDENED.image, "ARG RUNTIME's default"
    assert image_scan.base_image(TEMPLATE_DOCKERFILE, {"RUNTIME": "runtime-distroless"}) == DISTROLESS.image


def test_a_dockerfile_from_before_1_3_1_has_nothing_to_fall_back_to():
    old = 'FROM node:22 AS build\nFROM gcr.io/distroless/nodejs22-debian12:nonroot\nCMD ["server.js"]\n'
    assert base_images.candidates([HARDENED, DISTROLESS], old) == []
    assert image_scan.base_image(old) == DISTROLESS.image


def test_only_the_base_images_own_findings_are_worth_a_fallback():
    assert base_images.fallback_worth_trying(_verdict(_f("libssl3", "base")))
    assert base_images.fallback_worth_trying(_verdict(_f("zlib", "base", fixed=""))), "no fix in this distro"
    assert not base_images.fallback_worth_trying(_verdict(_f("next", "app"))), "the developer bumps it"
    assert not base_images.fallback_worth_trying(_verdict(_f("libssl3", "base"), _f("left-pad", "app", fixed="")))
    assert not base_images.fallback_worth_trying(_verdict()), "nothing blocks"


def test_an_image_needing_a_sign_in_is_skipped_without_one():
    assert base_images.plan_fallback([HARDENED, DISTROLESS], [], set()) == [DISTROLESS]
    assert base_images.plan_fallback([HARDENED, DISTROLESS], [], {"dhi"}) == [HARDENED, DISTROLESS]
    assert base_images.plan_fallback([HARDENED, DISTROLESS], ["runtime-hardened"], {"dhi"}) == [DISTROLESS]


def test_factory_decisions_cover_every_product_on_the_image_until_fixed_or_expired():
    key = "image cve-2026-31789 libssl3"
    risks = base_images.record(
        [], DISTROLESS.image + "@sha256:aaa", [(key, "CVE-2026-31789")], until=T0 + timedelta(days=7),
        reason="publisher hasn't shipped it", by="ada", now=T0,
    )  # fmt: skip
    assert base_images.accepted_for(risks, DISTROLESS.image + "@sha256:bbb", [key, "other"], T0) == {key}
    assert base_images.accepted_for(risks, HARDENED.image, [key], T0) == set(), "per image"
    assert base_images.accepted_for(risks, DISTROLESS.image, [key], T0 + timedelta(days=8)) == set(), "expired"
    renewed = base_images.record(
        risks, DISTROLESS.image, [(key, "CVE-2026-31789")], until=None, reason="r", by="b", now=T0
    )
    assert len(renewed) == 1 and renewed[0].until is None, "renewed, not duplicated"
    kept, closed = base_images.close_fixed(risks, DISTROLESS.image, [key], T0)
    assert (len(kept), closed) == (1, [])
    kept, closed = base_images.close_fixed(risks, DISTROLESS.image, [], T0)
    assert (kept, [r.key for r in closed]) == ([], [key]), "the image no longer has it"
    assert base_images.load(base_images.dump(risks)) == risks
    assert base_images.load([{"nonsense": 1}, "x"]) == []


def test_the_published_port_is_read_from_docker_port():
    assert base_images.published_port("0.0.0.0:49153\n[::]:49153\n") == 49153
    assert base_images.published_port("[dry-run] ok: docker port x 3000") is None


def test_a_pack_must_name_stages_its_dockerfile_has(tmp_path):
    import shutil

    pack = tmp_path / "nextjs-web"
    shutil.copytree(REPO / "packs" / "nextjs-web", pack, ignore=shutil.ignore_patterns("node_modules", ".next"))
    text = (pack / "pack.yaml").read_text()
    (pack / "pack.yaml").write_text(text.replace("stage: runtime-distroless", "stage: runtime-missing"))
    with pytest.raises(PackError, match="stages not in the template's Dockerfile: runtime-missing"):
        load_pack(pack)
    (pack / "pack.yaml").write_text(text)
    (pack / "template" / "Dockerfile").write_text("FROM node:22\n")
    with pytest.raises(PackError, match="ARG RUNTIME"):
        load_pack(pack)


# ----------------------------------------------------------- in the lane --
def _signed_in(monkeypatch) -> None:
    monkeypatch.setenv("DOCKERHUB_USERNAME", "registry-user")
    monkeypatch.setenv("DOCKERHUB_TOKEN", TOKEN)


async def test_a_base_image_hold_falls_back_to_the_next_approved_image(make_factory, monkeypatch):
    _signed_in(monkeypatch)
    ex = RecordingExecutor(scan_outputs={SCAN: [report([LIBSSL]), CLEAN], "docker port": "0.0.0.0:49153"})
    f = make_factory(ex)
    product = f.manager.create_product(WEB)
    change = f.manager.start_change(product)
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback

    builds = [c for c in ex.calls if "docker build --pull" in c]
    assert "--build-arg RUNTIME=runtime-hardened" in builds[0]
    assert "--build-arg RUNTIME=runtime-distroless" in builds[1]
    assert (
        "docker login dhi.io" in builds[0] and "--password-stdin" in builds[0] and "docker logout dhi.io" in builds[0]
    )
    assert all(TOKEN not in c for c in ex.calls), "the token goes on stdin, never argv"
    assert [env for cmd, env in ex.envs if "docker login" in cmd][0]["AF_REGISTRY_TOKEN"] == TOKEN
    assert "docker login" not in builds[1], "the fallback needs no sign-in"
    assert any(c.startswith("docker run -d --rm --name af-start-") for c in ex.calls), "it was started once"

    evidence = json.loads((f.manager.ws.data_dir / "artifacts" / change.id / "base-images.json").read_text())
    assert [a["stage"] for a in evidence["attempts"]] == ["runtime-hardened", "runtime-distroless"]
    scan = json.loads((f.manager.ws.data_dir / "artifacts" / change.id / "image-scan.json").read_text())
    assert scan["base_image"] == DISTROLESS.image and scan["action"] == "pass"
    upkeep = [i for i in f.store.list_work_items(product.id) if i.category == ledger.BASE_IMAGE_UPKEEP]
    assert len(upkeep) == 1 and upkeep[0].title == f"pack upkeep: make {DISTROLESS.image} the default runtime"
    stored = json.dumps([[e.message, e.data] for e in f.store.list_events(change.id)])
    assert TOKEN not in stored
    assert REDACTOR.knows(TOKEN)


async def test_without_a_sign_in_the_hardened_image_is_skipped(make_factory, monkeypatch):
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    ex = RecordingExecutor(scan_outputs={"docker port": "0.0.0.0:49153"})
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    builds = [c for c in ex.calls if "docker build --pull" in c]
    assert len(builds) == 1 and "--build-arg RUNTIME=runtime-distroless" in builds[0]
    messages = [e.message for e in f.store.list_events(change.id)]
    assert any(
        m.startswith(f"base image {HARDENED.image} skipped: no sign-in for registry_logins.dhi") for m in messages
    )


async def test_an_image_the_app_does_not_start_on_is_passed_over(make_factory, monkeypatch):
    _signed_in(monkeypatch)
    ex = RecordingExecutor(scan_outputs={"docker port": "0.0.0.0:49153"}, fail_on=["curl -fsS http://dind:49153"])
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    evidence = json.loads((f.manager.ws.data_dir / "artifacts" / change.id / "base-images.json").read_text())
    assert len(evidence["attempts"]) == 2, [c for c in ex.calls if "curl" in c or "docker run" in c]
    first, second = evidence["attempts"]
    assert first["stage"] == "runtime-hardened" and first["result"].startswith("does not start")
    assert second["stage"] == "runtime-distroless"


async def test_when_no_approved_image_passes_the_change_holds(make_factory, monkeypatch):
    _signed_in(monkeypatch)
    ex = RecordingExecutor(scan_outputs={SCAN: report([LIBSSL]), "docker port": "0.0.0.0:49153"})
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.held
    held = f.store.get_change(change.id)
    assert held.current_station == "build" and "base image" in held.summary
    assert len([c for c in ex.calls if "docker build --pull" in c]) == 2, "both approved images were tried"


async def test_a_base_image_risk_accepted_once_covers_the_next_product(make_factory, monkeypatch):
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)  # distroless only: nothing to fall back to
    ex = RecordingExecutor(scan_outputs={SCAN: report([LIBSSL]), "docker port": "0.0.0.0:49153"})
    f = make_factory(ex, FakeAgentRunner())
    first = f.manager.create_product(WEB)
    change = f.manager.start_change(first)
    assert await wait_run(f, change.id, 60) == ChangeStatus.held
    item = next(i for i in f.store.list_work_items(first.id) if i.category == ledger.IMAGE)
    src = item.sources[0]
    assert (src["origin"], src["base_image"]) == ("base", DISTROLESS.image)

    decide_work_items(
        f,
        first.id,
        WorkItemDecisionInput(
            items=[item.id], decision="accepted_risk", reason="publisher hasn't shipped it",
            until=datetime.now(UTC) + timedelta(days=7),
        ),
    )  # fmt: skip
    [risk] = base_images.load_risks(f.store)
    assert (risk.image, risk.cve, risk.product_id) == (DISTROLESS.image, "CVE-2026-31789", first.id)

    second = f.manager.create_product(WEB.model_copy(update={"title": "Second app"}))
    other = f.manager.start_change(second)
    assert await wait_run(f, other.id, 60) == ChangeStatus.awaiting_feedback, "no second hold for the same CVE"
    covered = next(i for i in f.store.list_work_items(second.id) if i.category == ledger.IMAGE)
    assert covered.status == "accepted_risk" and covered.decision_reason.startswith("factory decision")


async def test_a_factory_decision_closes_when_the_image_is_fixed(make_factory, monkeypatch):
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    ex = RecordingExecutor(scan_outputs={SCAN: CLEAN, "docker port": "0.0.0.0:49153"})
    f = make_factory(ex)
    key = image_scan.parse(report([LIBSSL]))[0].key
    base_images.save_risks(
        f.store,
        [ImageRisk(image=DISTROLESS.image, key=key, cve="CVE-2026-31789", reason="r", at=datetime.now(UTC))],
    )
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert base_images.load_risks(f.store) == []
    assert any(e.message.startswith("closed 1 factory decision") for e in f.store.list_events(change.id))


def test_the_registry_username_is_not_a_secret(monkeypatch):
    """Resolved as a secret it would be masked in every event naming that person (live risk)."""
    from agent_factory.engine.stations import _login_name

    monkeypatch.setenv("AF_TEST_REGISTRY_USER", "someone-unique-name")
    assert _login_name("env://AF_TEST_REGISTRY_USER") == "someone-unique-name"
    assert not REDACTOR.knows("someone-unique-name")
    assert _login_name("plain-user") == "plain-user" and _login_name("vault://x") == ""
