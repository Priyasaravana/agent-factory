# Plan: live-test usability findings (2026-11-03, assessed 2026-11-04)

Status: findings register, raised 2026-11-03 from the Expense claims delivery and the four-run live campaign
(J1 repeatability and J2, see [roadmap](../roadmap.md)); campaign closed 2026-11-04 01:42, results and an
assessment below. **Kept as a separate task from the campaign:** a finding here is not a campaign result. Ordering these against the
J-milestones, and anything that adds a control during the freeze, is Saravana's call (README rule 4).

## Why
Setting up one live run needed several explanations, two restarts and a workaround, and a delivery reported
"15/15 verified" while its main page didn't fit a phone. Each finding is a place where the factory's own
screens or output didn't tell the person the truth, or told it too late.

## Findings

| ID | Pri | What happened (how to reproduce) | Impact | Proposed improvement |
|---|---|---|---|---|
| UX-01 | High | Creating a product starts its first change at once on the default lane; org workflows can only be assigned to a product that already exists, and a running change keeps its pinned lane (`actions.py` `create_product`; `AssignmentsCard.tsx`). | No way, through the normal flow, to put a fresh product on a dedicated lane before its first run. Blocks the campaign's fresh-product set-up. | An admin-only pre-start assignment, or create without starting. Requesters still don't choose lanes. |
| UX-02 | High | The default new-product lane has no Promote stage, though the shipped template has one: upgrading the engine kept the old published lane. | An upgraded, healthy factory can keep delivering through an old flow without saying so. | Show installed versus current template (missing stages) and a guided upgrade before a run. Never replace a published workflow silently. |
| UX-03 | High | After upgrade, the CLI sandbox check passed 15/15, but server health said failed (factory-egress container-name conflict). Restarting the idle factory fixed sandbox and preflight. | Command output and dashboard disagree. | Serialize proxy start-up; upgrade confirms the **server's** sandbox and preflight state after its own check. Cause is a likely concurrent start-up race, not proved. |
| UX-04 | High | Expense claims delivered with 15/15 requirements verified while the UI review reported the Manage page overflowing at 390 px (screenshot 576 px wide, controls off-screen). | "Delivered" overstates phone usability. | A sideways-scroll check in the pack's e2e beside tap targets (backlog: "Visual standards enforced, not reported"); serious visual defects shown in delivery status and in the next iteration's work. |
| UX-05 | Med | Releases: choosing Blue/green reveals a separate "Use blue/green from the next release" button. The person wasn't sure it took; the saved strategy was unset (`ReleaseStrategy.tsx`). | A radio choice reads as saved. Why this save failed is not confirmed. | "Unsaved selection" state, save next to the choice, saved strategy and effective release shown apart. |
| UX-06 | Med | `local-ingress` was set for fastapi-service while nextjs-web still used `local`, in the same config file. | One blueprint's setting is read as factory-wide. | Show the environment per stack at creation, a summary of every blueprint's environment, and the setting's scope in plain words. |
| UX-07 | Med | Factory `localhost:8080`, product `localhost:8085`, ingress `*.localtest.me:8180` and the idle-colour address are different things; several explanations were needed. | People can't tell what an address is for or where to find it. | Label dashboard, live app, validation app and idle colour; show hostname and reachability on the product page. |
| UX-08 | Med | The `local` target can't give the separate address validation and blue/green need; the saved preference doesn't prove the effective strategy. | A person expects blue/green; the run falls back to in place. | Say what the target can do before saving; each release shows requested versus effective strategy and the fallback reason. |
| UX-09 | Med | Editing a lane with Block evaluation implies three extra full builds, changing the campaign's cost and time. | Surprise spend and a delay before the workflow is active. | Before publish: number of runs, expected cost and time, candidate versus active, and a test-workflow route. |
| UX-10 | Med | A regex false positive held a date check for a one-hour solo-admin cooling-off. **Fixed** (#146, `3ccefb6`). | Long stop on harmless code; a second admin was considered just to keep moving. | Keep the fix and its tests. Still open: show the exact time it becomes eligible and the supported solo-admin path. Never manufacture independent review with a second agent-controlled account. |
| UX-11 | Med | A duplicate email returned 500, not the specified 409, in hidden acceptance; the factory repaired it, then 15/15 live. Same bug as Kudos board. | An ordinary conflict looks like a server failure. | Keep the regression coverage; a clear duplicate-email field error. Recorded for this product, not assumed fixed in the template: check in later runs. |
| UX-12 | Med | Host sleep paused the run ~45 minutes with no clear progress; a sleep event was recorded afterwards. | Elapsed time and apparent inactivity confuse. | Last active time, "host asleep" status, a keep-awake hint before a run; progress distinguishes running, asleep, held and awaiting review. |
| UX-13 | Med | A failed preview command kept a tail full of sign-in logs, hiding the failing assertion. | A hold that needs digging before the decisive error is visible. | Keep the test summary and failed assertions apart from server logs; link the full report from the hold. |
| UX-14 | Med | Exhausted implementation attempts need Resume, while a first delivery may already be serving an unaccepted release (a warning was emitted). | Deployed is mistaken for accepted, and what Resume changes is unclear. | Show the release's acceptance state, what is serving, the exhausted budget and what Resume grants. |
| UX-15 | High | Expense iteration 2 (`096d8e827e4d`) failed at design: LiteLLM returned 422, per-call budget reached ($5.00/$5.00), while the factory recorded $0.81 for the architect's 30 turns. The hold read "design incomplete (no repair route)". | No one can tell whether to wait, resume, cut scope or change a budget; cost forecasts are unreliable while two meters disagree ~6×. | Hold leads with the budget: source (gateway key, `model_gateway.call_budget_usd`), cap, recorded usage on both meters, reset, and a budget-specific recovery. Reconcile the meters first. Never raise or drop a cap silently. |
| UX-16 | High | The CSV export picked on the card reached intake cut off mid-sentence ("with dat"); intake inferred the missing columns. Cause found: `ledger.py:785` builds the request with `it.detail[:400]`, and the suggestion's requirements are appended to that detail (line 740). | Requirements the person ticked are lost between the card and the agents, while the card shows them in full. | Send the whole selected item (title, detail, requirements) to intake; shorten only what's displayed. Test: a suggestion with 10 requirements arrives whole. |
| UX-17 | High | **Fixed** (#147, `1a366dd`). The validation release couldn't record its identity: the whole `helm status -o json` went through the executor's 6000-character tail and `rollback.helm_revision` got a fragment. | A healthy release was refused because its chart is large. | Done: compact metadata before truncation, large-response test. The guard (never approve an unknown digest) was right and stays. |
| UX-18 | High | Verifier at 20:23: provider said "temporarily limiting requests (not your usage limit)"; the factory said "usage limit reached" and showed a reset of 20:53, which is its own 30-minute fallback (`pipeline.py:1354`), not the provider's. Every wake redid the temporary deployment and e2e. | The person thinks the subscription is spent and waits for a made-up time; each wake repeats expensive validation. | Tell rate limiting from quota exhaustion (`RateLimitEvent` status and utilisation are already read in `agents/runner.py`); label the next retry as an estimate; bounded backoff that resumes the station, not the whole acceptance. |
| UX-19 | Med | At 20:53 the change held on Deploy's attempt budget after three **successful** temporary deployments, each redone only because the verifier was throttled and clean-up removed the environment (`pipeline.py:1167` counts every entry). | A healthy change holds as if deploying were broken. | Don't count a re-entry caused by a pause (limits, host sleep) against the station's budget; keep the budget for real failures. |
| UX-20 | High | **Detection fixed** (#148, `cd20e55`), **fail-open not fixed** (see L-3). Iteration 3 delivered in place on a blue/green product: `renders_colors` read the chart render through the 6000-character tail and missed a colour label, so promote fell back to in place, removed the kept green and the rollback target. | A saved strategy was silently downgraded and the rollback drill was invalid. | Done: compact render evidence. Open: fail closed once a product runs two colours. |
| UX-21 | Med | *Added in the assessment.* Kudos board's saved strategy read blue-green and no workflow was assigned, though in place was intended; found only by inspecting the DB before iteration 4. | Same family as UX-05: what is saved isn't what the person believes. | Show saved strategy and assigned lane on the product page and on the card before **Start the next iteration**. |
| UX-22 | Med | *Added in the assessment.* Kudos iteration 4 moved the live URL from `localhost:8083` to `kudos.localtest.me:8180` (the install's ingress setting), as part of a feature change. | An existing product's address changed without the change saying so; bookmarks and anyone using it break. | A change that moves a product's address says so before approval and in delivery, with the old and new address. |

## Campaign results (closed 2026-11-04 01:42; total $21.05 of $25)
Against J1's and J2's "Done when". Engine versions changed mid-campaign (`3ccefb6` → `1a366dd` → `cd20e55`), so
each result holds for the version it ran on.

| What | Result | Evidence and qualification |
|---|---|---|
| J1: a second product meets the bar on its first delivery | **Not run** | Run 1 skipped by Saravana 2026-11-03 because of UX-01. **J1 stays open.** |
| Iteration started from the card with a suggested feature | **Passed, qualified** | Expense iteration 2 (CSV export), but its requirements were truncated (UX-16). |
| …and a CVE fix | **Not exercised** | No CVE with a released fix was open on either product (allowed by J2's wording). |
| Temporary environment, gone when it stops | **Passed** | Removed on every stop, including each pause. |
| Production changes only by promoting the tested image | **Passed** | Expense revision 4, Kudos revision 8 (`sha256:15e55217…`), digests matched. |
| In-place → blue/green migration of an existing release | **Passed** | Expense iteration 2: users switched blue → green 18:07:31, blue kept, both Ready. |
| One product blue/green, another in place | **Passed** | Expense blue/green, Kudos in place (13/13). |
| A broken iteration rolls back while the previous version serves | **Failed, then passed for one case** | First drill invalid (UX-20). Corrected drill `e56fb20c67ad`: green failed readiness, revision 7 restored revision 5 (same blue pod, same digest), 2079/2079 probes 200. Proves an **idle-colour failure before the switch**, not recovery from a bad version **after** the switch. |
| Design answers persist across iterations | **Values only** | Five choices unchanged; iteration 1 was autonomous, so a person's answers surviving is not proved here (it was on Book lending, `9013b79`). |
| Continuous availability | **Qualified** | Three timeouts 19:45–19:50 (largest gap 987 s) before the drill touched production: unexplained, likely host or monitor. Corrected drill 2160/2160. |
| "Ready for" statement; accepted/closed state | **Not in the evidence** | Still J2 items. |

## Assessment (2026-11-04)
**1. Output truncation is a class of bug, not four bugs.** UX-13, UX-17 and UX-20 all come from decisions made on
`LocalExecutor`'s last 6000 characters (`executor.py:66`); UX-16 is the same failure at a different layer
(`[:400]`). Two of them invalidated or blocked campaign proofs. #147 and #148 fixed the two call sites that
were hit; the same pattern is still live elsewhere (L-1, L-2). The fix belongs in the executor: keep the full
output in a file and mark a result `truncated`, and make every parser that decides something either read
structured output or refuse a truncated result. A test can find decision code that reads `.output`.

**2. Where the factory can't honour a request, it downgrades instead of stopping.** UX-02 (old lane kept), UX-08
(in-place fallback), UX-20 (blue/green collapsed and the kept colour deleted), UX-21 (saved setting not what was
meant). The safe rule: a property the product already has (two colours, a lane's Promote stage) is never removed
by a fallback; the change holds and says why.

**3. "Waiting" has six causes and one vague label.** Host asleep (UX-12), budget cap (UX-15), provider rate limit
(UX-18), real quota, attempt budget (UX-19), exhausted implementation attempts (UX-14). Each needs the same four
facts: what it is waiting on, whose meter, when it can go on (and whether that time is known or guessed), and who
can act. One small model behind every hold and pause would cover all six instead of six wording fixes.

**4. Delivery overstates what it proved.** UX-04 (15/15 verified with an overflowing page), UX-16 (verified
against requirements that were cut short) and the rollback drill (delivered in place, reported as delivered).
Verification checks the requirements it was given; nothing checks it was given the right ones.

### Latent issues found reading the code (not hit in the campaign)
- **L-1 (High when J3 goes live).** `_changed_files` (`engine/stations.py`) reads `git diff --name-only` through the
  6000-character tail. For a team-repo change it scopes the tool checks to "files the change touched", so a change
  past roughly 150 files has scanner findings in the cut-off files ignored: a security check that fails open. Also
  used by the PR station and review.
- **L-2 (Medium).** The design station's guard that the architect edits only design documents (`_dirty`,
  `_keep_to_design_docs`) reads `git status --porcelain` the same way, so a large worktree change could keep edits
  the guard should undo.
- **L-3 (High, before the next blue/green run).** After #148, `blue_green.in_place_reason` still returns any reason
  and `_blue_green` promotes in place, including "the change was validated in place" (a lane without temporary
  validation). On a product already running two colours that deletes the kept colour, the rollback target.
  Fail closed: hold with the reason when production's layout is blue/green.
- **L-4 (hypothesis).** The 19:53 reset that hadn't fired at 19:59: `_wake_later` waits with `asyncio.sleep`
  (monotonic clock), which may not advance while the host or its VM sleeps. Unverified; and a paused change offers
  no Resume, so the person can only wait.
- **L-5 (to confirm).** A change's spend adds `max(CLI cost, gateway spend)` per call (`gateway.py`). With UX-15's
  meters ~6× apart, the campaign's $21.05 may be on either meter. Confirm which before using it for forecasts.
  UX-15's own likely cause: the gateway prices tokens (e.g. cache reads) differently from the CLI, and on a
  subscription its dollars are notional; not proved.

### Housekeeping left by the campaign
- The drills changed product source; the readiness fault was cleaned locally (`4f31bed`) but publishes only at the
  next Deliver. Check whether iteration 3's delivery published the fault to the product repo.
- Held trial `e56fb20c67ad` stays held with nothing to close it: the accepted/closed state (J2) is the place.
- A supported fault-injection switch (deploy-time, not source) would make drills repeatable without editing
  products: backlog, since it's new surface during the freeze.

## Proposed slices (for Saravana to order; revised 2026-11-04)
0. **Before any further blue/green live run:** L-3 (fail closed on two colours). Small and pure.
1. **Truncation as a class:** executor full output plus `truncated`; parsers fail closed; covers UX-13, UX-16
   (its own fix, same test idea), L-1, L-2, and guards #147/#148's call sites.
2. **One waiting-state model:** UX-12, UX-14, UX-15, UX-18, UX-19, L-4; reconcile meters (L-5) first.
3. **J1 unblock:** UX-01 and UX-02. Worth checking first whether re-publishing the default new-product lane from
   the current template (UX-02 by hand, no new control) lets a fresh product run on a lane with Promote; if it
   does, J1's repeatability run needs no exception to the freeze.
4. **Truthful status and settings clarity:** UX-03, UX-04, UX-05 to UX-09, UX-10's eligible time, UX-13, UX-21, UX-22.
5. **Watch in later runs:** UX-11.

## Original slices (2026-11-03)
1. **Before the campaign** (the run can't be set up cleanly otherwise): UX-01, UX-02, UX-03. For UX-02 the
   published `live-test-new-product` v2 lane (with Promote, Ask a person, evaluation off) is ready but not
   assigned, which is exactly UX-01. UX-03's interim step: restart the idle factory after an upgrade and check
   the dashboard's preflight, not only the CLI.
2. **Truthful status** (mostly display of what the engine already knows): UX-04, UX-08, UX-12, UX-13, UX-14,
   plus UX-10's eligible time.
3. **Settings clarity** (changes to existing controls, no new surface): UX-05, UX-06, UX-07, UX-09.
4. **Watch in later runs:** UX-11.

Freeze check: UX-01's create-without-start and UX-02's guided upgrade add control surface; the rest change
wording or show existing state. Whether slice 1 goes ahead during the freeze is an open decision.

## Evidence (on Saravana's Mac, `~/Saravana_Repos/agent-factory`)
- Expense claims: product `219a69ab0468`, change `fbf27f3403b6`; `.factory-data/artifacts/fbf27f3403b6/`
  `events.jsonl` and `ui-review.json`.
- Product creation: `engine/src/agent_factory/actions.py` `create_product`. Assignment UI:
  `web/src/components/AssignmentsCard.tsx`. Release preference: `web/src/components/ReleaseStrategy.tsx`.
- Campaign: Expense iterations `096d8e827e4d`, `4f5918eacbaa` (invalid drill), `e56fb20c67ad` (corrected drill,
  held); Kudos `eb1772326512`; `factory-live-test-report-2026-11-04.md`, `rollback-retry-result.json`, probe `.jsonl`.
- Ingress settings: local-only `.agent-factory/config.yaml` (now `local-ingress` for nextjs-web too; never committed).

## Campaign evidence asked for on 2026-11-03 (results above)
Fresh-product first delivery and phone checks; temporary validation and first promote; a suggested feature
from the iteration card; a real blue/green switch with the old colour kept; design answers persisting; a
deliberately failed iteration with the old version still serving and a rollback; Kudos board iteration 4 in
place. Record each as pass, fail or not run, with run IDs, release digests, colours, timestamps, cost and
exceptions. Set-up readiness is not proof of any of these.
