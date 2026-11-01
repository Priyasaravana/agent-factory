# ADR-0046: Validate in a temporary environment, promote the tested image, roll back to the last healthy release

**Status:** accepted · 2026-11-01 (accepted by Saravana 2026-11-01) · direction decided by Saravana 2026-11-01;
slice 1 merged (PR #131, `8680c44`: fail closed, crash-safe recording, verified rollback; engine errors settle
production; sidecar-safe digests); slices 2 and 3 merged together (PR #132, `72a7f7c`; decided 2026-11-01:
slice 2 alone would never update production on a `helm` target); slice 4 built on `feat/j2-blue-green`
(shape decided by Saravana 2026-11-01: two colours in production's one release, sharing its database) ·
builds on [ADR-0026](0026-cluster-target.md) (cluster target),
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
   the release's own containers report (`kubectl get pods`, imageID; containers are matched to the release by
   image repository, so sidecars on other images are ignored). A deploy that can't tell either one fails
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
   recorded image, `kubectl rollout status` completes, every running container of the release's image runs
   the recorded digest (terminating pods and sidecars aside), and the pack's liveness **and** readiness paths answer. Everything is recorded
   in `artifacts/<change>/rollbacks.json` (what was replaced, the target, the outcome, any refusal or
   failed check) and the event log (`warn`; `error` if it failed). On success the failure goes to repair as
   before, with a note that production is back on the healthy release. A refusal, a failed rollback or a
   failed check holds the change as `infra`: production needs a person, and nothing tries again unattended.
   No `--atomic`: it removes the failing pods before their logs are kept.
4. **Every stop settles production** (slice 1). A change that stops short of delivery while its own,
   unaccepted release may be serving (held, failed, paused, waiting, cancelled, an engine error in the
   pipeline itself, or interrupted by a restart,
   where the restore runs at start-up before any automatic resume) gets the same restore. On success its
   resume starts again from deploy; if the restore can't be done, it holds as `infra`. A first delivery has
   no healthy release to restore: the event log says production may be serving the unaccepted release.
5. **Validate in a temporary environment** (slice 2). Deploy installs the change's image as its own release
   in its own namespace with its own address (Helm: `<slug>-c<id>` at `<slug>-c<id>.<domain>`); live e2e,
   the verifier and the repair loop run there. Production is not touched. The temporary release is removed
   when the change stops, whatever the outcome, and on archive; a sweep at start-up removes any left over.
   It starts with empty data and its own first-admin Secret. A provider that can't give a second address
   (the `local` NodePort provider) validates in place and relies on rollback, and the evidence says so.
   *As built:* the temporary release is `validation.release_name` (the slug shortened, never the change
   id, to fit Helm's 53 characters); the pipeline records it on `Change.validation` (environment, target,
   cluster, namespace, release, URL) before the cluster is touched, and deploy adds the image, revision and
   digest; a digest it can't read holds as `infra` (`validation_unidentified`): it could never be promoted
   as exactly what was tested. Only deploy, deploy-repair and acceptance act on it (`validation.slot`);
   nothing records an intent on production there, so a failure in it rolls nothing back (`rollback.target`
   now restores only while the change's own release may serve production). The namespace is labelled
   `agent-factory.io/validation=<change>` for a person; removal and the sweep go by the change records,
   only for a release the change recorded as its own, never the product's name, and fail closed like a
   rollback: the target's identity for that name now (environment, target, cluster, namespace, release)
   must equal the recorded one (`validation.removal_problems`, review of PR #132). If the configuration has
   moved (another cluster or context), nothing is deleted, the environment stays on record as not removed
   (a warning), and archive stops: a same-named release elsewhere is never removed in its place. A resumed change whose
   temporary environment was removed deploys a new one before acceptance, never testing production
   instead (`validation.needs_redeploy`). A lane version without a `promote` station (older pinned
   versions) also validates in place, since production would otherwise never be updated; "Back to factory
   default" brings the station. In place, the healthy release is still recorded when acceptance passes.
6. **Promote the exact tested image** (slice 3). A `promote` station after acceptance deploys the same image
   reference (by digest where the registry gives one; never rebuilt) to production, runs read-only checks
   against it, and rolls back (decision 3) if they fail. Only then is the release recorded (decision 2).
   *Changed by Saravana 2026-11-01:* production gets **read-only checks only** (the liveness path, the readiness
   path and, for a web app, the home page, from the sandbox; no sign-in, nothing written), on a blue/green
   preview and after any promotion, in place or blue/green. Production holds users' data, and the pack's e2e and
   the hidden-scenario verifier already passed on that exact digest in the temporary environment, on empty data.
   (A target that validates in place, `local` or a lane without `promote`, still runs acceptance where the
   change is deployed: that is what validating in place means.)
   *As built:* `promote` (Deliver column, uses `deploy:*` and `promote`) deploys the recorded
   `repository:tag` over production (its intent saved first, as in decision 1), then requires production's
   running digest to equal the tested one (`promote_digest_mismatch`, `infra`), probes the target from the
   sandbox, and runs the read-only checks (as changed 2026-11-01). A failure is rolled back by the
   pipeline (decision 3) and holds for a person: the same image passed validation, so what differs is
   production, not the code. A change validated in place passes promote with nothing to do.
7. **Strategy per product** (slice 4). `in-place` (Helm upgrade, rolling, the default) or `blue-green` (two
   releases, the ingress switched to the new one after its checks, the old one kept for an instant switch
   back until the next promotion). Set per product, defaulted by the pack.
   *Decided by Saravana 2026-11-01:* two colours **in production's one Helm release**, not two releases. The
   packs' charts run Postgres inside the release, so two releases would mean two databases and a switch would
   lose the data; one release keeps one database under both colours.
   *As built:* the charts (nextjs-web 1.5.0, fastapi-service 1.1.0) render one Deployment per colour. Blue is
   the Deployment the app always had (same name and selector, plus a `color` label), so an in-place product
   moves to blue/green without replacing its pods; green has a selector of its own, so neither selects the
   other's pods. With `strategy=blue-green` the Service selects `blueGreen.live`, and a preview Service and
   Ingress (`<release>-next.<domain>`) reach the idle colour. `promote` on a blue/green product
   (`blue_green.py`, pure: `chosen`, `layout`, `plan`, `sets`, `in_place_reason`) reads production's Helm values,
   refuses unless production serves the recorded healthy image (`blue_green_unexpected`, `infra`), saves the
   intent, stages the tested image on the idle colour with the live colour unchanged, requires the tested digest
   on the idle colour's own pods, runs the read-only checks at the preview, switches the Service, requires the
   tested digest again and runs the read-only checks on production. The first blue/green release goes straight in as blue. The healthy release records its
   colour (`Release.color`). Rollback is unchanged: `helm rollback` to the recorded revision puts the Service back
   on the old colour, whose pods kept running, and the checks read the live colour's image from the values and
   that colour's pods only. A product is promoted in place, and the event log says why, when its target can't
   give the idle colour an address (`local`), the change was validated in place, its chart renders no colours
   (products made before these pack versions; an upkeep change from the pack brings the chart), or the preview
   address is another product's. The strategy is a product setting (`Product.deploy_strategy`; `GET`/`POST
   /api/products/{id}/deploy-strategy`, creator or admin), shown in the product page's **Releases** panel; both
   packs default to in place (`deploy:` in `pack.yaml`). Tests: `tests/test_blue_green.py`.
   *Safeguards (decided by Saravana 2026-11-01, before merge):*
   - **Schema compatible with the version kept running.** Before staging, `promote` reads the schema-defining
     code of the commit users are on (an image's tag is its commit) and of the tested commit (`schema_compat.py`,
     pure: SQL `create table` and other DDL in source and `.sql` files, SQLAlchemy models, Alembic-style
     operations; tests excluded). Only new tables and indexes are additive. A changed or no-longer-created
     table, or any new `alter`, `drop`, `truncate` or `rename`, holds the change as a **decision** for a person
     (`schema_not_additive`) before production is touched: make the change additive (expand now, contract in a
     later release) or set the product to in place under Releases and resume. A commit that can't be read
     holds the same way (fail closed).
   - **No test writes to users' data.** The preview gets the read-only checks of decision 6, never e2e.
   - **Charts checked by real Helm in CI.** `scripts/check-charts.py` (job `charts`, required on `main`) lints and
     renders both packs' charts in place, as a first blue/green release, staged and switched, and checks the
     structure the engine relies on; bad values must fail the render.

   *Known limits:* production runs two copies of the app; a schema change that blue/green holds still goes out
   in place if a person chooses so, and an in-place rollback then also runs the old version on the new schema.

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
- `identity` and `rollback` are optional on a deploy provider (`local` and `helm` have them). A target
  without them (a future Argo CD provider, say) records no releases and rolls nothing back; each deploy
  says so in the event log, and a stop after its deploy has nothing to restore.
- Answers ADR-0039's open promotion question 1 for the local setup: temporary namespaces in kind.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §1 Testing: test before production | Acceptance runs against a temporary environment, not the live app (slice 2); production only gets read-only checks, so no test writes users' data. |
| §1 Security & governance: same artifact promoted | The tested image reference is promoted, never rebuilt (slice 3; ADR-0039 §6). |
| §7 Reliability: compatible schema | Blue/green holds unless the schema change is additive against the version kept for the switch back (`schema_compat`). |
| §7 Reliability: rollback | The last healthy release is recorded with its revision and digest, restored to that revision exactly, and verified (rollout, digest, liveness and readiness). |
| §7 Reliability: crash safety | The deploy intent is saved before the cluster is touched; every stop and every restart settles production. |
| §1 Security & governance: immutable artifacts | Releases are identified by image digest and checked against the cluster before and after a rollback. |
| §5 Agent engineering: evidence before action | Diagnostics are collected before rollback, so repair keeps its evidence. |
| §7 Operational excellence | Every rollback is in the sealed evidence and the event log; a failed rollback holds for a person. |
