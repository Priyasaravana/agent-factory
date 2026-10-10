---
name: devsecops-practices
description: Use when writing code, CI/CD, IaC or deployment and ops changes. Applies ISO/IEC 27034 application security, shift-left security and testing, immutable infrastructure, and shift-right observability and chaos engineering.
---

# DevSecOps practices

Apply these whenever you write or review code, pipelines, infrastructure or operational changes.
Pair with `iso-12207-sdlc` (which stage) and `well-architected-standards` (which pillar).

## 1. Application security across the lifecycle (ISO/IEC 27034)
ISO/IEC 27034 asks for security controls at every stage, chosen from the application's risk level. Organisations keep an "Organisation Normative Framework" of approved controls; each app picks the controls that match its risk.

| Stage | Controls |
|---|---|
| Requirements | Security requirements from data classification and threats; abuse cases |
| Design | Threat model (STRIDE); authN/authZ design; encryption and key management; least privilege |
| Code | Secure coding standard (OWASP ASVS level chosen by risk); input validation; parameterised queries; no secrets in code |
| Commit | Pre-commit secret scanning (gitleaks), so secrets never reach a remote branch |
| CI | SAST (Semgrep/CodeQL); SCA (Dependabot, Snyk, OSV-Scanner); IaC scan (Checkov/tfsec); container scan (Trivy); licence check |
| Release | SBOM; signed images and provenance (SLSA); policy gates on severity |
| Run | DAST (ZAP baseline); runtime monitoring; patch SLAs (e.g. critical ≤ 7 days, high ≤ 30 days) |

Severity policy, unless the project sets another: **fail** the build on critical/high; **warn** on medium; any accepted risk needs an owner, a reason and an expiry date.

## 2. Shift left: security and testing
Defects are much cheaper to fix in design or code than in production (often quoted as up to 10× or more; treat the number as indicative, the direction as reliable).
- Write the test with, or before, the code. Every bug fix gets a regression test.
- Fast feedback: pre-commit hooks under seconds; CI under ~10 minutes for the main path.
- Static checks before anything is deployed: types, lint, SAST, SCA, IaC scan.
- Contract tests between services instead of relying on E2E for integration.
- Threat modelling and security review happen in design, not after the build.

## 3. Immutable infrastructure
Never change a running production server or container by hand.
- To change config or software: update the code or IaC → rebuild the image → redeploy.
- No SSH or `kubectl exec` changes in prod; break-glass access is logged and followed by a codified fix.
- Pin versions: base images by digest, dependencies by lockfile, Actions by commit SHA.
- Detect drift (e.g. `terraform plan` on a schedule, Argo CD sync status) and treat it as an incident.
- Patching is a rebuild: scheduled image rebuilds pick up OS and library fixes.

## 4. Shift right: observability and resilience
Testing doesn't end at release.
- **Telemetry:** structured JSON logs with request/trace ids; RED/USE metrics; distributed tracing (OpenTelemetry → X-Ray, Tempo, etc.).
- **SLOs:** an SLI and target per user journey; error budget; burn-rate alerts that link to a runbook.
- **Progressive delivery:** canary or blue/green, with automated rollback on health and SLO signals.
- **Chaos engineering** (AWS Fault Injection Service, Chaos Mesh, Litmus):
  1. state a steady-state hypothesis;
  2. start in non-prod, with a small blast radius and an automatic stop condition;
  3. inject one fault (instance or AZ loss, latency, dependency failure);
  4. check that auto-recovery met the RTO/RPO;
  5. fix what broke and repeat; graduate to prod game days once safe.
- Synthetic checks for critical journeys; real-user monitoring where there's a UI.

## Output: DevSecOps check
Finish code, pipeline or infra work with:

| Practice | Status | Evidence / gap |
|---|---|---|
| Secret scanning (pre-commit + CI) | ✅/🟡/⬜ | … |
| SAST + SCA + IaC + container scans with a severity gate | … | … |
| Tests at every pyramid level, linked to requirements | … | … |
| Immutable build, pinned versions, no manual prod change | … | … |
| SBOM, provenance, signing | … | … |
| Progressive delivery with automated rollback | … | … |
| Logs, metrics, traces, SLOs, alerts with runbooks | … | … |
| Resilience tested (restore test, chaos experiment) | … | … |

## Rules
- Don't disable a check to make a build pass. Fix the cause, or record an accepted risk with owner, reason and expiry.
- Never put secrets in code, config, logs or prompts; reference them from a secret manager.
- Prefer a tool's result as evidence over a written claim.
