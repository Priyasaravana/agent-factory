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


@dataclass
class FakeExecutor:
    """Dry-run / test executor. Succeeds unless a substring in `fail_on` matches.
    Each matching command fails once, then passes (simulates a fix loop)."""

    fail_on: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

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
                return CommandResult(command, 1, f"[dry-run] simulated failure for '{needle}'")
        await asyncio.sleep(0.05)
        return CommandResult(command, 0, f"[dry-run] ok: {command}")
