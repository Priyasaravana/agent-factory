"""Product repos and per-run worktrees (Builder Factory: fresh-per-run isolation).

<data>/products/<slug>/        product repo (main branch), one per order
<data>/runs/<run_id>/          git worktree on branch run/<run_id>
<data>/holdout/<slug>/         hidden acceptance scenarios — never in a worktree
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_factory.executor import CommandResult, Executor, LocalExecutor


class Workspace:
    def __init__(
        self,
        data_dir: Path,
        factory_home: Path,
        author_name: str,
        author_email: str,
        git_executor: Executor | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.factory_home = factory_home
        # git always runs for real (even in dry-run): it is local, cheap and safe,
        # and it makes dry-run produce a genuine product repo you can inspect.
        self.ex = git_executor or LocalExecutor()
        self.git_env = {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }

    def product_dir(self, slug: str) -> Path:
        return self.data_dir / "products" / slug

    def run_dir(self, run_id: str) -> Path:
        return self.data_dir / "runs" / run_id

    def holdout_dir(self, slug: str) -> Path:
        return self.data_dir / "holdout" / slug

    async def git(self, args: str, cwd: Path) -> CommandResult:
        return await self.ex.run(f"git {args}", cwd=cwd, timeout=120, env=self.git_env)

    async def ensure_product_repo(self, slug: str, template: str) -> Path:
        repo = self.product_dir(slug)
        if (repo / ".git").exists():
            return repo
        src = self.factory_home / template
        repo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, repo, dirs_exist_ok=True)
        await self.git("init -q -b main", repo)
        await self.git("add -A", repo)
        await self.git(f'commit -q -m "chore: scaffold from {template}"', repo)
        return repo

    async def create_worktree(self, slug: str, run_id: str) -> Path:
        wt = self.run_dir(run_id)
        if wt.exists():
            return wt  # resume: keep the run's own worktree untouched
        wt.parent.mkdir(parents=True, exist_ok=True)
        res = await self.git(f"worktree add -q -b run/{run_id} {wt} main", self.product_dir(slug))
        if not res.ok:
            raise RuntimeError(f"worktree creation failed: {res.output}")
        return wt

    async def commit_all(self, wt: Path, message: str) -> bool:
        await self.git("add -A", wt)
        res = await self.git(f'commit -q -m "{message}"', wt)
        return res.ok

    async def head_sha(self, wt: Path) -> str:
        res = await self.git("rev-parse --short HEAD", wt)
        return res.output.strip().splitlines()[-1] if res.ok and res.output.strip() else "dev"
