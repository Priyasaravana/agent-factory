# J4 + J5: the live run (run and maintain, then retire)

Built on `feat/j4-j5-run-retire` (2026-11-17, [ADR-0049](../../adr/0049-run-maintain-retire.md)). The
scripted journey (`engine/tests/test_journey.py::test_j4_j5_run_maintain_and_retire`, dry-run, in CI) proves the
steps fit together; this page is the live run that closes J4 and J5 (each milestone ends with one, roadmap). It
needs the stack on the Mac: Docker Desktop, `make up`, a model credential, the `local` (or `local-ingress`)
target and a `GITHUB_TOKEN` that may archive repositories in the products org.

## Before
1. Merge the PR, `git pull`, `make up` (the engine rebuilds; `policies.recover`, `operate`, `retire` are new
   config keys with defaults; nothing to migrate). In `.agent-factory/config.yaml` on the Mac (never committed),
   for this run only: `policies.recover.mode: auto` and `operate.probe_seconds: 30`.
2. Use the J1 product (Kudos board, nextjs-web) if it is still live; otherwise create a Next.js product and let it
   deliver. Its pack must be 1.6.0 (`stack.operate`): an older product picks it up at its next change.

## J4 (each step: what to do → what to see)
1. **Owner.** Admin → product → Ready for → "Name the owner" → a second account's user name.
   → "Running it" says incidents go to that person; the second account can press its buttons.
2. **Target.** "Running it" → "Tighten target" → 99.5. → 99.5% shown "set for this product"; 98 is refused.
3. **The watch.** Wait two minutes. → "Within target", "Watch: running, read …s ago", readings counted.
4. **Backup and restore.** "Back up now", then "Test a restore". → both ok; "restore proven";
   on `local-ingress` the drill names the scratch release `<slug>-cdrill` and "the app ran on it";
   `kubectl get ns | grep cdrill` shows nothing afterwards. Ready for: **production** once the watch has a
   reading and the drill passed.
5. **Inject a fault.** `kubectl -n app-<slug> scale deploy/<slug> --replicas=0`.
   → within `min_samples × probe_seconds` (2½ minutes at 30 s): "Outside target", one incident, "Needs you"
   on the owner's account, the incident on the Next iteration card.
6. **Fix it with no terminal.** Scale it back is *not* the fix: tick the incident → "Make this change".
   While it runs, at the `test` or `implement` station: `docker compose restart factory`.
   → the change says "resumed after the factory restarted, at <station> (1 of 2 …)" and delivers.
   (If the agent finds nothing to change in code, the redeploy itself restores the replicas: that is the fix.)
7. **The incident closes.** Two minutes after delivery. → "Within target", the incident "fixed", resolved by
   the fix change; "Needs you" empty.
8. **Upkeep.** "Running it" → "Check now". → "Last check ok", the dependencies behind (if any) on the Next
   iteration card under Upkeep; image findings under Security fixes.
9. **Restart loop guard (optional).** Make a station hang and restart three times. → the third restart leaves
   the change "interrupted" with "not resumed automatically: the factory restarted 3 times in a row".

## J5
1. **Retire.** Header → "Retire" → a reason, the product's name. → steps done in order: freeze, export (bytes and
   sha256), undeploy, archive the repository (the repo shows "archived" on GitHub), record sealed.
2. **Frozen.** The product page shows "retired"; no Next iteration card; a feedback call answers 409.
3. **History.** History panel: from "Product requested" to "Product decommissioned", every delivery with its
   sealed digest, the incident (found → addressed → fixed), backups, the drill, upkeep, the retirement.
   "Decommission record sealed and intact".
4. **Outcomes.** The Retired card lists the product with its record digest.
5. **Keep the evidence.** `data/decommission/<product>/` (record, history, ledger, operations, manifest,
   SHA256SUMS) and the export under `data/backups/<product>/` are the demo record.

## What to write down
Times (probe → alert, restart → resume), anything the UI said that was unclear, and any step that needed a
terminal. Findings go to [live-test-usability.md](live-test-usability.md), the result to roadmap J4/J5.
