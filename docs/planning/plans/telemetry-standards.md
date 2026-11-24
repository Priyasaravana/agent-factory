# Plan: logs, metrics and traces on open standards (factory and products)

Status: recorded 2026-11-24 at Saravana's request, so it isn't missed; revised the same day for his review on PR #198
(redaction as a tested boundary, collector isolation and credentials, metric semantics during migration). Proposed
for v0.1.0 (phase C of [open-source-release.md](open-source-release.md)); the slot is his call.

Related:
- Builds on agent observability ([ADR-0022](../../adr/0022-agent-observability.md)), which calls an exporter "a
  small, additive step".
- Builds on the external-first monitoring decided in [stations-and-teams.md](stations-and-teams.md).
- Replaces the roadmap backlog line "OpenTelemetry GenAI export".

## Why
Teams must be able to plug the factory, and every product it builds, into the tools they already run (Grafana,
Datadog, Elastic, Splunk, New Relic) without code changes and without a vendor SDK in any app. Swapping vendors
should mean changing one collector config. This is part of the positioning ([../vision.md](../vision.md)) and of
staying free of vendor lock-in.

## Where we are (main `233d89c`)
**Products: a good base, three gaps.** Both packs ship observability, and readiness checks keep it
(`packs/*/readiness.yaml`; `assess.py` checks the same on team repos).

| | fastapi-service | nextjs-web |
|---|---|---|
| Logs | JSON lines on stdout (`template/app/observability.py`) | pino JSON on stdout (`template/src/modules/logging/adapters/pino.ts`) |
| Metrics | Prometheus `/metrics` | Prometheus `/api/metrics` |
| Traces | OpenTelemetry over OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set | `@vercel/otel` over OTLP, same switch |

1. **Logs aren't joined to traces.** No line carries `trace_id` / `span_id`.
2. **Log field names are our own.**
   - FastAPI writes `ts` (epoch seconds), `level`, `msg`, `request_id`.
   - pino writes its defaults plus `service`.
   - Neither follows the OpenTelemetry log data model, so each backend needs a mapping.
3. **Metrics are our own:** a request counter `http_requests_total` with a `status` label. The watch reads it
   (`operate.request_counts`, named in `pack.yaml` `metrics.counter` / `status_label`).

The charts have no NetworkPolicy today.

**The factory itself: not on the standards.**
- `agent-factory serve` uses plain Python `logging` with no formatter, so its logs are default text lines.
- No OpenTelemetry, and no `/metrics`.
- Its rich telemetry (one `agent_call` record per call, JSONL transcripts, denials; ADR-0022) lives only in its
  own ledger and UI.

## Design
### 1. One log contract
- **Format:** JSON on the OpenTelemetry log data model:
  - `timestamp` (RFC 3339), `severity_text`, `severity_number`, `body`;
  - `trace_id`, `span_id`, `trace_flags`;
  - resource attributes `service.name`, `service.version`, `deployment.environment.name`;
  - everything else under attributes (`request_id`, route, status).
- **Applies to** the factory and every product.
- **Trace context** is injected by the instrumentation: OpenTelemetry's logging instrumentation in Python,
  `@opentelemetry/instrumentation-pino` in Node.
- **Versioned:** the contract is a JSON schema in the repo.
- **Two outputs:**
  - **stdout**, always, for `kubectl logs` and any cluster-wide logging the platform team already runs;
  - **OTLP**, when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (the OpenTelemetry logs bridge: a Python logging handler,
    a pino OpenTelemetry transport), so the product's collector needs no access to node log files (§3).

### 2. Redaction is a tested boundary for every exported signal
**Covered fields:**
- log `body` and every log attribute;
- span name and attributes, span events (including exceptions: `exception.message`, `exception.stacktrace`) and
  span status messages;
- resource attributes;
- metric labels.

**Where it runs:** inside the process, before anything is batched, written or sent.
- **Logs:** a redacting processor ahead of both outputs (the stdout formatter and the OTLP log processor).
- **Spans:** a redacting span processor that applies on start and on attribute set, plus a redacting exporter
  wrapper as the last step before the wire, so batches never hold raw values.
- **Metrics:** labels come from fixed allow-lists (§4); no free-text label exists.

**Allow-lists:**
- **The factory's spans and logs** export only declared attribute keys; anything else is dropped.
- **Resource attributes** come from a fixed list.
- **Baggage** is never copied into attributes or exported. Incoming baggage is dropped at the factory's edge.

**What is redacted:**
- the `REDACTOR`'s resolved secret values (`secret_refs.py`);
- token patterns (bearer, API keys, the gateway's virtual keys);
- prompt and tool-result content. It is never put in telemetry: OpenTelemetry's GenAI content capture stays off,
  and transcripts stay in the ledger.
- In products: the `Authorization` and `Cookie` headers, `password`-like fields and URL query strings, from the
  pack's request logging.

**Fail closed:**
- If redaction raises, or meets a value it can't vet (an unexpected type, an oversized value), the record is
  **dropped**. A log line becomes a minimal `telemetry_redaction_failed` line with no payload, and a span is not
  exported.
- The drop is counted on `telemetry_redaction_failures_total` and recorded as a factory event.
- Never a fallback to the raw value.

**Defence in depth, not the primary control:** the collector's `redaction` and `transform` processors block known
credential keys and drop `gen_ai` content attributes, in case an instrumentation library adds something.

### 3. Collector isolation and credentials
**Topology:**
- **Per product:** one collector **Deployment in the product's namespace**, never a node-level DaemonSet. It
  receives OTLP (logs, traces) from the product's pods and scrapes the product's own Service for `/metrics`.
- **No node access:** with logs sent over OTLP (§1), the collector needs no `hostPath`, no `filelog` receiver and
  no access to any other workload's logs. A node-level log agent (DaemonSet with `filelog`) stays the cluster
  platform team's choice; the factory never deploys one.
- **The factory:** a collector service in its compose stack, reached only on the compose network.

**Least privilege** (chart defaults; the chart check fails on any departure):
- **Container:** non-root (`runAsNonRoot`, fixed UID), `readOnlyRootFilesystem`, all capabilities dropped,
  `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`.
- **Kubernetes access:** no `hostPath`, no host network or ports, and `automountServiceAccountToken: false`. The
  scrape target is the product's Service by name, so there's no Kubernetes API discovery and no RBAC.
- **Network:** a NetworkPolicy allows ingress to OTLP (4317/4318) only from the product's own pods, and egress
  only to DNS and the configured exporter endpoint. The receivers listen on the pod IP, never `0.0.0.0` on a host
  port.
- **Resources:** requests and limits are set; the `memory_limiter` processor and a bounded sending queue stop a
  noisy app from taking the collector down.
- **Image:** a minimal distribution built with the OpenTelemetry Collector Builder, holding only the components
  used (otlp and prometheus receivers; memory_limiter, batch, redaction, transform processors; otlp, otlphttp,
  prometheusremotewrite, debug exporters). It is pinned by digest and runs no unused extensions.

**TLS:**
- **Exporters to a backend** require TLS (`insecure: false`); the chart check fails otherwise.
- **In-namespace traffic** from app to collector is limited by the NetworkPolicy; mTLS is used where the cluster
  has a service mesh.

**Credentials:**
- Never in chart values or the collector config.
- The chart takes the **name of an existing Kubernetes Secret** (`telemetry.exporter.credentialsSecret`). The
  collector reads it as environment variables and the config refers to `${env:…}`.
- The factory creates that Secret at deploy from the product's secret refs (the secret store), and the evidence
  records the ref, never the value.
- The chart check fails if any value under `telemetry` looks like a literal credential.

### 4. Metric semantics are kept through the migration
- **New metric:** `http.server.request.duration`, the stable OpenTelemetry HTTP convention. It is a **histogram in
  seconds**, not a renamed counter, so the migration keeps request counting explicit.
  - Bucket boundaries: 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 s (the spec's
    advisory).
  - Attributes: `http.request.method` (unknown methods become `_OTHER`), `url.scheme`, `http.response.status_code`,
    `http.route` (the framework's route template only, never the raw path; left unset when unmatched) and
    `error.type`.
  - In Prometheus format it appears as `http_server_request_duration_seconds_bucket`, `_sum` and `_count`.
- **What the watch reads:**
  - Requests per status come from the histogram's **`_count`**, a counter of completed requests, the same
    semantics as today's `http_requests_total`.
  - The new pack version sets `pack.yaml` `metrics.counter: http_server_request_duration_seconds_count` and
    `status_label: http_response_status_code`. `operate.request_counts` already sums a counter over its label
    sets, so the availability arithmetic is unchanged.
- **Coexistence:**
  - Pack version N+1 emits **both**: today's counter unchanged, plus the new histogram. Its `pack.yaml` points the
    watch at the new `_count`.
  - Products still on version N keep today's counter, and the watch reads whatever their `pack.yaml` names.
  - The latency histogram the FastAPI pack exposes today (`http_request_duration_seconds`) is kept alongside for
    the same one version.
- **Retiring the old counter** (version N+2) needs all three:
  - every live product the factory watches reports the new series (a factory check lists the ones that don't);
  - dashboards and alerts the factory ships read the new names;
  - a product upgraded to N+1 has passed a watch period on the new series.
- **Bounded cardinality** (acceptance, in the pack tests and readiness):
  - 1,000 requests to distinct raw paths (`/items/1` … `/items/1000`) and one request with method `FOO` add no
    series beyond the declared route templates.
  - `FOO` is recorded as `_OTHER`.
  - No label carries a user id, query string or free text.
  - The label set per series is exactly the attributes listed above.

### 5. The factory becomes a well-behaved service
- **Logs:** JSON under the contract.
- **`/metrics`:** changes running and queued, station durations, agent calls, cost, denials, gateway spend, waits,
  and `telemetry_redaction_failures_total`.
- **Traces:** change → station → agent call → tool call.
  - Agent spans use OpenTelemetry's `gen_ai.*` conventions (`gen_ai.operation.name`, `gen_ai.request.model`,
    `gen_ai.usage.input_tokens` / `output_tokens`).
  - Those conventions were still "Development" in late 2026, so the semconv version is pinned and renames are
    absorbed in the collector.
- **Export** is off by default, and on with the standard `OTEL_*` variables.

### 6. Evidence stays the record
- Telemetry is a copy for operations; the sealed ledger stays the source of truth.
- Each change's trace id goes into its evidence manifest, so evidence and traces can be joined.
- Hidden scenarios never leave the factory.

## Slices (AI build and check time)
Estimates are agent build time plus automated checks, not Saravana's time. His part is one live validation at the
end.

| # | Slice | AI build | Checks | Done when |
|---|---|---|---|---|
| T1 | Log contract (JSON schema); both packs on it, with trace context; OTLP logs bridge on the standard switch | ~2 h | ~40 min (pack unit and e2e tests) | A schema test passes on log lines from both packs; a line written inside a request span carries its `trace_id`; with the endpoint set, the same records reach an in-memory OTLP receiver |
| T2 | Factory JSON logs on the contract + `/metrics` | ~1 h | ~15 min | The engine's log lines pass the schema test; `/metrics` passes `promtool check metrics` |
| T3 | Factory traces (`gen_ai.*`); trace id in the evidence manifest; **redaction boundary** (§2) in the factory and both packs | ~3.5 h | ~45 min | Serialized OTLP payloads captured at the exporter boundary (a fake OTLP server), plus stdout log lines, contain none of the planted values: a resolved secret, a virtual key, a bearer token, prompt text, tool-result text, an exception whose message holds a secret, baggage holding a secret, and an environment value in a resource attribute. A forced redactor failure drops the record and increments `telemetry_redaction_failures_total`. Undeclared attribute keys are absent |
| T4 | Collector per product (Deployment, namespace-scoped) and in the factory's compose stack: security profile, NetworkPolicy, Secret by reference, TLS to backends, minimal pinned image, exporter examples per common backend | ~3 h | ~45 min (`otelcol validate`; chart tests on the rendered YAML) | `otelcol validate` passes on every shipped config. Chart tests fail on a missing or relaxed security setting, a `hostPath`, a mounted service-account token, missing limits, a missing NetworkPolicy, `insecure: true` on an exporter, or a literal credential under `telemetry` |
| T5 | `http.server.request.duration` alongside the old counter in pack version N+1; the watch reads `_count`; readiness and `assess.py` updated; retirement check | ~1.5 h | ~45 min | The watch gives the same availability from the old counter and from the new `_count` on the same traffic; the cardinality test (§4) passes; readiness passes on N and N+1 products; the factory lists products still on the old counter |
| T6 | Live validation (Saravana) | — | ~30–40 min of a live run | One product and the factory send logs, metrics and traces to their collectors; the debug output shows a log line's trace id matching the evidence manifest; a pod in another namespace **can't** reach the product's collector. The local cluster's network plugin must enforce NetworkPolicy; the check proves it |

Total: about 11 h of AI build and about 3 h of automated checks, plus the one live validation.

## Not in scope
- Running an observability backend for clients. They bring their own; the factory only ships collector configs.
- A node-level log agent. That is the cluster platform team's choice; the factory never deploys one.
- Profiles (the OpenTelemetry profiling signal).

## Sources
- [OpenTelemetry HTTP metrics conventions](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)
  (`http.server.request.duration`: stable histogram in seconds, advisory buckets, `_OTHER`, low-cardinality
  `http.route`).
- [Handling sensitive data](https://opentelemetry.io/docs/security/handling-sensitive-data/).
- [Collector hosting best practices](https://opentelemetry.io/docs/security/hosting-best-practices/): no privileged
  access unless the data is in a privileged location; narrow volume mounts; authenticate server-like components;
  protect config files holding credentials.
- [Collector configuration best practices](https://opentelemetry.io/docs/security/config-best-practices/):
  encryption and authentication, secrets kept out of config via environment variables, minimal components.
