"""How one sandbox container is started: pure functions, unit-tested without Docker.

A sandbox is a throw-away container inside dind with:
  * only the change worktree writable (plus tmpfs /tmp and $HOME)
  * the product's git metadata read-only, pinned skills read-only
  * no Docker socket, no TLS keys, no kubeconfig, no factory secrets
  * an internal network whose only way out is the egress proxy (allowlist)
  * a non-root user, no capabilities, no privilege escalation, resource limits
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SANDBOX_UID = 10001
SANDBOX_HOME = "/home/agent"
PROXY_PORT = 3128
CACHE_VOLUME = "factory-sandbox-cache"  # uv/pip downloads shared by sandboxes; owned by SANDBOX_UID

# Environment the agent CLI may see. Everything else in the engine's
# environment (GITHUB_TOKEN, SKILLS_GITHUB_TOKEN, AWS_*, DOCKER_*, KUBECONFIG, …)
# never reaches a sandbox. The model credential is the one exception the agent
# needs; a later step can move it behind a local auth proxy.
_FORWARD_EXACT = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "TRACEPARENT",
    "TRACESTATE",
}
_FORWARD_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "MAX_THINKING_TOKENS")
_NEVER = re.compile(r"(GITHUB|GH_|AWS_|VAULT|DOCKER|KUBE|SKILLS_|SECRET|PASSWORD)", re.I)

# Quiet, offline-friendly CLI defaults inside every sandbox.
SANDBOX_DEFAULT_ENV = {
    "HOME": SANDBOX_HOME,
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "UV_PYTHON_DOWNLOADS": "never",
    "UV_CACHE_DIR": f"{SANDBOX_HOME}/.cache/uv",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
}


def forwarded_env_names(env: dict[str, str]) -> list[str]:
    """Names of the engine's variables a sandboxed agent may inherit."""
    names = []
    for k in env:
        if _NEVER.search(k) and k not in _FORWARD_EXACT:
            continue
        if k in _FORWARD_EXACT or k.startswith(_FORWARD_PREFIXES):
            names.append(k)
    return sorted(names)


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    readonly: bool = True

    def arg(self) -> str:
        return f"type=bind,src={self.source},dst={self.target}" + (",readonly" if self.readonly else "")


@dataclass
class SandboxSpec:
    """Everything `docker run` needs for one sandbox."""

    name: str
    image: str
    network: str
    workdir: str
    mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # literal values (never secrets)
    pass_env: list[str] = field(default_factory=list)  # names inherited from the caller (secrets stay out of argv)
    labels: dict[str, str] = field(default_factory=dict)
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 512
    tmp_size: str = "1g"
    cache_volume: str | None = CACHE_VOLUME

    def argv(self, command: list[str], interactive: bool = False) -> list[str]:
        a = ["docker", "run", "--rm", "--init", "--name", self.name]
        if interactive:
            a.append("-i")
        a += [
            "--user", f"{SANDBOX_UID}:{SANDBOX_UID}",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids),
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--network", self.network,
            "--tmpfs", f"/tmp:rw,exec,mode=1777,size={self.tmp_size}",  # noqa: S108 - private to this container
            "--tmpfs", f"{SANDBOX_HOME}:rw,exec,size={self.tmp_size},uid={SANDBOX_UID},gid={SANDBOX_UID}",
            "--workdir", self.workdir,
        ]  # fmt: skip
        if self.cache_volume:
            # package downloads only (uv/pip); never code or credentials
            a += ["--mount", f"type=volume,src={self.cache_volume},dst={SANDBOX_HOME}/.cache"]
        for m in self.mounts:
            a += ["--mount", m.arg()]
        for k, v in sorted({**SANDBOX_DEFAULT_ENV, **self.env}.items()):
            a += ["-e", f"{k}={v}"]
        for k in self.pass_env:
            a += ["-e", k]  # value comes from the caller's environment, not the command line
        for k, v in sorted(self.labels.items()):
            a += ["--label", f"{k}={v}"]
        return [*a, self.image, *command]

    def to_json(self) -> str:
        d = self.__dict__.copy()
        d["mounts"] = [m.__dict__ for m in self.mounts]
        return json.dumps(d)

    @classmethod
    def from_json(cls, text: str) -> SandboxSpec:
        d = json.loads(text)
        d["mounts"] = [Mount(**m) for m in d.get("mounts", [])]
        return cls(**d)


def proxy_env(proxy_host: str) -> dict[str, str]:
    url = f"http://{proxy_host}:{PROXY_PORT}"
    # curl only reads the lowercase http_proxy; set both spellings for every tool
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "NO_PROXY": "",
        "no_proxy": "",
    }


def git_mounts(worktree: Path) -> list[Mount]:
    """A git worktree's `.git` is a file pointing into the product repo. Mount
    the repo's git metadata read-only (history, for `git log/diff`) and only
    this worktree's own admin dir writable (its index), so an agent can inspect
    history but cannot rewrite the product repo or other runs."""
    dot = worktree / ".git"
    if not dot.is_file():
        return []
    m = re.match(r"gitdir:\s*(.+)", dot.read_text().strip())
    if not m:
        return []
    admin = Path(m.group(1).strip())
    if not admin.is_absolute():
        admin = (worktree / admin).resolve()
    common_file = admin / "commondir"
    common = (admin / common_file.read_text().strip()).resolve() if common_file.exists() else admin.parent.parent
    return [Mount(str(common), str(common), True), Mount(str(admin), str(admin), False)]


def container_name(*parts: str) -> str:
    raw = "-".join(p for p in parts if p)
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-")
    return f"af-sbx-{safe}"[:63]


def content_tag(paths: list[Path], extra: str = "") -> str:
    """Stable image tag from the files that go into the sandbox image."""
    h = hashlib.sha256(extra.encode())
    for root in paths:
        files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]
        for f in files:
            if "__pycache__" in f.parts:
                continue
            h.update(str(f.relative_to(root.parent) if root.is_dir() else f.name).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]
