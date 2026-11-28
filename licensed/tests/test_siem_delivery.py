# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""SIEM delivery (ADR-0053): the audit record shipped byte for byte to an HTTPS collector or syslog over TLS, at least
once, in order, with the chain checkable where it lands; failures counted and retried, never in the factory's way;
the live licence asked before every batch."""

from __future__ import annotations

import datetime as dt
import fcntl
import http.server
import json
import os
import socket
import ssl
import threading
import urllib.error
from pathlib import Path

import pytest
from agent_factory import extensions
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import agent_factory_licensed as edition
from agent_factory_licensed import siem
from agent_factory_licensed.siem import delivery


class Sink:
    """A destination that records what it was sent, and can be told to fail."""

    name, kind = "https://siem.test/ingest", "https"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.fail = False

    def send(self, lines: list[str]) -> None:
        if self.fail:
            raise delivery.DeliveryError("HTTP 503")
        self.batches.append(list(lines))

    def close(self) -> None:
        pass

    @property
    def lines(self) -> list[str]:
        return [ln for b in self.batches for ln in b]


def _record(folder: Path, n: int) -> siem.AuditFile:
    f = siem.AuditFile(folder)
    for i in range(n):
        f.emit({"kind": "change.status", "change_id": f"c{i}", "to_status": "running", "at": "2026-11-28T08:00:00Z"})
    return f


def _fw(folder: Path, sink, grants=lambda: True, **kw) -> delivery.Forwarder:
    return delivery.Forwarder(folder, sink, grants, lambda: "licence: expired", **kw)


def _file_lines(folder: Path) -> list[str]:
    return [ln for p in sorted(folder.glob("audit-*.jsonl")) for ln in p.read_text().splitlines() if ln.strip()]


# ------------------------------------------------------------------------------------- order and chain --
def test_the_record_is_delivered_byte_for_byte_and_verifies_at_the_destination(tmp_path):
    _record(tmp_path, 5)
    sink = Sink()
    fw = _fw(tmp_path, sink)
    assert fw.deliver_once() == 5
    assert sink.lines == _file_lines(tmp_path), "the lines themselves, so prev still hashes"
    assert siem.verify_export(sink.lines, from_genesis=True, record=tmp_path) == (
        True,
        "5 event(s), seq 1..5, chain intact",
    )
    st = fw.status()
    assert (st["state"], st["delivered"], st["lag"], st["failures"]) == ("up to date", 5, 0, 0)
    # new events go out after the cursor, only once
    _record(tmp_path, 2)
    assert fw.deliver_once() == 2 and len(sink.lines) == 7
    assert fw.deliver_once() == 0
    assert siem.verify_export(sink.lines, from_genesis=True)[0]


def test_batches_are_bounded_and_resume_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "BATCH_LINES", 3)
    _record(tmp_path, 7)
    sink = Sink()
    assert _fw(tmp_path, sink).deliver_once() == 7
    assert [len(b) for b in sink.batches] == [3, 3, 1]
    # a new process picks the cursor up from disk (mode 0600)
    _record(tmp_path, 1)
    state = next((tmp_path / delivery.STATE_DIR).glob("*.json"))
    assert oct(state.stat().st_mode & 0o777) == "0o600"
    assert _fw(tmp_path, sink).deliver_once() == 1
    assert siem.verify_export(sink.lines, from_genesis=True)[0]


def test_a_failing_destination_loses_nothing_and_is_counted(tmp_path):
    _record(tmp_path, 3)
    sink = Sink()
    sink.fail = True
    fw = _fw(tmp_path, sink)
    assert fw.deliver_once() == 0 and fw.deliver_once() == 0
    st = fw.status()
    assert (st["state"], st["failures"], st["lag"], st["detail"]) == ("failing", 2, 3, "HTTP 503")
    assert fw._not_before > 0, "backs off before the next try"
    sink.fail = False
    assert fw.deliver_once() == 3
    st = fw.status()
    assert (st["state"], st["failures"], st["delivered"]) == ("up to date", 2, 3)
    assert siem.verify_export(sink.lines, from_genesis=True)[0]


def test_a_surprise_exception_is_named_by_type_only(tmp_path):
    _record(tmp_path, 1)

    class Boom(Sink):
        def send(self, lines):
            raise RuntimeError("token=abc123 in some library message")

    fw = _fw(tmp_path, Boom())
    assert fw.deliver_once() == 0
    assert fw.status()["detail"] == "RuntimeError", "never a message that might carry a credential"


def test_the_licence_is_asked_before_every_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "BATCH_LINES", 2)
    _record(tmp_path, 4)
    answers = iter([True, True, False])  # covered at the wake-up and the first batch, lapsed before the second
    sink = Sink()
    fw = _fw(tmp_path, sink, grants=lambda: next(answers, False))
    assert fw.deliver_once() == 2
    st = fw.status()
    assert (st["state"], st["detail"], st["lag"]) == ("paused", "licence: expired", 2)
    fw.grants = lambda: True  # renewed: resumes where it stopped
    assert fw.deliver_once() == 2
    assert siem.verify_export(sink.lines, from_genesis=True)[0]


def test_one_process_delivers_at_a_time(tmp_path):
    _record(tmp_path, 2)
    sink = Sink()
    fw = _fw(tmp_path, sink)
    lock = tmp_path / delivery.STATE_DIR / f"{fw.key}.lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert fw.deliver_once() == 0 and fw.status()["state"] == "elsewhere"
    finally:
        os.close(fd)
    assert fw.deliver_once() == 2


def test_start_now_skips_the_history_and_a_half_written_line_waits(tmp_path):
    _record(tmp_path, 3)
    sink = Sink()
    fw = _fw(tmp_path, sink, start_at="now")
    assert fw.deliver_once() == 0
    _record(tmp_path, 1)
    path = sorted(tmp_path.glob("audit-*.jsonl"))[-1]
    with path.open("a") as fh:
        fh.write('{"kind": "half')  # a line still being written
    assert fw.deliver_once() == 1 and json.loads(sink.lines[0])["seq"] == 4
    ok, why = siem.verify_export(sink.lines)
    assert ok and "seq 4..4" in why, "a destination may start mid-chain"
    assert not siem.verify_export(sink.lines, from_genesis=True)[0]


def test_the_thread_delivers_without_blocking_the_event(tmp_path):
    sink = Sink()
    fw = _fw(tmp_path, sink)
    export = siem.Export(siem.AuditFile(tmp_path), [fw])
    export.start()
    try:
        export.emit({"kind": "change.created", "change_id": "c1"})
        for _ in range(100):
            if sink.lines:
                break
            threading.Event().wait(0.05)
        assert len(sink.lines) == 1
    finally:
        fw.stop()
    rows = export.status()
    assert [r["state"] for r in rows] == ["writing", "up to date"]


# ---------------------------------------------------------------------------- checking an export --
def test_the_verifier_finds_an_edit_a_gap_a_reorder_and_a_replaced_repeat(tmp_path):
    _record(tmp_path, 4)
    lines = _file_lines(tmp_path)
    assert siem.verify_export(lines + [lines[1]])[1].endswith("1 repeat(s) skipped"), "at least once: a resend"
    assert "gap" in siem.verify_export([lines[0], lines[2]])[1]
    assert "gap" in siem.verify_export([lines[1], lines[0]])[1]
    edited = lines[2].replace('"c2"', '"c9"')
    assert "does not follow" in siem.verify_export([lines[0], lines[1], edited, lines[3]])[1]
    assert "different content" in siem.verify_export(lines + [edited])[1]
    assert "differs from the factory" in siem.verify_export([edited], record=tmp_path)[1]
    assert siem.verify_export(["not json", ""]) == (False, "no audit lines found")


def test_the_verifier_reads_hec_events_and_syslog_messages(tmp_path):
    _record(tmp_path, 3)
    lines = _file_lines(tmp_path)
    hec = delivery.HttpsTransport("https://splunk.test/services/collector/event", "splunk-hec", opener=lambda r, t: 0)
    body = hec.body(lines).decode()
    events = [json.dumps(e) for e in _concatenated_json(body)]
    assert siem.verify_export(events, from_genesis=True)[0]
    frames = [delivery.syslog_frame(ln, "factory-1").decode() for ln in lines]
    assert siem.verify_export(frames, from_genesis=True)[0], "octet-counted frames"
    stored = [f.split(" ", 1)[1] for f in frames]  # what a syslog server usually keeps: the message without the count
    assert siem.verify_export(stored, from_genesis=True)[0]
    assert stored[0].startswith("<110>1 2026-11-28T08:00:00Z factory-1 agent-factory - change.status [origin ")


def _concatenated_json(body: str) -> list[dict]:
    dec, out, i = json.JSONDecoder(), [], 0
    while i < len(body):
        obj, i = dec.raw_decode(body, i)
        out.append(obj)
    return out


def test_the_cli_verifies_an_export_file(tmp_path, capsys):
    _record(tmp_path / "audit", 2)
    export = tmp_path / "export.ndjson"
    export.write_text("\n".join(_file_lines(tmp_path / "audit")) + "\n")
    assert siem.main(["verify", str(export), "--from-genesis", "--record", str(tmp_path / "audit")]) == 0
    assert capsys.readouterr().out.startswith("VERIFIED: 2 event(s)")
    export.write_text(_file_lines(tmp_path / "audit")[1] + "\n")
    assert siem.main(["verify", str(export), "--from-genesis"]) == 1


# ------------------------------------------------------------------------------------ the transports --
@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    """A throwaway CA-less self-signed certificate for localhost."""
    folder = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return folder


def _server_ctx(tls) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tls / "cert.pem", tls / "key.pem")
    return ctx


def _https_server(tls, status=200, location=None):
    got: list[tuple[dict, bytes]] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(b'{"text":"Success","code":0}' if status == 200 else b"token=leaked-body")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    srv.socket = _server_ctx(tls).wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_https_ndjson_over_verified_tls_with_the_token_in_the_header_only(tls, tmp_path):
    srv, got = _https_server(tls)
    try:
        url = f"https://localhost:{srv.server_address[1]}/ingest"
        t = delivery.HttpsTransport(url, "ndjson", "tok-123", str(tls / "cert.pem"))
        _record(tmp_path, 2)
        assert _fw(tmp_path, t).deliver_once() == 2
        headers, body = got[0]
        assert headers["Authorization"] == "Bearer tok-123" and headers["Content-Type"] == "application/x-ndjson"
        assert body.decode().splitlines() == _file_lines(tmp_path)
        assert "tok-123" not in t.name
        # without the CA the certificate is refused: never sent unverified
        _record(tmp_path, 1)
        untrusted = delivery.HttpsTransport(url, "ndjson", "tok-123")
        with pytest.raises(delivery.DeliveryError, match="unreachable"):
            untrusted.send(["{}"])
    finally:
        srv.shutdown()


def test_splunk_hec_uses_its_header_and_a_refusal_is_reported_without_the_body(tls):
    srv, got = _https_server(tls)
    try:
        t = delivery.HttpsTransport(
            f"https://localhost:{srv.server_address[1]}/services/collector/event",
            "splunk-hec",
            "hec-1",
            str(tls / "cert.pem"),
        )
        t.send(['{"prev":"0","seq":1}'])
        assert got[0][0]["Authorization"] == "Splunk hec-1"
        assert json.loads(got[0][1]) == {"event": '{"prev":"0","seq":1}', "sourcetype": "agent_factory:audit"}
    finally:
        srv.shutdown()
    srv, _ = _https_server(tls, status=403)
    try:
        t = delivery.HttpsTransport(
            f"https://localhost:{srv.server_address[1]}/x", "ndjson", "tok", str(tls / "cert.pem")
        )
        with pytest.raises(delivery.DeliveryError) as err:
            t.send(["{}"])
        assert str(err.value) == "HTTP 403"
    finally:
        srv.shutdown()


def test_a_redirect_is_never_followed(tls):
    srv, got = _https_server(tls, status=307, location="https://elsewhere.example/steal")
    try:
        t = delivery.HttpsTransport(
            f"https://localhost:{srv.server_address[1]}/x", "ndjson", "tok", str(tls / "cert.pem")
        )
        with pytest.raises(delivery.DeliveryError, match="HTTP 307"):
            t.send(["{}"])
        assert len(got) == 1
    finally:
        srv.shutdown()


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://siem.test/x", "https://"),
        ("https://user:pw@siem.test/x", "token file"),
        ("https:///x", "https://"),
    ],
)
def test_https_settings_are_refused_when_unsafe(url, why):
    with pytest.raises(ValueError, match=why):
        delivery.HttpsTransport(url)
    with pytest.raises(ValueError, match="unknown SIEM format"):
        delivery.HttpsTransport("https://siem.test/x", "xml")


def test_syslog_over_tls_frames_each_line(tls, tmp_path):
    received = bytearray()
    srv = socket.create_server(("127.0.0.1", 0))
    ctx = _server_ctx(tls)

    def serve():
        conn, _ = srv.accept()
        with ctx.wrap_socket(conn, server_side=True) as s:
            try:
                while chunk := s.recv(65536):
                    received.extend(chunk)
            except OSError:
                pass  # the client closed without a TLS close_notify

    threading.Thread(target=serve, daemon=True).start()
    t = delivery.SyslogTlsTransport(f"localhost:{srv.getsockname()[1]}", str(tls / "cert.pem"), hostname="f1")
    _record(tmp_path, 3)
    assert _fw(tmp_path, t).deliver_once() == 3
    t.close()
    for _ in range(100):
        if received.count(b"<110>1 ") == 3:
            break
        threading.Event().wait(0.05)
    frames, data = [], bytes(received)
    while data:  # RFC 5425 octet counting
        n, rest = data.split(b" ", 1)
        frames.append(rest[: int(n)].decode())
        data = rest[int(n) :]
    assert len(frames) == 3 and siem.verify_export(frames, from_genesis=True)[0]
    srv.close()


def test_syslog_settings_and_a_dropped_connection(tmp_path):
    with pytest.raises(ValueError, match="host:port"):
        delivery.SyslogTlsTransport("siem.test")

    class Broken:
        def sendall(self, data):
            raise ConnectionResetError

        def close(self):
            pass

    t = delivery.SyslogTlsTransport("siem.test:6514", connect=Broken)
    with pytest.raises(delivery.DeliveryError, match="ConnectionResetError"):
        t.send(["{}"])
    assert t._sock is None, "reconnects on the next batch"


# ------------------------------------------------------------------------------- settings and wiring --
def test_tokens_are_files_by_default(tmp_path):
    (tmp_path / "t").write_text("from-file\n")
    assert delivery.secret({"X_FILE": str(tmp_path / "t"), "X": "env"}, "X") == "from-file"
    assert delivery.secret({"X": "env"}, "X") == "env"
    assert delivery.secret({}, "X") is None
    with pytest.raises(ValueError, match="X_FILE can't be read"):
        delivery.secret({"X_FILE": str(tmp_path / "missing")}, "X")


class _Live:
    def __init__(self, ok=True):
        self.ok = ok

    def grants(self, feature):
        return self.ok

    def why_not(self, feature):
        return "" if self.ok else "licence: expired"

    def now(self):
        raise NotImplementedError


def test_registration_names_the_destinations_and_refuses_a_bad_setting(tmp_path):
    (tmp_path / "tok").write_text("t")
    env = {
        "AGENT_FACTORY_AUDIT_DIR": str(tmp_path / "audit"),
        "AGENT_FACTORY_SIEM_HTTP_URL": "https://siem.test/ingest",
        "AGENT_FACTORY_SIEM_HTTP_TOKEN_FILE": str(tmp_path / "tok"),
        "AGENT_FACTORY_SIEM_SYSLOG": "siem.test:6514",
    }
    reg = extensions.Registry()
    started: list[object] = []
    real = siem.Export.start
    siem.Export.start = lambda self: started.append(self)  # no threads in this test
    try:
        edition._siem(reg, _Live(), env)
    finally:
        siem.Export.start = real
    assert [(e.name, e.status) for e in reg.loaded] == [("audit-file", "loaded"), ("siem-delivery", "loaded")]
    assert started, "delivery starts when a destination is named"
    rows = reg.audit_status()
    assert [(r["sink"], r["transport"]) for r in rows] == [
        ("audit-file", "file"),
        ("audit-file", "https"),
        ("audit-file", "syslog"),
    ]
    assert all("t" != r.get("detail") for r in rows) and not any("tok" in json.dumps(r) for r in rows)
    # a lapsed licence shows every row paused
    paused = extensions.Registry()
    edition._siem(paused, _Live(ok=False), {"AGENT_FACTORY_AUDIT_DIR": str(tmp_path / "a2")})
    assert {r["state"] for r in paused.audit_status()} == {"paused"}

    bad = extensions.Registry()
    edition._siem(bad, _Live(), {**env, "AGENT_FACTORY_SIEM_HTTP_URL": "http://siem.test/x"})
    assert [(e.name, e.status) for e in bad.loaded] == [("audit-file", "loaded"), ("siem-delivery", "refused")]
    assert "https://" in bad.loaded[1].reason


def test_no_destination_means_no_threads_and_the_file_alone(tmp_path):
    reg = extensions.Registry()
    edition._siem(reg, _Live(), {"AGENT_FACTORY_AUDIT_DIR": str(tmp_path)})
    assert [e.name for e in reg.loaded] == ["audit-file"]
    assert not any(t.name.startswith("siem-") for t in threading.enumerate())


def test_a_broken_sink_status_never_breaks_the_edition_view():
    reg = extensions.Registry()

    class Bad:
        def emit(self, event):
            pass

        def status(self):
            raise RuntimeError("x")

    reg.provide("bad", "siem", audit_sink=Bad())
    assert reg.audit_status() == [{"sink": "bad", "state": "unknown", "detail": "status failed: RuntimeError"}]


def test_an_http_error_from_urllib_is_reported_as_its_code_only():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom token=x", {}, None)

    t = delivery.HttpsTransport("https://siem.test/x", opener=opener)
    with pytest.raises(delivery.DeliveryError) as err:
        t.send(["{}"])
    assert str(err.value) == "HTTP 500"
