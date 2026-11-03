# Plan: live-test usability findings (2026-11-03)

Status: findings register, raised 2026-11-03 from the Expense claims delivery and the set-up for the four-run
live campaign (J1 repeatability and J2, see [roadmap](../roadmap.md)). **Kept as a separate task from the
campaign:** the campaign has not started, and nothing here says it passed. Ordering these against the
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

## Proposed slices (for Saravana to order)
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
- Ingress settings: local-only `.agent-factory/config.yaml` (now `local-ingress` for nextjs-web too; never committed).

## Campaign evidence still required (not covered by this task)
Fresh-product first delivery and phone checks; temporary validation and first promote; a suggested feature
from the iteration card; a real blue/green switch with the old colour kept; design answers persisting; a
deliberately failed iteration with the old version still serving and a rollback; Kudos board iteration 4 in
place. Record each as pass, fail or not run, with run IDs, release digests, colours, timestamps, cost and
exceptions. Set-up readiness is not proof of any of these.
