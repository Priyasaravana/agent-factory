# ADR-0017: Spec-driven development: numbered requirements, traceability and an optional review gate

**Status:** accepted · 2026-09-30 · guide: [workflows.md](../workflows.md#spec-driven-development)

## Context
Intake has always turned prose requirements into a spec (`docs/spec.md`), and
design into a technical design (`docs/design.md`). Hidden scenarios then judge
the running app. But nothing tied the three together:
- **No coverage proof.** We could not show that every requirement was built,
  tested and verified live. A missed requirement only surfaced when a person
  tried the app.
- **No checkpoint.** A wrong spec was found only after build, package and deploy
  had spent time and money on it. Warp's software-factory guide calls spec review
  the highest-leverage human checkpoint.
- **Opaque feedback.** Feedback changed code, but nobody could see how the
  contract itself had changed.
- **No reuse.** Teams that already had a spec had to paraphrase it as prose.

## Decision
1. **Numbered requirements.** Intake writes `docs/requirements.yaml`
   (`R1`, `R2`, …).
   - Every acceptance and hidden scenario lists the requirements it `covers`.
   - The engine validates coverage: each requirement has a scenario, and no
     scenario cites an unknown id. It allows one in-station correction, then
     holds the run with the reason.
   - On later iterations, intake receives the current requirements so ids stay
     stable. The event log records the diff (`spec updated: +R3 ~R1 −R2`).
2. **Traceability, requirement → scenario → test → live result.**
   - Tests carry `@pytest.mark.req("R1")` (the marker is registered in the
     golden path).
   - Readiness adds the Level 3 signal `requirements_traced`: every requirement
     has an acceptance scenario and a tagged test.
   - Acceptance records the matrix with each hidden scenario's live verdict, in
     `artifacts/<run>/traceability.json` and as an event.
   - All of this is a pure function of files (`traceability.py`), so readiness,
     the API and the evidence always agree.
3. **Optional spec review gate.** Set per workflow with
   `spec_review: off | first | always`, **default `off`**. When it applies, the
   run stops after design in the new status `awaiting_approval`.
   - The order's creator or an admin can **approve**, which continues to build.
   - They can **request changes**, which sends the run back through intake and
     design with the review notes. Notes accumulate and are shown on the spec.
   - They can **edit** the product spec and technical design directly; each edit
     is committed with the editor's name. Requirements change only through
     "request changes", so scenarios stay consistent.
   - Approval and changes go through the preflight gate like any other
     run-starting action (ADR-0015).
   - Off by default because fully autonomous delivery remains the default
     product. Teams choose the checkpoint per product line.
4. **Bring your own spec.** An order can set `requirements_format: "spec"`.
   Intake then maps the spec's numbered items 1:1 to requirements and keeps
   their wording, rather than rewriting it.
5. **API first.**
   - `GET /api/runs/{id}/spec` returns the spec, requirements, scenarios,
     changes, review notes and traceability;
   - `POST …/spec/approve` and `POST …/spec/changes`;
   - `PUT /api/runs/{id}/spec` edits;
   - `PATCH /api/workflows/{id}/draft/settings` sets the gate.

   The UI's Specification panel, the order form's "I have a spec" toggle and the
   editor's "Spec review gate" card use only these endpoints.

## Consequences
- Every delivered app carries evidence of what was asked, how it was tested and
  what was verified live. This is the basis for the Outcomes page and for
  auditors.
- A run can now wait on a person. `awaiting_approval` counts as "needs
  attention", and the Outcomes page will measure the time spent waiting.
- A generated repo without tagged tests drops below Level 3. Custom golden
  paths must register the `req` marker.
- File names stay `docs/spec.md` and `docs/design.md` for existing runs and
  agents; the UI labels them "Product spec" and "Technical design".
- Not done yet: a review station that checks the diff against the spec, and
  per-requirement cost and time.
