# Agent Factory: build plan

Updated 2026-11-01 (J2 slices 4 and 5 merged; J2 gains validate, promote and roll back, ADR-0046 accepted by
Saravana; rollback slice 1 merged, #131; slices 2 and 3 merged, #132; slice 4, blue/green per product, in review).
**This is the one page to read for "what's next".**
- How the plan is kept (and kept from drifting): [README.md](README.md).
- Why this plan: [critique-2026-10-21.md](critique-2026-10-21.md) (critical review, journey step by step).
- Long-term vision and principles: [master-plan.md](master-plan.md).
- Current working state and how we work: [handoff.md](handoff.md).
- How we judge the work: [docs/practices.md](../practices.md).
- Decisions: [docs/adr/](../adr/) (stack packs: ADR-0041; failure classes: ADR-0043; design decisions: ADR-0044).

> **Who edits this page (decided 2026-10-26, replaces the single-owner session of 2026-10-21):** any
> session, in the same branch as the work it describes (README rule 2). Ticking items, adding status and
> adding backlog entries need no one's agreement; reordering milestones, changing a "Done when" or lifting
> the freeze needs Saravana's, recorded with its date. Concurrent edits meet as git conflicts: keep both.

## 🎯 North star: the golden journey (decided 2026-10-21)
The factory must show **the whole life of a product**, live, end to end, on a product worth demoing:
launch → design choices agreed → build a good product → verify → publish → deploy → iterate safely →
team repo PR → run and maintain → end of life.

- **Freeze:** no new ADRs and no new control surface until J5 has run live once. Only the ADRs the
  milestones below need (ADR-0043, a trimmed ADR-0044, ADR-0046 for J2) go ahead.
- **Each milestone ends with a live run** whose sealed evidence is kept as the demo record, and a short demo.
- **A journey acceptance test** (scripted, dry-run, in CI) grows with each milestone, so a working step
  never silently breaks. J1 steps merged 2026-10-27 (`engine/tests/test_journey.py`, `fd81b9c`); each
  milestone adds its steps there when it lands.
- A new idea that isn't on the journey goes to the backlog.
- **Ready for, proven, owned** (decided 2026-10-31; [master-plan.md](master-plan.md) principle 7): every
  delivered product states what it is ready for, proves it, and has an owner for what happens after delivery.
  The delivery outcomes behind it (repeatable quality, then safe operation) are in the "Done when" of J1, J2
  and J4; team delivery and the improvement loop are the two phases after J5. The J-order and the freeze stay.

Journey at the critique (main `9604e60`): 7 of 23 steps live, 9 built but unproven, 7 missing.
**Now (main `a827cd4`, 2026-10-25): 9 live** (+ e2e before deploy, + publish to GitHub), **4 built but
unproven** (design sheet, align with the architect, modules and sign-in, the chart's admin Secret),
look and feel in progress, and J2–J5 not started.

## ⏭ Next, in order
**J0. Finish the open thread** ✅ folded into the J1 live run (2026-10-22). The evaluation harness can't run
pack cases yet (it always builds with the first blueprint), so the Next.js cases ran as ordinary products;
per-pack evaluation is in the backlog.

**J1. Launch, done well** (started 2026-10-21; about 3 days left: three small PRs, then the acceptance run)
1. ✅ **ADR-0043 PR 2, e2e before deploy** (`936bfca`, nextjs-web 1.1.0; loopback fix `d161b28`, 1.1.1).
2. **ADR-0044, trimmed to J1 and agreed 2026-10-23** ([docs/adr/0044-design-decisions-modules.md](../adr/0044-design-decisions-modules.md)); four PRs:
   - ✅ **PR 1, decision sheet** (patch 0049, `ce49828`): the pack's catalogue, options, recommendation,
     pillar rationale, "Other…"; lane setting `decisions: autonomous | ask` with `decision_constraints` per
     question; engine-checked; answers become ADRs in the product repo; per-product pillar roadmap folded
     into the ledger.
   - ✅ **PR 1b, align with the architect** (patch 0050, `eb60339`, `d5df28b`): intention → spec → plan; open
     questions go to the architect, not back to the requester; the person's choices, answers and comments
     go to the architect together in one turn; a requirements change goes back to intake; one approval on
     the align panel. Autonomous lanes never stop for questions.
   - ✅ **PR 2, modules and sign-in** (`a827cd4`, nextjs-web 1.3.0, merged 2026-10-25): auth, authz,
     logging, metrics and tracing behind ports, adapters and contract tests; local accounts with sign-in
     pages; first admin from the chart's `<release>-admin` Secret; live e2e and the verifier sign in (by env
     name, redacted); signals `modules_isolated` and `module_contracts`; **Design decisions** setting in the
     workflow editor.
   - ✅ **PR 3, look and feel** (`8a94f8b`, nextjs-web 1.4.0, merged 2026-10-26; follow-ups `69d9fe3`,
     `008a09d`, `c5ec9b0`): Tailwind + shadcn/ui, shared tokens, designer agent, screenshots per route,
     accessibility and interaction states, UI review reporting only. Folded in: the factory's product page at
     phone width, a **"Needs you"** panel, collapsible panels with summaries, engine-decided **event levels**,
     and the name **Agent Software Factory**.
   - ✅ **Approved base images** (ADR-0045, `66054c0`, nextjs-web 1.3.1): approved images, a fallback at Build,
     factory-wide image risk decisions. (A new ADR during the freeze: accepted because base-image CVEs held
     every live run.)
   - ✅ **Live e2e reaches the app** (`9564283`, nextjs-web 1.3.2): Playwright ignores `HTTP_PROXY`, so the
     template sets `use.proxy` when `BASE_URL` isn't loopback. Found by Book lending (2026-10-25), whose three
     `ENOTFOUND` failures were routed to the developer as work and held the change after ~$4.90.
   - ✅ **Planning in the repo** (`0794b67`, merged 2026-10-26): `docs/planning/` is the only copy of the plan,
     checked by `engine/tests/test_planning_docs.py`, so any agent on any account can pick up the work.
   - ⏳ **Before the acceptance run (~2.5 days):**
     - ✅ **No-progress stop and target probe (ADR-0043 follow-up; `9888491`, merged 2026-10-27):** the same failure (fingerprint) at the same station on the same committed tree, after a
       repair that changed no code, holds with the cause and what the repair agent said instead of looping
       (`RoutedFailure`, `failures.failure_fingerprint`); before live e2e and the verifier, acceptance probes the
       app's liveness path from the sandbox the way the test clients reach it (through the egress proxy, or
       directly for loopback), and an unreachable target is `infra`: retried in place, then held, never sent
       to the developer. Tests in `tests/test_no_progress.py`. Later, optional: classifier rules for
       `ENOTFOUND` / `ECONNREFUSED` on the target host; the golden-path skill stops the preview by pid, not `pkill`.
     - ✅ **Journey acceptance test (`fd81b9c`, merged 2026-10-27):** a scripted
       dry-run of the J1 flow, run by CI's engine tests (`engine/tests/test_journey.py`): one Next.js product
       driven through the HTTP API as a person would: Design decisions set to ask in the editor → request →
       review gate under "Needs you" → align (a choice, an open-question answer, a comment) → approve →
       Level 3 with the module signals → e2e against the preview before build → live e2e signed in as the
       chart's admin, after the target probe → publish to the org → sealed evidence. Each assertion names its
       journey step (J1.0–J1.7), so a failure says which step broke; it grows with each milestone.
     - ✅ **Pin the scanner images (`5d2c42a`, merged 2026-10-28):** Trivy and syft
       off `latest`, pinned by version: `aquasec/trivy:0.76.0` in the image scan, the config check (`tools.py`)
       and CI; `anchore/syft:v1.54.1` for the SBOM (the versions `latest` served on 2026-10-28, so findings
       don't move). `tests/test_scanner_pins.py` fails on a moving tag and on two Trivy versions (they share
       the `trivy-cache` DB). **Digests** (`823d72b`, merged 2026-10-28): index digests resolved by Saravana on
       the Mac, appended to every reference (`aquasec/trivy:0.76.0@sha256:2416d1f7…`, `anchore/syft:v1.54.1@sha256:3eb5379b…`);
       the test now requires a digest. This is the
       slice of "upgradable selections" J1 needs; the rest stays in the backlog.
3. ✅ **Publish to GitHub live** (2026-10-22): a dedicated GitHub org as `policies.publish.owner`, with a
   fine-grained token (Administration, Contents, Workflows read/write).
4. ✅ **First live run** (2026-10-22): "Book keeping" (nextjs-web 1.1.x): 9/9 requirements verified live by
   hidden scenarios, review 9/9, Level 3, published, $3.64. It found four factory bugs, all fixed and
   merged: preview probe blocked by the egress proxy (`d161b28`); git "dubious ownership" on the Mac mount
   widened the scan scope (`661badc`); image and tool scans bypassed infra retries (`d547252`); dind kept a
   stale base image, so builds now `--pull` (`dcf8c71`). Also merged: the product's Spend card and
   iteration summary (patch 0048, `40c8e8e`).
5. ✅ **Book lending, two live runs (2026-10-25 to 2026-10-27; not the acceptance run):** built on nextjs-web
   1.3.0, before the new look (1.4.0), the target probe and the no-progress stop. **Proved live:** a person
   choosing a design decision on the review page (look: warm) and the architect aligning to it; sign-in as the
   app's admin from the chart's `book-lending-admin` Secret on the cluster; 6/6 hidden scenarios live; Level 3
   (71/71); published to the org; a second iteration (`234341c4a9c5`, audit log and member erasure, $5.70)
   delivered and pushed to the product's `main` (by design: the factory owns its products' repos; branches and
   PRs are for team repos, J3). **Bug found and fixed** (`9013b79`, merged 2026-10-28): the iteration's autonomous design check made the architect take its recommendation,
   so the person's **warm** look became **neutral**. Now a person's earlier answers stand in autonomous mode
   (lane constraints still win) and keep their name in the ADRs.
6. ⏳ **J1 acceptance run** after PR 3, with Design decisions set to **Ask a person**: proves the align flow,
   modules, sign-in with the chart's admin Secret on the cluster, the target probe and the no-progress
   stop, and the new look, on one product. The journey acceptance test is its dry-run twin.
   **Run 2026-10-28/29: "Kudos board"** (product `5ff547d402cb`, change `75fd61391452`, nextjs-web 1.4.0,
   delivered, $6.38). **Proved live:** the review gate and alignment (the person changed the look to
   neutral-compact), sign-in from the chart's `kudos-admin` Secret, the target probe, the scanner digests, a
   hidden scenario catching a real bug (duplicate account 500 → 409, fixed by the loop), 11/11 requirements
   verified live, Level 3 (71/71), published and deployed. **Not met:** "a polished responsive UI": UI review
   3.4/5, responsive 2/5, controls about 28–30 px tall against R9's 44 px (the UI review only reports; the
   verifier uses curl). The no-progress stop didn't trigger (nothing to stop). **Factory bug found:** the Mac
   slept 1h04 during `make preview`; the preview never answered and went to the developer as code work (~15
   min, no change). Fixed (`6438826`, `d3fec2b`, merged 2026-10-29), broadened (agreed 2026-10-29: not about the
   Mac, about whether the environment is available, wherever the factory runs): after a failed command, the
   factory checks what it depended on (host sleep, Docker disk, sandbox, the environment's registry, deploy, scan
   and publish integrations) and retries or holds when something is down, instead of sending it to the developer.
   **Iteration 2, 2026-10-29** (change `d5e3de869ac0`, feature, product-change lane, autonomous decisions, $6.02,
   54 min): "every control at least 44 px on phones, keep the look". **Proved live:** the person's look stayed
   *neutral-compact, decided by admin* (decision sheet unchanged, `9013b79`); UI review 4.0/5, responsive 2 → 4
   ("controls reach 44 px, nothing scrolls sideways at 390 px"); the environment check's note reached the
   developer on a real e2e failure ("…checked what the command depends on (Agent sandbox; Docker disk space) and
   it is all available", `d3fec2b`); a hidden scenario caught a real phone bug (a 60-character name made the board
   scroll sideways), fixed by the loop; 11/11 verified live; Level 3. Not exercised: the sleep retry (the Mac
   stayed awake). **Found:** (1) the cause is the template: the *neutral-compact* look scales spacing by 0.75 and
   the root font to 14 px, so `min-h-11` renders ~29 px in every product on that look (backlog: template 1.4.1);
   (2) the verifier has no browser and judged the phone layout from the CSS, estimating widths (backlog); (3) on
   phones the header became a ~110 px two-row block and no longer shows the signed-in email (a candidate first
   suggested feature for J2). **J1 status (2026-10-29):** Kudos board meets the "Done when" after iteration 2;
   a new product would still start with small controls until template 1.4.1. Closing J1 on this run, or after
   1.4.1 and a new-product run, is Saravana's call. J2 starts in the next session.
- **Done when:** a new product, built from scratch, is one Saravana would show a client: agreed design
  choices, auth on protected writes, a polished responsive UI, published repo, deployed, Level 3.
  **And repeatable (decided 2026-10-31):** a second product of a different shape, built from scratch on
  template 1.4.1, meets the same bar **on its first delivery, without an iteration**, with tap targets measured
  by the pack's e2e so a miss fails the build. Kudos board alone needed iteration 2, so J1 stays open until then.

**J2. Iterate safely (~4.5 days; ~10 with validate, promote and roll back, decided 2026-11-01)**
- **First, plan the next iteration (~1.5 days; agreed 2026-10-26).** Today a delivered product continues
  through the feedback box or by ticking items in the Work items panel (pillar roadmap deferrals as
  "recommendation" items, image CVEs), but nothing suggests the product's **next features**, and the panel
  sits further down the page with assessment wording.
  - **Suggested features:** at each delivery, the **architect** proposes next features (from out-of-scope
    requirements, "later" answers to open questions, and obvious next user needs) and the **designer** adds UX
    improvements from the route screenshots; 3–5 in total, ranked by value, each with rough size and the
    requirements it would add. They go into the ledger in a new **`feature`** category, to accept, reject
    (with a reason) or leave for later.
  - **A "Next iteration" card in "Needs you"** once a change is delivered, grouped as Features (suggested),
    Quality (deferred pillar items) and Security fixes (open CVEs with a fix); tick, optionally add free text,
    **Start the next iteration**. The feedback box and the work items become this one flow (wording no longer
    says "assessment" for factory products).
  - **The engine picks the change kind** (pure, tested): CVE-only → upkeep; any feature → feature, carrying
    the fixes.
  - 🔄 **Build started 2026-10-28, alongside the J1 acceptance run** (asked for in the session that day; the live
    J2 run still waits for the J1 product). **Slice 1, merged** (`3a92577`): pure ledger
    functions, no API or web change: `parse_suggestions` (1–5, each with value, size and the requirements it
    would add), `fold_features` (category `feature`; a rejected suggestion stays rejected), `next_iteration`
    (features, quality, security fixes with a released fix), `pick_kind`, and a delivered feature closes on
    delivery (its acceptance verified it). Tests: `tests/test_next_iteration.py`. **Slice 2, merged** (`94ec787`,
    `b6538a6`): `suggest.py`, after each delivery of a factory product (like the retro:
    never delays or fails it), the architect (from the spec's out-of-scope, "later" answers and next needs, up to
    4) and, when the change kept route screenshots, the designer (UX improvements from them, up to 2) suggest
    next features, observe-only; the engine vets each one (`suggest.vet`: valid, no duplicate, best value first,
    top 5) and folds them into the work ledger; the prompts list earlier suggestions, so a rejected one isn't
    suggested again and an open one keeps its title. Tests: `tests/test_suggest.py`. **Slice 3, merged** (`f21ebdb`): `FeedbackInput.kind` optional: left out, the engine picks it from the ticked items
    (`ledger.pick_kind`; the feedback event records `kind_by` engine or person); `GET
    /api/products/{id}/next-iteration` (`NextIterationView`: features, quality, security, `can_start`, the
    latest change and its status). OpenAPI regenerated; `schema.d.ts` mirrored by hand (npm blocked), CI's web
    job confirms it. **Slice 4, merged** (`4c19e26`, 2026-10-31): the "Next iteration"
    card (`web/src/components/NextIteration.tsx`) right under "Needs you" on a factory product's page, from
    `GET /next-iteration`: Features (value and size), Quality and Security fixes to tick, optional free text,
    **Start the next iteration** → `POST /feedback` without a kind (the card previews the kind the engine will pick).
    Hidden while a change runs or is held. It replaces the change panel's feedback box for factory products (team
    repos keep their pull-request box); "Needs you" now points a delivered factory product at the card
    (`attention.py`, panel `next-iteration`); the work ledger no longer says "assessment" in its change text. The
    CI smoke test starts iteration 2 from the card. Not built or screenshotted in the session (npm blocked in the
    cloud and on the Mac's agent shell): CI's stack build and smoke test are the check.
    **Iteration 3 live, 2026-10-31** (Kudos board, change `2d17c4f736c8`, feature, delivered 17:08, ~$4.9): "on
    phones, show the signed-in email in the header; keep the header to one row" (R12). Live e2e failed once and the
    loop fixed it; 6/6 hidden scenarios, Level 3, published. **The first suggested features landed** (architect 3,
    designer 2: e.g. "Show Accounts only to admins", "Totals for this month"). Found: (1) Saravana found the page
    confusing (2026-10-31): two places started an iteration (the card and the Work items panel, which has its own
    kind dropdown, so this iteration was started there with the kind chosen by a person), empty groups vanished
    without a reason, nothing said what would start, and the card disappeared while a change ran; (2) the designer
    found no live screenshots (`screenshots/live/` missing) and suggested from `docs/ui.md` instead (backlog);
    (3) the host slept 5 min during implement (retried fine).
    **Slice 5, merged** (`61096f5`, 2026-10-31; layout decided by Claude at
    Saravana's request, his review of the feel to follow): one place starts an iteration. On factory products the
    Work items panel has **Add to next iteration** (ticks the item on the card) instead of its own start button
    and kind dropdown; team repos keep "Make a change". The card shows all three groups, an empty one says why
    ("None yet: suggested at each delivery", "No open vulnerabilities with a released fix"); long groups show 3 and
    "show N more"; a summary above the button says "Iteration N will be a feature (…): 1 feature, 1 security fix";
    while a change runs the card says "Iteration N is running" with its kind, what was asked and **Follow it**.
    `NextIterationView` gains `latest_iteration`, `latest_kind`, `latest_request`. Built and screenshotted at
    desktop and 390 px against a dry-run engine.
    **Next:** Saravana reviews the page; then iteration 4 on Kudos board from the card with a suggested feature
    (and a CVE fix if one is open: none was at iteration 3, so that half is his call), then rollback.
- Feedback → change → redeploy, run live on the **J1 acceptance product** (decided 2026-10-25: built on
  nextjs-web 1.4.0 with modules and the new look; not Book keeping, which predates modules).
- **Iterations keep agreed design decisions** (fixed 2026-10-28, see J1 item 5): the J2 live run checks that
  a person's earlier answers survive iteration 2 unchanged.
- **Validate, promote, roll back** (decided by Saravana 2026-11-01, replaces "rollback with `--atomic`";
  [ADR-0046](../adr/0046-validate-promote-rollback.md), proposed): production is never where a change is
  developed, debugged or repaired. Four slices (~6 days):
  1. ✅ **Last healthy release and rollback** (~1.5 days, PR #131, merged 2026-11-01, `8680c44`): deploy saves its
     intent before touching the cluster, then the Helm revision and the running image digest; the release
     is recorded and saved when acceptance passes. A deploy or acceptance failure, and any stop short of
     delivery (hold, cancel, engine error, restart), restores it: diagnostics first, then, fail closed, `helm rollback` to
     the recorded revision only if the record is complete, names this cluster, namespace and release, and
     the revision is in the history with the recorded image; then history, image, rollout, digest, liveness
     and readiness verified. Anything short of that holds as `infra` for a person. No `--atomic`.
  2. ✅ **Temporary validation environment** (~2 days; built with slice 3, decided by Saravana 2026-11-01, since
     slice 2 alone would never update production on a `helm` target; PR #132, merged 2026-11-01, `72a7f7c`): each change
     deploys to its own release and namespace (`<slug>-c<id>`, own ingress host, empty data, its own admin
     Secret); live e2e, verifier and repair run there; removed when the change stops and on archive, with a
     start-up sweep; a resumed change deploys a new one before acceptance. The `local` NodePort provider, and a
     lane version without `promote`, validate in place, and the event log says why.
  3. ✅ **Promote the tested image** (~1 day, same PR): a `promote` station after acceptance deploys the same
     image to production, checks it runs the tested digest, probes and runs e2e there; a failure rolls back and
     holds for a person; only then is the release recorded. Tests: `tests/test_validation_env.py`.
  4. 🔄 **Strategy per product** (~2 days; `feat/j2-blue-green`, in review): `in-place` (default) or `blue-green`,
     defaulted by the pack. **Shape decided by Saravana 2026-11-01:** two colours in production's one Helm
     release sharing its database (the charts run Postgres in the release, so two releases would split the
     data). Charts nextjs-web 1.5.0 and fastapi-service 1.1.0 render a Deployment per colour (blue is the one the
     app always had); `promote` stages the tested image on the idle colour, checks its digest, probes and runs e2e
     at `<release>-next`, then switches the Service; the old colour keeps running, so a rollback to the recorded
     revision switches users back without restarting it. Products made before these pack versions, the `local`
     target, and changes validated in place are promoted in place, and the event log says why. A **Releases**
     panel on the product page sets it per product. Tests: `tests/test_blue_green.py`.
- An **accepted / closed** state for a delivered change, so Outcomes can tell done from waiting.
- **"Ready for" statement** (decided 2026-10-31): each delivery states its tier, **demo**, **pilot** or
  **production**, worked out by the engine (pure, tested) from the sealed evidence, plus what the next tier
  still needs. First cut: demo = Level 3 and requirements verified live; pilot adds a named owner and a proven
  rollback; production adds J4's availability target, alert and tested restore. Shown on the product page and
  in the evidence; a product with no owner can't be above demo.
- **Done when:** iteration 2 is started from the "Next iteration" card with a suggested feature and a CVE fix,
  and a deliberately broken iteration rolls back while the previous version keeps serving. **And** every
  delivered change states what it's ready for and what the next tier needs (decided 2026-10-31). **And**
  (decided 2026-11-01) a change is validated in a temporary environment that is gone when it stops,
  production changes only by promoting the tested image, and one product runs blue/green while another
  stays in place.

**J3. Team repo (~3 days)**
- **Live test** of the push/PR path (only tested against a fake `gh`): a throwaway copy of one of Saravana's
  repos with a small CI workflow; `existing_repos.write_token_ref` = a fine-grained token (Contents + Pull
  requests read/write on that repo only).
- **PR loop:** read checks, reviews and merge state (schedule + "Refresh"); red CI → fix on the same branch,
  same PR; review comments → a change that continues the PR (a member starts it); merged → change closed,
  ledger items "awaiting verification"; closed unmerged → abandoned.
- **Done when:** an assessment item becomes a PR, CI fails once and is fixed on the same PR, and the merge
  closes the change.

**J4. Run and maintain (~4 days; ~6.5 with the 2026-10-31 additions)**
- **First, a named product owner** (decided 2026-10-31; master plan §3J, still "later" in `models.py`): each
  product has an owner, its creator by default, reassignable by an admin; incidents, upkeep changes and the
  retire flow name the owner, and "Needs you" routes product items to them.
- **Availability target and alert per product** (decided 2026-10-31): the pack sets a default target (e.g.
  health success rate over a window), the product may tighten it; a breach is the alert that opens the
  incident below.
- **Product data backup and tested restore** (decided 2026-10-31): the pack declares the product's data and
  how to back it up; a scheduled backup, and a restore into a scratch release that the e2e reads back. A
  backup that has never been restored doesn't count.
- **A run survives a factory restart** (decided 2026-10-31): restarting the factory mid-change resumes the
  change at its station (durable state, idempotent stations), instead of losing or failing it.
- **A minimal Operate phase:** watch health and error rate from the app's `/metrics` and health endpoints;
  a breach opens an **incident** item in the product ledger; "Make this change" turns it into a fix change.
- **Scheduled upkeep:** dependency bumps and image rescans per live product; a new CVE or outdated
  dependency opens an upkeep change (confirmed 2026-10-26: rescans of live products stay here; until then
  CVEs are found at build and fixed by hand from the Next iteration card).
- **Done when:** an injected fault becomes an incident, a fix change and a redeploy without anyone opening a terminal.
  **And** (decided 2026-10-31): the product has a named owner and a declared availability target whose breach
  alerts; its data is backed up and a restore is proven; a factory restart mid-change resumes the change.

**J5. End of life (~2 days)**
- A **retire** flow: freeze changes, final sealed evidence, data export, undeploy, archive the product repo,
  a decommission record on Outcomes.
- **Done when:** the J1 product is retired and its full history, from first request to decommission, can be
  shown from the evidence.

## ⏸ After J5
Two named phases follow the journey (decided 2026-10-31); their order and "Done when" are set when J5 has run.

**T. Team delivery:** ownership, review responsibilities, concurrent work.
- Composable-SDLC step 2 (human modes, owners, separation of duty) and step 3 (start anywhere).
- Team permissions per product: members, reviewers and approvers by role; approval responsibilities recorded
  in the evidence; traceable handoffs between people and stations.
- Concurrent changes on one product and several products at once (needs J4's durable runs), with a merge rule
  when two changes touch the same files.
- Discuss mode at other person-owned stations (spec review, risk approval, assessment triage).
- **Done when (draft):** two people deliver two concurrent changes on one product, each reviewed and approved by
  someone other than its requester, with the handoffs in the evidence.

**I. Improvement loop:** real outcomes decide which workflow changes are adopted.
- A workflow, skill or model change runs on the representative product set (backlog) against the current
  version, and is adopted only if outcomes hold or improve (principle 3 applied to the factory's own workflows).
- Production outcomes feed back: incidents, rollbacks, escaped defects and user feedback per product, as trends
  on Outcomes.
- **Skill usage and upkeep** (planned 2026-10-21, ~3.5 days): usage roll-up per skill × role; drop/archive
  suggestions into the workflow draft (archived = hidden, kept for pinned versions, restorable); try-without
  evaluations for preloaded skills; skill gap report from the retro. The roll-up page alone (~1 day) can come
  earlier if it's wanted.
- **Done when (draft):** a workflow change is rejected by the evaluation on outcomes, and another is adopted
  with its before/after recorded.

**Other deferred work:**
- **The rest of ADR-0044:** rate limits, caching, flags, UI shell module; chart modules (HPA, PDB); UI review
  blocking delivery once the rubric is calibrated; FastAPI modules (products built before nextjs-web 1.3.0
  keep Level 3 until an upkeep change adopts modules).
- **Packs:** `react-spa` (built modular, with generated-app resilience: timeouts, retries on idempotent calls,
  graceful shutdown, reliability signals); per-pack evaluation gating; full-stack Next.js + FastAPI; static
  site, agent service, worker / data pipeline.
- **Control plane integrations beyond GitHub** (Jira, Slack, Argo CD, Grafana, …): after the journey proves one.
- Secret store (ADR-0038, [plans/secret-store.md](plans/secret-store.md)), then gated promotion (ADR-0039 Q2
  still open); cluster-target live check on `local-ingress`.
- 1d lane rebase from the standard; GitHub App; GitHub Issues intake; release hygiene; Settings → Packs page.

## ✅ Done (merged to main)
1. Workflows, lane builder, skills imported from GitHub and pinned.
2. Phases 1–2: pluggable delivery providers; secret references with redaction and audit.
3. Sign-in service, admin UI, API tokens; new UI with themes and ⌘K.
4. Agent sandbox with egress allowlist; Level 3 app template and readiness station; SBOM and provenance; threat model.
5. Readiness checks and preflight; archive; 20 app ports; OpenSSF Scorecard and Dependabot.
6. Upgrade safety: CI tests shipped images; full-stack dry-run smoke test; `make backup` / `restore` / `upgrade`.
7. Spec-driven development (ADR-0017).
8. Least privilege (ADR-0018); SQLite reads under the lock.
9. Outcomes page (ADR-0019).
10. Spec review station (ADR-0020).
11. Learning from runs (ADR-0021).
12. Agent observability (ADR-0022).
13. Access rules for every mutating action.
14. Evidence manifest (ADR-0023).
15. Quality pillars (ADR-0024).
16. Evaluation harness (ADR-0025).
17. Host sleep shown as "Host asleep".
18. Intake hardening.
19. Evaluation start fix.
20. Phase 5a cluster target (ADR-0026): `oci` + `helm` providers (live check still pending, deferred).
21. Composable SDLC 1a, 1b-1, 1b-2, 1b-3a lanes, 1b-3b phase registry and capability boundaries (ADR-0037, ADR-0039).
22. Assessment rollup; scanners read the whole report; activity log; product work ledger (ADR-0040); quiet checks.
23. 1c builder: phase board with station drawer, presets per lane, Full package restores the standard, evaluation gate only on new-build lanes.
24. ADR-0041 stack packs: the ADR; step 1 pack foundation with FastAPI extracted; step 2 stack cards and "switch to <pack>"; step 3 `nextjs-web` with `a11y`, and the engine reading health, e2e, tag rule, egress and toolchain from packs (`340acc1`).
25. Next.js image scan scope (patch 0038, `891c180`): distroless runtime image (nextjs-web 1.0.1); scanners read only committed files; change-risk holds scanner exclusions; CI builds and scans the template images.
26. Dependabot bumps of 2026-10-18 in one change (`a36316a`).
27. Image findings as evidence, routed by type (patch 0039, ADR-0042, `f527a40`).
28. Time budget per run restarts when a person approves, answers or resumes (`fix/wall-clock`, `4b320d2`).
29. Team repos: stack packs detected per folder, as guidance and advisory signals (patch 0040, ADR-0041 §5, `bb83015`).
30. First live Next.js product, Guestbook (2026-10-20; `guestbook-2`, nextjs-web 1.0.1, Level 3, 8/8 requirements verified live, $3.45; not pushed: no `GITHUB_TOKEN`). It showed: virtual keys expiring mid-call and routed as code failures; e2e fixes needing a full build → deploy loop; a base-image CVE needing two resumes; 16 harmless reviewer denials; scanners silently reading the worktree; and **product quality below the bar** (plain UI, no auth, no modules).
31. ADR-0043 PR 1, failure classes (patch 0042, `9604e60`): every failure is `work`, `infra` or `decision`; infra retried in place with backoff, jitter and a cap (3 retries ≤ ~7 min), never sent to the developer or counted in budgets; an expired key renewed by an immediate retry; a 45-min limit per agent call and a 15-min stall check; the time budget enforced inside a station; decision holds name the action (`accept_risk`); loud scanner fallback; the observe-only guardrail allows harmless read commands; infra retries on Outcomes; scans no longer skip a large repo's first files.
32. ADR-0043 PR 2, e2e before deploy (`936bfca`, `d161b28`).
33. J1 first live run, "Book keeping" (2026-10-22), its four factory fixes (`d161b28`, `661badc`, `d547252`, `dcf8c71`), and the product summary: spend by phase, iteration summary (patch 0048, `40c8e8e`).
34. ADR-0044 PR 1 decision sheet (0049, `ce49828`), PR 1b align with the architect (0050, `eb60339`, `d5df28b`), PR 2 modules and sign-in (`a827cd4`, nextjs-web 1.3.0).
35. Approved base images (ADR-0045, `66054c0`, `4fc2c6f`, `65efb2d`; nextjs-web 1.3.1); live e2e through the egress proxy (`9564283`, 1.3.2).
36. ADR-0044 PR 3, look and feel plus factory UI usability: "Needs you", collapsible panels, event levels, phone width, the name Agent Software Factory (`8a94f8b`, `69d9fe3`, `008a09d`, `c5ec9b0`; nextjs-web 1.4.0).
37. Planning in the repo (`0794b67`): `docs/planning/`, one copy, drift checked in CI; the chat project keeps only a pointer.
38. No-progress stop and e2e target probe (ADR-0043 follow-up, `9888491`).
39. Journey acceptance test, J1 steps (`engine/tests/test_journey.py`, `fd81b9c`).
40. Scanner images pinned by version and digest: Trivy 0.76.0, syft v1.54.1 (`5d2c42a`, `823d72b`).
41. A person's design decisions survive autonomous iterations (`9013b79`, found on Book lending).
42. J2 slice 1: suggested features in the ledger, the next-iteration groups and the change-kind rule (`3a92577`, pure).
43. The environment check: a failed command is checked against what it depended on (host sleep, disk, sandbox, the delivery environment's integrations) before it goes to the developer (`6438826`, `d3fec2b`).
44. J2 slice 2: next features suggested at each delivery by the architect and the designer, vetted and folded into the work ledger (`94ec787`, `b6538a6`).
45. J2 slice 3: the engine picks the change kind from the ticked items; `GET /api/products/{id}/next-iteration` (`f21ebdb`).
46. J2 slices 4 and 5: the "Next iteration" card, and one flow to start an iteration (`4c19e26`, `61096f5`).

## 🔄 In review
- `feat/j2-rollback` (2026-11-01): ADR-0046 (proposed) and J2 rollback slice 1, the last healthy release and rollback.

## 🗂 Backlog
- **Blue/green follow-ups** (added 2026-11-01, slice 4): migrations as a signal (both colours share the data, so
  a schema change must work with the version still running); an upkeep change that brings an existing product's
  chart up to its pack's version (Kudos board and Book lending predate colours); a "switch back" button needs
  a person-facing control, so it waits for the freeze to lift.
- **Readiness outcomes** (added 2026-10-31, from the capability review: discovery, design, engineering,
  delivery, operations, team collaboration, continuous improvement). Items the J-milestones don't already carry:
  - **Representative product set:** 3–5 product shapes (CRUD with sign-in, public read-only site, admin
    workflow with roles, data-heavy list and search, an agent service once its pack exists) as evaluation
    cases with hidden scenarios and visual checks; repeated runs per case. Feeds J1's second product and phase I.
  - **Visual standards enforced, not reported:** UI review blocking once calibrated against the product set
    (the rubric already scores; today it only reports); tap targets and sideways scroll at 390 px measured in
    the pack's e2e (template 1.4.1).
  - **Database migrations as a signal:** a schema change ships a forward migration, tested against the last
    release's data, and a rollback path; a release with an unmigrated schema change fails Level 3.
  - **Performance budget:** the pack declares a latency target for key routes; a short load smoke test in
    acceptance records p95 in the evidence (measured, not asserted by an agent).
  - **Staged releases:** canary on the cluster target, promoted on health, before production tier is claimed
    (with gated promotion, ADR-0039 Q2). Blue/green moved into J2 (ADR-0046 slice 4, decided 2026-11-01).
  - **Interruption recovery beyond host sleep:** a killed agent call, a lost sandbox and a cluster restart each
    resume or hold with a cause, tested (the environment check covers host sleep and disk today).
  - **Defect trends on Outcomes:** escaped defects (a fix change that follows a delivery), rollbacks and
    incidents per product and per pack version, so quality is a trend and not one run.
- **Template 1.4.1, phone-ready controls in every look** (found on Kudos board, 2026-10-29): the *neutral-compact*
  look scales spacing by 0.75 and the root font to 14 px, so the template's `min-h-11` renders ~29 px. Recipe
  proven by Kudos board iteration 2: a `phone` custom variant in px (`@media (width < 640px)`) with
  `phone:min-h-[44px]` in the base Button, Input, Select and Textarea, the shell's nav links, the dialog close and
  the toast close (also `min-w`); plus an e2e check that measures tap targets at 390 px in every look, so it fails
  the build instead of being reported. Then a short new-product run proves new products start phone-ready.
- **Phone header** (Kudos board iteration 2): ~110 px, two rows, no signed-in email. In the template's shell, or as
  the product's first J2 suggested feature.
- **Upgradable selections** (decided 2026-10-18: every choice the factory makes is versioned data with an owner, a scan and an upgrade path):
  - one inventory of every pinned choice: pack base images, Node/Python/Playwright in the sandbox, scanner images (Trivy and syft pinned by version and digest in J1; Semgrep, OSV-Scanner and Snyk still on moving tags), Actions, models;
  - pin by digest; Dependabot/Renovate opens the upgrade PR; the evaluation harness gates it before it serves products;
  - template images scanned weekly in CI (blocking on the scheduled run) (live product rescans are in J4);
  - pack upgrades reach existing products: a new pack version offers each product an upkeep change bringing its Dockerfile, chart and CI up to the pack.
- **Per-pack evaluation:** `eval_blueprint` per case's pack, so pack cases run in the harness (today they always build with the first blueprint).
- `make preview` for `fastapi-service` (preview e2e is Next.js only; FastAPI's comes with its next minor version).
- Per-station cost for priced providers (Snyk, cloud runners) once one is in use; spend is model-only today.
- Image findings follow-ups (ADR-0042): require an expiry when accepting an image risk; show the image findings on the change page next to the SBOM.
- Rename lanes "existing repo" → **Team repo** (or Connected repo) and the factory side → **Factory product**. (The "Existing repo" switch on the Products page create card is easy to miss: make it a clear second option.)
- Assessment: top recommendations with "Make this change".
- Skill studio: create and edit skills by chat, test-run, versions; retro skill diffs with evaluated acceptance (master plan §3D).
- Re-enable the evaluation gate on the New product lane.
- Factory map page.
- Product creation form: provided entry artifacts.
- Builder scope bar (phase rail behind "Custom scope") and tidy-up (drop built-in template dropdown, GitHub import as a link, mark unused agents).
- Evaluation: repeated runs per case; side-by-side models; scheduled re-evaluation for drift.
- Hidden-scenario verifier for web apps with a browser (today it uses curl; the pack's Playwright suite covers the browser side). Kudos board iteration 2 (2026-10-29): it judged a phone layout from the CSS, estimating widths, because it had no browser.
- Security and models:
  - OpenTelemetry GenAI export;
  - model profiles and fallback;
  - requirements, feedback and align-flow text ("Other…" answers, comments) marked as untrusted data inside prompts (considered for J1 on 2026-10-25, left for later);
  - Actions pinned to SHAs.
- Execution: parallel tasks; staged rollouts.
- Code quality: split `actions.py` (1,875 lines), `stations.py` (1,973) and `pipeline.py` (1,136); a good moment is when J-milestones touch them.

## 🧹 Housekeeping
- The ruleset on main requires `engine`, `auth`, `web`, `golden-path`, `images` and `e2e`.
- Agents push feature branches and open PRs from the cloud workspace; Saravana reviews and merges (decided 2026-11-01). Agents never push to main, merge, or force-push a branch under review.
- The agent's shell on the Mac has no network (checked again 2026-10-28: GitHub, PyPI, npm and Docker Hub refused by its proxy): agents clone the public repo in the cloud and check it matches his local `main`; work goes back as a pushed branch and PR (a bundle only when pushing isn't possible). Commands that need the network (`git fetch`, `docker buildx imagetools inspect`) run in Saravana's own terminal.
- In an agent's cloud workspace, run the CLI with `AGENT_FACTORY_ALLOW_ROOT=1`.
