---
name: reliability-pillar
description: Use when designing, building or reviewing availability, failover, backups, DR, health checks, retries or scaling limits. Deep reliability checks aligned with ISO 22301 and ISO/IEC 27031, with RTO/RPO-driven design and evidence.
---

# Reliability pillar

- **Owner role:** Cloud architect / SRE.
- **Standards:** ISO 22301 (business continuity); ISO/IEC 27031 (ICT readiness for continuity).

The core idea: the business sets RTO/RPO, the architecture meets them, and a test proves it.

## 1. Set targets first
- **Criticality tier** per service (from a business impact analysis):

| Tier | Example | Availability SLO | RTO | RPO |
|---|---|---|---|---|
| 1 Critical | payments, login | 99.95%+ | ≤ 15 min | ≈ 0–5 min |
| 2 Important | core features | 99.9% | ≤ 1 h | ≤ 15 min |
| 3 Supporting | internal tools, reports | 99.5% | ≤ 24 h | ≤ 24 h |

- SLIs per user journey (success rate, latency), each with an SLO and an error budget.
- Dependencies listed with *their* SLOs. A service can't be more available than its hard dependencies allow.

## 2. Pick the DR pattern from the RTO/RPO

| Pattern | RTO / RPO | Cost |
|---|---|---|
| Backup & restore | hours / hours | lowest |
| Pilot light | tens of minutes / minutes | low |
| Warm standby | minutes / seconds–minutes | medium |
| Multi-site active/active | near zero / near zero | highest |

The default is **multi-AZ in one region**. Go multi-region only when the tier's RTO/RPO or regulation requires it, and record that in an ADR.

## 3. Controls checklist

**Architecture**
- No single points of failure: at least 2 AZs, managed services with Multi-AZ (RDS/Aurora, ElastiCache, EKS node groups across AZs).
- Stateless services; state kept in managed stores.
- Loose coupling: queues (SQS) and events between components so failures don't cascade.
- Static stability: keep working when the control plane (scaling, DNS changes) is impaired.

**Application resilience**
- Liveness, readiness and startup probes that test the right things (readiness checks the dependencies it truly needs).
- Timeouts on every outbound call; retries with exponential backoff, jitter and a cap, on idempotent operations only.
- Circuit breakers and bulkheads; graceful degradation (serve cached or partial results).
- Idempotency keys for writes; dead-letter queues with alarms.
- Graceful shutdown: handle SIGTERM, drain connections, and set `terminationGracePeriodSeconds` accordingly.

**Capacity**
- Service quotas known and alarmed at 80%; autoscaling with minimums sized to survive the loss of one AZ.
- PodDisruptionBudgets; resource requests and limits; topology spread across AZs.

**Data**
- Automated backups (AWS Backup) that are encrypted, copied cross-account (and cross-region for tier 1), and immutable (Vault Lock).
- Point-in-time recovery on databases; S3 versioning and replication where needed.
- **Restore tested on a schedule** (at least quarterly), and the measured restore time recorded against the RTO.

**Continuity**
- DR runbook: decision criteria, who declares, the steps, how to verify, how to fail back.
- Failover exercised (game day) at least once a year, or quarterly for tier 1.
- Recovery confirmed with chaos experiments (see `devsecops-practices` §4).

## 4. Evidence

| Claim | Evidence |
|---|---|
| Multi-AZ | IaC showing subnets and node groups across AZs |
| RPO met | backup/PITR config, last restore test report with timestamps |
| RTO met | game-day or failover test record with measured time |
| Probes work | chart/manifests + kill-a-pod test result |
| SLO met | SLO dashboard / error-budget report |

## 5. Anti-patterns: reject on sight
- A single instance, NAT or AZ; database without Multi-AZ for tier 1–2.
- Retries without backoff or jitter; retrying non-idempotent calls; no timeouts.
- A liveness probe that checks dependencies (one dependency outage then restarts everything).
- Backups never restored; backups in the same account as production.
- A DR plan that only exists in a document and has never been run.

## 6. Factory readiness signals (pillar: reliability)
`health_endpoints`. Proposed: `probes`, `graceful_shutdown`, `resource_limits`.

## Output
- Criticality tier, SLO, RTO/RPO, DR pattern.
- Then add the reliability row to the Pillar check:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Reliability | … | … | … | Cloud architect / SRE | ✅ / 🟡 / ⬜ |
