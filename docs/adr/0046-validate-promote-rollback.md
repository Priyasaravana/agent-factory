# ADR-0046: Validate in a temporary environment, promote the tested image, roll back to the last healthy release

**Status:** proposed · 2026-11-01 · direction decided by Saravana 2026-11-01; slice 1 built on
`feat/j2-rollback` (PR #131, review fixes 2026-11-01: fail closed, crash-safe recording, verified rollback) · builds on [ADR-0026](0026-cluster-target.md) (cluster target),
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
1. **What a deploy installs is known before and after** (slice 1). Before the cluster is touched, deploy
   saves its intent on the change (`Change.deployed`: change, iteration, image, and where it runs: delivery
   environment, deploy integration, cluster, namespace, Helm release). After `helm upgrade --wait` and the
   liveness check it adds the release's revision (`helm status -o json`) and the immutable image digest
   every running container reports (`kubectl get pods`, imageID). A deploy that can't tell either one fails
   closed: it holds as `infra` (no repair agent can fix it) and nothing is recorded as healthy.
2. **The healthy release is recorded the moment it is accepted** (slice 1). When acceptance passes (or
   handover, in a lane without acceptance), the change's complete release becomes `Product.release` and is
   saved at once, before handover merges, tags or publishes. A handover that fails afterwards leaves an
   accepted, tracked release serving; it is not rolled back.
3. **Rollback, diagnostics first, fail closed** (slice 1). When a deploy or live acceptance fails while this
   change's release may be serving, and the product has a healthy release in that environment, the engine
   (a) collects diagnostics, then (b) asks the provider to restore that release under the engine's own
   `rollback:<env>` capability. The provider **refuses**, without running any rollback, unless the record
   is complete (revision, digest, image, cluster, namespace, Helm release), names the cluster, namespace
   and release the deploy target points at now, and its revision is in `helm history` as a release that ran
   (`deployed` or `superseded`) with the recorded image in its values. It then runs
   `helm rollback <release> <recorded revision> --wait`, never "the previous revision", and (c) **verifies**
   the result: Helm's newest revision is deployed and reads `Rollback to <revision>`, its values carry the
   recorded image, `kubectl rollout status` completes, every live container runs the recorded digest
   (terminating pods aside), and the pack's liveness **and** readiness paths answer. Everything is recorded
   in `artifacts/<change>/rollbacks.json` (what was replaced, the target, the outcome, any refusal or
   failed check) and the event log (`warn`; `error` if it failed). On success the failure goes to repair as
   before, with a note that production is back on the healthy release. A refusal, a failed rollback or a
   failed check holds the change as `infra`: production needs a person, and nothing tries again unattended.
   No `--atomic`: it removes the failing pods before their logs are kept.
4. **Every stop settles production** (slice 1). A change that stops short of delivery while its own,
   unaccepted release may be serving (held, failed, paused, waiting, cancelled, or interrupted by a restart,
   where the restore runs at start-up before any automatic resume) gets the same restore. On success its
   resume starts again from deploy; if the restore can't be done, it holds as `infra`. A first delivery has
   no healthy release to restore: the event log says production may be serving the unaccepted release.
5. **Validate in a temporary environment** (slice 2). Deploy installs the change's image as its own release
   in its own namespace with its own address (Helm: `<slug>-c<id>` at `<slug>-c<id>.<domain>`); live e2e,
   the verifier and the repair loop run there. Production is not touched. The temporary release is removed
   when the change stops, whatever the outcome, and on archive; a sweep at start-up removes any left over.
   It starts with empty data and its own first-admin Secret. A provider that can't give a second address
   (the `local` NodePort provider) validates in place and relies on rollback, and the evidence says so.
6. **Promote the exact tested image** (slice 3). A `promote` station after acceptance deploys the same image
   reference (by digest where the registry gives one; never rebuilt) to production, runs the smoke check and
   the pack's e2e against it, and rolls back (decision 3) if either fails. Only then is the release recorded
   (decision 2).
7. **Strategy per product** (slice 4). `in-place` (Helm upgrade, rolling, the default) or `blue-green` (two
   releases, the ingress switched to the new one after its checks, the old one kept for an instant switch
   back until the next promotion). Set per product, defaulted by the pack.

## Consequences
- Users keep the last healthy release whenever a change fails after build: from slice 1 for every deploy
  target, and from slice 2 production isn't touched until the change has passed acceptance.
- A failed iteration costs one extra rollback per failed deploy round in slice 1 (seconds on kind), plus
  the checks around it (history, values, rollout, pods, two health paths).
- Fail closed means a person is called more often than an implicit "roll back to the previous revision"
  would: a missing or unverifiable record, a moved namespace or a release Helm no longer has all stop the
  change. That is the intent: production is only acted on when what it will become is known.
- A restart in the middle of a change can't leave an unaccepted release serving unnoticed: the intent is
  saved before the cluster is touched, and start-up restores production before resuming.
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
| §7 Reliability: rollback | The last healthy release is recorded with its revision and digest, restored to that revision exactly, and verified (rollout, digest, liveness and readiness). |
| §7 Reliability: crash safety | The deploy intent is saved before the cluster is touched; every stop and every restart settles production. |
| §1 Security & governance: immutable artifacts | Releases are identified by image digest and checked against the cluster before and after a rollback. |
| §5 Agent engineering: evidence before action | Diagnostics are collected before rollback, so repair keeps its evidence. |
| §7 Operational excellence | Every rollback is in the sealed evidence and the event log; a failed rollback holds for a person. |
