---
name: sustainability-pillar
description: Use when choosing regions, compute, storage and data retention, or when asked about cloud carbon footprint. Sustainability checks aligned with ISO 14001, the GHG Protocol and Software Carbon Intensity (ISO/IEC 21031), with measurable evidence.
---

# Sustainability pillar

- **Owner role:** Enterprise architect / sustainability lead.
- **Standards:** ISO 14001 (environmental management system); GHG Protocol (cloud usage is Scope 3 for the customer); ISO/IEC 21031 (Software Carbon Intensity, SCI).

The core idea: use less, and use it where and when the energy is cleaner. Most actions here also cut cost, so pair this with `cost-pillar`.

## 1. Set targets first
- A **carbon intensity metric per unit of work**: SCI = ((E × I) + M) per R, where:
  - E = energy;
  - I = grid carbon intensity;
  - M = embodied emissions;
  - R = the functional unit (per request, user or job).
- A baseline from the AWS Customer Carbon Footprint Tool (monthly, by service and region), and a reduction target.

## 2. Practices
- **Region:** where latency and data residency allow, prefer regions with lower grid carbon intensity. Record the trade-off in an ADR.
- **Utilisation:** right-size (see `cost-pillar`); consolidate; autoscale down; scale to zero; turn non-prod off out of hours.
- **Efficient hardware:** Graviton/ARM; managed and serverless services (higher shared utilisation); the right accelerator, used only when needed.
- **Time-shifting:** run flexible batch work (training, reports, re-indexing) when and where the grid is cleaner, using carbon-aware scheduling.
- **Data:**
  - keep only what you need, with retention and lifecycle policies to colder tiers;
  - compress and deduplicate;
  - avoid needless replication beyond what reliability requires.
- **Software efficiency:** efficient algorithms and queries; caching; fewer redundant calls; smaller container images; less over-fetching from the front end.
- **AI workloads:** the smallest model that passes evals, caching, batching, and no redundant generation.
- **Devices:** lighter pages and payloads cut client energy too.

## 3. Evidence

| Claim | Evidence |
|---|---|
| Measured | Customer Carbon Footprint Tool export / SCI calculation |
| Efficient | utilisation report, Graviton share |
| Lean data | lifecycle/retention policies as code |
| Considered | region ADR with the carbon trade-off |

## 4. Anti-patterns: reject on sight
- Idle capacity "just in case", running 24/7 in non-prod.
- Keeping data forever by default; cross-region copies of everything.
- Large models or GPUs where a smaller option passes the same evals.
- Claims of "green" or "carbon neutral" without measurement.

## 5. Factory readiness signals
There is no sustainability pillar in the factory yet (ADR-0024 has ten pillars). Either add it as an eleventh pillar or state it as not covered. Candidate signals: `resource_requests`, `scale_to_zero_nonprod`, `retention_policy`.

## Output
- Baseline, SCI per unit (if measurable), the main actions.
- Then add the sustainability row to the Pillar check:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Sustainability | … | … | … | Enterprise architect | ✅ / 🟡 / ⬜ |
