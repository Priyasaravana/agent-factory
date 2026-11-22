# Plan: user experience and journeys (review 2026-11-22)

Status: **proposed**, not decided. Reviewed against main `70d3282` (vision adopted 2026-11-21) by reading the UI
(`web/src`), the auth service, the README and runbook, and the findings already recorded from live runs
([live-test-usability.md](live-test-usability.md), UX-01 to UX-28). **Nothing was run for this review:** the
containerised stack can't start in the cloud session (handoff, "Not run here"), and the J4/J5 live run's own
findings are not written down yet (phase A item 1). Where a point rests on code rather than a live run, it says so.

## Why
The golden journey (J1–J5) proves what the **product** can do. It doesn't say what each **person** goes through.
The vision names four kinds of people ([vision.md](../vision.md), "Who it is for"); the factory is built, today,
for one: a single admin who is also the product owner, the security reviewer and the SRE. v0.1.0 puts it in front
of people who are none of those things, without Saravana beside them. This page maps each person's journey as it
is on main, marks the friction, and proposes what to fix before 2026-12-31 and what waits.

Scoring: **High** stops the person or makes them believe something false; **Med** costs time or needs someone
to explain; **Low** is polish. ✅ marks something already fixed that the journey relies on.

## The four journeys at a glance
| Stage | Open-source adopter | Engineering leader (buyer) | Product owner | Station team (security, QA, SRE) |
|---|---|---|---|---|
| Discover | README, GitHub | Consulting pitch, demo | Told by their lead | Told by their lead |
| Get in | Licence, install, first sign-in | Pilot install on their infra | An account from the admin | An account from the admin |
| First value | A dry-run product, then a live one | A sealed run they can check | A deployed app from a description | A finding they own, decided |
| Ongoing | Upgrades, more products | Outcomes, cost, trust tier | Feedback, iterations, accept | A queue across products, incidents |
| Exit | Retire products, uninstall | Renew or stop | Retire a product | — |
| **Weakest point** | No licence; a pitch that doesn't match the vision | Nothing yet a sceptic can verify | Waiting is silent | **The role doesn't exist yet** |

## 1. Open-source adopter (self-serve, no consultant)
Goal: from the GitHub page to a first live product, alone, in an afternoon.

| # | Step | What happens on main | Friction | Score |
|---|---|---|---|---|
| A1 | Can I use it? | No `LICENSE`: public but all rights reserved ([open-source-release.md](open-source-release.md)) | Nobody may legally run it | **High** (phase C/E) |
| A2 | What is it? | README sells "a local software factory… your laptop only needs Docker"; the vision sells a "control and evidence plane" for teams | The first page contradicts the positioning; readers judge it as a coding agent | **High** (phase E) |
| A3 | Will it run here? | Runbook and README assume a Mac (`claude setup-token` on your Mac, `caffeinate`); Linux and Windows aren't covered | Half the audience guesses | Med |
| A4 | Install | `cp .env.example .env`, `make up`; dry-run by default ✅ | Good default. Settings live in two places (`.env`: 13 set, 9 commented; and `.agent-factory/config.yaml`), and the config file mixes shipped defaults with local edits | Med (config split is phase B) |
| A5 | First sign-in | `make up` prints the one-time admin password ✅ | Fine; the one place it's documented is the runbook | Low |
| A6 | First product (dry-run) | Products page: title, stack card, "Describe it" or "I have a spec" | No empty state saying "try one in dry-run first, it costs nothing"; dry-run is a header pill only | Med |
| A7 | Go live | Model token, optional GitHub token (fine-grained, Administration), `FACTORY_MODE=live`, ~2 min for kind | No expected time or model spend per product is shown anywhere before Create (`ProductsPage.tsx` has no budget or estimate) | Med |
| A8 | Find the app | `127.0.0.1:8081…8100`, or `*.localtest.me:8180` with ingress; UI on `localhost:8080` | Four kinds of address (UX-07); `make apps` helps ✅ | Med |
| A9 | Leave it running | Host sleep pauses a change; shown only under Outcomes → Where the time goes and in events | Looks hung (UX-12) | Med |
| A10 | Upgrade | `make upgrade` | A published lane can stay behind its template silently except on its own page (UX-02, UX-25); duplicate lane names invite restoring the wrong one (UX-23); CLI and server can disagree on the sandbox (UX-03) | **High** |

## 2. Engineering leader (the buyer)
Goal: decide whether to let agents deliver and run software in their organisation, on evidence.

| # | Step | What happens on main | Friction | Score |
|---|---|---|---|---|
| B1 | The pitch | Demo through consulting | The strongest claim (operate, recover, retire, engine-judged) ran live once, 2026-11-21, and isn't written up | **High** (phase A item 1) |
| B2 | "Show me it's better" | Outcomes page: deliveries, lead time, change failure rate, autonomy, cost per change, with definitions ✅ | No baselines (G0), so every number lacks a "compared with" | **High** (phase A item 2) |
| B3 | "Show me I can trust the record" | Evidence zip with SHA-256 per file, re-checked on view ✅ | Hashed, not signed; no verifier someone else can run. A sceptic has to trust the factory to check itself | **High** (phase B) |
| B4 | "Is anything production-grade?" | Ready for: demo, pilot, production, computed ✅ | No product has reached production tier (G1) | **High** (G1) |
| B5 | Cost | Spend per change; meters reconciled ("Now" item 3) ✅ | No forecast before a change, no monthly view against a budget | Med |
| B6 | Pilot set-up | Single node, SQLite, one change at a time; two roles (admin, member); local sign-in only | A team of five queues behind one change; no SSO (phase D, licensed) | **High** for a pilot (phase T, D) |
| B7 | Report upwards | Outcomes and the change pages | No one-page view per product for a non-engineer; the product record comes after v0.1.0 | Med |

## 3. Product owner
Goal: describe something, get it built, judge it, and keep improving it, without touching code.

| # | Step | What happens on main | Friction | Score |
|---|---|---|---|---|
| C1 | Ask | Title, a **stack** card, prose or a spec (file upload) ✅ | Choosing a stack is a technical decision in a business person's form; no hint which to pick | Med |
| C2 | Expectation | Create starts at once (UX-01) | No "this usually takes about N minutes and costs about $N", no budget shown | Med |
| C3 | Questions and design decisions | Needs you, the decision sheet with "Other", spec review gate ✅ | Only seen if the page is open: `NeedsYou` polls every 5 s; no email, Slack or Teams message (no notifier in `engine/src` or `web/src`) | **High** |
| C4 | Waiting | Activity log, station strip, Overview/Advanced ✅ (UX-26, UX-27) | Six causes of waiting share one look (UX-12, UX-14, L-4); "held" vs "asleep" vs "my turn" isn't obvious at a glance | Med |
| C5 | Judge the app | Live app link, screenshots, Ready for, address changes stated ✅ (UX-22) | Fine on desktop; on a phone, the factory UI itself isn't checked the way products are (`web/e2e` runs at 1280–1300 px only) | Low |
| C6 | Accept or close | Accept, or close with a reason ✅ (#158) | — | — |
| C7 | Next iteration | Suggested features on the card, "Runs on the … lane, vN" ✅ (UX-24) | What is **saved** isn't always what **runs**: strategy and lane (UX-05, UX-08, UX-21) | Med |
| C8 | Share the product | Only the creator or an admin can steer (runbook, "Who may do what") | A PO can't hand a product to a colleague or cover a holiday | Med |
| C9 | Design only, then Jira | Vision lists a design-only preset; the create form has no "start at" | The promise is ahead of the product (composable step 3, 2027) | Med (say so in the README) |

## 4. Station-owning teams (security, QA, SRE)
Goal: own a stage across every product, with their own tools, policies and queue.

| # | Step | What happens on main | Friction | Score |
|---|---|---|---|---|
| D1 | Be someone | Two roles: admin, member (`auth`); approvals go to the creator or "an admin" | **There is no team, rota or station workspace** (stations-and-teams, "What exists": ❌) | **High** (slice 2, 2027) |
| D2 | Be told | As C3: nothing leaves the UI | A security reviewer has no reason to open the factory | **High** |
| D3 | Review a finding | Scanners built in (Trivy, Semgrep, OSV, Snyk with a token); change risk; image findings | Findings sit inside each change, not in a queue across products | Med |
| D4 | Approve a risky change | Second approver; solo-admin cooling-off ✅ (UX-10's false positive fixed) | Doesn't show when it becomes eligible (UX-10) | Med |
| D5 | Read what an agent did | Agent calls with transcripts | Transcripts only for the product's creator or an admin, so the reviewer who should read them can't | Med |
| D6 | Incident | Running it: availability, data, upkeep, what the factory did ✅ (J4) | Restore without a development cycle is phase A slice 0, not on main; no page to the on-call person | **High** (phase A) |
| D7 | Set the station's policy | Workflows editor | Saravana has found workflow editing confusing; duplicate names and stale lanes (UX-23, UX-25) make it risky | **High** |

## What the four journeys share
1. **The factory only speaks to someone who is looking.** Every person-owned step (questions, spec review, risk
   approval, feedback, holds, incidents) waits on a browser tab. Outcomes already measures "Waiting on a person
   now"; nothing tells the person. This is the largest gap between the product and "teams".
2. **Saved is not what runs.** UX-02, 05, 08, 20, 21, 23 and 25 are one class (assessment point 2 in
   [live-test-usability.md](live-test-usability.md)): a setting, a lane or a strategy that reads as applied and
   isn't. The fail-closed holds stop the worst cases; the screens still mislead.
3. **One person is the organisation.** Steering, transcripts, approvals and policy all resolve to the creator or
   an admin. That's correct for a single-node pilot and wrong for every persona except the adopter.
4. **The proof isn't visible to outsiders yet.** Baselines, signed evidence, a verifier and a production-tier
   product are all on the plan (phases A–B, G1). Until then the buyer's journey depends on a demo.
5. **No expectations before work starts.** No estimate of time or spend, no budget shown at Create, though the
   targets (≤ 60 min p50, ≤ $10 per change) exist.

## Proposed fixes (Saravana's call; none changes a milestone)
**Before v0.1.0, inside phases already planned.** These are what a stranger meets first:

| Fix | Journeys | Phase it fits | Size |
|---|---|---|---|
| README rewritten to the vision **with an honest "works live / built / planned" table** per stage, and design-only, teams and SSO marked as planned | A2, B1, C9 | E (propose doing it in C, so the public repo stops mis-selling earlier) | ½ day |
| Install for Linux next to the Mac path, and the release rehearsal run by someone who hasn't used the factory, timed | A3, A4 | E | ½ day + the run |
| **Upgrade honesty:** one banner listing every factory lane behind its template, "Restore all factory defaults" naming each change (UX-25); unique display names (UX-23); upgrade confirms the server's sandbox state (UX-03) | A10, D7 | A or B (hardening) | ~1.5 days |
| **Saved vs effective** on the product page: lane, release strategy and target, requested vs effective, before Start (UX-05, UX-08, UX-21) | C7 | A or B | ~1 day |
| Products empty state: "Start with a dry-run product (no model usage)", and at Create the change's budget cap plus the baseline's typical time and cost once G0 exists | A6, A7, C2 | after G0 (phase A) | ½ day |
| Buyer's evidence path: baselines on Outcomes, the verifier linked from the evidence panel, the production-tier product as the demo | B2–B4 | A, B, G1 (already planned: just make the demo walk through them) | — |

**New control surface, needs a decision:** a **minimal notifier**. When `/api/attention` gains an item, post to
one configured webhook (Slack, Teams or any URL) and optionally email, with the product, what's needed and a link.
No routing or rota (that is slice 2). It turns C3, D2 and part of D6 from High to Med for ~1 day, and the
attention data already exists. Options: (a) before v0.1.0, in phase A or B; (b) with slice 2 in 2027 as part of the
rota and escalation. Recommendation: (a), because every person-owned station depends on it and a public release
without it means "keep a tab open".

**After v0.1.0, already planned:** teams, rota and station workspaces (D1, D3, D5, C8; slice 2); the product record
and its role views (B7); SSO (B6, phase D licensed); concurrent changes (B6, phase T); design-only and start
anywhere (C9; slice 6); the full waiting-state model (C4; L-4).

## Measures for the journeys (proposed)
Beside the north star, so the experience is measured rather than argued:
- **Time to first dry-run product** for someone new, from clone (target ≤ 20 minutes), measured at the rehearsal.
- **Time to first live delivery**, same person (target ≤ 2 hours including set-up).
- **Time to notice:** from an attention item appearing to a person acting on it (Outcomes has the waiting time;
  this splits "noticing" from "deciding"). Before and after the notifier.
- **Unplanned explanations:** every time a pilot user needs Saravana to explain a screen, logged as a finding
  in [live-test-usability.md](live-test-usability.md), as the J1 and J2 runs did.

## Open questions for Saravana
1. The notifier: before v0.1.0 (option a) or with slice 2 (option b)?
2. The README rewrite: phase C or keep it in phase E?
3. Upgrade honesty and saved-vs-effective: in phase A/B hardening, or after the release?
4. Should the release rehearsal be run by someone other than Saravana (Priya, or a consulting contact), so A1–A10
   are tested by a real first-time user?
