# ADR-0030: Work items: every request is a change to a product

**Status:** accepted · 2026-10-08 · builds on [ADR-0029](0029-products-changes-blueprints.md) (products, changes, blueprints)

## Context
Until now there were two kinds of request: "build me an app" (a new product) and
feedback on a delivered app (the next change). The next steps (existing repos,
assessments, ticket intake, scheduled upkeep, removals) all need the factory to know
**what a change is for**, **where it came from** and **who asked**, and to know
**what the product is**: something built here, a team's repo, or the factory itself.
A bug report and a feature request also need different work: a bug should be
reproduced with a failing test before it is fixed, and upkeep must not change behaviour.

## Decision
1. **A change is a work item.** Every change records:
   - `kind`: `new` (builds the product; always its first change), `feature`, `bug`,
     `upkeep`, `assess`, `remove`;
   - `source`: `ui` (a signed-in member, in the UI or the API), `ticket`, `schedule`;
   - `requested_by`: the signed-in member who asked.
   Clients cannot set `source`: tickets and schedules will start changes through their
   own intake paths, where the trust rules of the master plan (§3J) apply.
2. **A product has a target:** `new` (built here from a blueprint), `repo` (an
   existing repository) or `factory` (the factory itself).
3. **Only what works is accepted.** Today: target `new`; kinds `new`, `feature`, `bug`
   and `upkeep`. The others are part of the model and are refused with HTTP 422 ("not
   available yet") until their workflows ship: `assess` and target `repo` next,
   `remove` with product-owner confirmation (ADR-0027 category 6), target `factory`
   with self-improvement. A product's first change must be `new`; later ones must not.
4. **Same workflow, different instructions.** `feature`, `bug` and `upkeep` run the
   blueprint's workflow. Bug and upkeep changes add a short "kind of change" section
   to the intake, design, implement and review prompts (`stations.KIND_GUIDANCE`):
   bugs are reproduced with a failing test tagged with the requirement before the fix;
   upkeep keeps requirements, scenarios and behaviour. `new` and `feature` prompts are
   unchanged, so the evaluation suites still measure the same prompts.
5. **Recorded as evidence.** Each change logs a `work item` decision event and the
   sealed manifest's `run` section gains `kind`, `source` and `requested_by`
   (added keys; schema v1 stands, ADR-0023).
6. **Old records still load.** Products and changes are stored as documents: a change
   without a kind reads as `new` for iteration 1 and `feature` after it; a product
   without a target reads as `new`. Nothing is migrated.
7. The feedback endpoint stays (`POST /api/products/{id}/feedback`) and takes an
   optional `kind` (default `feature`). The UI calls it "request a change".

## Consequences
- Existing-repo onboarding and assessment, ticket intake and scheduled upkeep plug into
  one model instead of each inventing its own.
- Upkeep's "behaviour stays the same" is an instruction, not yet a check. A
  deterministic check (requirements and scenarios unchanged in the diff) can join the
  change-risk station later.
- Outcomes are not split by kind yet; the data is there when we want it.
