"""Scanners read what a change would commit (`stations.committed_files`), never what a verify run
leaves in the worktree. Seen live (nextjs-web, 2026-10-18): gitleaks scanned node_modules and .next,
reported 14 "leaks" in third-party code, and the developer agent spent two loops chasing them."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import PRODUCT, wait_run

from agent_factory.engine.stations import committed_files
from agent_factory.engine.workspace import Workspace
from agent_factory.executor import FakeExecutor
from agent_factory.models import ChangeStatus


def _repo(root: Path) -> Path:
    wt = root / "runs" / "c1"
    wt.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(wt)]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True)
    (wt / ".gitignore").write_text("node_modules/\n.next/\n")
    (wt / "src").mkdir()
    (wt / "src" / "app.ts").write_text("export const ok = 1;\n")
    (wt / "README.md").write_text("x\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    (wt / "src" / "new.ts").write_text("export const n = 2;\n")  # new, not committed yet: still scanned
    (wt / "README.md").unlink()  # tracked but deleted: nothing to scan
    for leftover in ("node_modules/next/dist/key.pem", ".next/server/app.js"):
        (wt / leftover).parent.mkdir(parents=True, exist_ok=True)
        (wt / leftover).write_text("-----BEGIN PRIVATE KEY-----\n")
    return wt


async def test_the_scan_copy_holds_what_would_be_committed_and_is_removed(tmp_path):
    wt = _repo(tmp_path)
    ws = Workspace(tmp_path / "data", tmp_path, "t", "t@t")
    ctx = SimpleNamespace(ws=ws, worktree=wt, change=SimpleNamespace(id="c1"), station_id="quality-gate")
    async with committed_files(ctx) as scope:  # type: ignore[arg-type]
        files = sorted(p.relative_to(scope).as_posix() for p in scope.rglob("*") if p.is_file())
        assert files == [".gitignore", "src/app.ts", "src/new.ts"]
        assert scope.is_relative_to(ws.data_dir), "under the data dir, which dind sees at the same path"
    assert not scope.exists()
    assert (wt / "node_modules").is_dir(), "the worktree itself is untouched"


async def test_without_git_the_whole_worktree_is_scanned_and_it_says_so(tmp_path):
    """Guestbook (2026-10-19): a scan fell back to the worktree silently, and one code scan went
    from 7 medium findings to 1 high and 48 medium (ADR-0043 §6)."""
    wt = tmp_path / "plain"
    wt.mkdir()
    ws = Workspace(tmp_path / "data", tmp_path, "t", "t@t")
    said: list[tuple[str, dict]] = []

    async def emit(kind, message, data=None):
        said.append((message, data or {}))

    ctx = SimpleNamespace(ws=ws, worktree=wt, change=SimpleNamespace(id="c1"), station_id="scan", emit=emit)
    async with committed_files(ctx) as scope:  # type: ignore[arg-type]
        assert scope == wt, "never scan less than asked"
    assert said and said[0][1]["scan_scope"]["scope"] == "worktree"
    assert "not a git repository" in said[0][1]["scan_scope"]["why"]
    assert not list((ws.data_dir / "scan").glob("*.files")), "the listing is removed"


def test_engine_repos_are_trusted_whoever_the_mount_says_owns_them(tmp_path):
    ws = Workspace(tmp_path / "data", tmp_path, "t", "t@t")
    inside = ws.git_env_for(ws.change_dir("c1"))
    assert inside["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert inside["GIT_CONFIG_VALUE_0"] == str(ws.change_dir("c1").resolve()), "exactly this repo, never '*'"
    assert inside["GIT_AUTHOR_NAME"] == "t"
    assert "GIT_CONFIG_COUNT" not in ws.git_env_for(tmp_path / "elsewhere"), "other repos keep git's check"


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to give the worktree another owner")
async def test_a_worktree_with_another_owner_is_still_listed_not_scanned_whole(tmp_path):
    """Live run 2026-10-22 (Mac bind mount): git refused the worktree as "dubious ownership", the
    scan fell back to the whole worktree and gitleaks reported 14 findings in `.next/`."""
    data = tmp_path / "data"
    wt = _repo(data)
    for p in [wt, *wt.rglob("*")]:
        os.lchown(p, 65534, 65534)
    plain = subprocess.run(["git", "-C", str(wt), "ls-files"], capture_output=True, text=True)  # noqa: ASYNC221, S603, S607
    assert "dubious ownership" in plain.stderr, "the setup reproduces the refusal"
    ws = Workspace(data, tmp_path, "t", "t@t")
    said: list[str] = []

    async def emit(kind, message, data=None):
        said.append(message)

    ctx = SimpleNamespace(ws=ws, worktree=wt, change=SimpleNamespace(id="c1"), station_id="quality-gate", emit=emit)
    async with committed_files(ctx) as scope:  # type: ignore[arg-type]
        files = sorted(p.relative_to(scope).as_posix() for p in scope.rglob("*") if p.is_file())
    assert files == [".gitignore", "src/app.ts", "src/new.ts"] and not said


async def test_a_large_repo_is_copied_whole(tmp_path):
    """The listing is read from a file: command output is cut to its last 6000 characters, which
    used to drop the first files of a large repo from every scan."""
    wt = _repo(tmp_path)
    many = [f"src/feature_{i:04d}/component_with_a_long_name.ts" for i in range(400)]  # ~20,000 chars
    for rel in many:
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text("export {};\n")
    ws = Workspace(tmp_path / "data", tmp_path, "t", "t@t")
    ctx = SimpleNamespace(ws=ws, worktree=wt, change=SimpleNamespace(id="c1"), station_id="code-scan")
    async with committed_files(ctx) as scope:  # type: ignore[arg-type]
        files = {p.relative_to(scope).as_posix() for p in scope.rglob("*") if p.is_file()}
    assert set(many) | {".gitignore", "src/app.ts", "src/new.ts"} == files


async def test_the_quality_gate_and_scans_read_the_copy_not_the_worktree(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    gitleaks = [c for c in ex.calls if "gitleaks" in c]
    assert gitleaks, "the secret scan ran"
    worktree = str(f.manager.ws.data_dir / "runs")
    assert all("/scan/" in c and worktree not in c for c in gitleaks), gitleaks
