---
name: operability-pillar
description: Use for operational excellence and observability — logs, metrics, traces, SLOs, alerts, dashboards, runbooks, incident and change management. Aligned with ISO/IEC 20000-1, ITIL 4 and OpenTelemetry.
---

# Operational excellence and observability pillar

- **Owner role:** DevOps / SRE lead.
- **Standards:** ISO/IEC 20000-1 (IT service management); ITIL 4; COBIT; OpenTelemetry (semantic conventions, including GenAI for LLM calls).

The core idea: you can't operate what you can't see. Every alert leads to a runbook, and every incident leads to a lasting fix.

## 1. Set targets first
- SLIs and SLOs per user journey (with the reliability pillar). Burn-rate alerts (multi-window, e.g. 1 h / 6 h), not static thresholds.
- Ownership: a named team per service, an on-call rota and an escalation path.
- Change and incident targets, aligned with DORA: deploy frequency, lead time, change failure rate, time to restore.

## 2. Telemetry
**Logs**
- Structured JSON to stdout, including: `timestamp`, `level`, `service`, `version`, `env`, `trace_id`, `span_id`, `request_id`.
- No PII or secrets; sample debug logs; set retention per class (e.g. 30 days hot, 1 year archived for audit).

**Metrics**
- RED for services (Rate, Errors, Duration); USE for resources (Utilisation, Saturation, Errors).
- Business KPIs per journey. Expose `/metrics` (Prometheus) or emit to CloudWatch.
- Watch label cardinality: never put user ids or raw URLs in labels.

**Traces**
- OpenTelemetry SDK with auto-instrumentation; W3C `traceparent` propagated across HTTP and queues.
- Export via an OTel Collector to X-Ray, Tempo or Jaeger. Use tail sampling to keep errors and slow traces.
- For LLM/agent calls, use GenAI spans with model, tokens, cost and tool calls.

**Dashboards**
- One per service: golden signals, SLO / error budget, dependencies, and deploy markers.

## 3. Alerting
- Alert on symptoms (SLO burn, user-facing errors), not causes (CPU). Send cause metrics to dashboards.
- Every alert carries: severity, owner, a runbook link and a dashboard link. Delete or fix any alert that pages without action.
- Use paging only for user impact; route everything else to tickets.

## 4. Service management (ISO/IEC 20000-1, ITIL 4)
- **Change:** every change through PR + pipeline, recorded with who / what / when / why. Standard changes are pre-approved; normal changes reviewed; emergency changes logged and reviewed afterwards. Changes link to requirement ids.
- **Incident:** detect → declare severity → incident commander → communicate (status page / channel) → mitigate (rollback first) → resolve.
- **Problem:** blameless postmortem within 5 working days for sev 1–2. It records the timeline, contributing factors, what went well, and actions with owners and dates. Track actions to closure.
- **Runbooks** in the repo (`docs/runbook.md`): start, stop, deploy, roll back, scale, rotate secrets, restore, and the known failure modes with their fixes.
- **Everything as code:** infrastructure, pipelines, dashboards, alerts and SLOs (e.g. Terraform, sloth/OpenSLO).

## 5. Evidence

| Claim | Evidence |
|---|---|
| Structured logs | sample log line with trace_id |
| Metrics | `/metrics` output or CloudWatch namespace |
| Tracing | a trace spanning ≥ 2 services |
| SLOs and alerts | SLO definitions and alert rules as code |
| Runbook | `docs/runbook.md`, linked from each alert |
| Incident process | postmortem template + last postmortem |

## 6. Anti-patterns: reject on sight
- `print` debugging or unstructured logs; logs without request or trace ids.
- Alerts with no runbook or owner; CPU-threshold pages; alert fatigue tolerated.
- Manual console changes outside the pipeline; dashboards built by hand and lost.
- Postmortems that blame people, or actions never followed up.

## 7. Factory readiness signals (pillar: operability)
`structured_logs`, `metrics`, `tracing`. Proposed: `runbook`, `slo_alerts`.

## Output
- Telemetry plan, SLOs, alert list (each with its runbook).
- Then add the operability row to the Pillar check:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Operational excellence | … | … | … | DevOps / SRE lead | ✅ / 🟡 / ⬜ |
