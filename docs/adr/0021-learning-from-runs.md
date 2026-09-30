# ADR-0021: Learning from runs: the retro suggests, an admin decides

**Status:** accepted · 2026-10-01 · guide: [workflows.md](../workflows.md#learning-from-runs)

## Context
When a run needs help, the fix is lost the moment the run is delivered:
- a fix loop after `verify`;
- a spec review that sent the build back;
- a hold a person resolved;
- blocking intake questions.

The next order repeats the same mistake and pays for the same loop. Agents
already had **learnings** (human-approved lessons, versioned with the workflow,
put into the agent's system prompt), but nobody wrote them: it meant a person
reading event logs.

The risk runs the other way too. Lessons flow straight into future agents'
prompts, so an unreviewed "lesson" is a prompt-injection path (OWASP Agentic:
memory and context poisoning). And a "lesson" such as "skip flaky tests" would
quietly defeat our checks.

## Decision
1. **Only runs that needed help trigger a retro.** After a run is delivered, the
   engine looks for signals, all from recorded evidence, never agent claims:
   - fix loops: the routing event now keeps the failure evidence;
   - holds that a person resolved;
   - blocking questions that were answered.

   A clean run costs nothing extra.
2. **The retro is an observe-only, engine-owned agent.**
   - It runs sandboxed like every agent, cannot read the hidden scenarios, and
     has no write tools.
   - It runs *after* the run's concurrency slot is released, so it never delays
     or fails a delivery.
   - Its spend is added to the run's cost, so cost per delivered change stays
     honest (ADR-0019).
   - It suggests at most 3 lessons, each for the one agent whose work caused
     the problem, with the reason and the quoted evidence.
3. **The engine vets every suggestion** (`retro.vet`, a pure function). It
   rejects a lesson that:
   - names an unknown agent;
   - is outside 20–300 characters;
   - cites no evidence;
   - repeats an existing or pending lesson (fuzzy match);
   - would overflow the agent's learnings;
   - **would weaken a check** (skip, disable, bypass … a test, check, review,
     guardrail, scan …).

   Discarded suggestions are recorded, never silently dropped.
4. **A person decides; the workflow version is the unit of change.**
   - Survivors become *proposals* on the workflow page.
   - Only an **admin** can accept or reject. Accepting appends the lesson to
     that agent's learnings in the **draft**; it reaches agents only when the
     draft is published as a new, immutable version.
   - Runs stay pinned to their version, so every run is still reproducible, and
     a bad lesson is undone by activating the previous version.
5. **The loop can be turned off** per workflow (`learn_from_runs`, default on) in
   `workflow.yaml` or the editor.

## Consequences
- Recurring mistakes become one-click improvements, with the evidence attached.
  The Outcomes page (fix loops per delivery, autonomy) shows whether accepted
  lessons help.
- A run that needed help costs one more judgment-tier call after delivery.
- **Access.** Accept/reject are admin-only, like every other change to a workflow.
  *Correction (2026-10-01):* this ADR first said members could edit and publish
  workflows. That was wrong. The gateway identity middleware (ADR-0012) already
  refuses every non-GET call under `/api/workflows` and `/api/skills` from a
  non-admin. `tests/test_access.py` now pins this for every such action.
- Not done yet:
  - suggesting changes to skills (not just learnings);
  - learning from feedback that corrects a delivered behaviour;
  - a view of which accepted lessons reduced loops.
