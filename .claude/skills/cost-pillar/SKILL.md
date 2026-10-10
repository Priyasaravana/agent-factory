---
name: cost-pillar
description: Use when designing, building or reviewing anything with a running cost — compute sizing, storage, data transfer, tagging, budgets, commitments, LLM spend. FinOps checks aligned with ISO 55001 and the FinOps Framework, with unit-cost evidence.
---

# Cost optimisation pillar

- **Owner role:** FinOps specialist / IT controller.
- **Standards:** ISO 55001 (asset management: value over the whole lifecycle); FinOps Framework (Inform → Optimise → Operate).

The core idea: every resource has an owner and a purpose, and cost is measured per unit of business value.

## 1. Set targets first
- A monthly budget per product and environment; an expected **unit cost** (per request, customer, order, or delivered change).
- Cost estimate in the design (AWS Pricing Calculator or Infracost on the IaC diff), and the main cost drivers named.

## 2. Inform: visibility
- A **mandatory tagging** policy: `owner`, `product`, `environment`, `cost-centre`. Enforce it with SCPs or tag policies, and in CI with Checkov or OPA.
- Separate accounts per environment (AWS Organizations) for clean allocation.
- Cost Explorer / CUR with a dashboard per product; **Budgets** with alerts at 50/80/100%; **Cost Anomaly Detection** on.
- For Kubernetes: cost allocation per namespace (Kubecost / OpenCost).

## 3. Optimise
- **Right-size** from real utilisation (Compute Optimizer); set requests from load tests (`performance-pillar`).
- **Use the right pricing model:** Savings Plans or reservations only for the steady baseline (e.g. 60–70% of it); Spot for stateless, fault-tolerant and batch work; on-demand for the rest.
- **Graviton** and managed or serverless services where utilisation would otherwise be low.
- **Turn it off:** non-prod scheduled off out of hours or scaled to zero; ephemeral preview environments with a TTL.
- **Storage:** S3 lifecycle (Intelligent-Tiering, Glacier), gp3 instead of gp2, snapshot and log retention set, and orphaned volumes, IPs and load balancers deleted.
- **Data transfer:** VPC endpoints instead of NAT for AWS services; keep chatty traffic in-AZ; use a CDN for egress.
- **LLM spend:** the smallest model that passes evals, prompt caching, `max_tokens`, per-tenant spend limits, and cost per call recorded.

## 4. Operate: governance
- An Infracost (or similar) comment on IaC PRs showing the cost delta; big increases need approval.
- A monthly review per product: spend vs budget, unit cost trend, top 5 savings actions with owners.
- Lifecycle (ISO 55001): each asset has an owner, a review date and a decommission plan.

## 5. Evidence

| Claim | Evidence |
|---|---|
| Allocated | tag compliance report (≥ 95% of spend tagged) |
| Controlled | budgets + anomaly detection configured |
| Efficient | right-sizing report, commitment coverage/utilisation |
| Estimated | Infracost output for the change |
| Value | unit cost trend |

## 6. Anti-patterns: reject on sight
- Untagged resources; one shared account for every environment.
- Production-sized dev and test running 24/7; NAT Gateway carrying S3/DynamoDB traffic.
- Committing (Savings Plans or reservations) before the baseline is known; infinite log retention by default.
- Unbounded autoscaling or LLM usage with no spend limit.

## 7. Factory readiness signals (pillar: cost)
None yet (uncovered). Proposed: `resource_requests` (so the runtime cost can be estimated). The factory itself already reports cost per delivered change.

## Output
- Cost estimate, unit cost, the main drivers, savings actions.
- Then add the cost row to the Pillar check:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Cost optimisation | … | … | … | FinOps / IT controller | ✅ / 🟡 / ⬜ |
