# Agent Factory: build plan

Updated 2026-10-27 (no-progress stop and target probe merged as `9888491`; journey acceptance test in review).
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
  milestones below need (ADR-0043, a trimmed ADR-0044) go ahead.
- **Each milestone ends with a live run** whose sealed evidence is kept as the demo record, and a short demo.
- **A journey acceptance test** (scripted, dry-run, in CI) grows with each milestone, so a working step
  never silently breaks. J1 steps written 2026-10-27 (`engine/tests/test_journey.py`, in review); each
  milestone adds its steps there when it lands.
- A new idea that isn't on the journey goes to the backlog.

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
     - 🔄 **Journey acceptance test (branch `feat/journey-acceptance`, 2026-10-27, in review):** a scripted
       dry-run of the J1 flow, run by CI's engine tests (`engine/tests/test_journey.py`): one Next.js product
       driven through the HTTP API as a person would: Design decisions set to ask in the editor → request →
       review gate under "Needs you" → align (a choice, an open-question answer, a comment) → approve →
       Level 3 with the module signals → e2e against the preview before build → live e2e signed in as the
       chart's admin, after the target probe → publish to the org → sealed evidence. Each assertion names its
       journey step (J1.0–J1.7), so a failure says which step broke; it grows with each milestone.
     - **Pin the scanner images (~0.5 day):** Trivy and syft off `latest` (still `aquasec/trivy:latest` in
       `tools.py` on 2026-10-26), pinned by version (digest where the registry gives one). This is the slice
       of "upgradable selections" J1 needs; the rest stays in the backlog.
3. ✅ **Publish to GitHub live** (2026-10-22): a dedicated GitHub org as `policies.publish.owner`, with a
   fine-grained token (Administration, Contents, Workflows read/write).
4. ✅ **First live run** (2026-10-22): "Book keeping" (nextjs-web 1.1.x): 9/9 requirements verified live by
   hidden scenarios, review 9/9, Level 3, published, $3.64. It found four factory bugs, all fixed and
   merged: preview probe blocked by the egress proxy (`d161b28`); git "dubious ownership" on the Mac mount
   widened the scan scope (`661badc`); image and tool scans bypassed infra retries (`d547252`); dind kept a
   stale base image, so builds now `--pull` (`dcf8c71`). Also merged: the product's Spend card and
   iteration summary (patch 0048, `40c8e8e`).
5. ⏳ **J1 acceptance run** after PR 3, with Design decisions set to **Ask a person**: proves the align flow,
   modules, sign-in with the chart's admin Secret on the cluster, the target probe and the no-progress
   stop, and the new look, on one product. The journey acceptance test is its dry-run twin.
- **Done when:** a new product, built from scratch, is one Saravana would show a client: agreed design
  choices, auth on protected writes, a polished responsive UI, published repo, deployed, Level 3.

**J2. Iterate safely (~4.5 days)**
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
- Feedback → change → redeploy, run live on the **J1 acceptance product** (decided 2026-10-25: built on
  nextjs-web 1.4.0 with modules and the new look; not Book keeping, which predates modules).
- **Rollback to the last good release** when iteration 2+ fails after deploy (last-good record, `--atomic`).
- An **accepted / closed** state for a delivered change, so Outcomes can tell done from waiting.
- **Done when:** iteration 2 is started from the "Next iteration" card with a suggested feature and a CVE fix,
  and a deliberately broken iteration rolls back while the previous version keeps serving.

**J3. Team repo (~3 days)**
- **Live test** of the push/PR path (only tested against a fake `gh`): a throwaway copy of one of Saravana's
  repos with a small CI workflow; `existing_repos.write_token_ref` = a fine-grained token (Contents + Pull
  requests read/write on that repo only).
- **PR loop:** read checks, reviews and merge state (schedule + "Refresh"); red CI → fix on the same branch,
  same PR; review comments → a change that continues the PR (a member starts it); merged → change closed,
  ledger items "awaiting verification"; closed unmerged → abandoned.
- **Done when:** an assessment item becomes a PR, CI fails once and is fixed on the same PR, and the merge
  closes the change.

**J4. Run and maintain (~4 days)**
- **A minimal Operate phase:** watch health and error rate from the app's `/metrics` and health endpoints;
  a breach opens an **incident** item in the product ledger; "Make this change" turns it into a fix change.
- **Scheduled upkeep:** dependency bumps and image rescans per live product; a new CVE or outdated
  dependency opens an upkeep change (confirmed 2026-10-26: rescans of live products stay here; until then
  CVEs are found at build and fixed by hand from the Next iteration card).
- **Done when:** an injected fault becomes an incident, a fix change and a redeploy without anyone opening a terminal.

**J5. End of life (~2 days)**
- A **retire** flow: freeze changes, final sealed evidence, data export, undeploy, archive the product repo,
  a decommission record on Outcomes.
- **Done when:** the J1 product is retired and its full history, from first request to decommission, can be
  shown from the evidence.

## ⏸ Deferred until after J5
- **Skill usage and upkeep** (planned 2026-10-21, ~3.5 days): usage roll-up per skill × role; drop/archive
  suggestions into the workflow draft (archived = hidden, kept for pinned versions, restorable); try-without
  evaluations for preloaded skills; skill gap report from the retro. The roll-up page alone (~1 day) can come
  earlier if it's wanted.
- **The rest of ADR-0044:** rate limits, caching, flags, UI shell module; chart modules (HPA, PDB); UI review
  blocking delivery once the rubric is calibrated; FastAPI modules (products built before nextjs-web 1.3.0
  keep Level 3 until an upkeep change adopts modules).
- **Discuss mode at other person-owned stations** (spec review, risk approval, assessment triage), and
  composable-SDLC step 2 (human modes, owners, separation of duty) and step 3 (start anywhere).
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

## 🔄 In review
- `feat/no-progress-stop` (2026-10-27): no-progress stop and e2e target probe (J1, before the acceptance run).

## 🗂 Backlog
- **Upgradable selections** (decided 2026-10-18: every choice the factory makes is versioned data with an owner, a scan and an upgrade path):
  - one inventory of every pinned choice: pack base images, Node/Python/Playwright in the sandbox, scanner images (pinned in J1), Actions, models;
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
- Hidden-scenario verifier for web apps with a browser (today it uses curl; the pack's Playwright suite covers the browser side).
- Security and models:
  - OpenTelemetry GenAI export;
  - model profiles and fallback;
  - requirements, feedback and align-flow text ("Other…" answers, comments) marked as untrusted data inside prompts (considered for J1 on 2026-10-25, left for later);
  - Actions pinned to SHAs.
- Execution: parallel tasks; staged rollouts.
- Code quality: split `actions.py` (1,875 lines), `stations.py` (1,973) and `pipeline.py` (1,136); a good moment is when J-milestones touch them.

## 🧹 Housekeeping
- The ruleset on main requires `engine`, `auth`, `web`, `golden-path`, `images` and `e2e`.
- Saravana runs fetch, push and merge. Agents never push or commit to main.
- The Mac shell has no network: agents clone the public repo in the cloud and check it matches his local `main`; work goes back as bundles or patches.
- In an agent's cloud workspace, run the CLI with `AGENT_FACTORY_ALLOW_ROOT=1`.
