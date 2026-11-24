# Plan: logs, metrics and traces on open standards (factory and products)

Status: recorded 2026-11-24 at Saravana's request, so it isn't missed. Proposed for v0.1.0 (phase C of
[open-source-release.md](open-source-release.md)); the slot is his call. Builds on agent observability
([ADR-0022](../../adr/0022-agent-observability.md)), which calls an exporter "a small, additive step", and on the
external-first monitoring decided in [stations-and-teams.md](stations-and-teams.md). It replaces the backlog line
"OpenTelemetry GenAI export" in the roadmap.

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
2. **Log field names are our own.** FastAPI writes `ts` (epoch seconds), `level`, `msg`, `request_id`; pino writes
   its defaults plus `service`. Neither follows the OpenTelemetry log data model, so each backend needs a mapping.
3. **Metric names are our own** (`http_requests_total`), not the OpenTelemetry HTTP conventions
   (`http.server.request.duration`), so tools' ready-made dashboards don't recognise them. The watch reads the
   counter name from `pack.yaml`, so a rename goes through a pack version.

**The factory itself: not on the standards.**
- `agent-factory serve` uses plain Python `logging` with no formatter, so its logs are default text lines.
- No OpenTelemetry, and no `/metrics`.
- Its rich telemetry (one `agent_call` record per call, JSONL transcripts, denials; ADR-0022) lives only in its
  own ledger and UI. Nobody can watch the factory from an existing tool.

## Design
1. **One log contract for the factory and every product:** JSON lines on stdout using the OpenTelemetry log data
   model:
   - `timestamp` (RFC 3339), `severity_text`, `severity_number`, `body`;
   - `trace_id`, `span_id`, `trace_flags`;
   - resource attributes `service.name`, `service.version`, `deployment.environment.name`;
   - everything else under attributes (`request_id`, route, status).

   Trace context is injected by the instrumentation: OpenTelemetry's logging instrumentation in Python,
   `@opentelemetry/instrumentation-pino` in Node. The contract is a JSON schema in the repo, versioned.
2. **Integrate through an OpenTelemetry Collector, never a vendor SDK in an app.**
   - The product's Helm chart can run a collector. It reads stdout logs (`filelog` receiver), scrapes `/metrics`
     (`prometheus` receiver) and receives OTLP traces.
   - Exporters go to whatever the team uses, configured in one place (OTLP to Grafana Cloud, Datadog, Elastic,
     Honeycomb; `prometheusremotewrite` to Mimir or Thanos).
   - The local kind setup ships a collector with a debug exporter, so a live run can show all three signals.
3. **Metrics keep the Prometheus exposition format** (every tool reads it) and move to the OpenTelemetry HTTP
   names in a pack version bump. The old counter is kept for one pack version so the watch and existing products
   don't break.
4. **The factory becomes a well-behaved service:**
   - JSON logs under the same contract;
   - a `/metrics` endpoint: changes running and queued, station durations, agent calls, cost, denials, gateway
     spend, waits;
   - traces **change → station → agent call → tool call**. Agent spans use the OpenTelemetry `gen_ai.*`
     conventions (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` /
     `output_tokens`). The conventions were still "Development" in late 2026, so pin the semconv version and
     absorb renames in the collector;
   - export off by default; on with the standard `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_*` variables.
5. **Evidence stays the record.**
   - Telemetry is a copy for operations; the sealed ledger stays the source of truth.
   - Each change's trace id goes into its evidence manifest, so evidence and traces can be joined.
   - The `REDACTOR` applies to every exported log line and span attribute.
   - Prompt and transcript content stay out of spans (OpenTelemetry's GenAI content capture off), and the
     hidden scenarios never leave the factory.

## Slices (AI build and check time)
Estimates are agent build time plus automated checks, not Saravana's time. His part is one live validation at the
end.

| # | Slice | AI build | Checks | Done when |
|---|---|---|---|---|
| T1 | Log contract (JSON schema) + both packs' logs on it, with trace context | ~1.5 h | ~30 min (pack unit and e2e tests) | A schema test passes on log lines from both packs; a line written inside a request span carries its `trace_id` |
| T2 | Factory JSON logs on the contract + `/metrics` | ~1 h | ~15 min | The engine's log lines pass the schema test; `/metrics` scrapes cleanly with `promtool check metrics` |
| T3 | Factory traces with `gen_ai.*` spans; trace id in the evidence manifest; redaction on export | ~2.5 h | ~30 min | A dry-run change emits one trace change → stations → agent calls → tools to an in-memory exporter; no secret or prompt text in any attribute (test with a planted secret) |
| T4 | Collector in the product chart and the local setup (`filelog`, `prometheus`, `otlp`), one exporter example per common backend | ~1.5 h | ~30 min (`otelcol validate`, chart tests) | `otelcol validate` passes on every shipped config; the chart renders with the collector on and off |
| T5 | OpenTelemetry HTTP metric names in a pack version bump, old counter kept one version; readiness and `assess.py` updated | ~1 h | ~30 min | The watch reads either name; readiness passes on old and new products |
| T6 | Live validation (Saravana) | — | ~20–30 min of a live run | One product and the factory send logs, metrics and traces to the local collector, and its debug output shows the trace id from a log line matching the evidence manifest |

Total: about 7.5 h of AI build and about 2.5 h of automated checks, plus the one live validation.

## Not in scope
- Running an observability backend for clients. They bring their own; the factory only ships collector configs.
- OTLP log export from inside apps. The collector reads stdout, which works in every cluster.
- Profiles (the OpenTelemetry profiling signal).
