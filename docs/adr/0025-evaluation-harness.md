# ADR-0025: Evaluation harness: a new workflow version is measured before it serves real orders

**Status:** accepted · 2026-10-01 · guide: [workflows.md](../workflows.md#evaluation-gate)

## Context
Workflow versions change what agents do: prompts, learnings (ADR-0021), skills,
stations and gates. Until now a published version became active at once, and the
only way to see whether it was better or worse was to wait for real orders and
read Outcomes (ADR-0019) weeks later. By then the regression had already cost
real orders: failed deliveries, extra fix loops, higher spend.

Best practice for agent systems is the same as for models: a fixed evaluation
set, run on every change, with a gate on regression (best-practices §5).

## Decision
1. **The suite is part of the workflow.** `evals` is a list of up to 8 fixed
   orders (`id`, `title`, `requirements`, optional `answers`), versioned with
   the workflow. It comes from `evals.yaml` in the template and is edited in the
   draft. The default template ships three small cases: bookmarks, to-do list and
   URL shortener.
2. **An evaluation runs real builds.**
   - Every case runs as a hidden order pinned to the **candidate** version.
   - The **baseline** is the active version. When the suite has already been
     measured on the baseline, that result is reused (3 builds, not 6).
     Otherwise the baseline runs the same cases alongside.
   - The engine handles human gates:
     - the spec gate is approved "by evaluation", a planned touch;
     - blocking questions get the case's `answers`, which counts as an
       unplanned touch against autonomy;
     - a case without answers ends as `needed_input`.
   - Evaluation orders never appear in the orders list or on Outcomes. They are
     archived when the evaluation ends, so their app ports are freed.
   - One evaluation per workflow at a time. A factory restart cancels a running
     evaluation, because it can't be judged fairly.
3. **The verdict is a pure function** (`evals.judge`) of the two summaries.
   - **Regressions block a gated activation:**
     - pass rate lower;
     - autonomy lower;
     - Level 3 share lower;
     - requirements verified live down by more than 10 points;
     - cost per delivery up by more than 25% *and* $0.25;
     - fix loops per case up by more than 0.5.
   - **Warnings are reported, never blocking:**
     - cost up within the margin;
     - median lead time up by more than 50%;
     - nothing delivered on either side.

   Small suites are noisy, so cost and time get margins; quality metrics don't.
4. **The gate** (`eval_gate`, per workflow version):
   - `block` (the default template): publishing creates a **candidate**. It
     activates automatically when its evaluation passes. The draft is kept until
     then, so a failed candidate can be fixed and published again.
   - `warn`: publishing activates at once, then evaluates and reports.
   - `off`: no automatic evaluation. An admin can still run one by hand.

   Activating a newer `block` version by hand needs a passing evaluation. If
   its evaluation failed, an admin can activate it anyway with a written reason
   (10+ characters), recorded on the evaluation and shown in the version list.
   **Rolling back to an older version is never gated.**
5. **API and UI.**
   - `GET/POST /api/workflows/{id}/evals`, `GET …/evals/{eval_id}` and
     `POST …/evals/{eval_id}/cancel`; `PUT …/draft/evals`; `eval_gate` in the
     draft settings. All admin-only under `/api/workflows` (ADR-0012).
   - The workflow page's **Evaluation** card shows each evaluation: candidate
     against baseline, the verdict with its reasons, a metric table, and every
     case. Each version's evaluation state shows in the version list.
   - The workflow editor holds the gate and the suite.

## Consequences
- A workflow change is measured on the same orders before it serves real ones.
  Accepted learnings, prompt edits and skill updates are checked, not trusted.
- **Cost:** each gated publish builds every case once, plus the baseline's
  cases the first time a suite is measured on a version. With real models that
  costs about as much as 3–6 orders. The card says so, and `warn` or `off` are
  available for cheap iteration.
- **Noise:** three cases can't separate small differences, and a flaky case can
  fail a good version. The override (with a recorded reason) and a re-run are
  the escape hatches. More cases make the verdict sturdier and cost more.
- In dry-run (fake agents) evaluations are free and deterministic. Tests and CI
  cover the gate.
- Not done yet:
  - repeated runs per case (statistical confidence);
  - per-case expectations beyond the shared metrics (for example, specific
    requirements that must be verified live);
  - scheduled re-evaluation of the active version (drift in models or skills).

## Amendment (2026-10-02): starting is all or nothing
The first live gated publish (v4) started two of its six cases, then the third
found no app port: the local cluster was created before ADR-0015 and maps only 5
ports, while the start counted the 20 configured ones. The evaluation was never
saved, and two hidden orders kept running. Now:
- an evaluation counts only ports the cluster maps, and refuses before creating
  anything; the message suggests `make reset-cluster` when the cluster maps fewer;
- if a case still fails to start, the cases already created are cancelled and
  archived (nothing was built yet);
- a gated publish whose evaluation can't start records why. The version list
  shows "evaluation not started: …"; fix the cause and use **Run evaluation**;
- on startup, evaluation orders whose evaluation is missing or over are cancelled
  and archived.
