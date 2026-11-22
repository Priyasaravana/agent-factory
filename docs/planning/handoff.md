# Agent Factory: handoff (updated 2026-11-22)

Read [README.md](README.md) and [roadmap.md](roadmap.md) first. This page holds the **current state**
(replace it at the end of every session) and **how we work** (keep it; add lessons as they're learned).

## Current state (2026-11-22)
- **In review on `operate/incident-response-slice-0`:** phase A item 3, incident response slice 0 (ADR-0049 §4
  amended; minimal slice as the plan scoped it). Code map: pure rules in `respond.py` (mode, live state parsers,
  `stale_record`, `drift`, the ladder `next_step`, `episode` from operation records, `restored_at`, `resolve_due`,
  `classify`, `caused_by`); acting in `engine/operations.py` (`respond`, `respond_now`, `_act` with the snapshot
  first, `_restored`, `_resolve`, `note_restore` / `_reconcile` after a factory restore, faster readings while a
  reading fails or an incident is open). Live state and restart: `HelmRelease.state` / `.restart`, through
  `live_state` / `restart` on both deploy providers; re-apply is the ADR-0046 rollback to the recorded revision.
  Ledger: `ledger.resolve_watch`. API `POST /products/{id}/respond` (steering). CLI `agent-factory factory-drill
  passed|failed|show`. `scripts/restore.sh` leaves `factory-restored`. UI: the incident card in "Running it"
  (state, tried, held/proposed, timings, the step button). Dry-run Helm simulates drift and crashes
  (`FakeHelm.drift`). Tests: `test_respond.py` (pure), `test_operations.py` (drift → re-apply → restored →
  resolved; crash → restart once → held; newer release held; ask mode and the owner's step; recurrence goes up
  the ladder; rate limit; after a factory restore; off), `test_attention.py`, `test_cluster_target.py`
  (kubeconfig through the environment).
  **Not run live:** the "Done when" (an injected drift and a crash each restore in under 5 minutes, live) needs the
  Mac: `agent-factory factory-drill passed --by … --note …` after a factory restore drill (or leave it in ask mode and
  press the button), then `kubectl -n app-<slug> set image deployment/<slug> …=<other>` for drift and a crash
  (`kubectl -n app-<slug> exec deploy/<slug> -- kill 1` repeatedly, or a bad env var) for the restart.
  **Left in phase A:** items 1, 2, 4 and 5; rollback / diagnosis / bug change are slice 0's next part.
  **Live check 2026-11-22 (Kudos, local, factory at `7931666`; PR #190 comment):** web pod deleted → Kubernetes
  replaced it, health and ready answered in about 32 s (passes the 5 minutes). **Drift did not pass:** image set
  from `575ed0a` to `a26a161`, the factory proposed the re-apply, the step did not restore the release, and a
  manual `helm rollback kudos 9 -n app-kudos --wait --timeout 90s` did. The cause is not known yet: the step's
  record kept only "helm rollback failed" without Helm's output. Follow-up commit: a failed step now keeps its
  command's output (redacted) on the record and the card, isn't credited with a restore it didn't make, and the
  owner's step runs in the background. **Next live check:** repeat the drift with the follow-up and read the
  "Last step" output on the card. The availability card also looked unreconciled (open incident, 93.5 %, a 503,
  while health answered 200): the 503 was the window's last failure, which stays in the 30-minute window; the card
  now shows the newest reading and when the window clears. Decisions: the drill gate and the 120 s settle stay.

## Earlier state (2026-11-21)
- **J4 and J5 ran live** (Saravana, 2026-11-21): the golden journey is complete and the freeze lifts. Its evidence and
  findings are still to be written down (phase A item 1).
- **Decided 2026-11-21** (#188, #189): the vision in [vision.md](vision.md) is adopted (master plan §1 replaced;
  principle 7 now team-owned); v0.1.0 public by 2026-12-31; the work runs as phases **A–E** at AI build speed (roadmap
  "Next, in order"); incident slice 0 in phase A with `operate.respond: auto` and automatic rollback when the change
  is additive; first pilot a UK or EU scale-up on the full package. The one repo + `licensed/` split is recommended
  and waits for Saravana's confirmation before phase C.
- **Next:** phase A, starting with recording the J4/J5 run and the baselines.

## Earlier state (2026-11-17, end-to-end audit)
- **In review on `test/e2e-lifecycle-audit`:** an end-to-end audit of the product lifecycle (J1→J5) on main
  `702548e` for security, reliability, cost and delivery, with every decision taken autonomously as Saravana
  asked. Findings and fixes are in roadmap "Next, in order" under J4/J5. Code: `engine/retirement.py`
  (`_stop_waiting`, the product frozen before any await), `pipeline._execute` (no change runs on a retiring
  product) and `settle_production` (never onto a removed app), `operate.auto_upkeep_pick` and
  `Operations.start_upkeep`, `operate.max_backup_mb` with `providers.base.file_limit` (`kube(..., max_bytes=)`),
  `Operations.tick` (readings side by side, `PROBES_AT_ONCE`), `packs.SCRATCH_DATABASE`. ADR-0049 updated.
  Tests: `test_retire.py` (waiting changes, a change on a retiring product), `test_operations.py` (one automatic
  upkeep try, backup limit, side-by-side readings, scratch name), `test_operate.py`.
- **From the PR's review (2026-11-19, same branch):** export retention (`retire.keep_export_days`,
  `Retirer.purge_expired_exports`, run by the watch; tests in `test_retire.py`); auth idle timeout and the
  plain-HTTP sign-in refusal (`auth/`: `Store.session_user(idle=)`, `plain_http_refused`; tests in
  `auth/tests/test_auth.py`); `scripts/backup.sh` / `restore.sh` encrypt with age. Re-review fixes: the purge goes by the date in the
  sealed record (`retire.export_deadline`), and restore creates its private `backups/` on a fresh recovery
  checkout (`tests/test_backup_scripts.py`, stub docker, real or stub age). **On the Mac after merging:**
  `brew install age`, `age-keygen -o ~/.agent-factory-backup.key`, then `BACKUP_AGE_RECIPIENT` and
  `BACKUP_AGE_IDENTITY` in `.env`, or `make backup` / `make upgrade` refuse (assessment "Progress").
- **Not run here:** the containerised stack (Docker Hub is outside the cloud session's network policy), so CI
  `e2e`, `images`, `cluster-security` and `model-gateway` are the stack evidence; the live J4/J5 run on the Mac
  ([plans/run-and-retire.md](plans/run-and-retire.md)) is still what closes J4 and J5.

## Earlier state (2026-11-17)
- **J4 and J5 merged as #185** (`702548e`; built in one PR on `feat/j4-j5-run-retire`, asked for by Saravana 2026-11-16; ADR-0049, proposed).
  Code map: pure rules in `operate.py` (target, evaluate, error rate, backup/restore judgement, upkeep parsers,
  production proof) and `retire.py` (steps, timeline, record); acting in `engine/operations.py` (the watch,
  backup, restore drill, upkeep, `tick`/`start` from the app lifespan in live mode only) and
  `engine/retirement.py` (retire, history, verify). Ledger: `ledger.fold_watch` (categories `incident`,
  `upkeep`). Packs: `stack.operate` (`packs.PackOperate`), nextjs-web 1.6.0, fastapi-service 1.2.0. Restart:
  `ChangeManager.resume_after_restart` (policy auto, `max_auto`). Store: `health_samples`, `operation_records`.
  UI: "Running it" (`Operations.tsx`), Retire / History / Retired (`Retire.tsx`), Next iteration groups
  Incidents and Upkeep, Outcomes "Retired". Tests: `test_operate.py` (pure), `test_operations.py`,
  `test_retire.py`, journey J4.0–J5.3. **Not run live:** checklist in
  [plans/run-and-retire.md](plans/run-and-retire.md).
- **Open on main (2026-11-17):** `tests/test_checks.py::test_snyk_answers_with_its_token_only_in_the_environment`
  fails locally on main `f580a4c` without this branch (the Snyk fixture's finding isn't counted); see the PR.

## Earlier state (2026-11-16)
- **Decided by Saravana 2026-11-16:** J3 is closed, and the assessment's first group ("close the paths around
  the sandbox": S-01, S-02, S-04, S-03's refusal) comes now, before J4, in that order (roadmap, "Next, in order").
- **S-01 merged 2026-11-16 (#181):** identity headers count only with the gateway's
  proof (ADR-0012, updated). `cluster/init.sh` makes the secret once, in two volumes (engine's copy for uid
  10001, nginx's config line for uid 101); `web/nginx.conf` includes it and sets `X-Gateway-Proof` on both
  locations that pass to the engine; the engine reads `GATEWAY_PROOF_FILE` at start (refusing gateway mode
  without it) and answers only `/api/health` without the proof. Tests: `tests/test_identity.py` (forged admin
  headers, wrong proofs, the webhook, start refusal), `tests/test_least_privilege.py` (who holds the proof,
  every engine location sets it). In tests the client from `test_api._client` stands for the gateway and sends
  the proof. Proved on the real stack by CI `e2e` (`scripts/gateway-check.sh`); not yet run on the Mac:
  `make upgrade` (rebuilds factory and web, makes the volumes), then `make sandbox-check`.
- **J3's steps are in the journey test** (`test_j3_team_repo`, J3.0 to J3.4, same branch): assessment item →
  PR → red CI read → fix on the same PR → green → merge closes the change and its fix.
- **In review on `fix/sandbox-paths` (#182, 2026-11-16):** S-02 (three of
  four fixes), S-04 and S-03's refusal, one PR as Saravana decided 2026-11-16. ADR-0048 (proposed) has the
  decisions; the roadmap's "Next, in order" has what each does.
  - **#182 review fix (2026-11-16):** [P1] an agent-written chart could undo the namespace's egress policy
    with a second, allow-all NetworkPolicy (policies are additive). Under `restricted` the deploy now refuses
    a rendered chart that widens or sidesteps the policy (`app_network.widening`) and reads the namespace
    back after the release (`after_release`, removing what it finds). Tests in `tests/test_app_network.py`.
    Not a cluster-side ceiling: that is an admission policy, proposed with S-05 (ADR-0048, "Not decided").
  - **#182 second review (2026-11-16, sarvap37's assessment of `e3bdbe0`):** [P2] the firewall did not fail
    closed (one NEW-only rule, a 15 s poll, a marker file). Now two rules in two tables, the first before
    dockerd starts, both cutting established connections; a 1 s watcher; a health check that reads the real
    rules and the watcher. Apps already running on the local cluster get the policy at start
    (`ChangeManager.secure_app_namespaces`). The browser smoke test checks that a signed-in browser sends
    no factory cookie to a listener on 127.0.0.1 (and does to one on localhost, as a control). Residual
    risks are a table in ADR-0048; **none is accepted: that needs Saravana (owner, reason, scope, expiry).**
  - **Proved on the real stack (CI `e2e`):** dind starts behind `cluster/dind-firewall.sh` and the stack comes
    up healthy; from a container on dind's default bridge and from a Dockerfile `RUN` step the engine, auth
    and the gateway are unreachable, and with the rule lifted for a moment the engine is reachable again
    (`scripts/sandbox-paths-probe.sh`, `PROBE_PROVE_RULE=1` in CI only); the UI on 127.0.0.1 redirects to
    localhost and cross-site writes get 403 (`scripts/gateway-check.sh`); sandbox check and the browser smoke
    test still pass.
  - **Re-review 2026-11-17 [P1], fixed:** a chart could bind its account to an existing role (`cluster-admin`)
    and a pod delete the policy later. App charts are now held to an allowlist (`app_network.APP_KINDS`: no
    RBAC, bindings, webhooks or cluster-wide objects), and the read-back also removes bindings.
  - **Live evidence now run in CI on every PR (2026-11-17):**
    - `cluster-security` (a real kind cluster, `scripts/cluster-security-check.py`): from a pod with the
      engine's policy no internet (by address and name), no other namespace, no service on the host, each
      with a control that reaches it without the policy; DNS and its own namespace work. An adversarial chart
      (allow-all policy, `cluster-admin` binding, hook) is refused by the check; installed anyway it really
      opens the internet and grants the delete, and the read-back removes both. RBAC refuses the namespace's
      own account (403). **Found:** app pods still reach the Kubernetes API server (kindnet lets node traffic
      through); RBAC is the control there, a residual in ADR-0048.
    - `model-gateway` (LiteLLM with its database, `scripts/model-gateway-check.py`): the engine's
      `LiteLLMGateway` mints a key per call; after a call that succeeds, fails or times out the gateway refuses
      the key; a key is limited to its models, budget and expiry.
    - `e2e` probe: the image start check run with the engine's own commands (answers its probe, nothing
      published, no internet or factory from inside, no capabilities, bounded), and dind restarted with
      everything closed again.
  - **Still not proved (needs the Mac):** Docker Desktop cold start, restart and rule loss (CI is Linux
    Docker); a real Next.js build where the start check falls back to the next base image (needs live mode
    and a model); a deploy of a real generated app passing its smoke check and e2e under the policy;
    cross-site writes through a reverse proxy, if one is used.
  - **After merging, on the Mac:** `make upgrade`, then `make sandbox-check` (now also runs both probe
    scripts). App links move to `http://127.0.0.1:<port>`.
- **Open, placement not decided (Saravana):** S-02's last part, builds and the open internet (roadmap item 3).
- **Next:** the Mac run above; then J4, unless Saravana places the builds item first.

## Earlier state (2026-11-15, J3 live follow-up)
- Core J3 proved live on sarvap37/sample-app PRs #8 and #9: assessment item to PR, deliberate red CI fixed on the same branch, merge recorded merged outcomes for the original and all CI/feedback continuations. Inline-thread replies, decline, self-reply filtering, question-only response and read-only-token hold/Resume also exercised. PR #9 closed iterations 9–14. Follow-up PR #10 proved autonomous feedback once per comment (iteration 16), automatic CI repair before queued feedback (17 then 18), one reply summary each, and merge closure of iterations 15–18. Trial settings published as repo-change v3, restored to manual as v4.
- `fix/j3-live-controls`: live testing found the workflow editor hid CI/feedback settings for the shipped repo-change lane (handover handler; UI checked only pull-request). Accept both handlers for repo lanes; show each feedback author's permission beside its kind. TypeScript and Docker web build passed; corrected controls and permission labels verified in live UI. Local web image rebuilt and activated. Final outside-account retest passed on sample-app PR #11: Priyasaravana permission read, labelled outside the team, no answer action or continuation. Fix PR #180 reviewed at ae2e2a8 with no actionable findings; CI required before merge. Local sign-in failures with the modified config do not reproduce on untouched main (8 passed). Existing config edits and Claude outputs are unrelated and retained.

## Current state (2026-11-15, security assessment)
- **Security assessment in review on `docs/security-assessment-2026-11-15` (docs only, no code changed):**
  [../security/assessment-2026-11-15.md](../security/assessment-2026-11-15.md), 18 findings (4 high) from a
  code review and scanners on main `43b36d3`. **Nothing was run live:** S-01 and S-02 (the network findings)
  come from reading the code and the compose file, and each names the probe that would prove it; the first
  step of the fix is to run that probe. Roadmap backlog entry added with a proposed placement; **waiting on
  Saravana to decide the order** (the proposal puts S-01, S-02, S-04 and S-03's refusal before more J3 live
  runs). The threat model now names the boundaries the assessment found missing. It changes nothing
  in the J3 state above and below.

## Current state (2026-11-13, evening)
- **J3 slice 3 in review on `feat/j3-pr-feedback` (2026-11-13):** the team's feedback on a change's PR is
  answered or addressed on the same PR ([plans/pr-loop.md](plans/pr-loop.md), "Feedback"). Scope from
  Saravana (2026-11-13): every form of feedback (comments, changes requested, inline review), each answered or
  addressed. Reviews, inline comments and conversation comments are read with the PR (`pr_loop.feedback_of`);
  only authors with write access count as the team. **Answer feedback** (change page, Needs you) or the lane's
  new **Feedback on a pull request** setting (`pr_feedback`: ask | autonomous) starts a change continuing the
  PR (`Change.continues.reason` = feedback, source `review`); the developer reports one response per piece
  (addressed | answered | declined), judged by `pr_loop.judge_responses`; the pull-request station pushes
  fast-forward when code changed (or nothing), then replies in each inline thread and with one conversation
  comment, every body carrying `pr_loop.FEEDBACK_MARK`. Checks in the cloud session: `scripts/check all` (result
  in the PR); web build clean. Things to look at in review: the "team" rule (author association), and that an
  answer-only change still runs the lane's test/review/scan stations on an empty diff. **Not run live.** The
  live run (slice 4) needs the same throwaway repo and token as before; the token's Pull requests read/write
  already covers replies, and reading authors' roles needs only repository Metadata: read (always granted).
- **#178 review fixes (2026-11-14):** [P1] the team is decided by each author's role on the repo read from the
  host (`pr_loop.role_of`, `with_roles`; unknown fails closed), not their association; [P2] the station holds
  until every reply is posted, and Resume posts only the rest without pushing again; [P2] each reply carries
  `pr_loop.reply_marker`, and the PR's comments are read before posting and after any failed POST, so a reply
  that reached the host is never posted twice (nothing is posted if they can't be read).

## Current state (2026-11-13)
- **J3 slice 2 merged as #176 (2026-11-13):** red CI fixed on the same PR
  ([plans/pr-loop.md](plans/pr-loop.md), "Red CI"). Slice 1 (follow the PR) merged as #166. A failing head
  commit offers **Fix CI** on the change page and in Needs you; the fix is a change that continues the PR
  from the failing commit on its branch, pushes fast-forward only (`ls-remote` checked first, never forced,
  never `gh pr create`), and the PR is read again. Lane setting **Red CI on a pull request** (`ci_fix`: ask |
  autonomous; autonomous starts once per failing commit, at most `existing_repos.ci_fix_auto_rounds` = 2 per
  PR). Checks run in the cloud session: the new and touched engine tests pass, web build clean (full
  `scripts/check all` result in the PR). The fix is gated on **its own diff only**
  (`base_ref` = start commit), and the lane's setting is read from its **active** version, not the version the
  original change was pinned to. **Waiting on Saravana for the live run** (unchanged): a throwaway copy of one
  of his repos with a small CI workflow, and a fine-grained token on it only (Contents, Pull requests
  read/write; Checks read; Commit statuses read/write) at `existing_repos.write_token_ref`; for webhook mode
  also a public tunnel to the UI port and `REPO_WEBHOOK_SECRET`. Next code: slice 3, review comments → a
  change that continues the PR (the continuation mechanics are reusable).
## Current state (2026-11-09)
- **Merged as #167 (2026-11-12), `feat/repo-sources`:** repository sources ([ADR-0047](../adr/0047-repo-sources.md),
  status proposed): one source per org or host with its own token, webhook secret and read/write access; admins
  manage them on **Integrations → Repository sources**, members choose one when onboarding (the engine picks the
  source naming the repo's owner), admins can move a repo to another source on its page. Clone, push, PR and
  PR reading all use the product's source, re-checked every time. Existing installs need nothing: the
  `existing_repos` token settings are the `default` source. **For the J3 live run** the throwaway repo's token can
  now be its own source (e.g. `env://REPO_TRIAL_TOKEN` in `.env`, restart once, then add the source in the UI).
  Checked in the cloud session: engine tests for repos, PR loop, access, API and config; web build; UI
  screenshotted at desktop and 390 px against a dry-run engine. Not run live (no GitHub reachable here with a
  real token). **Review fixes (2026-11-09, Saravana's review of `2682adb`):** no redirects on authenticated PR
  reads; webhook deliveries count every source whose secret verifies (default sources on several hosts share
  the legacy secret); token references kept out of member-visible events and hold reasons; source changes in an
  append-only audit log that survives deletion. ADR-0047 stays proposed. **Second review (sarvap37, `0409ba0`):**
  the legacy token is bound to `api_url`'s host only (other hosts: read-only, no credentials, until a source
  binds one); default source ids are host-based (`default-<host>`), positional ones rebound; the PR station
  reloads the source and rechecks write access before each push, PR and status write.
- **J2 closed 2026-11-06 (Saravana)** on Plant watering's iteration 4 (`c4888c5614a1`): validated in a temporary
  environment (its first, broken attempt never reached users), the tested image promoted blue/green (live
  green, blue kept), the environment removed, Ready for: pilot. Detail in the roadmap's J2. Both factory lanes
  are on their shipped versions: New product v15, Product change v3. **Next: J3 (team repo).** Lane UX
  findings UX-23 to UX-25 are open (UX-24, which lane an iteration runs on, is the cheapest, ~½ day).
- **J1 closed 2026-11-05 (Saravana)** on Plant watering's first delivery (`9e7e05c0c395`), with one Resume
  after a factory fault (Chromium crash in the sandbox, fixed in #156).
- **Merged 2026-11-04 (`e0aad44`):** item 3, waiting and budgets minimum (L-5, UX-15, UX-18, UX-19). Not proved
  yet: the cause of the ~6× meter gap; the next live run's agent-call events (tokens per model, both meters)
  are the evidence to read first. A paused change keeps its temporary environment until it wakes; a throttle
  without a known time backs off from 2 minutes (a hold after 8 in a row). #151 and #152 are merged.
- **Merged 2026-11-04 (#150):** L-3, blue/green fails closed. **Decided by Saravana 2026-11-04 (review of
  #151):** an explicit Resume reruns the blue/green checks after a person fixes the cause elsewhere: pass,
  carry on in blue/green; fail, keep the hold. Switching to In place stays a separate release decision.
  Follow-up branch `fix/blue-green-resume`.
- **In review, `fix/truncated-output` (2026-11-04):** item 2 of "Now", cut-off output as a class (UX-13,
  UX-16, L-1, L-2, #147/#148's call sites); detail in the roadmap. Two behaviour changes to look at in
  review: a scan, review or PR station whose changed-file listing fails now stops with an engine error
  instead of scanning nothing, and the design guards do the same when `git status` fails (kept, Saravana
  2026-11-04). Review fixes: changed files and worktree status are read NUL-separated (`-z`), so names git
  quotes (`café.py`, a tab) match findings and are undone; renames undo both paths; a restore git refuses,
  or a path still changed afterwards, stops the guard. Checks: lint,
  mypy, openapi, web, auth clean; engine 1,193 passed, 5 failed, the same five that fail on main in the
  cloud image (`test_checks.py` snyk, four in `test_tools.py`). **Next:** item 3, waiting and budgets minimum.
- **Live campaign closed 2026-11-04 01:42** ($21.05 of $25; main `cd20e55`). Proved: in-place → blue/green migration
  (Expense iteration 2), one product blue/green and one in place (Kudos iteration 4), temporary validation and
  promotion of the tested image, rollback of an idle-colour failure before the switch (`e56fb20c67ad`). Not proved:
  J1's fresh-product first delivery (skipped, UX-01), recovery after the switch, a CVE fix from the card.
  Results and the assessment: [plans/live-test-usability.md](plans/live-test-usability.md). Left open: held trial
  `e56fb20c67ad`; Expense's drill fault cleaned locally (`4f31bed`), published at the next Deliver.
- **Next (decided by Saravana 2026-11-04):** the correctness set under "Now" in the roadmap, in order: L-3,
  cut-off output as a class (with UX-16), waiting and budgets minimum, UX-04's sideways-scroll check, UX-02 by
  hand; then J1's fresh-product run and the rest of J2. One branch per item.
- **2026-11-03, live-test blocker:** full Helm chart renders exceeded the executor's 6000-character tail,
  hiding colour labels and falsely selecting the in-place compatibility fallback. Compact colour evidence
  before executor truncation, preserving Helm failure and legacy-chart detection. The controlled drill
  delivered in place, so rollback is **not proven**; repeat only after the fix is installed.

- **Live-test blocker 2026-11-03:** Expense claims iteration 2 `096d8e827e4d` reached a healthy temporary ingress deployment, then held because large Helm status JSON loses its revision at the executor output-tail boundary. Branch `fix/helm-status-metadata` extracts only revision metadata before truncation; regression tests cover large output, failed Helm, malformed JSON and missing revision. Validation: 1,171 engine tests passed (10 skipped), 9 auth tests passed, lint/format clean, no new mypy errors, OpenAPI current; isolated tests use the committed default configuration rather than this install's ingress overrides. Not installed yet; run 1 first-delivery proof was skipped by Saravana due to pre-start workflow assignment limitation.
- **Main** (`6ecd8ce` on GitHub): J2 slices 1–5 and ADR-0046 slices 1–4 merged (rollback #131; temporary
  validation environment and promotion of the tested image #132; strategy per product, blue/green, #134);
  Dependabot roll-up #135.
- **Decided 2026-11-02 (Saravana):** template 1.4.1 (shipped as nextjs-web **1.5.1**) comes before the blue/green
  live run, and that run is a new product of a different shape, so it also proves J1's repeatability bar.
  Iteration 4 on Kudos board includes a CVE fix if one is open when it starts.
- **Live run 2026-11-03, Expense claims** (new product, nextjs-web 1.5.1, blue/green, decisions asked): held at
  change risk on `src/lib/validation.ts:82`, `/^(\d{4})-(\d{2})-(\d{2})$/.exec(s)`, a date check: rule
  `exf-dynamic-exec` matched `.exec(` on a regex. Approved as a false positive (break-glass after the cooling-off).
  **In review:** `fix/risk-regex-exec` (#146): every `exec(` and `.exec(` still holds except right after a regex
  literal with no flags (`/…/.exec(`; with flags it reads like division, `1/g.exec(x)`); a regex in a variable still waits for a person, since a name proves nothing and the
  scan sees only added lines (Saravana's review: `cp.exec(userInput)`, `builtins.exec(source)`); `new Function(`,
  `child_process` and the sync calls added; tests both ways in `tests/test_change_risk.py`.
- **In review:** `feat/template-1.5.1`: phone-ready controls in every look (roadmap J1 "Done when"). The template's
  e2e was run in the cloud session (npm reached the registry; Playwright's browser download is refused, so the
  run used the image's `/usr/bin/google-chrome` through a local config, not committed). Note for checking it by
  hand: the template's pages are prerendered, so `AUTH=local` at run time doesn't turn sign-in on; set
  `DEFAULTS.auth` to `local` and rebuild (then revert) to measure the sign-in form.
- **Decided 2026-11-01 (Saravana):** blue/green (ADR-0046 slice 4) is two colours in production's **one** Helm
  release sharing its database, not two releases (the charts run Postgres in the release).
- **Merged 2026-11-02 (#134):** ADR-0046 slice 4, strategy per product.
  - Charts (nextjs-web **1.5.0**, fastapi-service **1.1.0**): a Deployment per colour; blue is the Deployment the
    app always had (same name and selector, plus `app.kubernetes.io/color`), green has its own selector
    (component `web-green` / `api-green`); `strategy=blue-green` points the Service at `blueGreen.live`, a preview
    Service and Ingress at `<release>-next.<domain>` reach the idle colour; bad values `fail` at render. Rendered
    in the cloud with a stdlib-only text/template stand-in (no Helm there: get.helm.sh and the Go proxy are
    refused); CI's golden path is the real check.
  - Engine: `blue_green.py` (pure: `chosen`, `layout`, `values_image`, `plan`, `sets`, `in_place_reason`,
    `renders_colors`); `HelmProvider.deploy_colors` / `layout` / `renders_colors` / `preview_url` over a shared
    `_upgrade`; `HelmRelease` reads one colour's pods and the release values; `rollback.values_image` reads the
    live colour's image; `Release.color`; `Product.deploy_strategy`; `PackDeploy` (`deploy:` in `pack.yaml`, both
    packs default in place). Stations: `_record_intent` / `_deployed` split out of `_deploy_production`;
    `promote` → `_blue_green` (or in place, with the reason in the event log) → `_promote_blue_green` (stage on
    idle, digest, preview probe + e2e, switch, digest, production probe). `_probe_target` / `_run_e2e` take a URL.
  - API and UI: `GET`/`POST /api/products/{id}/deploy-strategy` (creator or admin; in `MEMBER_ACTIONS` and
    `STEERING`); **Releases** panel on the product page (`ReleaseStrategy.tsx`), screenshotted at desktop and
    390 px against a seeded dry-run engine (users on green, blue kept).
  - Tests: `tests/test_blue_green.py`, `FakeHelm` keeps each revision's `--set` values and answers per colour;
    pack version pins updated.
  - **Safeguards asked for before merge (Saravana, 2026-11-01; schema check tightened after his review
    2026-11-02):** `schema_compat.py` (pure: Python `ast`, a `.ts`/`.js` lexer, a SQL tokenizer; verdict additive,
    not additive or unsure) + `_schema_guard` / `_commit_diff` in `promote` (every changed file between the healthy
    release's commit and the tested one, in full via files, never the executor's 6000-character tail). Not additive
    holds with `RequiredAction` `schema_in_place` (resume once the product is In place); unsure holds with
    `approve_schema` (resume once `POST /api/changes/{id}/schema-approval` recorded an approval for those exact
    commits, or In place). `SchemaHold.tsx` on the change page; "Needs you" kind `approve_schema`. `_read_only_checks` replaces e2e at promote everywhere (liveness, readiness, `/` for web apps; no
    sign-in). `scripts/check-charts.py` + CI job `charts` (real Helm, every strategy); the `Main` ruleset on `main` requires
    engine, auth, web, golden-path, images, e2e and charts (Saravana adds `charts`; the session's proxy refuses ruleset writes).
- **Next:** Saravana reviews and merges `feat/template-1.5.1`; reviews the Next iteration card's feel; then a live run
  on `local-ingress` with a **new** product of a different shape from Kudos board (a form- or table-heavy app), on
  nextjs-web 1.5.1, Design decisions set to ask, set to blue/green (existing products' charts predate colours): it
  must pass on its first delivery (J1 repeatability) and prove J2's "one blue/green, another in place"; then
  iteration 4 on Kudos board from the card (suggested feature, plus a CVE fix if one is open); a deliberately
  broken iteration that rolls back; the "ready for" statement; the accepted/closed state.
- **Live: Kudos board** (`software-factory-apps/kudos`, look neutral-compact decided by admin): iteration 1
  `75fd61391452` ($6.38), iteration 2 `d5e3de869ac0` (44 px phone controls, $6.02) and iteration 3
  `2d17c4f736c8` (signed-in email in the phone header, R12, ~$4.9, 2026-10-31), all delivered. Iteration 3's
  delivery made the first five suggested features. Its designer found no `screenshots/live/` and suggested from
  `docs/ui.md` (worth a look: where the live route screenshots are kept).
- **J1:** met on Kudos board after iteration 2; closing it is Saravana's call (roadmap J1 item 6).
- **Live:** Book lending (product `8aef2c7d1b8c`, nextjs-web 1.3.0): iterations 1 and 2 delivered (2026-10-26/27).
- **Publish:** a dedicated GitHub org set as `policies.publish.owner`. A fine-grained token with the org as
  resource owner, All repositories, and Administration, Contents and Workflows read/write. If the org
  requires approval for tokens, approve it under Settings → Personal access tokens.

### Seen on Book lending (2026-10-25), kept for the no-progress stop and target probe
- Live e2e failed 3× with `getaddrinfo ENOTFOUND dind` (`make e2e BASE_URL=http://dind:8084`). The classifier
  called it `work` (unknown → work), so it went to implement; rounds 1–2 made no change (same image `22dcafa`
  rebuilt), and build's attempt budget held the change (~$4.90 spent).
- Real cause: the sandbox network is `--internal`; the only way out is the egress proxy, which allows
  `dind:8081-8100`. The sandbox sets `HTTP_PROXY`/`HTTPS_PROXY` (`sandbox/spec.py`), but **Playwright ignores
  them**, so it resolved `dind` itself. FastAPI's e2e clients honour the env, so only `nextjs-web` hit it.
  Preview e2e passes because it runs on `127.0.0.1`. Fixed in the template (`9564283`).
- Built on `feat/no-progress-stop` (2026-10-27): the no-progress stop (same failure fingerprint at the same
  station on the same committed tree → hold with the cause) and the target probe (acceptance checks the
  app's liveness path from the sandbox the way the test clients reach it; a failure is `infra`). Later, optional: classifier rules for `ENOTFOUND` /
  `ECONNREFUSED` / `ERR_NAME_NOT_RESOLVED` on the target host, each with a test that a real app error stays
  `work`; the golden-path skill stops the preview by pid, not `pkill`.
- Agreed: no SRE/infra agent for this; agents stay unaware of the environment by design (ADR-0039). An ops
  agent belongs in J4, with the engine still routing.

## Code maps (for the areas most recently changed)
- **Design decisions and align (ADR-0044):** `decisions.py` (pure: catalogue, sheet incl. open questions,
  judge, stamp, apply_answers, apply_open_answers, diff, render_adrs, parse_roadmap); `engine/design_station.py`
  (design, alignment turns, `_keep_to_design_docs`); `decision_actions.py` (GET `/decisions`, POST `/align`);
  `ledger.fold_roadmap`. Intake writes the spec; questions with a reasonable default go to
  `docs/open-questions.yaml`. Everything on the review page goes through `POST /api/changes/{id}/align`
  (`ChangeManager.align`: choices + open answers saved and judged first, then one architect turn with
  `ALIGN_SCHEMA` {reply, requirements_change}); a requirements change sets `Change.rework_requirements`, and
  the pipeline routes back to intake. Approve is on the align panel (`DecisionsPanel.tsx`).
- **Modules and sign-in:** template `src/modules/<name>/{port.ts,index.ts,adapters/,<name>.contract.test.ts}`,
  `src/modules/config.ts` (DEFAULTS follow the sheet, env overrides); `.dependency-cruiser.cjs` in `make lint`.
  Engine: `PackAdminSecret` (packs.py), `Blueprint.admin_secret`, `secret_data` + `read_secret` (providers),
  `_app_admin` / `sign_in_note` (stations.py acceptance), `AgentRequest.secret_env`, `SandboxSpec.allow_env`,
  `Redactor.knows`. Tests: `tests/test_app_sign_in.py`.
- **1c builder:** `station_templates.py` (pure: `StationTemplate`, `insert_position`, `instantiate` /
  `with_station`, `added_problems`, `SCOPE_PRESETS` / `presets_for` / `apply_preset`, `preset_of`). Publish
  refuses the custom starter prompt and `eval_gate != off` on lanes without `new`.
  `phases.load_phases(cfg, home, packs=None)`; `packs/ops-pack`. API `/draft/templates`,
  `/draft/stations/from-template`, `/draft/preset`, `PATCH /draft/settings`, `GET /api/products/{id}/entry?kind=`.
  Web: `PhaseBoard.tsx`, `ProvidedArtifacts.tsx`.
- **Phases and capabilities (1b-3b):** `contract.py` (`CAPABILITIES`, `grants`, `narrow`, `BUILTIN_PHASES`,
  `PhaseRegistry`, `StationType.uses`, `capability_errors`); `WorkflowDoc.scope` / `scope_phases` / `grant`;
  runtime `ctx.grant`, `ctx.agent` drops ungranted tools, `ctx.secret(ref, purpose, capability)`.
- **Data reset:** after 1b-3a (data generation 4) `docker compose run --rm --no-deps --entrypoint agent-factory
  factory reset --confirm`. Later changes need no reset.

## How we work
### People and permissions
- **Saravana** owns the repo, reviews and merges ("Rebase and merge"). **Since 2026-11-01** (his call) an agent
  in the cloud workspace pushes its feature branch and opens or updates the PR itself; he reviews, comments
  and merges, and the agent pushes the fixes to the same branch. Agents never push to main, never merge, and
  never force-push a branch under review (new commits only).
- **Repo on the Mac:** `/Users/saravana/Saravana_Repos/agent-factory` (through the remote-device bridge:
  `$HOME/mnt/agent-factory`).
- **His local config edits:** uncommitted edits in `.agent-factory/config.yaml` (environment `local-ingress`,
  gateway `litellm` + subscription). With them in place, 19 tests fail on main. Never run `uv` or
  `scripts/check` inside his folder: it must stay clean, and tests there read his config.
- **How Saravana likes to work:** poll-style questions and concise answers; UI that is presentable and easy
  to use; each change states its value per part; he asks what value each option brings. When he answers
  "explain", give the issue, options and a recommendation in plain words, then ask again.

### Rules
- Each change states its value per part.
- Never put secrets in config or the DB: references only.
- Don't delete Saravana's files without asking.
- One session per branch. Keep this page short: replace "Current state" each session.

### The loop (cloud workspace ↔ Mac)
- The agent's shell on the Mac has no network (npm and pypi unreachable since 2026-10-18; GitHub and Docker Hub
  also refused when checked on 2026-10-28). Saravana's own terminal does Docker Hub lookups; branches and PRs go
  from the cloud workspace to GitHub (needs the GitHub account connected to Claude to have write access).
- **Cloud:** the repo is public: `GIT_LFS_SKIP_SMUDGE=1 git clone --depth 30 https://github.com/Priyasaravana/agent-factory`.
  First check GitHub main equals his local main (`git -C $HOME/mnt/agent-factory log -1 main`). If his local
  main is ahead, or a branch exists only on the Mac, bring it over as a bundle
  (`git bundle create .cache/<name>.bundle origin/main..<branch>`, then stage it). Then `uv sync --extra dev`
  in `engine`, `npm ci` in `web`, build and test with `scripts/check`.
- **Back to the Mac:** a bundle (new file name each time) committed into `.cache/`, then
  `git fetch -f .cache/<name>.bundle +<branch>:<branch>` on the Mac; or `git format-patch` into `.cache/`, then
  `mv` it to `.git/pr/`. Without file tools on the Mac, send the patch and PR text to Saravana to save into `.git/pr/`.
- **Apply on the Mac:** in a worktree (`git worktree add $HOME/wt-x <branch>`, `git am`, remove + prune) so his
  uncommitted config never blocks a checkout. Stash `.agent-factory/config.yaml` first when the patch touches it.
- **Engine-only loop on the Mac** (needs a venv already synced): `git worktree add $HOME/wt <branch>`,
  `export UV_PROJECT_ENVIRONMENT=$HOME/venv-engine`, pytest in two chunks of ≤170 s each (files 0–18, then 19+),
  commit in the worktree, then `git worktree remove --force` and `prune`.
- **Commits:** author `Claude <noreply@anthropic.com>` (or the agent's own identity), trailers
  `Co-Authored-By: Ezhil Priyadharshini Krishna <dharshini.prasad@gmail.com>`, the agent's co-author line and
  its session link. PR text goes in `.git/pr/<name>.md` and ends with the tool line plus the session link.

### Checks (save tokens: never stream test or build output into a session)
- `scripts/check` prints one line per step and, on failure, only the FAILED list; full logs in `.cache/check/<step>.log`.
- While developing: `scripts/check changed`, or `scripts/check test tests/test_x.py -k name`; `--lf` reruns
  the last failures; rerun one failure with `--tb=long`.
- Once, before the patch: `scripts/check bg all`, then
  `until ! scripts/check status | grep -q RUNNING; do sleep 10; done` (about 6 min). Commit first (the web step
  diffs `schema.d.ts` against git), and don't edit files while it runs.
- mypy: only errors not in `engine/.mypy-baseline.txt` are shown. Never grow the baseline. Don't start a
  comment with `# type:`.
- pytest collects imported `test_*` helpers (e.g. `traceability.test_tags`): import the module instead.
- A new station in the default lanes changes the station lists pinned in `test_pipeline.py`,
  `test_workflow.py`, `test_station_contract.py` (and its "lane new-product: N stations" count) and
  `test_station_phases.py`. A pack version bump changes the pins in `test_nextjs_pack.py`,
  `test_stack_switch.py` and `test_team_repo_packs.py`.
- UI/API changes: `uv run agent-factory openapi --out ../web/openapi.json`, then in `web/`:
  `npm run gen:api && npm run build`. Format: `uv run ruff format src tests`.
- A new mutating action: add it to `MEMBER_ACTIONS` (and `STEERING` if it acts on a product or change) in
  `tests/test_access.py`, plus a 403 case.
- **UI checks:** the engine in dry-run plus `npx vite preview`, then Playwright screenshots
  (`uv run --with playwright`). Seeds: `conftest.make_seeds`. Env: `FACTORY_MODE=dry-run FACTORY_HOME=<repo>
  FACTORY_CONFIG=<repo>/.agent-factory/config.yaml DATA_DIR=… SKILL_SEEDS_DIR=…`. For a custom setup: a small
  script calling `build_factory(settings, cfg, SqliteStateStore(":memory:"), None, FakeAgentRunner(...))`,
  then `uvicorn.run(create_app(f), port=8000)`. `web/e2e/builder.py` drives the builder.

### Signing in to an app the factory built
- `make apps` lists every product: address, status and its first admin's email (never a password).
- `make app-login APP=<slug>` shows one app's address and admin email and copies the admin password to the
  Mac's clipboard without printing it; `make app-password APP=<slug>` prints it. Both read the chart's Secret
  `<slug>-admin` in namespace `app-<slug>` (production's release), through `agent-factory apps`.

### Lessons
- **A browser crash is not the app's bug.** Chromium "Target crashed" in e2e (Docker's 64 MB `/dev/shm`) went to
  the developer as a code failure until implement's attempts ran out (Plant watering, 2026-11-05). The sandbox
  now has `--shm-size`, and an e2e run whose only failures are crashes runs once more; agent-written commands
  still never steer the route.
- **Editing a pack's template:** `scripts/check all` doesn't run the template's own checks; CI does. Run
  `make verify` in a copy of `packs/<id>/template` (its Prettier, eslint, tests, audit and build) before pushing.
- **Mac git:** delete permission resets with each new linked session: request it before the first git write.
  Otherwise `git am` leaves `.lock` files and `.git/rebase-apply`, and the branch switch stops halfway. To
  recover: remove only the locks with your command's timestamp, `git reset -q` (index only), check
  `git diff HEAD` is empty, then `git checkout main`. Mac git leaves `.git/objects/maintenance.lock` without
  delete permission: `mv` it aside under `.git/`.
- **Remote file tools:** committing a file to a path under `.git/` is refused: commit into `.cache/`, then `mv`.
  Committing to the same device path can leave the old file: give each new bundle a new name.
- **Keep the Mac awake during live runs** (`caffeinate -dims`): sleep paused a run four times and cut off a
  Trivy DB download; on Kudos board (2026-10-28/29) it slept 13 times overnight and cut off a preview build.
- **The verifier has no browser** (2026-10-29): for layout requirements (phone widths, tap targets) it reads the
  CSS and estimates. Put such checks in the pack's Playwright e2e, which measures them.
- **Look densities change sizes** (2026-10-29): Tailwind sizes in rem scale with a look's root font and density;
  a hard floor (44 px tap targets) must be in px.
- **Reading live state:** stage `.factory-data/factory.db` (plus `-wal` and `-shm`) and `artifacts/<change>/`,
  then read them with Python's sqlite3 in the cloud (no `sqlite3` CLI there). A product's git is
  `GIT_DIR=.factory-data/products/<name>/.git`, branch `run/<change>`.
- **Cloud container:** never create a uid 10001 user (the CLI drops to it and can't write `.cache/check`);
  run the CLI with `AGENT_FACTORY_ALLOW_ROOT=1`. `pkill -f` / `pgrep -f` with a pattern in the command line
  kills the shell itself: use `ps -ef | grep next-server` and kill the pid.
- **Cloud without PyPI (2026-10-27):** on some accounts the cloud workspace can't reach pypi.org or
  files.pythonhosted.org (403 from the egress proxy), so `uv sync` fails; GitHub and npm still work. Workaround:
  `python3 -m venv --system-site-packages engine/.venv` (the image has pydantic, starlette, uvicorn, PyYAML,
  jsonschema, claude-agent-sdk), copy pytest, httpx, mypy and ruff from `~/.cache/uv/archive-v0/*` into its
  site-packages, clone fastapi, pydantic-settings, pytest-asyncio and annotated-doc from GitHub at the locked
  tags and list them in a `.pth` (pytest-asyncio needs a hand-made dist-info with its `pytest11` entry point),
  then run checks with `UV_NO_SYNC=1`. ruff and mypy may be a minor version behind the lock.
  Also give the venv its entry points, or `uv run` falls back to the system pytest and can't import
  `agent_factory`: a `.venv/bin/pytest` script calling `pytest.console_main()`, symlinks to the image's
  `ruff` and `mypy` in `~/.local/bin`, and `.venv/bin/agent-factory` calling `agent_factory.cli:run`.
- **Cloud venv without PyPI, extra (2026-10-28):** `pypi.org` is in the proxy's `no_proxy` list, so with
  PyPI blocked uv fails on DNS rather than a 403. Don't copy `mypy` from the uv cache (its compiled helper
  module is missing); symlink the image's `mypy` instead. A `.pth` with `engine/src` stands in for the
  editable install.
- **Cloud without npm either (2026-10-27):** some sessions also refuse registry.npmjs.org, so `npm ci` and
  `scripts/check web` can't run there; an engine-only change can still be checked, and CI builds the web.
  On 2026-10-30 every npm mirror (yarnpkg, npmmirror, jsdelivr, unpkg) was refused too, the Mac's agent shell got
  a 403 for npm, and the Mac repo had no `web/node_modules`. For a quick syntax and unused-name check of `.tsx`
  files, the global `tsc` with shorthand module stubs (`declare module "react";` …) works; ignore the stub errors.
- **Cloud Playwright:** the template pins Playwright 1.64.1 (chromium revision 1248), and its browser download
  is blocked in the cloud. Make a folder of symlinks `chromium-1248 → /opt/pw-browsers/chromium-1243` and
  `chromium_headless_shell-1248 → /opt/pw-browsers/chromium_headless_shell-1243`, then
  `PLAYWRIGHT_BROWSERS_PATH=<that folder> make e2e …`.
- **Scanner binaries in the cloud image (2026-10-29):** five tests fail there on main too
  (`test_checks.py::test_snyk_answers_with_its_token_only_in_the_environment` and four in `test_tools.py`: warnings
  kept, blocking finding, fallback to the agent, hold without fallback): the image has real scanners on PATH, so a
  "tool that doesn't run" runs. Compare against main before chasing them; CI is the reference.
- **Secret scan:** test passwords in the template are low-entropy words (`testtesttesttest`,
  `preview-preview`) so gitleaks' generic rule (entropy > 3.5) never flags a generated product.
- **Actions in their own module:** FastAPI resolves endpoint annotations from `actions.py`'s globals, so a
  separate actions module (like `decision_actions.py`) must not use `from __future__ import annotations`; it is
  imported at the end of `actions.py`.
- **API tests that start runs:** use the async client (`test_api._client`), not `TestClient`: runs scheduled
  inside a `TestClient` block die with its event loop.
- **UI:** buttons get a base style (nowrap, primary colours). A card built on `<button>` must undo it with
  utilities (`whitespace-normal text-foreground font-normal shadow-none justify-start`).

## Decisions captured (the ADRs hold the detail)
- **Trust:** only signed-in members start work. Text from tickets and repos is data, never instructions.
- **Change risk:** 6 categories; categories 1–4 need a second approver (ADR-0027).
- **Self-improvement:** allowed, with the owner approving PRs; the safety core is out of bounds.
- **Presets (2026-10-16):** they fit the lane. New builds can stop early but can't start late; assessments keep their shape.
- **Evaluation gate (2026-10-16):** only on lanes that build new products.
- **Golden journey (2026-10-21):** launch to end of life, live, on a demo-worthy product; freeze new ADRs and
  control surface until it runs once (roadmap).
- **Design decisions (2026-10-21, 2026-10-23):** ADR-0044. Chat ("discuss") only at stations a person owns,
  never at build, scan or deploy.
- **Stack packs (2026-10-16):** one pack per stack, serving new products, factory-product changes and team
  repos (ADR-0041). Built: FastAPI, Next.js.
- **Image findings (2026-10-18):** ADR-0042. **E2E before deploy (2026-10-21):** ADR-0043 PR 2.
- **Spend (2026-10-23):** the product shows model spend by phase; tests, scans, builds and deploys are local and free today.
- **Infra failures (2026-10-25):** the engine owns environment knowledge and classifies infra failures; no
  SRE/infra agent for routing.
- **Factory UI usability (2026-10-26):** "Needs you", collapsible panels, engine-decided event levels (in PR 3).
- **Planning in the repo (2026-10-26):** `docs/planning/` is the only copy of the plan; any session may edit
  it in the same branch as its work; chat projects hold only a pointer.
- **Ready for, proven, owned (2026-10-31):** master plan principle 7. Repeatable quality and safe operation
  folded into J1, J2 and J4 "Done when" (J1 stays open for a second product delivered first time; J2 adds the
  "ready for" tier; J4 adds the product owner first, availability target and alert, tested restore, resume
  after restart); team delivery (T) and the improvement loop (I) are the phases after J5.
- **Name:** undecided ("Agent Software Factory" is the working name in the UI).
- **Secret store (agreed 2026-10-11, deferred until after J5):** [plans/secret-store.md](plans/secret-store.md).
