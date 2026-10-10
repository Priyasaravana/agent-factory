---
name: performance-pillar
description: Use when designing, building or reviewing latency, throughput, scaling, caching, data access or load testing. Deep performance-efficiency checks aligned with ISO/IEC 25010/25023 and ISO/IEC/IEEE 29119, with numeric targets and load-test evidence.
---

# Performance efficiency pillar

- **Owner role:** Cloud systems engineer.
- **Standards:** ISO/IEC 25010 (performance efficiency: time behaviour, resource utilisation, capacity); ISO/IEC 25023 (how to measure it); ISO/IEC/IEEE 29119 (testing).

The core idea: numbers first. "Fast" is not a requirement; "p95 < 300 ms at 200 RPS" is.

## 1. Set targets first
- Per endpoint or journey:
  - latency at **p50 / p95 / p99**;
  - throughput (RPS or messages per second) at expected and peak load (e.g. 3× expected);
  - error rate under load.
- Capacity: largest dataset, concurrent users, growth over 12 months.
- Resource budget: CPU and memory per pod or function at peak.

## 2. Design choices
- **Compute:** containers (EKS/ECS) for steady load, Lambda for spiky or event-driven load, Graviton by default for price-performance.
- **Data:** pick the store from the access pattern (relational for transactions, DynamoDB for key-value at scale, OpenSearch for search, S3 + Athena for analytics). Design indexes from the queries.
- **Caching:** at the CDN (CloudFront), the application (ElastiCache), and the query level. Each cache needs a TTL and an invalidation strategy.
- **Async:** move slow work off the request path (SQS, EventBridge, Step Functions).
- **Network:** keep calls in-region or in-AZ where possible; use connection pooling, HTTP keep-alive and compression; paginate and cap payload sizes.
- **Scaling:** HPA/KEDA or target tracking on the signal that limits you (RPS, queue depth, latency), not just CPU. Set requests and limits from load-test data.

## 3. Code-level checks
- No N+1 queries; batch reads and writes; prefer streaming to loading everything into memory.
- Bounded concurrency and pools; no blocking I/O in async code.
- Profile hot paths before optimising; keep the measured before/after.
- For LLM features: prompt caching, streaming responses, the smallest model that passes evals, and `max_tokens` limits.

## 4. Testing (ISO/IEC/IEEE 29119)

| Test | When | Tool | Pass criterion |
|---|---|---|---|
| Load smoke | every PR / deploy | k6 (short, low VU) | p95 and error rate within target |
| Load | before release | k6 / Locust / Gatling | targets met at expected peak |
| Stress | quarterly | k6 | find the breaking point; it degrades gracefully |
| Soak | before major release | k6 (hours) | no memory leak or latency drift |

Run them in an environment like production, and keep the results as artifacts.

## 5. Evidence

| Claim | Evidence |
|---|---|
| Latency target met | k6 summary (p95/p99, RPS, errors) |
| Scales | autoscaling config + scale test graph |
| Efficient | utilisation at peak (CPU/memory) vs requests |
| Data access fine | query plans / slow-query log clean |

## 6. Anti-patterns: reject on sight
- No numeric target; load testing only after an incident.
- Scaling on CPU for an I/O-bound service; no requests or limits.
- Chatty synchronous chains across services; unbounded queries or payloads.
- Caches without TTL or invalidation; optimisation without a profile.

## 7. Factory readiness signals (pillar: performance)
None yet (uncovered). Proposed: `load_smoke` (k6 in verify), `autoscaling`.

## Output
- Targets table, design choices, load-test results.
- Then add the performance row to the Pillar check:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Performance efficiency | … | … | … | Cloud systems engineer | ✅ / 🟡 / ⬜ |
