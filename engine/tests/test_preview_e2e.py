"""E2E before deploy (ADR-0043 §5): a pack's `preview`, the `preview-e2e` station and its script."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import PRODUCT, REPO, wait_run

from agent_factory import contract
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus
from agent_factory.packs import PREVIEW_NOT_READY, PackError, PackPreview, load_pack, preview_e2e_script
from agent_factory.workflow import HANDLER_INFO, WorkflowDoc

WEB = PRODUCT.model_copy(update={"blueprint": "nextjs-web"})
LANES = REPO / "workflow-templates" / "standard" / "lanes"


# ------------------------------------------------------------------ the pack --
def test_nextjs_declares_a_preview_and_fastapi_does_not_yet():
    web = load_pack(REPO / "packs" / "nextjs-web").stack
    api = load_pack(REPO / "packs" / "fastapi-service").stack
    assert web is not None and web.preview == PackPreview(command="make preview", url="http://127.0.0.1:3000")
    assert api is not None and api.preview is None


def _pack_with(tmp_path: Path, **stack: object) -> Path:
    import shutil

    folder = tmp_path / "pack"
    shutil.copytree(REPO / "packs" / "nextjs-web", folder, ignore=shutil.ignore_patterns("node_modules", ".next"))
    raw = yaml.safe_load((folder / "pack.yaml").read_text())
    raw["stack"].update(stack)
    raw["stack"] = {k: v for k, v in raw["stack"].items() if v is not None}
    (folder / "pack.yaml").write_text(yaml.safe_dump(raw))
    return folder


def test_a_preview_without_an_e2e_suite_is_refused(tmp_path):
    with pytest.raises(PackError, match="preview needs stack.e2e"):
        load_pack(_pack_with(tmp_path, e2e=None))


@pytest.mark.parametrize("url", ["http://example.com:3000", "https://127.0.0.1:3000", "http://127.0.0.1"])
def test_a_preview_serves_only_inside_the_sandbox(tmp_path, url):
    with pytest.raises(ValueError, match="url"):
        load_pack(_pack_with(tmp_path, preview={"command": "make preview", "url": url}))


# ------------------------------------------------------------- the script ---
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True, timeout=60)  # noqa: S603, S607


def test_the_script_runs_e2e_against_a_live_preview_then_stops_it(tmp_path):
    port = _free_port()
    (tmp_path / "healthz").write_text("ok")
    preview = PackPreview(
        command=f"{sys.executable} -m http.server {port} --bind 127.0.0.1", url=f"http://127.0.0.1:{port}"
    )
    script = preview_e2e_script(preview, "curl -fs {url}/healthz && echo E2E-RAN", "/healthz")
    res = _run(script, tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "E2E-RAN" in res.stdout and "preview output" not in res.stdout
    with socket.socket() as s, pytest.raises(ConnectionRefusedError):  # stopped: nothing answers
        s.connect(("127.0.0.1", port))


def test_loopback_bypasses_the_sandbox_proxy(tmp_path):
    """The sandbox routes all HTTP through its egress proxy with NO_PROXY empty, and the proxy
    refuses loopback (first live run, 2026-10-21): the probe and the suite must not use it."""
    port, dead = _free_port(), _free_port()
    (tmp_path / "healthz").write_text("ok")
    preview = PackPreview(
        command=f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
        url=f"http://127.0.0.1:{port}",
        ready_seconds=15,  # a regression fails fast instead of waiting out the default
    )
    script = preview_e2e_script(preview, "curl -fs {url}/healthz && echo E2E-RAN", "/healthz")
    proxy = f"http://127.0.0.1:{dead}"  # nothing listens: any request through it fails
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "NO_PROXY": "", "no_proxy": ""}
    env |= {k: proxy for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")}
    res = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)  # noqa: S603, S607
    assert res.returncode == 0, res.stdout + res.stderr
    assert "E2E-RAN" in res.stdout


def test_the_template_e2e_target_bypasses_the_proxy_for_loopback():
    make = (REPO / "packs" / "nextjs-web" / "template" / "Makefile").read_text()
    assert "NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost BASE_URL=$(BASE_URL) npx playwright test" in make


def test_a_failing_suite_shows_its_output_and_the_previews(tmp_path):
    port = _free_port()
    preview = PackPreview(
        command=f"{sys.executable} -m http.server {port} --bind 127.0.0.1", url=f"http://127.0.0.1:{port}"
    )
    res = _run(preview_e2e_script(preview, "echo SUITE-SAYS-NO; exit 7", "/"), tmp_path)
    assert res.returncode == 7
    assert "SUITE-SAYS-NO" in res.stdout and "--- preview output" in res.stdout


def test_a_preview_that_never_answers_fails_fast_with_its_output(tmp_path):
    preview = PackPreview(command="echo BUILD-BROKE; exit 2", url=f"http://127.0.0.1:{_free_port()}", ready_seconds=30)
    res = _run(preview_e2e_script(preview, "echo SHOULD-NOT-RUN", "/healthz"), tmp_path)
    assert res.returncode == 3
    assert PREVIEW_NOT_READY in res.stdout and "BUILD-BROKE" in res.stdout and "SHOULD-NOT-RUN" not in res.stdout


def test_the_script_quotes_the_preview_command():
    preview = PackPreview(command="make preview; echo 'x'", url="http://127.0.0.1:3000")
    script = preview_e2e_script(preview, "make e2e BASE_URL={url}", "/api/healthz")
    assert "setsid bash -c 'make preview; echo '\"'\"'x'\"'\"''" in script
    assert "http://127.0.0.1:3000/api/healthz" in script and "( make e2e BASE_URL=http://127.0.0.1:3000 )" in script


# ------------------------------------------------------- type and lanes ---
def test_the_station_type_sits_in_verify_and_skips_team_repos():
    t = contract.STATION_TYPES["preview-e2e"]
    assert t.column == "verify" and {str(g) for g in t.gives} == {"report.test@1"}
    assert t.implementations == {"factory": "preview-e2e", "repo": None}
    assert t.uses == frozenset({"read-repo", "run-sandbox"})
    assert HANDLER_INFO["preview-e2e"].phase == "verify"


@pytest.mark.parametrize("lane", ["new-product.yaml", "product-change.yaml"])
def test_factory_lanes_run_it_right_after_test(lane):
    doc = WorkflowDoc.model_validate(yaml.safe_load((LANES / lane).read_text()) | {"agents": {}})
    ids = [s.id for s in doc.stations]
    assert ids[ids.index("test") + 1] == "preview-e2e"
    assert doc.station("preview-e2e").on_fail == "implement"


@pytest.mark.parametrize("lane", ["repo-change.yaml", "repo-assessment.yaml"])
def test_team_repo_lanes_leave_it_out(lane):
    assert "preview-e2e" not in (LANES / lane).read_text()


# ----------------------------------------------------------- in the lane ---
async def test_a_next_js_change_runs_e2e_against_the_preview_before_the_image(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    preview = next(i for i, c in enumerate(ex.calls) if "setsid bash -c 'make preview'" in c)
    build = next(i for i, c in enumerate(ex.calls) if c.startswith("docker build"))
    assert preview < build
    assert "make e2e BASE_URL=http://127.0.0.1:3000" in ex.calls[preview]
    messages = [e.message for e in f.store.list_events(change.id)]
    assert "end-to-end tests passed against the preview" in messages
    assert "end-to-end tests passed against the live app" in messages, "acceptance still runs it live"


async def test_a_failing_preview_e2e_goes_to_implement_before_anything_is_built(make_factory):
    ex = FakeExecutor(fail_on=["make e2e BASE_URL=http://127.0.0.1:3000"])
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(WEB))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback  # fixed on the second pass
    events = f.store.list_events(change.id)
    finished = [e.message for e in events if e.message.startswith("preview-e2e: ")]
    assert finished[0] == "preview-e2e: failed — end-to-end tests failed against the preview"
    first_fail = next(i for i, c in enumerate(ex.calls) if "127.0.0.1:3000" in c)
    assert not any(c.startswith("docker build") for c in ex.calls[:first_fail])


async def test_a_pack_without_a_preview_passes_and_says_why(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    assert not any("setsid" in c for c in ex.calls)
    messages = [e.message for e in f.store.list_events(change.id)]
    assert any(m.startswith("end-to-end before deploy not run: fastapi-service@") for m in messages)
