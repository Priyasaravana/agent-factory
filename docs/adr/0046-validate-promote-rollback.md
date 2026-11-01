# ADR-0046: Validate in a temporary environment, promote the tested image, roll back to the last healthy release

**Status:** proposed · 2026-11-01 · direction decided by Saravana 2026-11-01; slice 1 built on
`feat/j2-rollback` · builds on [ADR-0026](0026-cluster-target.md) (cluster target),
[ADR-0039](0039-lanes-phases.md) §6 (gated promotion), [ADR-0043](0043-failure-classes.md)
(failure classes) and [ADR-0023](0023-evidence-manifest.md) (sealed evidence)

## Context
A change to a live product deploys over the running release, then runs live e2e and the hidden-scenario
verifier against it. When either fails, the repair loop (develop → verify → build → deploy) runs while the
**broken** release serves users, for minutes to an hour, and if the change finally holds, it stays broken.
Nothing records which release was last healthy, so nothing can restore it. `helm upgrade --atomic` would
undo a failed upgrade, but it removes the failing pods before the engine collects their logs (repair loses
its evidence) and it doesn't cover a release that installs but then fails smoke or acceptance.

Saravana's direction (2026-11-01): every release is deployed and validated in an isolated, temporary
environment first; failed validation is diagnosed and repaired there without touching production; a
passing release is promoted to production as the exact tested artifact; a production deploy or acceptance
failure rolls back at once to the last healthy release, diagnostics first; production stays on that
release until a replacement passes every gate; temporary environments are cleaned up; production is never
where development, debugging or repair happen. The deployment strategy (in place or blue/green) is a
choice per product, not imposed on every small app.

## Decision
1. **The last healthy release is recorded.** When a change is delivered, the engine records the product's
   live release (`Product.release`: change, iteration, image, the deploy target's revision, when). It is the
   release a later failure restores. A deploy provider returns its revision from `deploy` (Helm: the
   release's `version` from `helm status -o json`).
2. **Rollback, diagnostics first** (slice 1). When a deploy, its smoke check or live acceptance fails on a
   product that has a healthy release on that deploy target, the engine (a) collects diagnostics, then
   (b) calls the provider's `rollback(ctx, release)` (Helm: `helm rollback <release> <revision> --wait`),
   then (c) checks the restored release answers its liveness path, and records all three in
   `artifacts/<change>/rollbacks.json` and the event log. The failure then goes to repair as before, with a
   note that production is back on the last healthy release. A rollback that fails, or a restored release
   that doesn't answer, holds the change as `infra` with the cause: a person must look at production. The
   first delivery has nothing to restore and behaves as today. No `--atomic`: the engine rolls back, after
   it has the evidence.
3. **Validate in a temporary environment** (slice 2). Deploy installs the change's image as its own release
   in its own namespace with its own address (Helm: `<slug>-c<id>` at `<slug>-c<id>.<domain>`); live e2e,
   the verifier and the repair loop run there. Production is not touched. The temporary release is removed
   when the change stops, whatever the outcome, and on archive; a sweep at start-up removes any left over.
   It starts with empty data and its own first-admin Secret. A provider that can't give a second address
   (the `local` NodePort provider) validates in place and relies on rollback, and the evidence says so.
4. **Promote the exact tested image** (slice 3). A `promote` station after acceptance deploys the same image
   reference (by digest where the registry gives one; never rebuilt) to production, runs the smoke check and
   the pack's e2e against it, and rolls back (decision 2) if either fails. Only then is the release recorded
   (decision 1).
5. **Strategy per product** (slice 4). `in-place` (Helm upgrade, rolling, the default) or `blue-green` (two
   releases, the ingress switched to the new one after its checks, the old one kept for an instant switch
   back until the next promotion). Set per product, defaulted by the pack.

## Consequences
- Users keep the last healthy release whenever a change fails after build: from slice 1 for every deploy
  target, and from slice 2 production isn't touched until the change has passed acceptance.
- A failed iteration costs one extra rollback per failed deploy round in slice 1 (seconds on kind).
- A temporary environment doubles a product's footprint while a change runs; its data starts empty, so a
  change that needs a data migration is checked against empty data only (backlog: migrations as a signal).
- Products delivered before slice 1 have no recorded release: their first change after it records one.
- Answers ADR-0039's open promotion question 1 for the local setup: temporary namespaces in kind.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §1 Testing: test before production | Acceptance runs against a temporary environment, not the live app (slice 2). |
| §1 Security & governance: same artifact promoted | The tested image reference is promoted, never rebuilt (slice 3; ADR-0039 §6). |
| §7 Reliability: rollback | The last healthy release is recorded and restored automatically, and its health is checked. |
| §5 Agent engineering: evidence before action | Diagnostics are collected before rollback, so repair keeps its evidence. |
| §7 Operational excellence | Every rollback is in the sealed evidence and the event log; a failed rollback holds for a person. |
