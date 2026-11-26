# Plan: the factory UI, simple by default (design review 2026-11-26)

Status: **proposed**. Placed in **phase D, before the release** by Saravana (2026-11-26): mockups first, two or
three directions to choose from. The direction is still his call (open questions at the end).

## What was done
The UI on main `25156de` was run in dry-run with sign-in off (`AUTH_MODE=off`, the single-user admin "local"),
with one product per stack pack, and every route was screenshotted at desktop (1440 × 900) and phone (390 × 844)
width. `web/src/App.tsx`, every page and `LoginPage.tsx` were read. A script compared the API's response models
(`web/src/api/schema.d.ts`) with what `web/src` reads, and the config's top-level sections with what any page
shows. The scores follow [user-journeys.md](user-journeys.md): **O** observed here (a screenshot or a measurement),
**C** code-confirmed, **I** inferred. Nothing was shown to a first-time user; the release rehearsal (phase E) is the
first chance to watch one.

The mockups are a private Design canvas, one page per direction, each with the home page after sign-in, a product
page (with a working Simple / Advanced switch), the sign-in page and the public site; every artboard has a light and
dark toggle under Tweaks: [Factory UI redesign directions](https://claude.ai/artifact/7FwPpb3U3bbknRqE841RjU)
(Saravana's account; share it from its page before anyone else can open it).

## What a person meets today
The factory works; the UI shows every part of it at once. Each page is written for the person who built the
engine: engineering words on the first screen (lane, Level 3, pillars, loops, "model no auth"), several calls to
action competing on one page, and settings and admin tools in the same navigation as daily work. Simple things
are there, but nothing is simple by default.

### The shell and navigation
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-01 | The header carries five status pills in engineering words: `dry-run`, `model no auth`, `github on`, `readiness degraded`, `0 active` | O (every screenshot) | One health dot ("Ready" / "1 thing to check") linking to Integrations; dry-run becomes a plain banner, "Practice mode: agents are simulated, nothing is built, deployed or charged", with a "Go live" link (also user-journeys A6) |
| UI-02 | Daily work (Products, Outcomes) and factory settings (Workflows, Skills, Integrations) share one flat list | O | Two groups: **Work** (Home, Products, Insights) and **Factory** (Workflows, Skills, Integrations, People). A member sees only Work; the Factory pages are admin work already (`tests/test_access.py`) |
| UI-03 | With sign-in off, the user menu offers "Account & API tokens", and both `/account` and `/admin` show only "Sign-in is not enabled on this factory (local mode)": a dead end. Admin is hidden from the nav for the same admin | O (`desk-account`, `desk-admin`) | In local mode, hide Account and say once, in Factory settings, what local mode means and how to turn sign-in on |
| UI-04 | The UI asks for `/auth/me` even with sign-in off; without the auth service every page logs a console error | O (vite proxy log) | Ask only when the engine says sign-in is on (`/api/health` or config) |
| UI-05 | `/orders/:productId` and `/line` still redirect (from before ADR-0029); ADR-0037 started fresh and no public user has those links | C (`App.tsx`) | Remove both before v0.1.0 |
| UI-06 | "API reference ↗" sits in the main sidebar; the brand block wraps to three lines | O | Move it to a Help item in the user menu; one-line brand |
| UI-07 | The factory UI is tested only at 1280–1300 px; Outcomes overflows a phone (`scrollWidth` 677 at 390) | O (measured), C (`web/e2e`) | Every page fits 390 px with no horizontal page scroll, checked in `web/e2e` (also user-journeys C5) |

### Home (today the Products page)
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-10 | The "New product" form is always open beside the list, with a second "New product" button above it; on a phone the form is below the list | O (`desk-home`, `phone-home`) | Home answers "what needs me?": the pending actions (`attention.pending_actions`) first, then the products. New product is its own focused page |
| UI-11 | Four count tiles (Products, In progress, Awaiting feedback, Need attention) repeat the list's statuses and fill a phone's first screen | O | Counts become filter chips on the list |
| UI-12 | Choosing a stack is the first decision in a business person's form | O, C (`StackCards`); user-journeys C1 | Ask what it is in plain words ("A web app people use", "An API other software calls", "Improve a repository I have"); the stack card, workflow and spec upload move under Advanced |
| UI-13 | "Existing repo" is a small tab at the top of the same form, though onboarding a repo is a different journey | O | A separate start: "Bring a repository" |
| UI-14 | The empty state doesn't invite a free first try; nothing says how long a change takes or what it costs before Create | C; user-journeys A6, A7, C2 | An empty home with a sample request in practice mode; a line under Create from the engine ("usually about N minutes; in live mode about $N"), computed from Outcomes, never guessed in the page |

### Product page
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-20 | Three calls to action compete: "Nothing waits on you", the Next iteration card, and Accept / Close | O (`desk-product-*`) | One "What's next" card from `attention.pending_actions`; everything else is secondary |
| UI-21 | "delivered · awaiting feedback" appears three times | O | Once, in the title row |
| UI-22 | Retire is a prominent button at the top right | O | Under a "More" menu with Share and Settings; retiring keeps its recorded steps |
| UI-23 | The Overview tab is still dense and uses lane, loops, Level 3, pillars and agent-readiness | O; user-journeys E3 | Simple shows: the app link, a status sentence, what's next, running / backups / spent, and the versions. Stations, pillars, readiness level, workflow, work items, agent calls and evidence files move to Advanced |
| UI-24 | The engine knows things a person needs but the page never shows them: when a paused change resumes (`Pause.resume_at`, user-journeys A9 "looks hung"), budget spent so far (`Change.budget_spend_usd`), how it will deploy (`Product.deploy_strategy`, user-journeys C7), who approved the spec (`Change.spec_approved_by`), whether the PR can merge (`PullRequestState.mergeable`, `merged_by`), the incident timeline (`IncidentResponse.opened_at`, `restored_by`, `resolved_at`, user-journeys D7), when retiring started (`Retirement.started_at`) | C (schema scan) | Show each where its reader looks: resume time on the waiting banner, spend beside the budget, strategy on the release card, approver in the spec gate, merge state on the PR card, the timeline on the incident |

### Insights (today Outcomes)
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-30 | Six KPI tiles and engineering tables (pillars, agent effort by station) on the first screen | O (`desk-outcomes`) | Lead with three numbers against their baselines (lead time, change failure rate, cost per delivered change; baselines from phase A); the rest under Advanced |
| UI-31 | The period isn't stated: `OutcomesView.window_days` and `generated_at` are never shown | C | "Last N days · updated M minutes ago" under the title |
| UI-32 | `fix_loops_per_delivery` is computed and never shown | C | Show it under Advanced or stop computing it (Saravana's call) |

### Factory settings (Workflows, Skills, Integrations)
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-40 | The Workflows list explains itself with "(ADR-0039)" | O (`desk-workflows`) | No ADR numbers in UI text; a "Learn more" link to docs |
| UI-41 | The workflow editor is one page 4,410 px tall: phase board, spec review gate, design decisions, an evaluation gate with editable suites, learn from changes, start from a template or GitHub, agents, reference docs | O (`desk-workflow-edit`); UX-23, UX-25 | Tabs: **Stations** · **Approvals** (spec gate, design decisions) · **Agents** · **Docs** · **Evaluation**; a sticky Publish bar; "start from a template / import from GitHub" moves to a "New workflow" flow on the list |
| UI-42 | The Skills page opens with the GitHub import form; each skill row has three buttons | O (`desk-skills`) | "Import a skill" opens a dialog; a row opens the skill, actions in its menu. Usage per skill belongs to phase I's skill upkeep |
| UI-43 | Integrations lists every readiness check for every stack (22 rows, almost all green) before the integrations | O (`desk-integrations`) | A summary first ("Everything a change needs is ready · 1 warning", the warning shown); the per-stack checks open on demand; Environments under Advanced |
| UI-44 | Most config sections have no page at all: `budgets`, `limits`, `gates`, `policies`, `change_risk`, `model_gateway`, `sandbox`, `preflight`, `models`, `checks`, `timezone`. An admin can't see what the factory is set to without reading two YAML files | C (config, `web/src`) | A read-only **Factory settings** page: each effective value and where it came from (shipped default or `config.local.yaml`). Editing stays in the file (phase B) |
| UI-45 | API fields the UI never reads: `ConfigView.repo_blueprints`, `CatalogView.handlers` / `handler_info`, `Product.node_port`, `Product.eval_run_id`, `EvidenceKeyView.public_key`, `EvalRun.suite_hash` and others | C (schema scan) | Decide each: show it (the evidence public key belongs beside "Check it anywhere"), or drop it from the response model. Fields for API clients stay |

### Sign-in
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-50 | A small card on gradient blurs; the help line tells every user to run `make reset-admin` on the host | C (`LoginPage.tsx`) | One sentence on what the factory does; "Continue with your company account" when SSO is configured (phase D's SSO slice), the local form below; "An admin can reset your password" for everyone, the host command in the runbook |

### The public site (new)
| # | Finding | Evidence | Proposed |
|---|---|---|---|
| UI-60 | There is no public site; the README is the front door and sells a laptop tool (user-journeys A2) | C | A small static site from [vision.md](../vision.md): the headline, the journey from inception to retirement, three proofs (the engine judges, a record you verify yourself, it runs what it builds), the quick start in practice mode, open source and enterprise side by side, Security, Licence, Contributing. Built in phase D, published with v0.1.0 in phase E |

## The rules the redesign keeps
1. **One question per page.** Home: what needs me? Product: how is it, and what's next? Insights: are we getting
   better? Factory settings: is the factory set up and healthy?
2. **Plain words in Simple, engineering words in Advanced.** Simple never shows an ADR number, a station handler, a
   lane, "Level", "pillar" or a raw status key. A test renders each page in Simple and fails on a word list.
3. **One primary action per screen**, chosen by the engine (`attention.pending_actions`), never by the page.
4. **Advanced is a switch, not a second app.** A per-user "Show engineering details" switch (kept in the browser for
   v0.1.0) reveals the extra tabs and panels in place; links and URLs don't change.
5. **Works on a phone** at 390 px with no horizontal scroll, in light and dark, checked in `web/e2e`.
6. **Nothing hidden that a person must act on.** Advanced holds detail, never a pending action.

## Three directions (the mockups)
- **A · Quiet.** Light first, a top bar instead of a sidebar (Home, Products, Insights; settings behind the gear),
  one column, a health dot, the practice banner. Calm and readable for product owners and executives; engineers
  switch on Advanced.
- **B · Console.** Today's dark app refined: the sidebar split into Work and Factory, dense tables, ⌘K, one health
  line instead of five pills, Advanced adds tabs (Stations, Agent calls, Work items). Cheapest to build; best for
  engineering teams who live in it.
- **C · Guided.** Home opens on "What should the factory build next?" with three plain starts; the stack is chosen
  for you (editable under Advanced). A product reads as a story from request to live app, with one "What's next"
  card. Most inviting for first-time users and the public release; the most work.

All three share the navigation, the Simple / Advanced rules and the fixes above; they differ in look and in how much
the home page leads with a request. A and C can share one implementation of the shell with different home pages.

## Slices (phase D, by 2026-12-14, web only, beside the paid features)
1. **Shell:** the two nav groups, the health dot and practice banner, local-mode user menu, `/auth/me` only when
   sign-in is on, the old redirects removed, the Simple / Advanced switch, the plain-words test, `web/e2e` at
   390 px in light and dark for every page. (UI-01 to UI-07)
2. **Home and New product:** pending actions first, filter chips, the plain-words start with Advanced, "Bring a
   repository", the empty state, the engine's estimate. (UI-10 to UI-14)
3. **Product page:** one "What's next" card, the More menu, Simple and Advanced content, the fields in UI-24.
4. **Insights:** three numbers against baselines, the period, phone layout. (UI-30 to UI-32)
5. **Factory settings:** workflow editor tabs, the skills dialog, the integrations summary, the read-only Factory
   settings page, the unused-fields decisions. (UI-40 to UI-45)
6. **Sign-in and the public site:** the sign-in page with the SSO slot (the button joins phase D's SSO work), the
   static site in the repo, published in phase E. (UI-50, UI-60)

**Done when:** every page fits 390 px without horizontal scroll, light and dark, in `web/e2e`; no page in Simple
fails the plain-words test; each page has one primary action from the engine; the release rehearsal (phase E)
creates a first product from the home page in practice mode without the docs. The engine changes only where a
finding needs a value it doesn't serve yet (the estimate in UI-14, the period in UI-31, `/auth/me` in UI-04).

**Risk to phase D:** four paid features already share the phase, and the compliance pack is the first to slip
([open-source-release.md](open-source-release.md), Risks). These slices touch only `web/` (and the small engine
values named above), so they run beside them; if the phase is short, slices 1–3 come first and 4–6 may follow in
phase E before the rehearsal.

## Open questions for Saravana
1. Which direction: A, B, C, or a mix (for example B's sidebar with C's home page)?
2. Advanced as a per-user switch (proposed), or only "Details" sections per page?
3. The stack in plain words on the create form, with the stack cards under Advanced: agreed?
4. Light or dark as the default for a new install?
5. The public site: a GitHub Pages site under the project now, or wait for the name and trademark (phase E)?
6. Remove the `/orders` and `/line` redirects for v0.1.0?
