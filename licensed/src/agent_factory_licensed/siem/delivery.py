# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""Direct delivery of the audit log to a SIEM (feature `siem`, phase D, ADR-0053).

The hash-chained audit file (`AuditFile`) stays the record and is the queue: each destination keeps a cursor (the
last `seq` it acknowledged, and where that line ends in which file) and a forwarder ships the lines after it, in
order, **byte for byte**, so the chain (`seq`, `prev` = SHA-256 of the line before) can be checked at the
destination with `agent-factory-siem verify`. Nothing is copied into a second queue that could overflow or diverge:
the backlog is the distance between a cursor and the end of the file, it is reported (`lag`), and it never drops an
event. Delivery is at least once: a cursor moves only after the destination acknowledged, so a line may arrive
twice after a failure, never out of order or edited (the verifier skips an identical repeat and fails a different
one).

Transports, vendor neutral:
- `https`: a POST per batch to an HTTP event collector. `ndjson` (the lines, newline-separated, for Vector, Fluent
  Bit, Logstash, Elastic Agent's HTTP input, Cribl) or `splunk-hec` (Splunk's HTTP Event Collector, one
  `{"event": "<line>"}` object per line, the line kept as a string so its hash still holds). https only, the
  certificate verified, no redirects followed, the token sent only in `Authorization`.
- `syslog`: RFC 5424 messages over TLS (RFC 5425, octet-counted), facility log audit (13), the line as the message.

Fail safe: delivery runs in its own thread, never in a change's path. A failure is counted, its reason kept
(status codes and exception types, never a body or a token) and retried with capped backoff; the factory goes on.
Before each batch the live licence is asked again (`licence.Live`): a lapsed licence pauses delivery, a renewed one
resumes it where it stopped. Only one process delivers to a destination at a time (an advisory file lock), so a CLI
started beside the server never double-sends.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

BATCH_LINES = 500
BATCH_BYTES = 1_000_000
TIMEOUT_S = 15.0
BACKOFF_MAX_S = 300.0
IDLE_S = 5.0
LAG_ALERT = 1000  # events behind before the status says "behind"
STATE_DIR = ".delivery"


class DeliveryError(Exception):
    """A batch was not acknowledged. The message is safe to show: no body, no token."""


class Transport(Protocol):
    name: str  # what the status calls it, e.g. https://siem.example.com/services/collector/event
    kind: str

    def send(self, lines: list[str]) -> None:
        """Deliver the lines in order, or raise `DeliveryError`."""
        ...

    def close(self) -> None: ...


# ------------------------------------------------------------------------------------------------ secrets --
def secret(env: dict[str, str], name: str) -> str | None:
    """S-06: a credential is a file by default (`NAME_FILE`, mounted read-only from .secrets/), else `NAME`."""
    if env.get(f"{name}_FILE"):
        try:
            return Path(env[f"{name}_FILE"]).read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            raise ValueError(f"{name}_FILE can't be read: {exc.strerror or type(exc).__name__}") from exc
    return env.get(name) or None


def _tls(ca_file: str | None, cert_file: str | None = None, key_file: str | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_file or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname, ctx.verify_mode = True, ssl.CERT_REQUIRED
    if cert_file:
        ctx.load_cert_chain(cert_file, key_file or None)
    return ctx


# ------------------------------------------------------------------------------------------------- https --
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is answered as an error: the token never follows it anywhere


FORMATS = ("ndjson", "splunk-hec")


class HttpsTransport:
    kind = "https"

    def __init__(
        self,
        url: str,
        fmt: str = "ndjson",
        token: str | None = None,
        ca_file: str | None = None,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("the SIEM endpoint must be an https:// address")
        if parts.username or parts.password:
            raise ValueError("put the SIEM credential in its token file, not in the address")
        if fmt not in FORMATS:
            raise ValueError(f"unknown SIEM format '{fmt}': {', '.join(FORMATS)}")
        self.url, self.fmt, self._token = url, fmt, token
        self.name = f"{parts.scheme}://{parts.hostname}{f':{parts.port}' if parts.port else ''}{parts.path}"
        if opener is None:
            built = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=_tls(ca_file)))
            self._open: Callable[[urllib.request.Request, float], Any] = lambda req, t: built.open(req, timeout=t)
        else:
            self._open = opener

    def body(self, lines: list[str]) -> bytes:
        if self.fmt == "splunk-hec":
            # HEC takes concatenated event objects; the line stays a string, so its SHA-256 still matches `prev`
            return "".join(json.dumps({"event": ln, "sourcetype": "agent_factory:audit"}) for ln in lines).encode()
        return ("\n".join(lines) + "\n").encode()

    def send(self, lines: list[str]) -> None:
        headers = {"Content-Type": "application/x-ndjson" if self.fmt == "ndjson" else "application/json"}
        if self._token:
            headers["Authorization"] = f"Splunk {self._token}" if self.fmt == "splunk-hec" else f"Bearer {self._token}"
        req = urllib.request.Request(self.url, data=self.body(lines), headers=headers, method="POST")  # noqa: S310
        try:
            with self._open(req, TIMEOUT_S) as resp:
                status = getattr(resp, "status", 200)
                raw = resp.read(4096)
        except urllib.error.HTTPError as exc:
            raise DeliveryError(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "reason", exc)
            raise DeliveryError(f"unreachable: {type(reason).__name__}") from None
        if not 200 <= status < 300:
            raise DeliveryError(f"HTTP {status}")
        if self.fmt == "splunk-hec":
            try:
                code = json.loads(raw or b"{}").get("code", 0)
            except ValueError:
                code = 0
            if code not in (0, None):
                raise DeliveryError(f"HEC refused the batch (code {code})")

    def close(self) -> None:
        return None


# ------------------------------------------------------------------------------------------------ syslog --
SYSLOG_PRI = 13 * 8 + 6  # facility 13 (log audit), severity 6 (informational)
_MSGID = re.compile(r"[^!-~]")


def syslog_frame(line: str, hostname: str, now: datetime | None = None) -> bytes:
    """One RFC 5424 message, octet-counted for TLS (RFC 5425 §4.3). Pure. The message is the audit line itself,
    after a BOM (RFC 5424 §6.4: UTF-8), so the destination can recompute the chain from it."""
    try:
        doc = json.loads(line)
    except ValueError:
        doc = {}
    stamp = str(doc.get("at") or (now or datetime.now(UTC)).isoformat())
    msgid = _MSGID.sub("_", str(doc.get("kind") or "-"))[:32] or "-"
    host = _MSGID.sub("_", hostname)[:255] or "-"
    msg = f'<{SYSLOG_PRI}>1 {stamp} {host} agent-factory - {msgid} [origin software="agent-factory"] ﻿{line}'
    data = msg.encode()
    return f"{len(data)} ".encode() + data


class SyslogTlsTransport:
    kind = "syslog"

    def __init__(
        self,
        address: str,
        ca_file: str | None = None,
        cert_file: str | None = None,
        key_file: str | None = None,
        hostname: str | None = None,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        host, sep, port = address.rpartition(":")
        if not sep or not host or not port.isdigit():
            raise ValueError("the syslog destination is host:port (TLS, RFC 5425; usually port 6514)")
        self.host, self.port = host.strip("[]"), int(port)
        self.name = f"syslog+tls://{self.host}:{self.port}"
        self.hostname = hostname or socket.gethostname()
        self._ctx = None if connect else _tls(ca_file, cert_file, key_file)
        self._connect = connect or self._dial
        self._sock: Any = None

    def _dial(self) -> Any:
        assert self._ctx is not None
        raw = socket.create_connection((self.host, self.port), timeout=TIMEOUT_S)
        return self._ctx.wrap_socket(raw, server_hostname=self.host)

    def send(self, lines: list[str]) -> None:
        payload = b"".join(syslog_frame(ln, self.hostname) for ln in lines)
        try:
            if self._sock is None:
                self._sock = self._connect()
            self._sock.sendall(payload)
        except (OSError, ssl.SSLError) as exc:
            self.close()
            raise DeliveryError(f"syslog: {type(exc).__name__}") from None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# ------------------------------------------------------------------------------------------------ cursor --
@dataclass
class Cursor:
    seq: int = 0
    file: str = ""
    offset: int = 0
    delivered_total: int = 0
    failures_total: int = 0
    consecutive_failures: int = 0
    last_ok_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str = ""


def _digest(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def _files(folder: Path) -> list[Path]:
    return sorted(folder.glob("audit-*.jsonl"))


def pending(
    folder: Path, cur: Cursor, limit: int | None = None, max_bytes: int | None = None
) -> list[tuple[int, str, str, int]]:
    """The lines after the cursor, in order: (seq, line, file, end offset). Reads from the cursor's file and offset
    when the line there continues the chain, else finds `seq + 1` by scanning (a moved or rotated file)."""
    limit = BATCH_LINES if limit is None else limit
    max_bytes = BATCH_BYTES if max_bytes is None else max_bytes
    out: list[tuple[int, str, str, int]] = []
    size = 0
    files = _files(folder)
    start = 0
    if cur.file and (folder / cur.file).is_file():
        names = [f.name for f in files]
        start = names.index(cur.file)
    for i, path in enumerate(files[start:]):
        offset = cur.offset if i == 0 and cur.file == path.name else 0
        with path.open("rb") as fh:
            fh.seek(offset)
            for raw in fh:
                offset += len(raw)
                if not raw.endswith(b"\n"):
                    return out  # a line still being written: wait for its newline
                line = raw.decode().rstrip("\n")
                if not line.strip():
                    continue
                seq = int(json.loads(line).get("seq", 0))
                if seq <= cur.seq:
                    continue
                if seq != (out[-1][0] if out else cur.seq) + 1:
                    if not out and cur.file:  # the cursor's position doesn't lead to seq+1: rescan from the start
                        return pending(folder, Cursor(seq=cur.seq), limit, max_bytes)
                    return out  # a gap in the file itself: stop; `verify_chain` reports it
                out.append((seq, line, path.name, offset))
                size += len(raw)
                if len(out) >= limit or size >= max_bytes:
                    return out
    return out


def last_seq(folder: Path) -> int:
    """The newest complete line's seq (a line still being written, without its newline, isn't one yet)."""
    for path in reversed(_files(folder)):
        lines = [ln for ln in path.read_text().splitlines(keepends=True) if ln.endswith("\n") and ln.strip()]
        if lines:
            return int(json.loads(lines[-1]).get("seq", 0))
    return 0


# -------------------------------------------------------------------------------------------- forwarder --
@dataclass
class Forwarder:
    """Ships the audit file to one destination. `deliver_once` is the whole step (tests drive it directly); `start`
    runs it in a daemon thread with capped, jittered backoff."""

    folder: Path
    transport: Transport
    grants: Callable[[], bool]  # the live licence, asked before every batch
    why_not: Callable[[], str] = lambda: "the licence does not cover siem"
    start_at: str = "beginning"  # beginning | now: where a destination with no cursor starts
    lag_alert: int = LAG_ALERT
    clock: Callable[[], float] = time.monotonic
    _wake: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _paused: str = ""
    _not_before: float = 0.0
    _lock_held_elsewhere: bool = False

    @property
    def key(self) -> str:
        return hashlib.sha256(self.transport.name.encode()).hexdigest()[:16]

    def _state(self) -> Path:
        return self.folder / STATE_DIR / f"{self.key}.json"

    def load(self) -> Cursor:
        try:
            return Cursor(**json.loads(self._state().read_text()))
        except (OSError, ValueError, TypeError):
            return Cursor(seq=last_seq(self.folder) if self.start_at == "now" and self.folder.is_dir() else 0)

    def _save(self, cur: Cursor) -> None:
        path = self._state()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(vars(cur), fh)
        os.replace(tmp, path)

    def notify(self) -> None:
        self._wake.set()

    def deliver_once(self) -> int:
        """Send what is pending, batch by batch, until caught up or a batch fails. Returns the number delivered.
        Never raises."""
        if not self.grants():
            self._paused = self.why_not()
            return 0
        self._paused = ""
        if not self.folder.is_dir():
            return 0
        lockfile = self.folder / STATE_DIR / f"{self.key}.lock"
        lockfile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(lockfile, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._lock_held_elsewhere = True  # another process delivers here; it moves the cursor
                return 0
            self._lock_held_elsewhere = False
            return self._drain()
        finally:
            os.close(fd)  # closing releases the lock

    def _drain(self) -> int:
        cur, sent = self.load(), 0
        if not self._state().exists():
            self._save(cur)  # pin where a new destination starts (`now`), so later events are never skipped
        while True:
            if not self.grants():  # asked again before every batch, not only once per wake-up
                self._paused = self.why_not()
                return sent
            try:
                batch = pending(self.folder, cur)
            except (OSError, ValueError) as exc:  # an unreadable record: say so, deliver nothing past it
                cur.consecutive_failures += 1
                cur.last_error = f"the audit record can't be read: {type(exc).__name__}"
                self._save(cur)
                return sent
            if not batch:
                return sent
            cur.last_attempt_at = datetime.now(UTC).isoformat()
            try:
                self.transport.send([line for _, line, _, _ in batch])
            except Exception as exc:  # noqa: BLE001 — a destination failing never reaches the factory
                cur.failures_total += 1
                cur.consecutive_failures += 1
                cur.last_error = str(exc) if isinstance(exc, DeliveryError) else type(exc).__name__
                self._save(cur)
                delay = min(BACKOFF_MAX_S, 2.0 ** min(cur.consecutive_failures, 9))
                self._not_before = self.clock() + delay * random.uniform(0.5, 1.0)  # noqa: S311 — jitter only
                return sent
            seq, _, name, offset = batch[-1]
            cur.seq, cur.file, cur.offset = seq, name, offset
            cur.delivered_total += len(batch)
            cur.consecutive_failures, cur.last_error = 0, ""
            cur.last_ok_at = datetime.now(UTC).isoformat()
            self._save(cur)
            sent += len(batch)

    def status(self) -> dict[str, Any]:
        """What Settings → Edition shows. No token, no address credentials, no event content."""
        cur = self.load()
        try:
            lag = max(0, last_seq(self.folder) - cur.seq) if self.folder.is_dir() else 0
        except (OSError, ValueError):
            lag = -1
        if self._paused:
            state, detail = "paused", self._paused
        elif self._lock_held_elsewhere:
            state, detail = "elsewhere", "another factory process is delivering to this destination"
        elif cur.consecutive_failures:
            state, detail = "failing", cur.last_error
        elif lag > self.lag_alert:
            state, detail = "behind", f"{lag} event(s) waiting"
        else:
            state, detail = ("delivering" if lag else "up to date"), ""
        return {
            "destination": self.transport.name,
            "transport": self.transport.kind,
            "state": state,
            "detail": detail,
            "delivered": cur.delivered_total,
            "failures": cur.failures_total,
            "lag": lag,
            "last_seq": cur.seq,
            "last_ok_at": cur.last_ok_at,
        }

    # ------------------------------------------------------------------------------------- the thread --
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"siem-{self.key}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=TIMEOUT_S + 1)
        self.transport.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            wait = max(0.0, self._not_before - self.clock())
            if wait:
                self._stop.wait(wait)  # backing off: new events don't hurry a failing destination
            else:
                try:
                    self.deliver_once()
                except Exception:  # noqa: BLE001,S110 — the thread must outlive any surprise; counted next round
                    pass
                self._wake.wait(IDLE_S)
                self._wake.clear()


# ---------------------------------------------------------------------------------------- from settings --
def transports_from_env(env: dict[str, str]) -> list[Transport]:
    """The destinations an install names (.env). Raises ValueError with a message that names the setting."""
    out: list[Transport] = []
    if url := env.get("AGENT_FACTORY_SIEM_HTTP_URL"):
        out.append(
            HttpsTransport(
                url,
                env.get("AGENT_FACTORY_SIEM_HTTP_FORMAT", "ndjson"),
                secret(env, "AGENT_FACTORY_SIEM_HTTP_TOKEN"),
                env.get("AGENT_FACTORY_SIEM_HTTP_CA_FILE"),
            )
        )
    if address := env.get("AGENT_FACTORY_SIEM_SYSLOG"):
        out.append(
            SyslogTlsTransport(
                address,
                env.get("AGENT_FACTORY_SIEM_SYSLOG_CA_FILE"),
                env.get("AGENT_FACTORY_SIEM_SYSLOG_CERT_FILE"),
                env.get("AGENT_FACTORY_SIEM_SYSLOG_KEY_FILE"),
                env.get("AGENT_FACTORY_SIEM_HOSTNAME"),
            )
        )
    return out


def start_from_env(env: dict[str, str]) -> str:
    start = env.get("AGENT_FACTORY_SIEM_START", "beginning")
    if start not in ("beginning", "now"):
        raise ValueError("AGENT_FACTORY_SIEM_START is beginning or now")
    return start
