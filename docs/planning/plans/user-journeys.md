# Plan: user experience and journeys (review 2026-11-22)

Status: **proposed**, not decided. Reviewed against main `70d3282` (vision adopted 2026-11-21) by reading the UI
(`web/src`), the auth service, the README and runbook, and the findings already recorded from live runs
([live-test-usability.md](live-test-usability.md), UX-01 to UX-28). **Nothing was run for this review:** the
containerised stack can't start in the cloud session (handoff, "Not run here"), and the J4/J5 live run's own
findings are not written down yet (phase A item 1). No customer or first-time user was asked.

## Why
The golden journey (J1–J5) proves what the **product** can do. It doesn't say what each **person** goes through.
The vision names who the factory is for ([vision.md](../vision.md), "Who it is for"); today it is built for one
of them: a single admin who is also the product owner, the security reviewer and the SRE. v0.1.0 puts it in front
of people who are none of those things, without Saravana beside them. This page maps six journeys as they are on
main, marks the friction, and proposes what to fix before 2026-12-31 and what waits.

## How to read the scores (review of PR #192, 2026-11-22)
Every finding carries its **evidence**, so a hypothesis isn't read as user research:
- **O · observed:** seen in a live run and recorded (a UX-nn finding, or a statement by Saravana).
- **C · code-confirmed:** what the step does was checked in the code or the docs (file named); the friction it
  causes is still a judgement.
- **I · inferred:** a judgement about how a person would react, with nothing observed or confirmed behind it.

The **score** is the reviewer's judgement of impact, not a measured customer severity: **High** stops the person
or makes them believe something false; **Med** costs time or needs someone to explain; **Low** is polish. A score on
a C or I finding is a hypothesis until the release rehearsal (phase E) or a pilot observes it; see "Validating
this page". ✅ marks something already fixed that the journey relies on.

## The six journeys at a glance
| Stage | Open-source adopter | Engineering leader (buyer) | Product owner | Station team (security reviewer, QA, SRE) | Business owner / executive (reader) | CISO / security decision-maker |
|---|---|---|---|---|---|---|
| Discover | README, GitHub | Consulting pitch, demo | Told by their lead | Told by their lead | A report or a link from their lead | Asked to approve the pilot |
| Get in | Licence, install, first sign-in | Pilot install on their infra | An account from the admin | An account from the admin | An account, or nothing (a pushed summary) | Threat model, assessment, data flows |
| First value | A dry-run product, then a live one | A sealed run they can check | A deployed app from a description | A finding they own, decided | "Is my product healthy, who owns it, what does it cost?" | "What leaves our network, who can approve what, can I audit it?" |
| Ongoing | Upgrades, more products | Outcomes, cost, trust tier | Feedback, iterations, accept | A queue across products, incidents | A periodic summary | Audit export, accepted risks, re-reviews |
| Exit | Retire products, uninstall | Renew or stop | Retire a product | — | Retirement record | Decommission record, data deleted |
| **Weakest point** | No licence; a pitch that doesn't match the vision | Nothing yet a sceptic can verify | Waiting is silent | **The role doesn't exist yet** | **No view made for them** | Residual risks unaccepted; no read-only role |

The buyer (engineering leader) and the CISO are different people with different questions: the first asks
"does it deliver, and is it worth it?", the second "can it hurt us, and can I prove it didn't?". The executive asks
neither; they want state, owner, cost and risk in one line per product.

## 1. Open-source adopter (self-serve, no consultant)
Goal: from the GitHub page to a first live product, alone, in an afternoon.

| # | Step | What happens on main | Friction | Evidence | Score |
|---|---|---|---|---|---|
| A1 | Can I use it? | No `LICENSE` in the repo: public but all rights reserved ([open-source-release.md](open-source-release.md)) | Nobody may legally run it | C (no `LICENSE` file) | **High** (phase C/E) |
| A2 | What is it? | README sells "a local software factory… your laptop only needs Docker"; the vision sells a "control and evidence plane" for teams | Readers judge it as a coding agent | C (README vs vision); reaction I | **High** (phase E) |
| A3 | Will it run here? | Runbook and README assume a Mac (`claude setup-token` on your Mac, `caffeinate`); Linux and Windows aren't covered | Half the audience guesses | C (runbook); reaction I | Med |
| A4 | Install | `cp .env.example .env`, `make up`; dry-run by default ✅ | Settings live in two places (`.env`: 13 set, 9 commented; and `.agent-factory/config.yaml`), and the config file mixes shipped defaults with local edits | C (`.env.example`, open-source-release.md) | Med (config split is phase B) |
| A5 | First sign-in | `make up` prints the one-time admin password ✅ | Documented only in the runbook | C (runbook) | Low |
| A6 | First product (dry-run) | The empty state says "Describe your first app"; dry-run is a header pill only | Nothing invites a free first try | C (`ProductsPage.tsx`); reaction I | Med |
| A7 | Go live | Model token, optional GitHub token (fine-grained, Administration), `FACTORY_MODE=live`, ~2 min for kind | No expected time or model spend is shown before Create | C (`ProductsPage.tsx` has no budget or estimate) | Med |
| A8 | Find the app | `127.0.0.1:8081…8100`, or `*.localtest.me:8180` with ingress; UI on `localhost:8080`; `make apps` helps ✅ | Four kinds of address | **O** (UX-07) | Med |
| A9 | Leave it running | Host sleep pauses a change; shown under Outcomes → Where the time goes and in events | Looks hung | **O** (UX-12) | Med |
| A10 | Upgrade | `make upgrade` | A lane can stay behind its template, said only on its own page; duplicate lane names invite restoring the wrong one; CLI and server can disagree on the sandbox | **O** (UX-02, UX-25, UX-23, UX-03) | **High** |

## 2. Engineering leader (the buyer)
Goal: decide whether to let agents deliver and run software in their organisation, on evidence.

| # | Step | What happens on main | Friction | Evidence | Score |
|---|---|---|---|---|---|
| B1 | The pitch | Demo through consulting | The strongest claim (operate, recover, retire, engine-judged) ran live once, 2026-11-21, and isn't written up | C (handoff, phase A item 1) | **High** (phase A) |
| B2 | "Show me it's better" | Outcomes: deliveries, lead time, change failure rate, autonomy, cost per change, with definitions ✅ | No baselines, so every number lacks a "compared with" | C (vision G0; none in `docs/outcomes.md`) | **High** (phase A) |
| B3 | "Show me I can trust the record" | Evidence zip with SHA-256 per file, re-checked on view ✅ | Hashed, not signed; no verifier someone else can run | C (vision G2, phase B) | **High** (phase B) |
| B4 | "Is anything production-grade?" | Ready for: demo, pilot, production, computed ✅ | No product has reached production tier | C (vision G1) | **High** (G1) |
| B5 | Cost | Spend per change; the two meters reconciled after they disagreed ~6× ✅ | No forecast before a change, no monthly view against a budget | O (UX-15, fixed); gap C | Med |
| B6 | Pilot set-up | Single node, SQLite, one change at a time; local sign-in only | A team of five queues behind one change; no SSO | C (vision risks, phase D) | **High** for a pilot (phase T, D) |

## 3. Product owner
Goal: describe something, get it built, judge it, and keep improving it, without touching code.

| # | Step | What happens on main | Friction | Evidence | Score |
|---|---|---|---|---|---|
| C1 | Ask | Title, a **stack** card, prose or a spec (file upload) ✅ | Choosing a stack is a technical decision in a business person's form | C (`ProductsPage.tsx`, `StackCards`); reaction I | Med |
| C2 | Expectation | Create starts the first change at once | No "this usually takes about N minutes and costs about $N" | O (UX-01) for starting at once; C for no estimate | Med |
| C3 | Questions and design decisions | Needs you, the decision sheet with "Other", spec review gate ✅ | Only seen with the page open: `NeedsYou` polls every 5 s; no email or chat message | C (no notifier in `engine/src` or `web/src`); impact I | **High** |
| C4 | Waiting | Activity log, station strip, Overview/Advanced ✅ (UX-26, UX-27) | Six causes of waiting share one look | **O** (UX-12, UX-14, L-4) | Med |
| C5 | Judge the app | Live app link, screenshots, Ready for, address changes stated ✅ (UX-22) | The factory UI itself isn't checked on a phone the way products are | C (`web/e2e` runs at 1280–1300 px only) | Low |
| C6 | Accept or close | Accept, or close with a reason ✅ (#158) | — | C | — |
| C7 | Next iteration | Suggested features on the card, "Runs on the … lane, vN" ✅ (UX-24) | What is saved isn't always what runs: strategy and lane | **O** (UX-05, UX-08, UX-21) | Med |
| C8 | Share the product | Only the creator or an admin can steer | A PO can't hand a product to a colleague or cover a holiday | C (runbook, "Who may do what"); impact I | Med |
| C9 | Design only, then Jira | The vision lists a design-only preset; the create form has no "start at" | The promise is ahead of the product (composable step 3, 2027) | C | Med (say so in the README) |

## 4. Station-owning teams (security reviewer, QA, SRE)
Goal: own a stage across every product, with their own tools, policies and queue. The three share the structural
gap (D1, D2); where their steps differ, the row says whose it is.

| # | Step | What happens on main | Friction | Evidence | Score |
|---|---|---|---|---|---|
| D1 | Be someone | Two roles: admin, member; approvals go to the creator or "an admin" | No team, rota or station workspace | C (`auth`, stations-and-teams "What exists": ❌) | **High** (slice 2, 2027) |
| D2 | Be told | As C3: nothing leaves the UI | A reviewer has no reason to open the factory | C; impact I | **High** |
| D3 | Security reviewer: a finding | Scanners built in (Trivy, Semgrep, OSV, Snyk with a token); change risk; image findings | Findings sit inside each change, not in a queue across products | C | Med |
| D4 | Security reviewer: a risky change | Second approver; solo-admin cooling-off ✅ (false positive fixed) | Doesn't show when it becomes eligible | **O** (UX-10) | Med |
| D5 | Security reviewer: what an agent did | Agent calls with transcripts | Transcripts only for the product's creator or an admin | C (runbook) | Med |
| D6 | QA: the evidence of a test | Hidden scenarios, traceability, e2e, screenshots per change ✅ | A failing assertion was hidden in server logs (fixed); no QA view across products | O (UX-13, fixed); gap C | Med |
| D7 | SRE: an incident | Running it: availability, data, upkeep, what the factory did ✅ (J4) | Restore without a development cycle is phase A slice 0, not on main; no page to the on-call person | C; **O** (Saravana, 2026-11-21: incidents shouldn't go through the full cycle) | **High** (phase A) |
| D8 | Whoever sets station policy | Workflows editor | Hard to follow; duplicate names and stale lanes make it risky | **O** (Saravana; UX-23, UX-25) | **High** |

## 5. Business owner / executive (reader)
Goal: know, for each product they're accountable for, its state, owner, cost and risk, without learning the tool.

| # | Step | What happens on main | Friction | Evidence | Score |
|---|---|---|---|---|---|
| E1 | Get access | Any signed-in member sees everything **and can create products**; there is no read-only role | Giving an executive a look also gives them the power to spend | C (runbook, "Who may do what"; `auth` roles) | Med |
| E2 | Find their product | Products list by status; product page Overview ✅ | Fine for one product; no portfolio view by owner or business area | C (`ProductsPage.tsx`); need I | Med |
| E3 | "Is it healthy?" | Ready for tier, Running it (availability, backups), incidents in the ledger ✅ | In engineering words (tier, Level 3, pillars, lanes) | C (`ReadyFor.tsx`, `Operations.tsx`); reaction I | Med |
| E4 | "What does it cost?" | Spend per change; cost per delivered change on Outcomes | No cost per product per month, nor running cost (only model spend) | C (roadmap backlog: spend is model-only) | Med |
| E5 | "What's the risk?" | Accepted risks and findings live per change and in the ledger | No one-line risk summary | C | Med |
| E6 | Without logging in | Nothing is pushed: no summary, digest or export for a non-user | Executives won't open a tool weekly | C; behaviour I | **High** for this reader |
| E7 | End of life | A sealed decommission record ✅ (J5) | Readable as evidence, not as a summary | C (`Retire.tsx`) | Low |

The product record's business view (state, owner, cost per change, risk summary) is planned with slice 4
(stations-and-teams, "The product record"), after v0.1.0. Until then this reader is served by the consultant.

## 6. CISO / security decision-maker
Goal: approve (or refuse) letting agents act in the organisation, then keep that approval honest.

| # | Step | What happens on main | Friction | Evidence | Score |
|---|---|---|---|---|---|
| F1 | "Show me the threat model" | [threat model](../../security/threat-model.md), the [2026-11-15 assessment](../../security/assessment-2026-11-15.md), ADR-0048 ✅ | Strong and public; its open groups (S-05, S-06, S-07, S-08, S-10, S-11) close in phase A | C | Med until phase A |
| F2 | "What risks are you carrying?" | ADR-0048 lists residual risks; **none is accepted** (needs owner, reason, scope, expiry) | A CISO sees open risks with no owner | C (handoff 2026-11-16) | **High** |
| F3 | "What leaves our network?" | Code and prompts go to the model provider (Anthropic, or any model through LiteLLM); Snyk receives code or dependency manifests when it is used; GitHub gets the product repo | Not stated in one place, per destination, with what is sent | C (README, ADR-0036, scan stations); stations-and-teams plans per-integration declarations | **High** |
| F4 | "Who can do what?" | admin and member; members create products and see everything; no read-only, no SSO, no per-product access | Least privilege can't be shown | C (runbook, `auth`) | **High** (SSO phase D; roles slice 2) |
| F5 | "Can I audit it?" | Sealed evidence, agent calls with refused actions, repo-source audit log ✅ | No export to a SIEM; evidence not signed | C (phase B, phase D) | **High** until B/D |
| F6 | "Is the factory itself safe to run?" | No root, sandboxed agents, `make sandbox-check`, `make privilege-check` ✅; privileged dind is an accepted risk in ADR-0018 | Good evidence; dind privilege needs explaining for production | C (README, ADR-0018) | Med |
| F7 | Ongoing | Dependabot, the security groups, a history secrets scan (phase A) | No published cadence for re-assessment or vulnerability disclosure (SECURITY.md is phase E) | C (open-source-release.md) | Med |

## What the journeys share
1. **The factory only speaks to someone who is looking.** Every person-owned step (questions, spec review, risk
   approval, feedback, holds, incidents) waits on a browser tab, and nothing reaches the executive at all. Outcomes
   measures "Waiting on a person now"; nothing tells the person. (C; impact I.)
2. **Saved is not what runs.** UX-02, 05, 08, 20, 21, 23 and 25 are one class (assessment point 2 in
   [live-test-usability.md](live-test-usability.md)): a setting, a lane or a strategy that reads as applied and
   isn't. The fail-closed holds stop the worst cases; the screens still mislead. (O.)
3. **One person is the organisation.** Steering, transcripts, approvals and policy resolve to the creator or an
   admin, and every reader is also a member who can spend. Correct for a single-node pilot, wrong for five of the
   six people here. (C.)
4. **The proof isn't visible to outsiders yet.** Baselines, signed evidence, a verifier, a production-tier
   product, accepted residual risks and a data-flow statement are all on the plan (phases A–B, G1) or missing (F2,
   F3). Until then the buyer's and the CISO's journeys depend on a demo and a conversation. (C.)
5. **No expectations before work starts.** No estimate of time or spend at Create, though the targets (≤ 60 min
   p50, ≤ $10 per change) exist. (C.)

## Proposed fixes (Saravana's call; none changes a milestone)
**Before v0.1.0, inside phases already planned.** These are what a stranger meets first:

| Fix | Journeys | Phase it fits | Size |
|---|---|---|---|
| README rewritten to the vision **with an honest "works live / built / planned" table** per stage, and design-only, teams, SSO and the product record marked as planned | A2, B1, C9, E | E (propose doing it in C, so the public repo stops mis-selling earlier) | ½ day |
| **A data-flow statement:** one page listing every destination (model provider or gateway, GitHub, Snyk, registries, the cluster), what each receives and the setting that turns it off | F3 | E, with the licence audit | ½ day |
| **Residual risks accepted or closed:** each ADR-0048 residual with an owner, reason, scope and expiry, or a fix | F2 | A (with the slipped security groups) | Saravana's decision, then ½ day |
| Install for Linux next to the Mac path, and the release rehearsal run by someone who hasn't used the factory, timed | A3, A4 | E | ½ day + the run |
| **Upgrade honesty:** one banner listing every factory lane behind its template, "Restore all factory defaults" naming each change (UX-25); unique display names (UX-23); upgrade confirms the server's sandbox state (UX-03) | A10, D8 | A or B (hardening) | ~1.5 days |
| **Saved vs effective** on the product page: lane, release strategy and target, requested vs effective, before Start (UX-05, UX-08, UX-21) | C7 | A or B | ~1 day |
| Products empty state: "Start with a dry-run product (no model usage)", and at Create the change's budget cap plus the baseline's typical time and cost once G0 exists | A6, A7, C2 | after G0 (phase A) | ½ day |
| A **viewer** role (reads everything, steers and creates nothing) | E1, F4 | C or D (beside SSO); touches `auth` and `tests/test_access.py` | ~1 day |
| Buyer's evidence path: baselines on Outcomes, the verifier linked from the evidence panel, the production-tier product as the demo | B2–B4, F5 | A, B, G1 (already planned: make the demo walk through them) | — |

**Telling people the factory needs them: in two steps (revised after the PR #192 review).** An outbound notifier
(Slack, Teams, a webhook or email) is an integration, and the stations-and-teams rules say no integration ships
before the secret store (ADR-0038), and that each integration declares what leaves the factory. So:
- **Step 1, before v0.1.0, nothing leaves the factory:** the factory's own UI says it. A count of pending
  items in the browser tab's title and favicon on every page, and an optional browser notification (the Web
  Notifications API, permission asked once) when a new attention item appears, carrying only "Agent Software
  Factory: N items need you". No credentials, no outside destination, no product data in the notification, so the
  integration rules don't apply. It helps only people with a factory tab open (C3 and D2 from High to Med for
  them; E6 unchanged). ~1 day, reading the `/api/attention` the UI already polls.
- **Step 2, after the secret store (slice 1), as the first integration under the rules:** a webhook or email per
  team, its URL or SMTP credential a secret reference, with a **declared payload**: by default only the kind of
  action, a product id and a link back (no titles, requirements, findings, code or people's names), richer
  payloads an explicit setting of the owning security team, every send recorded. It joins the rota and escalation
  (slice 2). Sized with the slice, not as a day: the secret store, the data declaration and the audit are the work.

Recommendation: step 1 in phase A or B; step 2 as the first integration of slice 3, not before.

**After v0.1.0, already planned:** teams, rota and station workspaces (D1, D3, D5, C8; slice 2); the product record
and its role views (E2–E5, the executive's view; slice 4); SSO (B6, F4, phase D licensed); SIEM export (F5, phase D);
concurrent changes (B6, phase T); design-only and start anywhere (C9; slice 6); the full waiting-state model (C4;
L-4). Not planned anywhere yet: a pushed summary for readers who never log in (E6); worth a backlog entry beside the
product record.

## Validating this page
The scores are a starting hypothesis. They become evidence the way J1–J5's findings did:
- **The release rehearsal (phase E)** is run by someone who hasn't used the factory, following the README from the
  GitHub page. Each A-step is marked observed (with the time it took and anything that needed help) or not
  reached, and new findings go to [live-test-usability.md](live-test-usability.md).
- **The first pilot** walks the buyer, product owner, CISO and executive steps with the real people; each C or I
  finding here is confirmed, rescored or dropped.

Measures (proposed), beside the north star:
- **Time to first dry-run product** for someone new, from clone (target ≤ 20 minutes), at the rehearsal.
- **Time to first live delivery**, same person (target ≤ 2 hours including set-up).
- **Time to notice:** from an attention item appearing to a person acting on it (Outcomes has the waiting time;
  this splits noticing from deciding), before and after each notifier step.
- **Unplanned explanations:** every time a pilot user needs someone to explain a screen, logged as a finding.

## Open questions for Saravana
1. Notifier step 1 (in-app, nothing leaves) in phase A or B, and step 2 with slice 3 as proposed?
2. The README rewrite and the data-flow statement: phase C, or keep them in phase E?
3. Upgrade honesty, saved-vs-effective and the viewer role: before the release, or after?
4. ADR-0048's residual risks: accept each (owner, reason, scope, expiry) before v0.1.0, or fix them first?
5. Who runs the release rehearsal as the first-time user (Priya, or a consulting contact)?
