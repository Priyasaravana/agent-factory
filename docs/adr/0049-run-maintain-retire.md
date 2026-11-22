# ADR-0049: Run and maintain, then retire (J4, J5)

**Status:** proposed · 2026-11-17 (J4 and J5 built in one PR at Saravana's request, 2026-11-16) · §4 amended
2026-11-22 (incident response, slice 0: restore service first, decided by Saravana 2026-11-21) · builds on
[ADR-0023](0023-evidence-manifest.md) (sealed evidence), [ADR-0040](0040-product-work-ledger.md) (the work
ledger), [ADR-0041](0041-stack-packs.md) (stack packs), [ADR-0042](0042-image-findings.md) (image findings) and
[ADR-0046](0046-validate-promote-rollback.md) (validation releases and rollback)

## Context
Until now the factory stopped caring about a product once it was delivered. The roadmap's J4 asks it to run
the product: a named owner, an availability target whose breach alerts, backed-up data with a restore that is
proven, a change that survives a factory restart, a minimal Operate phase that turns a breach into an incident
and a fix, and scheduled upkeep. J5 asks it to end a product's life: freeze it, export its data, undeploy,
archive its repository, and keep a record from which its whole history can be shown.

## Decision
1. **What running a stack takes is in its pack** (`stack.operate`, nextjs-web 1.6.0, fastapi-service 1.2.0):
   the default availability target (share of health checks over a window, at least N readings, optionally a
   server-error rate), where the request counter is read (Prometheus text), the data's backup, restore and
   fingerprint commands (they run inside the release's workload with `kubectl exec`, never in the engine; each
   names `{database}`, refused at load otherwise, so a drill can never be pointed at production by mistake),
   a scratch database for targets that can't give a drill its own release, and the dependency check. Adding
   a stack is still adding a folder.
2. **The owner** is the product's creator unless an admin names one (J2 already stored it). From J4 the owner
   may steer the product like its creator (`_may_steer`), and "Needs you" routes product items (an incident,
   upkeep, a retirement that stopped) to the owner (`attention.product_actions`, pure).
3. **The Operate watch** (`engine/operations.py`) reads every live, factory-built product once per
   `operate.probe_seconds`: its liveness path and its metrics, one bounded read-only request each, never
   recorded in a change's event log, never while a change is deploying to it. Readings of different products
   are taken side by side (at most `PROBES_AT_ONCE`, 8), so one app waiting out its timeout never delays the
   others' readings or alerts (added 2026-11-17, end-to-end audit). Readings are numbers only
   (`health_samples`). The pure `operate.evaluate` judges the window since the live release was delivered:
   too few readings is `unknown`, never a breach and never proof; a counter that went down claims nothing.
   A product may tighten its pack's target, never loosen it (`operate.tighten`).
4. **A breach is the alert, and the alert is an incident in the ledger** (`ledger.fold_watch`, category
   `incident`, change kind `bug`): one per product while it lasts; an operation record and a change event name
   the owner. "Make this change" on the Next iteration card starts the fix. The incident is fixed only when
   the watch ran again and found the product within its target, after the fixing change is no longer running
   (a reading before the fix is live proves nothing); the same breach after the fix reopens it ("still
   present"). The engine judges; no agent is asked whether a product is healthy.

   **Amended 2026-11-22: restore service first (incident response, slice 0;
   [plan](../planning/plans/stations-and-teams.md#slice-0-incident-response-decided-by-saravana-2026-11-21)).**
   A fix change is for the root cause; restoring service no longer waits for one. The engine **responds** to
   an open incident like it backs data up (`Operations.respond`, rules pure in `respond.py`), outside any
   change and with no model on the path: confirm → **snapshot first** → restore → verify → classify.
   - **The snapshot** is taken before anything is applied: live against recorded (revision, image, digests,
     replicas), the pods' readiness and waiting reasons, the deploy target's diagnostics (pods, events, the
     failing pods' log tails), redacted and kept owner-only under `<data>/incidents/<product>/`; its file and
     sha256 go in the operation record, never its text in an event.
   - **The ladder**, each step at most once per incident, at most `operate.max_actions_per_hour` (3) steps per
     product per hour, never while a change deploys to the product or it is being retired, under the engine's
     own `rollback:<environment>` capability: (1) the live state differs from `Product.release` (the image the
     release names, the digest its containers run, a workload's replicas against the release's manifest) →
     **re-apply** the recorded release, which is ADR-0046's fail-closed rollback to its revision, verified the
     same way; (2) the state matches → **restart** the release's Deployments once (never a StatefulSet: the
     data lives there), rollout and health paths verified. Anything further (rollback to an earlier release,
     a data restore, a dependency that is down) holds the incident for the owner with what was tried; the next
     step is taken only after `SETTLE_SECONDS` (120) and only while readings still fail.
   - **Never undo a newer release.** Helm's newest revision must be the recorded one or the factory's own
     re-apply of it ("Rollback to <recorded>"); anything else holds the incident as `stale record` and nothing is
     applied over it (the factory may have been restored from a backup older than the last deploy).
   - **States**, derived from the operation records (`respond.episode`): `open → mitigating → restored →
     resolved`, and `reopened` when the same breach is judged again on the readings since it was restored (the
     ladder goes on from the step it reached). Restored: `operate.restored_after` (3) healthy readings in a
     row since the last step. A failed reading or an incident being responded to is read every
     `operate.respond_probe_seconds` (10), so a breach is confirmed and a restore seen within a minute.
     Resolved: restored and no recurrence for `operate.resolve_after_hours` (24), recorded as operational
     (re-apply: the live state had drifted; restart: the release was unchanged); while the response is under
     way the window's return to target doesn't close the item, since the window still holds the outage. An
     incident nobody responded to closes as before (recovered by itself, or the fix change delivered).
   - **DORA:** time to detect (first failed reading → alert) and time to restore (first failed reading →
     restored) are in the `restored` and `resolved` records. An incident counts against the change whose
     release was live when it began, if that release was delivered at most `operate.incident_change_hours`
     (24) before the first failed reading (`caused_by`).
   - **Mode:** `operate.respond` is `auto` by default (decided 2026-11-21); `ask` proposes the step to the
     owner, who runs it from the Operations panel (`POST /products/{id}/respond`, steering, same checks and
     limits); `off` leaves incidents to a fix change. `auto` takes effect only on an install whose factory
     restore drill has passed (`agent-factory factory-drill passed`, recorded in the factory's meta), and after
     a factory restore (`scripts/restore.sh` leaves a marker) only for a product whose recorded release a
     reading has matched with the live one since; until then the step is proposed.
   - Not in slice 0, next: rollback to the previous healthy release when `schema_compat` says the change was
     additive (unsure or breaking asks the owner), the diagnosis agent (ops pack, `report.incident-diagnosis@1`,
     reading the snapshot file only), and a bug change raised when the engine proves a code defect.
5. **Backup and a tested restore.** A backup is the pack's command's output, kept under
   `<data>/backups/<product>/` (0700/0600), counted only when the data's fingerprint was the same before and
   after it was taken, and never larger than `operate.max_backup_mb` (5 GB; the shell caps the file with
   `ulimit -f`, so a dump the app's container makes, which agent-written code controls, can't fill the
   factory's own volume: added 2026-11-17). A **restore drill** restores the newest backup away from production (on a target that
   validates separately, a scratch release of the live image, `<slug>-cdrill`, which must also report ready;
   otherwise a scratch database beside production's, which the app doesn't read: the record says which) and
   compares the restored fingerprint with the backup's (`operate.judge_restore`). A backup that has never been
   restored doesn't count: the production tier needs a passed drill. A scratch release left by a drill the
   engine stopped in the middle of is removed at start.
6. **Upkeep** scans the live image again (folded like a Build scan, ADR-0042) and runs the pack's outdated
   command in the product's sandbox (agent-written code: its package manager runs where agent-written code
   runs); dependencies behind are one `upkeep` ledger item. `operate.upkeep_start`: `ask` (default) leaves them
   on the Next iteration card for the owner; `auto` starts the upkeep change itself when nothing else runs on
   the product and it is in no incident (it spends model budget). It tries an item **once**: an item a
   scheduled change addressed and the check still finds is left for the owner (`operate.auto_upkeep_pick`,
   said once in the change's events), so a fix that doesn't hold never restarts on every cycle (added
   2026-11-17). A pack's database may not be named like the drill's scratch database (`restore_drill`).
7. **A change survives a factory restart** when `policies.recover.mode` is `auto`: an interrupted change is
   queued again at its station with the budgets it had. At most `max_auto` (2) restarts in a row at one station
   are resumed by the factory; the next waits for a person, with the reason in the change's events, so a
   station that takes the engine down can't loop. The shipped default stays `manual` until Saravana decides.
8. **Ready for production** (`ready_for`, J2) is now proved from what was measured: a target, the watch
   reading the product now, and a passed restore drill (a stack that declares no data says so instead).
9. **Retire** (`engine/retirement.py`, `retire.py` pure): asked for by a person with the product's name typed
   again and a reason; refused while a change runs. Freezing it cancels every change still waiting on the
   product (held, paused, interrupted, waiting for answers or approval), each settling production first, and
   no change runs on a product being retired, whatever schedules it (`_execute`); production is never
   restored onto a product whose app was removed (added 2026-11-17: a held change used to survive retirement
   and a Resume would deploy to it). Fixed steps, each saved on the product as it finishes:
   freeze, a final data export (the only backup kept), undeploy, archive the repository (`gh repo archive`,
   token through the environment; a team's repo is never touched), and the **decommission record**:
   `record.json` (facts, every change's sealed manifest digest and whether its folder is still intact),
   `history.json`, the work ledger and the operation records, sealed like a change's evidence; its digest is
   kept on the product and re-verified when shown. A step that fails holds the retirement there and the next
   call continues it; when the data can't be reached any more, the person may go on without the export, and
   the record says so. Nothing new starts on a retired product, and Outcomes lists it with its record.
10. **History** (`GET /products/{id}/history`, `retire.timeline`, pure) is built from what is stored: the
   request, every change with its outcome and sealed evidence, incidents and upkeep from the ledger, backups,
   drills and the retirement. No agent writes it.

## Not decided here
- `policies.recover.mode` stays `manual` by default: making `auto` the default is Saravana's call.
- Whether the drill on a target without separate releases (the local target) is enough for production.
  Today it proves the data restores and matches, not that the app runs on it.
- Alerts outside the factory (email, chat, a pager): the alert is the incident and "Needs you" for now.
- Whether the factory restore drill can be run by the factory itself: slice 0 records a person's run of it
  (`agent-factory factory-drill`), which is what lets `operate.respond: auto` act alone.
- Whether a workload whose replicas an autoscaler owns needs "scale to the release's replicas" instead of a
  re-apply: such a workload states no replicas in its manifest and is not compared (to verify live).
- How the error rate behaves on a quiet product: both templates already export `http_requests_total` with a
  `status` label, and a window with no requests claims nothing, but the 5% default has not met real traffic.

## Consequences
- The engine now runs a background task in live mode (never in dry-run) and keeps product data backups on
  its volume: `make backup` of the factory includes them; their size is bounded by `operate.keep_backups` × `operate.max_backup_mb`. A retired product's final
  export is kept `retire.keep_export_days` (90; none: until a person deletes it) after the retirement: the sealed
  record states the date (`data_export.kept_until`), the watch then deletes it and appends a `retire` record
  naming each deleted file with its size and sha256, the digest the sealed record holds (decided 2026-11-19 from
  the PR #186 review; `Retirer.purge_expired_exports`). The date in the sealed record decides, never the setting
  at purge time (a later change to `keep_export_days` applies to later retirements only); a record that names no
  date (sealed before retention existed, or kept until deleted) or no longer verifies keeps the export
  (`retire.export_deadline`, re-review).
- Two tables (`health_samples`, pruned after `keep_samples_hours`, and the append-only `operation_records`)
  and fields on products (`availability`, `retirement`) and changes (`auto_resumed`). New data: no migration.
- The archive button stays only for products never delivered; a delivered one is retired.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §2 DORA: time to restore service, change failure rate | A breach becomes an incident and a fix change with no terminal; the incident closes only on a reading of the fixed release, and a fix that doesn't hold reopens it. From §4's amendment, service is restored before any fix (re-apply, restart) with time to detect and time to restore recorded per incident, and the incident linked to the change it counts against. |
| §7 Reliability: recover automatically, fail closed | The ladder acts only through ADR-0046's checks, once per step, rate-limited, never over a newer release, and in auto only once a factory restore drill has passed. |
| §7 Reliability | A declared availability target per product, backups proven by a restore drill (a backup that was never restored doesn't count), changes that survive a restart without looping. |
| §7 Operability & observability | The watch reads health and the request counter of every live product; what the factory did is an append-only record per product. |
| §7 Security | Data commands run in the release's workload, never the engine; backups are owner-only files never logged; the dependency check runs in the sandbox; retiring archives the repo with the token in the environment, and never touches a team's repo. |
| §7 Compliance & evidence | Retirement seals a decommission record naming every change's sealed evidence; the full history is shown from stored and sealed facts, re-verified when read. |
