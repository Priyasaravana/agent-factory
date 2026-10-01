# ADR-0024: Quality pillars as the factory's quality model

- **Status:** Accepted
- **Date:** 2026-10-01
- **Guide:** [practices.md §7](../practices.md#7-quality-pillars-adr-0024) · [outcomes.md](../outcomes.md#quality-pillars-of-what-is-live)
- **Related:** ADR-0014 (sandbox), ADR-0017 (spec-driven), ADR-0019 (outcomes), ADR-0023 (evidence manifest)

## Context

The factory measures quality in several places:

- the Readiness station, which scores 21 signals plus a secret scan and fails runs below Level 3;
- the Autonomy Maturity Model table in `docs/practices.md` §1;
- DORA outcomes on the Outcomes page;
- OWASP agentic, SLSA and Scorecard checks.

These answer specific questions well, but there is no single model that says:

- what "good" means for a running product;
- which qualities we cover and which we ignore;
- how a client's priorities change the bar.

The Autonomy Maturity Model scores the *repository* that agents work in. It does not cover runtime qualities such as reliability under failure, scalability, cost to run, usability or interoperability.

Clients will judge generated apps the way architecture review boards judge any system. Those boards ask in terms of the AWS / Azure / GCP Well-Architected pillars and the ISO/IEC 25010 quality characteristics.

## Decision

### 1. Adopt ten quality pillars

The ten pillars combine the six Well-Architected pillars with the ISO/IEC 25010 characteristics that Well-Architected underplays.

| id | Pillar | The question it answers | Source |
|---|---|---|---|
| `security` | Security | Is it protected, least-privilege, auditable, with a trusted supply chain? | WA, ISO |
| `reliability` | Reliability | Does it keep working, or degrade gracefully, when parts fail? Can it be restored? | WA, ISO |
| `performance` | Performance and scalability | Does it meet latency and throughput needs, and scale with load? | WA, ISO |
| `operability` | Operability and observability | Can operators see, deploy, diagnose and recover it? | WA |
| `cost` | Cost | Is the cost to build and run visible and justified? | WA |
| `interoperability` | Interoperability | Does it integrate through documented, standard interfaces? | ISO (compatibility) |
| `usability` | Usability | Can its users learn and use it, including users with accessibility needs? | ISO |
| `maintainability` | Maintainability | Is it cheap and safe to change? | ISO |
| `portability` | Portability | Can it be installed and moved across environments? | ISO |
| `compliance` | Compliance and evidence | Can we prove what was built, from what, and that it meets its requirements? | Governance |

Notes on what each pillar covers:

- Fault tolerance, backup, restore and disaster recovery belong to `reliability`.
- Scalability belongs to `performance`.
- Integrations belong to `interoperability`.
- Sustainability (WA's sixth pillar) is tracked under `cost` until a client needs it reported separately.

### 2. Tag every readiness signal with exactly one primary pillar

- Each signal in the readiness registry (`readiness.py`) gets a required `pillar` field; the pillars live in `pillars.py`.
- The engine tests fail if a signal has no pillar, or an unknown one. `tests/test_pillars.py` also pins the whole mapping, so moving a signal is a deliberate, reviewed change.
- The readiness report is grouped by pillar, and each pillar shows signals passed out of signals applicable.
- **The Level 3 gate is unchanged.** Pillars are a view on the same signals, not a second gate.

The mapping, reconciled against the real registry (only readiness signals count; Trivy, SBOM, provenance and the review are package- and review-station evidence, indexed by file in §4 but not counted as signals):

| Pillar | Signals |
|---|---|
| `security` | `secret_scan`, `container_nonroot`, `codeowners`, `dependency_updates` |
| `reliability` | `health_endpoints` |
| `performance` | none: uncovered |
| `operability` | `structured_logs`, `metrics`, `tracing` |
| `cost` | none: uncovered |
| `interoperability` | `design_docs` (spec, design and the OpenAPI contract) |
| `usability` | none: uncovered. `e2e_tests` checks behaviour on a deployment, not usability |
| `maintainability` | `readme`, `linter`, `formatter`, `unit_tests`, `agents_md`, `verify_target`, `precommit`, `ci`, `coverage_gate` |
| `portability` | `lockfile` |
| `compliance` | `acceptance_tests`, `requirements_traced`, `e2e_tests` |

The Readiness station records passed/applicable per pillar with its scorecard (and as `readiness.json` in the run's evidence), and a failing run's missing signals are listed by pillar.

### 3. Show pillar coverage on Outcomes

- The Outcomes page adds pillar coverage per delivered app and across apps. Coverage is the share of applicable signals passing per pillar, from each live app's latest delivered iteration.
- A pillar with no signals at all is shown as **uncovered**, not as 100%. Signal counts are shown next to percentages.
- Scorecards recorded before this change have no pillar field; they are tallied by signal id against the current registry.
- The run page shows the run's own pillar scores.

### 4. Organise the evidence manifest by pillar

- The sealed evidence (ADR-0023) gets `pillars.json`: per pillar, its readiness signals with results and its evidence files.
- Files have one primary pillar too: SBOM → `security`; provenance, traceability, review, readiness scorecard and spec → `compliance`; events and agent transcripts → `operability`. Anything else is listed as other.
- The seal and digest are unchanged; the index is one more sealed file.

### 5. Per-workflow pillar profiles (later)

A workflow version may raise or relax the bar per pillar. The shape below is illustrative; the final schema will be decided in its own change.

```yaml
quality:
  require:            # extra signals that must pass for this product line
    compliance: [evidence_manifest]
    security: [image_signed]
  relax:              # signals reported but not gating
    performance: [load_smoke]
```

Rules for profiles:

- Relaxing a pillar never removes signals from the report.
- Profiles cannot relax the Level 3 floor signals; publish-time checks reject such a version.
- Profiles are pinned with the workflow version, like `skill_pins`.

### 6. Formal frameworks are mapped on demand

- NIST SSDF, ISO/IEC 42001, ISO 27001 and SOC 2 are mapped onto the pillars and evidence only when a client needs them, as already stated in best-practices §6.

## Consequences

**Positive**

- Gaps become visible. For example, no generated app has a performance signal today, and the pillar view makes that obvious.
- The factory speaks the language of client architecture and audit reviews.
- Per-client customisation has a clear, bounded place: pillar profiles.
- New signals have an obvious home, and coverage gaps become roadmap items.

**Negative and risks**

- Every new signal needs a pillar decision. Some signals span pillars; we pick the primary one and accept the simplification.
- Coverage percentages can be gamed by adding trivial signals. Mitigations: signals are reviewed like code, and Outcomes shows signal counts next to percentages.
- Profiles add configuration surface that must be validated at publish.

## Alternatives considered

- **Well-Architected only:** this is cloud-runtime-centric and misses usability, interoperability, maintainability and portability.
- **ISO/IEC 25010 only:** this has no cost or operability pillar, and its vocabulary is less familiar to cloud buyers.
- **Extend the Autonomy Maturity Model:** that model scores the repository and agent-readiness, not the running product. It stays as is, for repo-level scoring, alongside the pillars.
- **Do nothing:** quality stays spread across separate lists, and runtime gaps stay invisible.

## Rollout

1. **Done in this change:**
   - the `pillar` field on every signal, with tests;
   - the readiness report and failures grouped by pillar;
   - the pillar index in the sealed evidence;
   - pillar coverage on Outcomes and on the run page;
   - the practices §7 table.
2. **With phase 5 (`deploy/helm`):** add the reliability, performance, portability and cost signals (probes, graceful shutdown, resource limits and requests, autoscaling, chart lint).
3. **Then:** add the usability (`a11y`) and interoperability (`openapi_valid`) signals, and per-workflow pillar profiles.
