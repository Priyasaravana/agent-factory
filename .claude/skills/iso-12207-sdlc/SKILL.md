---
name: iso-12207-sdlc
description: Use before planning, building, testing, releasing or operating software. Walks the ISO/IEC/IEEE 12207 lifecycle stages with the best practice, required outputs and exit gate for each, mapped onto agile and CI/CD.
---

# Software lifecycle (ISO/IEC/IEEE 12207)

ISO/IEC/IEEE 12207 defines the software lifecycle processes. Run them iteratively (agile, trunk-based, CI/CD), not as a waterfall: each change passes through every stage, in small batches.

Use this to:
- decide which stage the current work is in and what it must produce;
- check nothing was skipped before calling work "done".

Pair with `well-architected-standards` (the non-functional requirements and the pillar check) and `devsecops-practices` (security, release and operations practices).

## Stages

### 1. Requirements analysis
- Functional requirements plus non-functional ones (security, performance, reliability, cost, compliance), each **measurable** and numbered (e.g. `REQ-012`).
- Acceptance criteria per requirement (Given/When/Then).
- Start a **Requirements Traceability Matrix**: requirement → design element → code → test → result in production.
- **Outputs:** requirements list, acceptance criteria, RTM.
- **Gate:** every requirement is testable and has an owner.

### 2. Architecture and detailed design
- Threat model every new trust boundary (STRIDE; PASTA for high-risk systems).
- Modular design with clear interfaces; choose monolith vs services on team and scaling needs, not fashion.
- Infrastructure as Code (Terraform, AWS CDK) under version control, with environments defined as code.
- Record significant decisions as ADRs (context, decision, consequences).
- Run the six-pillar check from `well-architected-standards`.
- **Outputs:** design doc, diagrams, threat model, ADRs, IaC skeleton, API contract (OpenAPI).
- **Gate:** every requirement maps to a design element; every high threat has a mitigation.

### 3. Implementation and integration
- Coding standards enforced by tools: linter, formatter, type checks (ISO/IEC 25010 maintainability).
- Trunk-based development: short-lived branches, small PRs, feature flags for unfinished work.
- Branch protection: required checks and at least one reviewer who is not the author.
- Pre-commit hooks: format, lint, secret scan.
- Requirement ids referenced in tests (and commits where useful).
- **Outputs:** code, unit tests, passing CI, reviewed PR.
- **Gate:** CI green, review approved, no secrets, no new high/critical findings.

### 4. Verification and validation
- Testing pyramid: many unit tests → fewer integration/contract tests → few system/E2E tests.
- In CI: SAST, SCA (dependency vulnerabilities and licences), secret scanning, container and IaC scanning.
- Non-functional tests against the stated targets: load (k6), and DAST against a deployed environment.
- Validation: acceptance tests against the acceptance criteria, in an environment like production.
- Test documentation per ISO/IEC/IEEE 29119-3 (it replaces the withdrawn IEEE 829).
- **Outputs:** test reports, coverage, scan results, updated RTM.
- **Gate:** every requirement has a passing test; coverage meets the agreed threshold; no unaccepted high/critical findings.

### 5. Deployment and release
- Immutable artifacts: build once, promote the same image through dev → staging → prod.
- Zero-downtime strategies: blue/green or canary, with automated rollback triggered by health checks and SLO burn.
- Database migrations are backward compatible (expand → migrate → contract).
- SBOM and provenance for every artifact; sign images where a registry is used.
- Release notes or changelog linked to requirement ids.
- **Outputs:** versioned release, SBOM, deployment record, rollback plan.
- **Gate:** health checks pass after rollout; the rollback has been tested.

### 6. Operation and maintenance
- Service management per ISO/IEC 20000-1: incident, problem, change and request management.
- Telemetry: structured logs, metrics, distributed tracing (OpenTelemetry, AWS X-Ray); SLOs with error budgets and alerts.
- Runbooks for every alert; blameless postmortems with tracked actions.
- Scheduled upkeep: dependency updates, patching via rebuild and redeploy, rescans, restore tests.
- Close the loop: production results update the RTM; incidents become new requirements.
- **Outputs:** dashboards, SLO reports, incident records, postmortems.
- **Gate (continuous):** SLOs met or an error-budget policy is in action.

### 7. Retirement (when needed)
- Data retention and deletion plan, notify consumers, remove infrastructure via IaC, archive evidence.

## Output: lifecycle status
When you plan or report on work, finish with this table:

| Stage | Outputs present | Gate met? | Gaps |
|---|---|---|---|
| Requirements | … | ✅/🟡/⬜ | … |
| Design | … | … | … |
| Implementation | … | … | … |
| Verification & validation | … | … | … |
| Release | … | … | … |
| Operation | … | … | … |

## Rules
- Never mark work done while a gate is unmet. Say what is missing.
- Keep the RTM current: a requirement without a passing test is not done.
- Small batches: if a change is too big to review in one sitting, split it.
