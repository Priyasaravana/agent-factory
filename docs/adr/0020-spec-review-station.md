# ADR-0020: A spec review station: the reviewer reports, the engine judges

**Status:** accepted · 2026-10-01 · guide: [workflows.md](../workflows.md#spec-review-station)

## Context
Spec-driven development (ADR-0017) made every requirement traceable, but "traced"
isn't "built right". Before this change:
- a test tagged `R2` could pass while R2's behaviour was incomplete;
- nobody compared the change against `docs/design.md` or `docs/openapi.yaml`;
- that gap was found only by the hidden scenarios after deploy, which is late
  and expensive, or by a person trying the app.

A custom review agent was already possible (the generic handler), but it had two
weaknesses:
- **It saw too little:** not the numbered requirements, not what changed in
  this iteration.
- **Its word was final:** the engine trusted the agent's own `passed: true`.
  That breaks our rule that stations never report success without evidence.

## Decision
1. **A built-in `review` handler.** A station with id `review` gets it. It runs
   after `verify` and `readiness`, so paid review time is only spent on code that
   already passes its tests and Level 3. The stage order enforces
   build → review → package.
2. **The reviewer reports; the engine judges** (`review.judge`, a pure function).
   - The report covers **every** numbered requirement (`implemented`, `partial`
     or `missing`, plus *where*) and lists findings as `blocker`, `major` or
     `minor`.
   - It passes only when every requirement is implemented and no blocker or
     major finding remains.
   - Minor findings never block, but they are recorded.
3. **An incomplete review is the reviewer's problem, not the builder's.** A
   report that skips or invents a requirement id gets one in-station retry.
   After that the run is **held** for a person; it is never looped back to build.
4. **The reviewer sees the change.**
   - Its prompt includes the requirements, `git diff --stat main...HEAD`, the
     change request and any spec review notes.
   - On a re-review it also sees its own earlier blocking items, read from the
     run's `review.json`, so it checks that they were fixed.
   - Observe-only agents may now run read-only git (`diff`, `log`, `show`,
     `status`, `ls-files`, `blame`). Writing through git (commit, checkout,
     `--output`, redirects) stays blocked.
5. **Evidence.**
   - `artifacts/<run>/review.json` holds the report and the judgement, and a
     decision event carries the same data.
   - The spec API and the Specification panel show a review status per
     requirement (the traceability chain becomes requirement → scenarios →
     tests → review → live) and the findings.
6. **Templates.** `default` and `api-security-review` include the review station
   and a `reviewer` agent (judgment tier, observe-only). The `review` role must be
   observe-only with shell access, or publishing is blocked.

## Consequences
- Every iteration pays for one more judgment-tier agent call. The Outcomes page
  (ADR-0019) shows whether it pays for itself: fewer failed acceptances, fewer
  rescues, fewer fix loops after deploy.
- Existing product lines keep their pinned workflow version and see
  "template updated". An admin adopts the review by adding a `review` station
  (with the `reviewer` agent) in the editor, or by restarting from the template.
- Still to do: the self-improving loop, where recurring findings become skill or
  learnings drafts for an admin to approve. Findings are recorded now so that
  loop has data to learn from.
