# ADR-0049: Run and maintain, then retire (J4, J5)

**Status:** proposed · 2026-11-17 (J4 and J5 built in one PR at Saravana's request, 2026-11-16) · builds on
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
   recorded in a change's event log, never while a change is deploying to it. Readings are numbers only
   (`health_samples`). The pure `operate.evaluate` judges the window since the live release was delivered:
   too few readings is `unknown`, never a breach and never proof; a counter that went down claims nothing.
   A product may tighten its pack's target, never loosen it (`operate.tighten`).
4. **A breach is the alert, and the alert is an incident in the ledger** (`ledger.fold_watch`, category
   `incident`, change kind `bug`): one per product while it lasts; an operation record and a change event name
   the owner. "Make this change" on the Next iteration card starts the fix. The incident is fixed only when
   the watch ran again and found the product within its target, after the fixing change is no longer running
   (a reading before the fix is live proves nothing); the same breach after the fix reopens it ("still
   present"). The engine judges; no agent is asked whether a product is healthy.
5. **Backup and a tested restore.** A backup is the pack's command's output, kept under
   `<data>/backups/<product>/` (0700/0600), counted only when the data's fingerprint was the same before and
   after it was taken. A **restore drill** restores the newest backup away from production (on a target that
   validates separately, a scratch release of the live image, `<slug>-cdrill`, which must also report ready;
   otherwise a scratch database beside production's, which the app doesn't read: the record says which) and
   compares the restored fingerprint with the backup's (`operate.judge_restore`). A backup that has never been
   restored doesn't count: the production tier needs a passed drill. A scratch release left by a drill the
   engine stopped in the middle of is removed at start.
6. **Upkeep** scans the live image again (folded like a Build scan, ADR-0042) and runs the pack's outdated
   command in the product's sandbox (agent-written code: its package manager runs where agent-written code
   runs); dependencies behind are one `upkeep` ledger item. `operate.upkeep_start`: `ask` (default) leaves them
   on the Next iteration card for the owner; `auto` starts the upkeep change itself when nothing else runs on
   the product and it is in no incident (it spends model budget).
7. **A change survives a factory restart** when `policies.recover.mode` is `auto`: an interrupted change is
   queued again at its station with the budgets it had. At most `max_auto` (2) restarts in a row at one station
   are resumed by the factory; the next waits for a person, with the reason in the change's events, so a
   station that takes the engine down can't loop. The shipped default stays `manual` until Saravana decides.
8. **Ready for production** (`ready_for`, J2) is now proved from what was measured: a target, the watch
   reading the product now, and a passed restore drill (a stack that declares no data says so instead).
9. **Retire** (`engine/retirement.py`, `retire.py` pure): asked for by a person with the product's name typed
   again and a reason; refused while a change runs. Fixed steps, each saved on the product as it finishes:
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
- How the error rate behaves on a quiet product: both templates already export `http_requests_total` with a
  `status` label, and a window with no requests claims nothing, but the 5% default has not met real traffic.

## Consequences
- The engine now runs a background task in live mode (never in dry-run) and keeps product data backups on
  its volume: `make backup` of the factory includes them; their size is bounded by `operate.keep_backups`.
- Two tables (`health_samples`, pruned after `keep_samples_hours`, and the append-only `operation_records`)
  and fields on products (`availability`, `retirement`) and changes (`auto_resumed`). New data: no migration.
- The archive button stays only for products never delivered; a delivered one is retired.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §2 DORA: time to restore service, change failure rate | A breach becomes an incident and a fix change with no terminal; the incident closes only on a reading of the fixed release, and a fix that doesn't hold reopens it. |
| §7 Reliability | A declared availability target per product, backups proven by a restore drill (a backup that was never restored doesn't count), changes that survive a restart without looping. |
| §7 Operability & observability | The watch reads health and the request counter of every live product; what the factory did is an append-only record per product. |
| §7 Security | Data commands run in the release's workload, never the engine; backups are owner-only files never logged; the dependency check runs in the sandbox; retiring archives the repo with the token in the environment, and never touches a team's repo. |
| §7 Compliance & evidence | Retirement seals a decommission record naming every change's sealed evidence; the full history is shown from stored and sealed facts, re-verified when read. |
