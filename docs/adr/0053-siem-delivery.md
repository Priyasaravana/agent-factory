# ADR-0053: Audit delivery to a SIEM, and the account id beside the name

**Status:** proposed · 2026-11-28 (phase D, part 2) · builds on [ADR-0051](0051-open-core-seam.md) (the open core
seam and the `siem` audit file) and [ADR-0052](0052-single-sign-on.md) (accounts bound to an immutable `uid`)

## Context
Phase C shipped the `siem` feature as a hash-chained JSON-lines audit file a SIEM agent tails. Many organisations
want the factory to deliver to their collector itself: Splunk's HTTP Event Collector, an HTTP input in front of
Elastic or Sentinel (Vector, Fluent Bit, Logstash, Cribl, Elastic Agent), or a syslog server. Delivery must never get
in a change's way, must not lose or reorder events, must keep the tamper evidence the file has, and must handle
credentials the way the rest of the factory does (S-06). Separately, the engine still knew people only by name:
ADR-0052 bound accounts to an immutable `uid` in the auth service, and the roadmap asked for that id to reach the
engine's ownership records before one sign-in serves several factories.

## Decision
1. **The audit file stays the record, and is the queue.** Each destination keeps a cursor (the last `seq` it
   acknowledged, with the file and byte offset where that line ends) in `$AUDIT_DIR/.delivery/<key>.json` (mode 0600,
   written atomically). A forwarder ships the lines after the cursor in order. No second queue exists, so nothing can
   overflow, be dropped or diverge from the record: the backlog is the distance from a cursor to the end of the file,
   reported as `lag`, and a batch is bounded (500 lines, 1 MB). The record itself is bounded by the volume the
   operator sizes, as before.
2. **Lines are sent byte for byte**, so the chain (`prev` is the SHA-256 of the line before, `seq` goes up by one)
   can be checked where they land. `agent-factory-siem verify` reads an export (NDJSON lines, Splunk HEC events whose
   `event` is the line as a string, or RFC 5424 messages whose MSG is the line) and checks order, links and, with
   `--record`, that each line equals the factory's. A destination may start mid-chain (delivery turned on later)
   unless `--from-genesis`.
3. **At least once, never out of order.** A cursor moves only after the destination acknowledged the batch (2xx and,
   for HEC, `code` 0; for syslog, the bytes written to the TLS stream, which has no application acknowledgement). A
   batch may arrive twice after a failure; the verifier skips an identical repeat and fails a line with a seen `seq`
   and different content.
4. **Two vendor-neutral transports.**
   - `https`: one POST per batch. `ndjson` (newline-separated lines, `Authorization: Bearer`) or `splunk-hec`
     (`Authorization: Splunk`). https only, the certificate verified (system store or `…_CA_FILE`), TLS 1.2 or later,
     no redirects followed, no credentials in the address.
   - `syslog`: RFC 5424 over TLS (RFC 5425 octet counting), facility log audit (13), severity informational, APP-NAME
     `agent-factory`, MSGID the event kind, the IANA `origin` SD-ID, the line after a BOM as MSG; optional client
     certificate. Reconnects on the next batch after a dropped connection.
   Settings are environment lines (`.env`), credentials files by default (`AGENT_FACTORY_SIEM_HTTP_TOKEN_FILE`, S-06).
5. **Fail safe.** Delivery runs in a daemon thread per destination, woken by each event and every 5 s. A failed batch
   is counted, its reason kept as a status code or exception type only (never a response body or a message that
   could carry a token), and retried with jittered exponential backoff capped at 5 minutes. A destination failing
   never slows an event, a change or the record. A setting that can't be used (an `http://` address, an unknown
   format, an unreadable token file) refuses `siem-delivery` with the reason; the record is still written.
6. **The licence is asked before every batch** (`licence.Live`), not only at start: a lapsed licence pauses delivery
   (shown as `paused` with the reason), a renewed one resumes from the cursor.
7. **One process per destination.** An advisory file lock beside the cursor means a CLI process started next to the
   server never delivers at the same time; the second reports `elsewhere`.
8. **Status in core, delivery in `licensed/`.** The `audit_sink` point gains an optional `status()`; the core's
   `Registry.audit_status` reads it (never raising) and `GET /api/edition` returns it as `audit` (only the view's
   fields: destination, transport, state, counts, lag, last delivered). Settings → Edition shows it. No new point.
9. **The account id beside the name.** The auth service's `/auth/check` sends the account's `uid` as `X-Auth-Uid`;
   nginx forwards it only from the auth subrequest and clears it on the webhook; the engine honours it only with the
   gateway proof (S-01) and refuses a malformed one. Products record `created_by_uid`, changes and risk holds
   `requested_by_uid`, audit events carry it. Steering matches the creator by id when both the record and the caller
   have one (`identity.is_person`), else by name, so records from before keep working and a reused or renamed name
   never inherits another account's product. A risk self-approval counts as one's own by name **or** id. The owner
   an admin names is still a name (the engine can't resolve a name to an id without the auth service).

## Consequences
- An install can send its audit log straight to Splunk, Elastic, Sentinel or a syslog server, and anyone holding the
  export can prove it complete and unedited from any point on.
- Delivery adds no dependency beyond the standard library and what core ships (`cryptography` only in tests).
- At-least-once means a SIEM may hold duplicates after an outage; they carry the same `seq` and are easy to dedupe.
- Syslog has no acknowledgement: a server that accepts the TCP stream and then loses data can't be detected by the
  factory; the verifier at the destination is how to find out. HTTPS is the recommended transport.
- Not done here: Sentinel's Logs Ingestion API (needs short-lived Entra tokens; use an HTTP collector in front), the
  owner's id (named by name), a factory-wide metrics export of the delivery counters (the telemetry work, O2).

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §6 Enterprise governance | Every change's creation and status change reaches the organisation's SIEM, tamper-evident end to end, checkable with a shipped verifier. |
| §7 Security: secrets | The collector token is a mounted file, sent only in a header over verified TLS, never in an address, a log line or the status. |
| §7 Security: fail closed | https only, certificates verified, no redirects; an unusable setting refuses delivery instead of sending in the clear. |
| §7 Security: authorization | The engine records the account id beside the name, so rights follow the account, not a name that could be reused. |
| §7 Quality pillars: reliability | Delivery is out of band: a SIEM outage is retried with backoff and never stops a change; nothing is dropped, the record is the queue. |
| §7 Quality pillars: operability | Each destination's state, delivered and failed counts and lag are shown on Settings → Edition. |
