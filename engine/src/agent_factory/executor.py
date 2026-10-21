"""Executor seam: where deterministic commands (docker, kind, helm, make) run.

v0: LocalExecutor runs them inside the factory container, talking to the
Docker-in-Docker daemon over TLS. Scaled: a KubernetesJobExecutor would run
each command in a sandboxed pod — same interface.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

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
        return CommandResult(command, 0, f"[dry-run] ok: {command}")
