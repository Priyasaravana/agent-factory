# Agent Factory: handoff (updated 2026-11-01)

Read [README.md](README.md) and [roadmap.md](roadmap.md) first. This page holds the **current state**
(replace it at the end of every session) and **how we work** (keep it; add lessons as they're learned).

## Current state (2026-11-01, evening)
- **Main** (`8680c44` on GitHub): J2 slices 1–5 and ADR-0046 slice 1 (rollback, PR #131) merged.
- **Decided 2026-11-01 (Saravana):** ADR-0046 accepted; slices 2 (temporary validation environment) and 3
  (promote the tested image) go in one PR, because slice 2 alone would never update production on a `helm`
  target.
- **In review:** `feat/j2-validate-promote`: ADR-0046 slices 2 and 3.
  - Engine: `validation.py` (pure: `release_name`, `in_place_reason`, `slot`, `needs_redeploy`,
    `promote_problems`, `same_image`); `Change.validation` (`models.Validation`); `StationContext.slot` /
    `release_name`, which the `helm` provider uses for host, namespace, release, Secrets and diagnostics
    (`validates_separately = True`; `local` is `False` and refuses to undeploy a slot).
  - Pipeline: `_validation_slot` records the temporary environment before deploy touches the cluster;
    `remove_validation` at every stop (`_execute`, cancel), on archive and in `sweep_validation` at start-up
    (`app_factory`); a resumed change redeploys before acceptance; the healthy release is recorded at
    `promote` (still at acceptance in place); `rollback.target` restores only while the change's own release
    may serve production.
  - Stations: `deploy` splits into `_deploy_for_validation` and `_deploy_production`; new `promote` station
    (contract type in the Deliver column, uses `deploy:*` + `promote`), added after acceptance in the New
    product and Product change lanes. Existing DB lanes keep validating in place until "Back to factory
    default" (or an edit) adds `promote`.
  - Tests: `tests/test_validation_env.py` (15); `test_cluster_target`, `test_rollback`, `test_journey` updated.
    `FakeHelm` handles `uninstall`.
- **Checks on the branch (cloud, 2026-11-01):** lint, mypy (no new errors), auth, OpenAPI drift and web (tsc +
  vite) clean; engine 1082 passed, 5 failed: `test_tools.py` ×4 and `test_checks.py::test_snyk_…`, the same
  five that fail on clean main in this cloud workspace (its scanner tools change what the fakes see).
  `golden-path` and `images` need Docker and Helm, absent here; no pack, template, image or Dockerfile is
  touched. GitHub CI is the gate.
- **Next:** Saravana reviews and merges; iteration 4 on Kudos board from the card (on `local`: in place,
  records its first release); a live run on `local-ingress` exercises the temporary environment for real
  (egress `*.localtest.me:8180` already covers `<slug>-c<id>` hosts); then slice 4, strategy per product.
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

### Lessons
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
