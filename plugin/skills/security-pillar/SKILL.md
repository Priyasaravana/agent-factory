---
name: security-pillar
description: Use when designing, building or reviewing anything that touches identity, data, secrets, network exposure or dependencies. Deep security checks aligned with ISO/IEC 27001/27017, NIST SP 800-53 and OWASP, with evidence to produce.
---

# Security pillar

- **Owner role:** Information Security Officer / DevSecOps.
- **Standards:** ISO/IEC 27001 (ISMS); ISO/IEC 27017 (cloud) and 27018 (PII in cloud); ISO/IEC 27034 (application security); NIST SP 800-53; SOC 2; OWASP ASVS, OWASP Top 10, and the OWASP Top 10 for LLM and Agentic Applications.

Treat this pillar as **a single veto**: a solution with an unmitigated high-risk finding is not done, however good it is elsewhere.

## 1. Set targets first
- Data classification for every store and flow (public / internal / confidential / restricted), and where the data lives (residency).
- An OWASP ASVS level chosen by risk: L1 for low-risk internal tools, L2 by default, L3 for money, health or identity.
- Patch SLAs: critical ≤ 7 days, high ≤ 30 days, medium ≤ 90 days.

## 2. Threat model
- Draw the data flow: actors, trust boundaries, stores, external calls.
- STRIDE per element: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.
- For each threat, record likelihood × impact, the mitigation, and the evidence that it's in place. Keep it in `docs/threat-model.md` and update it when a boundary changes.
- For LLM or agent features, add: prompt injection (direct and indirect), tool misuse, excessive agency, data leakage through prompts or logs, and unbounded spend.

## 3. Controls checklist

**Identity and access**
- SSO / OIDC for people; MFA enforced; no shared accounts.
- Workload identity (IAM roles, IRSA, OIDC from CI); **no long-lived access keys**.
- Least privilege: scoped IAM policies with no `*:*`; permission boundaries; reviewed with IAM Access Analyzer.
- Authorisation checked server-side on every request (object-level: no IDOR).

**Data protection**
- Encryption at rest with KMS (customer-managed keys for restricted data), with rotation.
- TLS 1.2+ everywhere, HSTS on the web, mTLS between services where the risk justifies it.
- PII minimised, masked in logs, and retention defined.

**Secrets**
- Kept in Secrets Manager, SSM or Vault, and **referenced** by name, never stored in code, config, images, logs or prompts.
- Rotation defined; gitleaks in pre-commit and CI.

**Network**
- Private subnets by default; public exposure only through a load balancer, API Gateway or CloudFront with WAF.
- Security groups that allow only what's needed; egress restricted where feasible; VPC endpoints for AWS services.
- CORS restricted to known origins; rate limits on public endpoints.

**Application**
- Validate input and encode output; use parameterised queries; set security headers (CSP, X-Content-Type-Options, frame-ancestors).
- Dependencies scanned (SCA) and pinned by lockfile; only the containers' required packages; run as non-root with a read-only filesystem.

**Detection and response**
- Turn on CloudTrail (all regions, log file validation), GuardDuty, Security Hub and AWS Config rules.
- Keep an audit log of who did what and when, retained per policy and tamper-evident.
- Have an incident response runbook with contacts, severity levels, containment steps and evidence preservation.

**Supply chain**
- Produce an SBOM (syft) and provenance (SLSA) for every image; sign images (cosign) when using a registry.
- Pin Actions to commit SHAs; give workflow tokens least privilege.

## 4. Tools and the evidence they produce

| Check | Tool | Evidence |
|---|---|---|
| Secrets | gitleaks | report with zero findings |
| SAST | Semgrep / CodeQL | SARIF, no high or above |
| SCA | OSV-Scanner / Dependabot / Snyk | report, no unaccepted high or above |
| Container | Trivy | report, non-root confirmed |
| IaC | Checkov / tfsec | report |
| DAST | OWASP ZAP baseline | report against the deployed app |
| Cloud posture | Security Hub / Prowler | findings summary |
| Supply chain | syft, cosign, SLSA provenance | SBOM, signature, provenance files |

## 5. Anti-patterns: reject on sight
- `0.0.0.0/0` on admin ports; public S3 buckets or databases.
- Access keys in env files, CI variables or code; `AdministratorAccess` on workloads.
- `CORS: *` together with credentials; disabled TLS verification; auth "temporarily" removed.
- Secrets or PII in logs or traces; verbose errors returned to clients.
- A scanner disabled or its threshold lowered to make the build pass.

## 6. Factory readiness signals (pillar: security)
`secret_scan`, `container_nonroot`, `codeowners`, `dependency_updates`. Proposed: `image_signed`, `branch_protection`.

## Output
- Threat model summary (top threats, each with its mitigation and evidence).
- Then add the security row to the Pillar check:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Security | … | … | … | ISO / DevSecOps | ✅ / 🟡 / ⬜ |

- Any accepted risk needs an owner, a reason and an expiry date. Write "aligned with ISO/IEC 27001", never "compliant".
