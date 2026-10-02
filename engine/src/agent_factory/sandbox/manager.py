"""Sandbox lifecycle inside dind: image, network, egress proxy, per-session containers.

factory container (engine, secrets, docker client)
    │ docker run (TLS to dind)
    ▼
dind ── network "factory-sandbox" (--internal: no route out)
         ├── af-sbx-<run>-<station>   agent CLI / `make verify`, worktree only
         └── factory-egress           allowlist proxy, also on the default bridge
                                      (the only way to the internet or the apps)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from agent_factory.config import SandboxConfig
from agent_factory.executor import CommandResult, Executor, LocalExecutor
from agent_factory.providers.base import Readiness
from agent_factory.sandbox import egress
from agent_factory.sandbox.spec import (
    CACHE_VOLUME,
    PROXY_PORT,
    SANDBOX_UID,
    Mount,
    SandboxSpec,
    container_name,
    content_tag,
    forwarded_env_names,
    git_mounts,
    proxy_env,
)

if TYPE_CHECKING:
    from agent_factory.agents.runner import AgentRequest

log = logging.getLogger("agent_factory.sandbox")

LABEL = "af.sandbox"


def cli_version() -> str:
    try:
        from claude_agent_sdk._cli_version import __cli_version__

        return str(__cli_version__)
    except ImportError:  # pragma: no cover - SDK always installed in the engine
        return "unknown"


def bundled_cli() -> Path | None:
    try:
        import claude_agent_sdk

        p = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        return p if p.exists() else None
    except ImportError:  # pragma: no cover
        return None


def assemble_context(factory_home: Path, dest: Path) -> str | None:
    """Build context for the sandbox image: its Dockerfile, the factory plugin,
    the egress proxy and the Claude CLI the engine's SDK bundles (same version).
    Used by the engine (build inside dind) and by CI (build and test the image)."""
    cli = bundled_cli()
    if cli is None:
        return "Claude CLI binary not found in the engine's SDK install"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(factory_home / "images" / "sandbox", dest)
    shutil.copytree(factory_home / "plugin", dest / "plugin", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(egress.__file__, dest / "egress.py")
    shutil.copy2(cli, dest / "claude")
    return None


class SandboxManager:
    build_attempts = 2
    build_retry_delay = 20.0  # seconds between image build attempts

    def __init__(
        self,
        cfg: SandboxConfig,
        factory_home: Path,
        data_dir: Path,
        ex: Executor | None = None,
    ) -> None:
        self.cfg = cfg
        self.home = factory_home
        self.data_dir = data_dir
        self.ex = ex or LocalExecutor()
        self.state = Readiness("unknown", ["not started"])
        self._ensure: asyncio.Task[Readiness] | None = None
        self._tag: str | None = None

    # ------------------------------------------------------------ identity --
    @property
    def context_dir(self) -> Path:
        return self.home / "images" / "sandbox"

    @property
    def tag(self) -> str:
        if self._tag is None:
            self._tag = content_tag(
                [self.context_dir, self.home / "plugin", Path(egress.__file__)], extra=f"cli={cli_version()}"
            )
        return self._tag

    @property
    def image(self) -> str:
        return f"{self.cfg.image}:{self.tag}"

    def egress_hash(self) -> str:
        return content_tag([], extra=json.dumps([self.image, sorted(self.cfg.egress), sorted(self.cfg.routes)]))

    # ---------------------------------------------------------- bootstrap --
    def start(self) -> None:
        """Prepare in the background (first image build takes a few minutes)."""
        if self._ensure is None or self._ensure.done():
            self._ensure = asyncio.create_task(self.ensure())

    async def ready(self) -> Readiness:
        if self.state.state == "ready":
            return self.state
        if self._ensure is None:
            self.start()
        assert self._ensure is not None
        return await self._ensure

    async def _sh(self, cmd: str, timeout: float = 120) -> CommandResult:
        return await self.ex.run(cmd, timeout=timeout)

    async def ensure(self) -> Readiness:
        try:
            self.state = Readiness("unknown", ["preparing"])
            steps = [self._image, self._cache, self._network, self._proxy, self._cleanup_stale]
            for step in steps:
                problem = await step()
                if problem:
                    self.state = Readiness("failed", [problem])
                    log.error("sandbox not ready: %s", problem)
                    return self.state
            self.state = Readiness("ready", [f"image {self.image}", f"{len(self.cfg.egress)} egress rules"])
            log.info("sandbox ready: %s", self.image)
        except Exception as exc:  # noqa: BLE001 - reported as readiness, never raised into a run
            self.state = Readiness("failed", [f"{type(exc).__name__}: {exc}"])
        return self.state

    async def _image(self) -> str | None:
        if (await self._sh(f"docker image inspect {self.image} >/dev/null 2>&1")).ok:
            return None
        ctx = self.data_dir / "sandbox" / "build"
        problem = assemble_context(self.home, ctx)
        if problem:
            return problem
        log.info("building sandbox image %s (first start only)", self.image)
        # --progress=plain (not -q): a failing step's own output (e.g. a download
        # error) is kept, not just the Dockerfile excerpt. One retry covers a
        # transient network failure; the layer cache makes it cheap.
        cmd = f"docker build --progress=plain -t {self.image} {ctx}"
        for attempt in range(1, self.build_attempts + 1):
            res = await self._sh(cmd, timeout=1800)
            if res.ok:
                break
            log.warning("sandbox image build failed (attempt %d/%d):\n%s", attempt, self.build_attempts, res.output)
            if attempt < self.build_attempts:
                await asyncio.sleep(self.build_retry_delay)
        shutil.rmtree(ctx, ignore_errors=True)
        if res.ok:
            return None
        return f"sandbox image build failed after {self.build_attempts} attempts:\n{res.output[-4000:]}"

    async def _cache(self) -> str | None:
        """A new named volume is root-owned; sandboxes run as SANDBOX_UID. Hand the
        package cache to that user once (idempotent), or `uv sync` cannot write it."""
        vol = CACHE_VOLUME
        await self._sh(f"docker volume create --label {LABEL}=cache {vol} >/dev/null")
        res = await self._sh(
            f"docker run --rm --user 0 --network none -v {vol}:/cache {self.image} "
            f"python3 -c 'import os; os.chown(\"/cache\", {SANDBOX_UID}, {SANDBOX_UID})'"
        )
        return None if res.ok else f"could not prepare the package cache volume {vol}: {res.output[-500:]}"

    async def _network(self) -> str | None:
        net = self.cfg.network
        if (await self._sh(f"docker network inspect {net} >/dev/null 2>&1")).ok:
            internal = await self._sh(f"docker network inspect -f '{{{{.Internal}}}}' {net}")
            if internal.output.strip().endswith("true"):
                return None
            return f"network {net} exists but is not --internal; remove it (docker network rm {net})"
        res = await self._sh(f"docker network create --internal --label {LABEL}=network {net}")
        return None if res.ok else f"could not create network {net}: {res.output[-500:]}"

    async def _proxy(self) -> str | None:
        name, want = self.cfg.proxy, self.egress_hash()
        cur = await self._sh(
            f"docker inspect -f '{{{{index .Config.Labels \"af.egress\"}}}} {{{{.State.Running}}}}' {name}"
        )
        if cur.ok and cur.output.strip().split()[-2:] == [want, "true"]:
            return None
        await self._sh(f"docker rm -f {name} >/dev/null 2>&1")
        allow = " ".join(f"--allow {shlex.quote(r)}" for r in self.cfg.egress)
        allow += "".join(f" --route {shlex.quote(r)}" for r in self.cfg.routes)
        run = (
            f"docker run -d --name {name} --restart unless-stopped --label {LABEL}=egress --label af.egress={want} "
            f"--add-host dind:host-gateway --read-only --cap-drop ALL --security-opt no-new-privileges "
            f"--user {SANDBOX_UID}:{SANDBOX_UID} --memory 256m --pids-limit 256 "
            f"{self.image} python3 /opt/egress.py --port {PROXY_PORT} {allow}"
        )
        res = await self._sh(run)
        if not res.ok:
            return f"could not start egress proxy: {res.output[-800:]}"
        res = await self._sh(f"docker network connect {self.cfg.network} {name}")
        if not res.ok:
            return f"could not attach egress proxy to {self.cfg.network}: {res.output[-500:]}"
        for _ in range(40):  # ready once it logged "listening" (a fresh start takes a moment)
            logs = await self._sh(f"docker logs {name} 2>&1 | head -5")
            if '"listening"' in logs.output:
                return None
            await asyncio.sleep(0.25)
        return f"egress proxy did not start listening:\n{logs.output[-800:]}"

    async def _cleanup_stale(self) -> str | None:
        """Sessions left behind by a crash or restart."""
        await self._sh(f"docker ps -aq --filter label={LABEL}=session | xargs -r docker rm -f >/dev/null 2>&1")
        return None

    # -------------------------------------------------------------- specs --
    def base_spec(self, name: str, workdir: Path, labels: dict[str, str]) -> SandboxSpec:
        return SandboxSpec(
            name=name,
            image=self.image,
            network=self.cfg.network,
            workdir=str(workdir),
            mounts=[Mount(str(workdir), str(workdir), readonly=False), *git_mounts(workdir)],
            env=proxy_env(self.cfg.proxy),
            labels={LABEL: "session", **labels},
            memory=self.cfg.memory,
            cpus=self.cfg.cpus,
            pids=self.cfg.pids,
        )

    def agent_spec(self, req: AgentRequest) -> SandboxSpec:
        name = container_name(req.run_id, req.station, uuid.uuid4().hex[:6])
        spec = self.base_spec(name, req.cwd, {"af.run": req.run_id, "af.station": req.station})
        extra = [req.imported_plugin] if req.imported_plugin else []
        extra += list(req.readable_extra_dirs)
        spec.mounts += [Mount(str(p), str(p), readonly=True) for p in extra if p and Path(p).exists()]
        return spec

    def write_spec(self, spec: SandboxSpec) -> Path:
        d = self.data_dir / "sandbox" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{spec.name}.json"
        p.write_text(spec.to_json())
        return p

    async def remove(self, name: str) -> None:
        await self._sh(f"docker rm -f {name} >/dev/null 2>&1")
        (self.data_dir / "sandbox" / "specs" / f"{name}.json").unlink(missing_ok=True)

    async def remove_run(self, run_id: str) -> None:
        await self._sh(f"docker ps -aq --filter label=af.run={run_id} | xargs -r docker rm -f >/dev/null 2>&1")

    def executor(self, run_id: str = "", station: str = "") -> SandboxExecutor:
        return SandboxExecutor(self, run_id, station)

    # -------------------------------------------------------------- probe --
    async def probe(self) -> list[dict[str, object]]:
        """Run the isolation checks from inside a real sandbox."""
        ready = await self.ready()
        if ready.state != "ready":
            return [{"check": "sandbox ready", "ok": False, "detail": "; ".join(ready.reasons)}]
        work = self.data_dir / "sandbox" / "probe"
        work.mkdir(parents=True, exist_ok=True)
        hand_to_sandbox(work)
        spec = self.base_spec(container_name("probe", uuid.uuid4().hex[:6]), work, {"af.run": "probe"})
        spec.pass_env = forwarded_env_names(dict(os.environ))
        script = Path(__file__).with_name("probe.py").read_text().replace("__PROXY__", f"{self.cfg.proxy}:{PROXY_PORT}")
        res = await self.ex.run(shlex.join(spec.argv(["python3", "-c", script])), timeout=120)
        try:
            return json.loads(res.output.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return [{"check": "probe ran", "ok": False, "detail": res.output[-1500:]}]


def hand_to_sandbox(path: Path) -> None:
    """A directory the engine creates for a sandbox must be writable by SANDBOX_UID.
    As the normal runtime user (10001) it already is; when run as root (a CLI call
    through `docker compose exec`), hand it over explicitly."""
    if os.geteuid() == 0:
        os.chown(path, SANDBOX_UID, SANDBOX_UID)


class SandboxExecutor:
    """Executor for untrusted commands (the generated app's own build/tests):
    same interface as LocalExecutor, but each command runs in a fresh sandbox
    with only its working directory mounted and none of the engine's env."""

    def __init__(self, mgr: SandboxManager, run_id: str = "", station: str = "") -> None:
        self.mgr, self.run_id, self.station = mgr, run_id, station

    async def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 600,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        ready = await self.mgr.ready()
        if ready.state != "ready":
            return CommandResult(command, 125, "sandbox not ready: " + "; ".join(ready.reasons))
        workdir = Path(cwd) if cwd else Path.cwd()
        name = container_name(self.run_id or "exec", self.station, uuid.uuid4().hex[:6])
        spec = self.mgr.base_spec(name, workdir, {"af.run": self.run_id or "exec", "af.station": self.station})
        spec.env.update(env or {})  # explicit, non-secret values only
        argv = spec.argv(["bash", "-lc", command])
        res = await self.mgr.ex.run(shlex.join(argv), timeout=timeout)
        if res.timed_out:
            await self.mgr.remove(name)
        return CommandResult(command, res.returncode, res.output, res.timed_out)
