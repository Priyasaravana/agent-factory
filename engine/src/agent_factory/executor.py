"""Executor seam: where deterministic commands (docker, kind, helm, make) run.

v0: LocalExecutor runs them inside the factory container, talking to the
Docker-in-Docker daemon over TLS. Scaled: a KubernetesJobExecutor would run
each command in a sandboxed pod — same interface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

TAIL_CHARS = 6000


@dataclass
class CommandResult:
    command: str
    returncode: int
    output: str  # combined stdout+stderr, tail-truncated
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Executor(Protocol):
    async def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 600,
        env: dict[str, str] | None = None,
    ) -> CommandResult: ...


class LocalExecutor:
    async def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 600,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, **(env or {})},
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(command, -1, f"timed out after {timeout}s", timed_out=True)
        text = out.decode(errors="replace")
        return CommandResult(command, proc.returncode or 0, text[-TAIL_CHARS:])


# Reports of the built-in tool stations when they find nothing (tools.py, ADR-0034)
CLEAN_SCANS = {
    "semgrep scan": '{"version": "2.1.0", "runs": []}',
    "osv-scanner": '{"results": []}',
    "--report-format json --report-path /dev/stdout": "[]",  # gitleaks
    " config --format json": '{"Results": []}',  # trivy config
    " image --quiet --format json": '{"SchemaVersion": 2, "Results": []}',  # trivy image (ADR-0042)
    "snyk code test": '{"version": "2.1.0", "runs": []}',
    "snyk test --all-projects": '{"version": "2.1.0", "runs": []}',
    "snyk iac test": '{"version": "2.1.0", "runs": []}',
    "/code-scanning/alerts": "[]",
    "/dependabot/alerts": "[]",
    "/secret-scanning/alerts": "[]",
}


_UPGRADE = re.compile(r"\bupgrade --install (\S+) ")
_SET = re.compile(r"--set (?:'([^']*)'|(\S+))")
_ROLLBACK = re.compile(r"\brollback (\S+) (\d+)\b")
_REVISION = re.compile(r"--revision (\d+)")
_UNINSTALL = re.compile(r"\buninstall (\S+) ")
_COLOR = re.compile(r"app\.kubernetes\.io/color=(\w+)")


def _values(command: str) -> dict[str, Any]:
    """The `--set a.b=c` values of a Helm command, nested as `helm get values -o json` prints them."""
    out: dict[str, Any] = {}
    for quoted, bare in _SET.findall(command.split(" upgrade --install ", 1)[-1]):
        key, _, value = (quoted or bare).partition("=")
        *path, last = re.split(r"(?<!\\)\.", key)
        node = out
        for part in path:
            node = node.setdefault(part.replace("\\.", "."), {})
        node[last.replace("\\.", ".")] = value
    return out


@dataclass
class FakeHelm:
    """A dry-run Helm: `upgrade --install` and `rollback` add revisions with the values they set,
    `uninstall` removes a release; `status`, `history`, `get values` and the pods' imageIDs (`get
    pods … jsonpath`, one colour's on a blue/green release) answer from them. The digest of an image
    is the SHA-256 of its name, so the same image always has the same digest."""

    releases: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # a mesh proxy in every pod, on its own image: digests are read for the app's containers only
    sidecar: str = "docker.io/istio/proxyv2:1.24.0=docker.io/istio/proxyv2@sha256:" + "f" * 64

    @staticmethod
    def digest(image: str) -> str:
        return "sha256:" + hashlib.sha256(image.encode()).hexdigest()

    @staticmethod
    def images(values: dict[str, Any]) -> dict[str, str]:
        """The image each colour runs (in place: blue only), from a revision's values."""
        from agent_factory import blue_green

        found = blue_green.layout(json.dumps(values))
        return dict(found.images) if found else {}

    def answer(self, command: str) -> tuple[int, str] | None:
        if "helm" not in command and "kubectl" not in command:
            return None
        if m := _UPGRADE.search(command):
            name = m.group(1)
            self._add(name, _values(command), "Upgrade complete" if name in self.releases else "Install complete")
            return 0, f"[dry-run] Release {name} has been upgraded."
        if m := _UNINSTALL.search(command):  # the release and its history go (a temporary one, archive)
            self.releases.pop(m.group(1), None)
            return 0, f"[dry-run] release {m.group(1)} uninstalled"
        if m := _ROLLBACK.search(command):
            name, rev = m.group(1), int(m.group(2))
            old = next((r for r in self.releases.get(name, []) if r["revision"] == rev), None)
            if old is None:
                return None
            self._add(name, old["values"], f"Rollback to {rev}")
            return 0, "Rollback was a success! Happy Helming!"
        for name, revs in self.releases.items():
            if not revs or not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", command):
                continue
            latest = revs[-1]
            if " status " in command and "-o json" in command:
                return 0, json.dumps(
                    {"name": name, "version": latest["revision"], "info": {"status": latest["status"]}}
                )
            if " history " in command and "-o json" in command:
                return 0, json.dumps([{k: r[k] for k in ("revision", "status", "description")} for r in revs])
            if " get values " in command:
                asked = _REVISION.search(command)
                row = next((r for r in revs if r["revision"] == int(asked.group(1))), None) if asked else latest
                return (0, json.dumps(row["values"])) if row else None
            if "get pods" in command and "jsonpath" in command:
                images = self.images(latest["values"])
                color = _COLOR.search(command)
                running = [images[color.group(1)]] if color and color.group(1) in images else list(images.values())
                lines = []
                for image in running:
                    repo = image.rsplit(":", 1)[0]
                    app = f"docker.io/library/{image}=docker.io/library/{repo}@{self.digest(image)}"
                    lines.append(f"|{app} {self.sidecar} ")
                return 0, "\n".join(lines) + "\n"
        if " get values " in command:  # a release Helm doesn't have
            return 1, "Error: release: not found"
        return None

    def latest_image(self, name: str) -> str | None:
        """What users of release `name` reach now (blue/green: the live colour)."""
        from agent_factory import blue_green

        revs = self.releases.get(name) or []
        return blue_green.values_image(json.dumps(revs[-1]["values"])) if revs else None

    def _add(self, name: str, values: dict[str, Any], description: str) -> None:
        revs = self.releases.setdefault(name, [])
        for r in revs:
            if r["status"] == "deployed":
                r["status"] = "superseded"
        revs.append({"revision": len(revs) + 1, "status": "deployed", "description": description, "values": values})


@dataclass
class FakeExecutor:
    """Dry-run / test executor. Succeeds unless a substring in `fail_on` matches.
    Each matching command fails once, then passes (simulates a fix loop)."""

    fail_on: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    # what a command prints, by a substring of it: scanners answer like a clean scan (ADR-0034).
    # A list answers in turn, its last entry from then on (a scan before and after a fix).
    scan_outputs: dict[str, str | list[str]] = field(default_factory=lambda: dict(CLEAN_SCANS))
    # what a failing command prints, by its `fail_on` needle (simulates an infra error, ADR-0043)
    fail_with: dict[str, str] = field(default_factory=dict)
    # Helm's release history and the pods it runs, so dry-run deploys and rollbacks (ADR-0046) see
    # real revisions, values and digests; `scan_outputs` still answers first
    helm: FakeHelm = field(default_factory=lambda: FakeHelm())

    async def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 600,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.calls.append(command)
        for needle in list(self.fail_on):
            if needle in command:
                self.fail_on.remove(needle)
                return CommandResult(
                    command, 1, self.fail_with.get(needle, f"[dry-run] simulated failure for '{needle}'")
                )
        await asyncio.sleep(0.05)
        for needle, output in self.scan_outputs.items():
            if needle in command:
                if isinstance(output, list):
                    output = output.pop(0) if len(output) > 1 else output[0]
                return CommandResult(command, 0, output)
        if (helm := self.helm.answer(command)) is not None:
            return CommandResult(command, *helm)
        return CommandResult(command, 0, f"[dry-run] ok: {command}")
