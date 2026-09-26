"""Fetch a folder from a GitHub repo at a pinned commit.

Used for importing workflow templates and skills. It uses plain git (a sparse,
shallow checkout) rather than the REST API, so it works the same for public and
private repos. Private repos need a read-only token, which is only ever placed
in the git URL of a throwaway process and never logged or stored.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPO = re.compile(r"^(?:https://github\.com/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_FILES = 200
MAX_BYTES = 2_000_000


class GitHubError(Exception):
    """A repo, path or ref could not be fetched (surfaced as HTTP 409)."""


@dataclass
class Fetched:
    repo: str  # owner/name
    path: str
    ref: str  # what the user asked for (branch, tag or sha)
    sha: str  # the exact commit fetched
    root: Path  # local folder with the files at `path`
    _tmp: Path

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


def parse_repo(repo: str) -> str:
    m = _REPO.match(repo.strip())
    if not m:
        raise GitHubError(f"'{repo}' is not a GitHub repo (use owner/name or https://github.com/owner/name)")
    return f"{m['owner']}/{m['name']}"


def _url(repo: str, token: str | None) -> str:
    return f"https://x-access-token:{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"


def _git(args: list[str], cwd: Path | None = None, token: str | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubError(f"git failed: {exc}") from exc
    if out.returncode != 0:
        msg = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["unknown error"]
        text = msg[0].replace(token, "***") if token else msg[0]
        raise GitHubError(f"git {args[0]} failed: {text}")
    return out.stdout


def resolve_ref(repo: str, ref: str, token: str | None = None) -> str:
    """Branch/tag -> commit sha. A full sha is returned as-is."""
    if _SHA.match(ref):
        return ref
    out = _git(["ls-remote", _url(repo, token), ref, f"refs/tags/{ref}^{{}}"], token=token)
    shas = [line.split()[0] for line in out.splitlines() if line.strip()]
    if not shas:
        raise GitHubError(f"ref '{ref}' not found in {repo}")
    return shas[-1]  # prefer the peeled tag commit when present


def fetch_dir(repo: str, path: str, ref: str = "main", token: str | None = None) -> Fetched:
    repo = parse_repo(repo)
    path = path.strip().strip("/")
    if not _SAFE_PATH.match(path) or ".." in path.split("/"):
        raise GitHubError(f"unsafe path '{path}'")
    sha = resolve_ref(repo, ref, token)
    tmp = Path(tempfile.mkdtemp(prefix="af-gh-"))
    try:
        _git(["init", "-q"], cwd=tmp)
        _git(["remote", "add", "origin", _url(repo, token)], cwd=tmp, token=token)
        if path:
            _git(["sparse-checkout", "set", "--no-cone", f"/{path}/"], cwd=tmp)
        _git(["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", sha], cwd=tmp, token=token)
        _git(["checkout", "-q", "FETCH_HEAD"], cwd=tmp)
        _git(["remote", "remove", "origin"], cwd=tmp)  # drop the token-bearing URL immediately
        root = tmp / path if path else tmp
        if not root.is_dir():
            raise GitHubError(f"path '{path}' not found in {repo}@{sha[:7]}")
        files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
        if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
            raise GitHubError(f"'{path}' is too large to import (max {MAX_FILES} files / {MAX_BYTES} bytes)")
        return Fetched(repo=repo, path=path, ref=ref, sha=sha, root=root, _tmp=tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
