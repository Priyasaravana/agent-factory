# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""Audit export (feature `siem`, ADR-0051, ADR-0053). The record is a JSON-lines file a SIEM agent (Splunk Universal
Forwarder, Elastic Agent, Fluent Bit, Vector) can tail; phase D adds direct delivery to an HTTPS event collector or
syslog over TLS (`delivery.py`), shipping the same lines byte for byte so the chain can be checked where they land
(`verify_export`, `agent-factory-siem verify`).

One file per UTC day, `audit-YYYY-MM-DD.jsonl`, in `AGENT_FACTORY_AUDIT_DIR` (default
`$DATA_DIR/audit`), mode 0600. Every line carries `seq` and `prev`, the SHA-256 of the line before it
(across days and restarts), so a line removed or edited breaks the chain: `verify_chain` finds where.
Events hold ids, statuses and who acted; never secret values or tool output (the core emits only those).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Iterable
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


class Export:
    """The audit sink the licensed edition registers: writes the chained record first, then wakes each delivery
    (`delivery.Forwarder`), which ships it from the file in its own thread. A destination never slows an event."""

    def __init__(self, record: AuditFile, forwarders: list[Any] | None = None) -> None:
        self.record, self.forwarders = record, list(forwarders or [])

    def emit(self, event: dict[str, Any]) -> None:
        self.record.emit(event)
        for fw in self.forwarders:
            fw.notify()

    def start(self) -> None:
        for fw in self.forwarders:
            fw.start()

    def status(self) -> list[dict[str, Any]]:
        """One entry per destination; the record itself is always written while the licence covers `siem`."""
        out: list[dict[str, Any]] = [
            {"destination": "audit file", "transport": "file", "state": "writing", "detail": str(self.record.folder)}
        ]
        for fw in self.forwarders:
            try:
                out.append(fw.status())
            except Exception as exc:  # noqa: BLE001 — a status read never breaks Settings
                out.append({"destination": fw.transport.name, "state": "unknown", "detail": type(exc).__name__})
        return out


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


# ------------------------------------------------------------------------------- at the destination (ADR-0053) --
_SYSLOG = re.compile(r"^(?:\d+ )?<\d{1,3}>1 \S+ \S+ \S+ \S+ \S+ (?:-|(?:\[[^\]]*\])+) ?(.*)$")


def audit_line(raw: str) -> str | None:
    """The audit line inside what a destination stored: the line itself (NDJSON), a Splunk HEC event
    (`{"event": "<line>"}`), or an RFC 5424 message whose MSG is the line. Pure. None: not an audit line."""
    text = raw.rstrip("\r\n")
    if not text.strip():
        return None
    if m := _SYSLOG.match(text):
        text = m.group(1)
    text = text.removeprefix("\ufeff")
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if isinstance(doc, dict) and isinstance(doc.get("event"), str) and "seq" not in doc:
        return audit_line(doc["event"])
    return text if isinstance(doc, dict) and "seq" in doc and "prev" in doc else None


def verify_export(raw_lines: Iterable[str], from_genesis: bool = False, record: Path | None = None) -> tuple[bool, str]:
    """Check what a SIEM received (pure over the lines; `record`: the factory's audit folder to compare against).
    The chain must be unbroken and in order: each line's `prev` is the SHA-256 of the line before and `seq` goes up
    by one. A destination may start mid-chain (delivery turned on later) unless `from_genesis`. Delivery is at least
    once, so an identical repeat is skipped and counted; a different line with a seen `seq` fails."""
    known: dict[int, str] = {}
    if record is not None:
        for path in sorted(record.glob("audit-*.jsonl")):
            for line in path.read_text().splitlines():
                if line.strip():
                    known[int(json.loads(line)["seq"])] = line
    seen: dict[int, str] = {}
    prev: str | None = GENESIS if from_genesis else None
    seq: int | None = 0 if from_genesis else None
    first, repeats, n = None, 0, 0
    for i, raw in enumerate(raw_lines, 1):
        line = audit_line(raw)
        if line is None:
            continue
        doc = json.loads(line)
        s = int(doc["seq"])
        if s in seen:
            if seen[s] != line:
                return False, f"line {i}: seq {s} arrived twice with different content"
            repeats += 1
            continue
        if seq is not None and s != seq + 1:
            return False, f"line {i}: seq {s} where {seq + 1} was expected (a gap or reordering)"
        if prev is not None and doc.get("prev") != prev:
            return False, f"line {i}: seq {s} does not follow the line before (edited or replaced)"
        if s in known and known[s] != line:
            return False, f"line {i}: seq {s} differs from the factory's audit record"
        first = s if first is None else first
        seen[s], prev, seq, n = line, _digest(line), s, n + 1
    if not n:
        return False, "no audit lines found"
    tail = f", {repeats} repeat(s) skipped" if repeats else ""
    return True, f"{n} event(s), seq {first}..{seq}, chain intact{tail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent-factory-siem", description="The audit log and its SIEM delivery")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check an export from a SIEM (NDJSON, HEC events or syslog lines)")
    v.add_argument("files", nargs="*", help="exported files; none: standard input")
    v.add_argument("--from-genesis", action="store_true", help="the export must start at the first event")
    v.add_argument("--record", type=Path, help="the factory's audit folder, to compare line by line")
    sub.add_parser("verify-record", help="check the factory's own audit files")
    sub.add_parser("status", help="each destination's delivery status")
    args = ap.parse_args(argv)
    if args.cmd == "verify":
        lines = (ln for f in args.files for ln in Path(f).read_text().splitlines()) if args.files else sys.stdin
        ok, why = verify_export(lines, args.from_genesis, args.record)
    elif args.cmd == "verify-record":
        ok, why = verify_chain(default_dir())
    else:
        from agent_factory_licensed.siem import delivery

        env = dict(os.environ)
        for t in delivery.transports_from_env(env):
            st = delivery.Forwarder(default_dir(env), t, lambda: True).status()
            print(json.dumps({k: st[k] for k in ("destination", "delivered", "failures", "lag", "last_ok_at")}))
        return 0
    print(("VERIFIED: " if ok else "FAILED: ") + why)
    return 0 if ok else 1
