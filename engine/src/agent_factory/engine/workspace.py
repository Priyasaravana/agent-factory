"""Product repos and per-run worktrees (Builder Factory: fresh-per-run isolation).

<data>/products/<slug>/        product repo (main branch), one per product; for an existing
                               repo (ADR-0031): a shallow, read-only clone of its branch
<data>/runs/<run_id>/          git worktree on branch run/<run_id>
<data>/holdout/<slug>/         hidden acceptance scenarios — never in a worktree
"""

from __future__ import annotations

import re
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from agent_factory.executor import CommandResult, Executor, LocalExecutor
from agent_factory.packs import IGNORED


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

    def git_env_for(self, cwd: Path) -> dict[str, str]:
        """The git environment for a repository the engine made under its data dir: also trusted
        whoever the filesystem says owns it. A host bind mount (Docker Desktop, Rancher/Lima on a
        Mac) can report another owner at times, and git then refuses the repo ("dubious
        ownership"); the scan scope fell back to the whole worktree that way (2026-10-22). Command
        line config, so it never touches a global file; repositories elsewhere keep git's check."""
        try:
            inside = cwd.resolve().is_relative_to(self.data_dir.resolve())
        except OSError:
            inside = False
        if not inside:
            return dict(self.git_env)
        return {
            **self.git_env,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(cwd.resolve()),
        }

    async def git(self, args: str, cwd: Path) -> CommandResult:
        return await self.ex.run(f"git {args}", cwd=cwd, timeout=120, env=self.git_env_for(cwd))

    async def ensure_product_repo(self, slug: str, template: str, owner: str = "") -> Path:
        repo = self.product_dir(slug)
        if (repo / ".git").exists():
            return repo
        src = self.factory_home / template
        repo.parent.mkdir(parents=True, exist_ok=True)
        # build leftovers in the factory's own copy of a template (node_modules, .venv, …) stay out
        shutil.copytree(src, repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*IGNORED))
        render_codeowners(repo, owner)
        await self.git("init -q -b main", repo)
        await self.git("add -A", repo)
        await self.git(f'commit -q -m "chore: scaffold from {template}"', repo)
        return repo

    async def only_scaffold(self, slug: str) -> bool:
        """The product repo holds nothing but the template it was scaffolded from: no change has
        landed on main yet (ADR-0041 §2: the stack may still switch)."""
        repo = self.product_dir(slug)
        if not (repo / ".git").exists():
            return True
        res = await self.git("rev-list --count main", repo)
        return res.ok and res.output.strip().splitlines()[-1:] == ["1"]

    async def discard_scaffold(self, slug: str, change_ids: list[str]) -> None:
        """Remove a product repo that is still only its template, with the changes' worktrees and
        hidden scenarios, so the next run scaffolds from another stack (ADR-0041 §2)."""
        for cid in change_ids:
            shutil.rmtree(self.change_dir(cid), ignore_errors=True)
        shutil.rmtree(self.product_dir(slug), ignore_errors=True)
        shutil.rmtree(self.holdout_dir(slug), ignore_errors=True)

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

    async def tree_id(self, wt: Path) -> str:
        """The committed tree at HEAD: equal for two commits with the same files, so it tells
        whether a repair changed any code. Empty when git can't say."""
        res = await self.git("rev-parse HEAD^{tree}", wt)
        out = res.output.strip().splitlines() if res.ok else []
        return out[-1] if out and re.fullmatch(r"[0-9a-f]{40,64}", out[-1]) else ""

    async def head_sha(self, wt: Path) -> str:
        res = await self.git("rev-parse --short HEAD", wt)
        return res.output.strip().splitlines()[-1] if res.ok and res.output.strip() else "dev"
