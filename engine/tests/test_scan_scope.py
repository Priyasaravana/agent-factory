"""Scanners read what a change would commit (`stations.committed_files`), never what a verify run
leaves in the worktree. Seen live (nextjs-web, 2026-10-18): gitleaks scanned node_modules and .next,
reported 14 "leaks" in third-party code, and the developer agent spent two loops chasing them."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

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


async def test_without_git_the_whole_worktree_is_scanned(tmp_path):
    wt = tmp_path / "plain"
    wt.mkdir()
    ws = Workspace(tmp_path / "data", tmp_path, "t", "t@t")
    ctx = SimpleNamespace(ws=ws, worktree=wt, change=SimpleNamespace(id="c1"), station_id="scan")
    async with committed_files(ctx) as scope:  # type: ignore[arg-type]
        assert scope == wt, "never scan less than asked"


async def test_the_quality_gate_and_scans_read_the_copy_not_the_worktree(make_factory):
    ex = FakeExecutor()
    f = make_factory(ex)
    change = f.manager.start_change(f.manager.create_product(PRODUCT))
    assert await wait_run(f, change.id, 60) == ChangeStatus.awaiting_feedback
    gitleaks = [c for c in ex.calls if "gitleaks" in c]
    assert gitleaks, "the secret scan ran"
    worktree = str(f.manager.ws.data_dir / "runs")
    assert all("/scan/" in c and worktree not in c for c in gitleaks), gitleaks
