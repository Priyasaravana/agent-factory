# ADR-0028: Stations named after the DevOps loop, grouped by phase

**Status:** accepted · 2026-10-02 · guide: [workflows.md](../workflows.md#workflow-file-workflowyaml)

## Context
The station names came from the first prototype, and several read wrong to
anyone who knows a CI/CD pipeline:

- `build` was the coding agent, while `package` built the image: the opposite
  of what "build" means in DevOps.
- `verify` (tests, lint, coverage) and `readiness` (the Level 3 standards check
  and secret scan) didn't say what they check.
- `deliver` was easily confused with `deploy`.

A lane of eleven stations also had no structure, so it didn't read like the
pipeline people already know. More stations are coming (change risk, security
tools, the operate loop), which makes both problems worse.

## Decision
1. **New handler names**, used as the ids in the shipped templates:

   | Phase | Stations (new ← old) |
   |---|---|
   | Plan | `requirements` ← intake, `design` |
   | Code | `implement` ← build |
   | Test | `test` ← verify, `quality-gate` ← readiness, `code-review` ← review |
   | Release | `build` ← package (image, scan, SBOM, provenance) |
   | Deploy | `deploy`, `deploy-repair` ← deploy_fix |
   | Validate | `acceptance` |
   | Handover | `handover` ← deliver |

   Operate and monitor are added with the operate loop. The change-risk station
   (ADR-0027) will sit in Test, after `code-review`.
2. **Phases are data.** Every built-in handler has a label, a phase and a hint
   (`workflow.HANDLER_INFO`), served to the UI through the catalog. A custom
   station sits in the phase of the station before it, or sets `phase:`. The
   lane builder and every run's timeline group stations by phase. A lane that
   steps back to an earlier phase gets a warning, not an error.
3. **Stored versions keep their ids.** Runs, events, evidence and evaluation
   results are pinned to station ids, so nothing stored is rewritten. Old names
   resolve to today's handlers (`LEGACY_HANDLERS`, per kind, because `build` the
   agent is now `implement` and `build` the check is the image), and the UI shows
   today's labels for them. Outcomes counts agent effort under today's names, so
   old and new versions compare. A workflow adopts the new ids by starting a
   draft from the updated template.
4. **Evidence keys stay.** Event data keys (`readiness`, `review`) and evidence
   files (`readiness.json`, `review.json`) keep their names: they're a stable
   format for sealed bundles (ADR-0023), not labels.
5. **The rest of the vocabulary changes together, later.** "Order" becomes
   **Product**, "run" becomes **Change** and "product line" becomes
   **Blueprint**. That lands with work items (master plan, phase 2), so the
   data migration happens once.

## Consequences
- A run reads as Plan → Code → Test → Release → Deploy → Validate → Handover.
- Python code reads like the lane (`implement`, `run_tests`, `quality_gate`,
  `code_review`, `build_image`, `handover`).
- Two names coexist until a workflow is re-drafted from the template. Labels
  hide this in the UI; the YAML of an old version still shows old ids.
- `build` changed meaning. Anyone reading an old version's YAML or event log
  must read `build` as the coding agent there.
