# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""Audit export (feature `siem`, ADR-0051). Phase C ships the first sink: a JSON-lines file a SIEM
agent (Splunk Universal Forwarder, Elastic Agent, Fluent Bit, Vector) tails. Phase D adds direct
delivery (HTTP event collectors, syslog).

One file per UTC day, `audit-YYYY-MM-DD.jsonl`, in `AGENT_FACTORY_AUDIT_DIR` (default
`$DATA_DIR/audit`), mode 0600. Every line carries `seq` and `prev`, the SHA-256 of the line before it
(across days and restarts), so a line removed or edited breaks the chain: `verify_chain` finds where.
Events hold ids, statuses and who acted; never secret values or tool output (the core emits only those).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def default_dir(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    if env.get("AGENT_FACTORY_AUDIT_DIR"):
        return Path(env["AGENT_FACTORY_AUDIT_DIR"])
    return Path(env.get("DATA_DIR") or "/data") / "audit"


def _digest(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


class AuditFile:
    """An append-only, hash-chained JSON-lines audit log."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        self._seq: int | None = None
        self._prev = GENESIS

    def _files(self) -> list[Path]:
        return sorted(self.folder.glob("audit-*.jsonl"))

    def _resume(self) -> None:
        """Pick the chain up where the last line left it (after a restart)."""
        self._seq, self._prev = 0, GENESIS
        for path in reversed(self._files()):
            lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                self._seq, self._prev = int(last["seq"]), _digest(lines[-1])
                return

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self._seq is None:
                self._resume()
            assert self._seq is not None
            seq = self._seq + 1
            line = json.dumps({**event, "seq": seq, "prev": self._prev}, sort_keys=True, default=str)
            path = self.folder / f"audit-{datetime.now(UTC):%Y-%m-%d}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line + "\n")
            self._seq, self._prev = seq, _digest(line)


def verify_chain(folder: Path) -> tuple[bool, str]:
    """Pure over the files: (intact, where it broke)."""
    prev, seq = GENESIS, 0
    for path in sorted(folder.glob("audit-*.jsonl")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                return False, f"{path.name}:{n}: not JSON"
            if doc.get("prev") != prev or doc.get("seq") != seq + 1:
                return False, f"{path.name}:{n}: the chain breaks here (seq {doc.get('seq')}, expected {seq + 1})"
            prev, seq = _digest(line), seq + 1
    return True, f"{seq} event(s), chain intact"
