"""Product repos and per-run worktrees (Builder Factory: fresh-per-run isolation).

<data>/products/<slug>/        product repo (main branch), one per product; for an existing
                               repo (ADR-0031): a shallow, read-only clone of its branch
<data>/runs/<run_id>/          git worktree on branch run/<run_id>
<data>/holdout/<slug>/         hidden acceptance scenarios — never in a worktree
"""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from agent_factory.executor import CommandResult, Executor, LocalExecutor


def render_codeowners(repo: Path, owner: str) -> None:
    """Fill the template's CODEOWNERS with the publishing owner (user or org/team)."""
    f = repo / ".github" / "CODEOWNERS"
    if not f.exists():
        return
    text = f.read_text()
    if owner:
        f.write_text(text.replace("__OWNER__", owner.lstrip("@")))
    else:
        f.write_text(
            text.replace("* @__OWNER__", "# * @your-org/your-team   (set policies.publish.owner in the factory config)")
        )


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

    # where a repo URL is cloned from, and whether local paths may be cloned: only tests
    # point these at a local repository; otherwise only https is allowed
    clone_source: Callable[[str], str] = staticmethod(lambda url: url)  # type: ignore[assignment]
    local_clones: bool = False

    def product_dir(self, slug: str) -> Path:
        return self.data_dir / "products" / slug

    def change_dir(self, change_id: str) -> Path:
        return self.data_dir / "runs" / change_id

    def holdout_dir(self, slug: str) -> Path:
        return self.data_dir / "holdout" / slug

    async def git(self, args: str, cwd: Path) -> CommandResult:
        return await self.ex.run(f"git {args}", cwd=cwd, timeout=120, env=self.git_env)

    async def ensure_product_repo(self, slug: str, template: str, owner: str = "") -> Path:
        repo = self.product_dir(slug)
        if (repo / ".git").exists():
            return repo
        src = self.factory_home / template
        repo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, repo, dirs_exist_ok=True)
        render_codeowners(repo, owner)
        await self.git("init -q -b main", repo)
        await self.git("add -A", repo)
        await self.git(f'commit -q -m "chore: scaffold from {template}"', repo)
        return repo

    async def sync_repo(self, slug: str, url: str, branch: str | None, timeout: float = 300) -> tuple[str, str]:
        """Clone an existing repository read-only, or fetch its branch again (ADR-0031).
        Shallow, no submodules, no hooks, no LFS, no prompts, and pushing is disabled.
        Returns (branch, base ref for the change's worktree)."""
        repo = self.product_dir(slug)
        env = {**self.git_env, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GIT_ASKPASS": "/bin/false"}
        safe = "-c core.hooksPath=/dev/null -c protocol.allow=never -c protocol.https.allow=always" + (
            " -c protocol.file.allow=always" if self.local_clones else ""
        )
        src = shlex.quote(self.clone_source(url))
        if not (repo / ".git").exists():
            repo.parent.mkdir(parents=True, exist_ok=True)
            pick = f"--branch {shlex.quote(branch)} " if branch else ""
            res = await self.ex.run(
                f"git {safe} clone --quiet --depth 1 --single-branch --no-recurse-submodules {pick}-- {src} {repo}",
                cwd=repo.parent,
                timeout=timeout,
                env=env,
            )
            if not res.ok:
                shutil.rmtree(repo, ignore_errors=True)
                raise RuntimeError(f"could not clone {url}: {res.output.strip()[-800:]}")
            await self.git("remote set-url --push origin no-push://read-only", repo)
            if not branch:
                head = await self.git("rev-parse --abbrev-ref HEAD", repo)
                branch = head.output.strip().splitlines()[-1] if head.ok and head.output.strip() else "main"
        else:
            branch = branch or "main"
            spec = shlex.quote(f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
            res = await self.ex.run(
                f"git {safe} fetch --quiet --depth 1 --no-recurse-submodules origin {spec}",
                cwd=repo,
                timeout=timeout,
                env=env,
            )
            if not res.ok:
                raise RuntimeError(f"could not fetch {url} ({branch}): {res.output.strip()[-800:]}")
        return branch, f"origin/{branch}"

    async def create_worktree(self, slug: str, change_id: str, base: str = "main") -> Path:
        wt = self.change_dir(change_id)
        if wt.exists():
            return wt  # resume: keep the change's own worktree untouched
        wt.parent.mkdir(parents=True, exist_ok=True)
        res = await self.git(f"worktree add -q -b run/{change_id} {wt} {shlex.quote(base)}", self.product_dir(slug))
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
