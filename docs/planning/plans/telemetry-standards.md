# Plan: logs, metrics and traces on open standards (factory and products)

Status: recorded 2026-11-24 at Saravana's request, so it isn't missed; revised the same day for his two reviews on PR #198:
- redaction as a tested boundary, collector isolation and credentials, metric semantics during migration;
- egress enforcement across clusters, existing collectors, duplicate log ingestion, setup and usability.
 Proposed
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
- **Two outputs, one ingestion path:**
  - **stdout**, always, so `kubectl logs` works;
  - **OTLP** (the OpenTelemetry logs bridge: a Python logging handler, a pino OpenTelemetry transport), so a
    product-scoped collector needs no access to node log files (§3).
- **Duplicate-ingestion policy: every log record reaches the backend through exactly one path.** The product's
  setting `telemetry.logs.ingest` says which:
  - `otlp`: the logs bridge exports, and nothing is expected to collect stdout;
  - `cluster-agent`: the cluster's own log agent already collects stdout, so the logs bridge is **off**. The JSON
    lines on stdout carry `trace_id` / `span_id`, so correlation still works.
- **The factory asks once** ("Does your cluster already collect container logs?", §6) and never guesses. Traces
  and metrics have one path anyway.

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

### 3. Collector modes, isolation and credentials
**Two modes, chosen per product** (`telemetry.collector.mode`; §6 shows how the choice is offered):

| | `existing`: use the organisation's collector | `bundled`: a product-scoped collector |
|---|---|---|
| For | Teams with a shared OTLP gateway or agent (most companies with observability) | Self-contained setups, pilots, the local cluster |
| What the chart deploys | **No collector.** The product's pods export OTLP straight to the organisation's endpoint | One collector Deployment in the product's namespace |
| Who runs it | The platform team: their collector, their egress, their backend credentials | The factory, with the profile below |
| Egress control | The product's pods may reach only the organisation collector: by pod and namespace selector when it runs in the cluster, or by CIDR when it doesn't | The exporter's egress, enforced as below |

- **Default:** `existing` when the team gives an endpoint. `bundled` with a debug exporter on the local cluster.
  Off until the team chooses, so nothing leaves the cluster by surprise.

**Bundled-mode topology:**
- One collector **Deployment in the product's namespace**, never a node-level DaemonSet.
- It receives OTLP (logs, traces) from the product's pods and scrapes the product's own Service for `/metrics`.
- With logs over OTLP (§1), it needs no `hostPath`, no `filelog` receiver and no access to any other workload's
  logs. A node-level log agent stays the cluster platform team's choice; the factory never deploys one.
- **The factory itself:** a collector service in its compose stack, reached only on the compose network, with the
  same two modes.

**Least privilege** (chart defaults; the chart check fails on any departure):
- **Container:** non-root (`runAsNonRoot`, fixed UID), `readOnlyRootFilesystem`, all capabilities dropped,
  `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`.
- **Kubernetes access:** no `hostPath`, no host network or ports, and `automountServiceAccountToken: false`. The
  scrape target is the product's Service by name, so there's no Kubernetes API discovery and no RBAC.
- **Ingress:** a NetworkPolicy allows OTLP (4317/4318) only from the product's own pods. This is portable: it
  uses pod selectors. The receivers listen on the pod IP, never on a host port.
- **Egress to the backend: an explicit enforcement mechanism, chosen and checked** (`telemetry.egress.enforcement`).
  The standard NetworkPolicy API selects destinations only by pod or namespace selector or IP CIDR; it has no
  hostname selector, and enforcement depends on the cluster's network plugin. A backend such as
  `otlp-gateway.grafana.net` resolves to changing IPs, so a plain NetworkPolicy can't express "only this host". The
  chart supports these mechanisms:

  | Mechanism | How | When |
  |---|---|---|
  | `fqdn-cilium` | A `CiliumNetworkPolicy` with `toFQDNs` (`matchName` for the backend host) and a DNS rule for kube-dns, beside the standard policy that denies all other egress | Clusters running Cilium. The chart checks that the `cilium.io/v2` API is present |
  | `cidr` | A standard NetworkPolicy `ipBlock` list the team supplies (a backend's published ranges, or a private endpoint / PrivateLink address) | Backends with stable addresses |
  | `egress-proxy` | The collector's only egress is the organisation's egress proxy (selector or CIDR). Exporters use it through the standard `HTTPS_PROXY` / `NO_PROXY` settings, and the proxy holds the hostname allow-list | Organisations that already run an egress proxy |
  | `unenforced` | No egress restriction beyond DNS and port 443 | Only with `telemetry.egress.acknowledgeUnenforced: true` |

- **Fails closed:**
  - With no mechanism set, the chart **refuses to render**, with a message naming the four choices.
  - `fqdn-cilium` without the Cilium API also refuses, and says so.
  - `unenforced` renders, but the factory records it in the product's evidence and shows it on the product page
    as "telemetry egress not enforced". It blocks the **production** readiness tier (pilot is allowed), and it can
    be changed in one step.
- **Proven at deploy:** the factory runs an egress probe from a pod with the collector's labels. A request to the
  backend host must succeed and a request to any other host must fail. The result goes into the evidence, so
  "enforced" is a measured fact, not a setting.
- **Local cluster:** the exporter is `debug`, so there's no backend egress at all. Deny-all egress (except DNS) is
  enforceable with standard NetworkPolicy, given a plugin that enforces it; the probe proves that too.
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

### 6. Setup and everyday use (usability)
**Setup: one card on the product's page, three plain choices:**
- **Send to our existing collector:** an endpoint, plus an optional Secret name for its token.
- **Run a collector for this product:** a backend picker (Grafana Cloud, Datadog, Elastic, Honeycomb, other
  OTLP), the endpoint, the Secret name, and the egress mechanism.
- **Off** (the default).

**Questions in the product's own words, one at a time, each with a recommended answer:**
- "Does your cluster already collect container logs?" sets `logs.ingest`.
- "How should we limit where telemetry may go?" offers the egress mechanisms that this cluster supports. The
  factory detects Cilium and existing proxies where it can, and lists only what applies.

**Test connection:**
- Sends one test log line, span and metric through the chosen path.
- Shows "received" with the time, or the exact step that failed (DNS, TLS, auth, egress blocked, collector
  rejected).
- The result is saved as evidence.

**Status, at a glance on the product page:**
- "Telemetry: sending to Grafana Cloud via your collector, last data 2 minutes ago".
- Or a single amber line with the fix, such as "egress not enforced: choose a mechanism" or "no data for 15
  minutes: test connection".

**The same choices for the factory itself,** in its settings.

**Docs:** one short page per common backend (endpoint, token, egress), written as steps.

### 7. Evidence stays the record
- Telemetry is a copy for operations; the sealed ledger stays the source of truth.
- Each change's trace id goes into its evidence manifest, so evidence and traces can be joined.
- Hidden scenarios never leave the factory.

## Slices (AI build and check time)
Estimates are agent build time plus automated checks, not Saravana's time. His part is one live validation at the
end.

| # | Slice | AI build | Checks | Done when |
|---|---|---|---|---|
| T1 | Log contract (JSON schema); both packs on it, with trace context; OTLP logs bridge; the `logs.ingest` switch (one ingestion path) | ~2.5 h | ~45 min (pack unit and e2e tests) | A schema test passes on log lines from both packs; a line written inside a request span carries its `trace_id`; with `ingest: otlp` each line reaches a fake OTLP receiver exactly once; with `ingest: cluster-agent` none does and stdout lines still carry trace ids |
| T2 | Factory JSON logs on the contract + `/metrics` | ~1 h | ~15 min | The engine's log lines pass the schema test; `/metrics` passes `promtool check metrics` |
| T3 | Factory traces (`gen_ai.*`); trace id in the evidence manifest; **redaction boundary** (§2) in the factory and both packs | ~3.5 h | ~45 min | Serialized OTLP payloads captured at the exporter boundary (a fake OTLP server), plus stdout log lines, contain none of the planted values: a resolved secret, a virtual key, a bearer token, prompt text, tool-result text, an exception whose message holds a secret, baggage holding a secret, and an environment value in a resource attribute. A forced redactor failure drops the record and increments `telemetry_redaction_failures_total`. Undeclared attribute keys are absent |
| T4 | Both collector modes (`existing`, `bundled`) for products and the factory; bundled profile (security settings, ingress NetworkPolicy, Secret by reference, TLS, minimal pinned image); the four egress mechanisms with fail-closed rendering; the deploy-time egress probe | ~4.5 h | ~1 h (`otelcol validate`; chart tests on the rendered YAML for each mode and mechanism) | `otelcol validate` passes on every shipped config. `existing` renders no collector, points the pods at the given endpoint and limits their egress to it. `bundled` chart tests fail on a relaxed security setting, a `hostPath`, a mounted token, missing limits, a missing ingress policy, `insecure: true`, or a literal credential. The chart refuses to render with no egress mechanism, and with `fqdn-cilium` when the Cilium API is absent; `unenforced` renders only when acknowledged and blocks the production tier. The egress probe's pass and fail cases are both tested |
| T5 | `http.server.request.duration` alongside the old counter in pack version N+1; the watch reads `_count`; readiness and `assess.py` updated; retirement check | ~1.5 h | ~45 min | The watch gives the same availability from the old counter and from the new `_count` on the same traffic; the cardinality test (§4) passes; readiness passes on N and N+1 products; the factory lists products still on the old counter |
| T6 | Setup card, guided questions, Test connection, status line, per-backend docs (§6) | ~2.5 h | ~45 min (UI tests, an accessibility check, a screenshot review of the card at phone and desktop widths) | A new user can choose each mode and run Test connection without leaving the product page; a failed test names the failing step; the status line shows the last data time or one amber fix |
| T7 | Live validation (Saravana) | — | ~40 min of a live run | Set up one product in `bundled` mode and the factory in `existing` mode (pointed at the bundled collector) from the UI. Test connection passes for both. A log line's trace id matches the evidence manifest. The egress probe passes (allowed host reached, other hosts blocked), and a pod in another namespace can't reach the product's collector |

Total: about 15.5 h of AI build and about 4.25 h of automated checks, plus the one live validation.

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
- [Kubernetes NetworkPolicies](https://kubernetes.io/docs/concepts/services-networking/network-policies/):
  destinations by pod or namespace selector or IP block; the cluster's network plugin must enforce them.
- [Cilium DNS-based policies](https://docs.cilium.io/en/stable/security/dns/) (`toFQDNs` with `matchName` /
  `matchPattern`, enforced through Cilium's DNS proxy).
- [Collector configuration best practices](https://opentelemetry.io/docs/security/config-best-practices/):
  encryption and authentication, secrets kept out of config via environment variables, minimal components.
