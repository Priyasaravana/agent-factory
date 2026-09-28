# ADR-0011: Pluggable delivery: providers, secret references, and a narrow control plane

**Status:** accepted · 2026-09-28 · design: [docs/design/integrations.md](../design/integrations.md)

## Context
Every delivery step (build, registry, scan, deploy, test, publish) is currently
local: an image loaded into kind, Trivy in dind, Helm to kind. Real use means
external endpoints (ECR, EKS/Argo CD, a company scanner, a test tool, GitHub).

Credentials also sit in `.env` inside the factory container, where agents can
reach them: the guardrail hooks are a denylist, not isolation.

We considered growing the factory into a general platform dashboard or IDP
(catalog, scorecards, self-service for everything). That is a crowded category
(Backstage, Port, Cortex, Humanitec) and would pull effort away from what is
new here: agents that build, deploy and independently verify software.

## Decision
1. **Stations stay; providers are pluggable.** Each delivery station calls a
   provider chosen per environment (`local` today; `ecr`, `argocd`, `eks-helm`,
   `trivy-server`, `github`, … later). Providers run in the engine
   deterministically. **Agents never call external systems and never hold
   credentials.**
2. **Credentials are references, never values.** Config holds
   `secretRef: aws-sm://…` / `vault://…` / `env://…` (dev only). The engine
   resolves them just in time, for one step, in memory only. Prefer
   short-lived workload identity (IRSA, OIDC federation, Vault dynamic creds)
   and least-privilege roles per provider.
3. **Agents run in a sandbox**, one per run: the run worktree only, no secrets,
   no Docker socket or TLS keys, restricted egress. The hooks stay as a second
   layer.
4. **Readiness is checked, not assumed.** Every provider implements `check()`
   (reachable, identity, permissions via dry run, quota). There is an
   Integrations page, a preflight check when an order is submitted, and
   periodic re-checks.
5. **A narrow control plane, not an IDP.** It covers integrations, secret
   references, environments and promotion rules, policies and approvals, the
   audit log, runs and metrics. Nothing more.
6. **Plug into existing platforms.** A "handoff" mode opens a PR and lets the
   company's CI/CD build, scan and deploy; the factory watches pipeline status
   and runs acceptance against the deployed app. A Backstage/Port plugin or
   template action comes later, driven by results.

## Consequences
- Local mode keeps working unchanged (provider `local`, secret refs `env://`).
- Security moves from pattern-matching commands to not having secrets where
  agents run.
- One real remote path is built end to end before adding more providers.
- A general IDP is explicitly out of scope; revisit only with measured success
  rates from the evaluation set.
