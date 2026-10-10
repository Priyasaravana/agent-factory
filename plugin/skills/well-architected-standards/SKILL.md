---
name: well-architected-standards
description: Use before designing, building or reviewing any cloud solution, architecture, IaC or app. Maps the six AWS Well-Architected pillars to owner roles and ISO standards and routes to the pillar skills for the deep checks.
---

# Well-Architected pillars: overview and router

Use this whenever you design a solution, review an architecture, PR or IaC, write an ADR, or set acceptance criteria.

It works alongside:
- `iso-12207-sdlc`: which lifecycle stage you are in;
- `devsecops-practices`: how to secure, ship and run the work.

## Pillars, roles, standards and skills

| Pillar | Owner role | Primary standard | Supporting | Deep skill |
|---|---|---|---|---|
| Operational excellence (incl. observability) | DevOps / SRE lead | ISO/IEC 20000-1 | ITIL 4, COBIT, OpenTelemetry | `operability-pillar` |
| Security | Information Security Officer / DevSecOps | ISO/IEC 27001 | ISO/IEC 27017, 27018, 27034, NIST SP 800-53, SOC 2 | `security-pillar` |
| Reliability | Cloud architect / SRE | ISO 22301 | ISO/IEC 27031 | `reliability-pillar` |
| Performance efficiency | Cloud systems engineer | ISO/IEC 25010 | ISO/IEC 25023, ISO/IEC/IEEE 29119 | `performance-pillar` |
| Cost optimisation | FinOps specialist / IT controller | ISO 55001 | FinOps Framework | `cost-pillar` |
| Sustainability | Enterprise architect / sustainability lead | ISO 14001 | GHG Protocol, ISO/IEC 21031 (SCI) | `sustainability-pillar` |

## How to apply
1. **Find the pillars this work touches.** A full design touches all six. Load each relevant pillar skill and follow its checks.
   - Narrow work loads only what it touches. For example, an IAM change loads security; a backup change loads reliability (and cost).
2. **Set measurable targets before choosing a design.** Use the "Set targets first" section of each pillar skill.
3. **Settle trade-offs explicitly** and record them in an ADR. Common ones:
   - multi-region (reliability) vs cost and carbon;
   - caching (performance) vs freshness;
   - mTLS (security) vs latency and complexity.
   - Security is a veto: an unmitigated high risk can't be traded away.
4. **Prove each choice with evidence.** A tool's or test's result is better than a statement.
5. **Never skip a pillar silently.** Mark it **not covered** and give the reason.

## Output: Pillar check
Finish every solution or review with this table:

| Pillar | Requirement (measurable) | Design choice | Evidence | Owner | Status |
|---|---|---|---|---|---|
| Operational excellence | … | … | … | DevOps / SRE lead | ✅ / 🟡 / ⬜ |
| Security | … | … | … | ISO / DevSecOps | … |
| Reliability | … | … | … | Cloud architect / SRE | … |
| Performance efficiency | … | … | … | Cloud systems engineer | … |
| Cost optimisation | … | … | … | FinOps / IT controller | … |
| Sustainability | … | … | … | Enterprise architect | … |

Legend: ✅ in place · 🟡 partial · ⬜ not covered (with the reason).

## Using the standards correctly
- **Organisations get certified, apps don't.** Management-system standards (27001, 20000-1, 22301, 14001, 55001) certify an organisation, never a single app. For a solution, write "aligned with" and produce the evidence an auditor would ask for. Never write "compliant" or "certified".
- **ISO/IEC 25010:2023 covers more than performance.** It has nine quality characteristics, so use it to phrase non-functional requirements for every pillar.
- **IEEE 829 is withdrawn.** Use ISO/IEC/IEEE 29119-3 for test documentation instead.
