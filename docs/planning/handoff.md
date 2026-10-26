# Agent Factory: handoff (updated 2026-10-26)

Read [README.md](README.md) and [roadmap.md](roadmap.md) first. This page holds the **current state**
(replace it at the end of every session) and **how we work** (keep it; add lessons as they're learned).

## Current state (2026-10-26)
- **Main** (`c5ec9b0`) includes ADR-0044 PR 3 (look and feel, `nextjs-web` 1.4.0, "Needs you", collapsible
  panels, event levels), ADR-0045 (approved base images, with its CI/template fixes `ef7ac48` and
  `afe4148`) and the Playwright proxy fix in the template (`9564283`, 1.3.2).
- **In review:** `docs/planning-in-repo`, the plan moved from the chat project into `docs/planning/`.
- **Live:** Book lending `66697b75071c` was resumed on 2026-10-26 to prove the Playwright proxy fix. If it
  still says `ENOTFOUND`, cancel rather than loop. Outcome not recorded yet: check the change before acting.
- **Next:** the "Before the acceptance run" items in the roadmap (no-progress stop and target probe, journey
  acceptance test, pin Trivy and syft), then the J1 acceptance run with Design decisions set to **Ask a
  person**. J2 then iterates on **that acceptance product**, not Book keeping.
- **Not yet proved live:** the chart's admin Secret (no helm in the cloud; CI renders it) and the cluster
  read of it. The J1 acceptance run proves both.
- **After merging on the Mac:** New product and Product change show "template update available": Back to
  factory default, then set **Design decisions → Ask a person** in the editor (card under Spec review gate)
  and publish.
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
- Still to build (roadmap J1): the no-progress stop (empty implement diff + same failure fingerprint → hold
  with the cause) and the target probe (acceptance checks the e2e URL from the sandbox the way the test
  client reaches it; a failure is `infra`). Later, optional: classifier rules for `ENOTFOUND` /
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
- **Saravana** owns the repo, runs fetch, push and merge ("Rebase and merge"). Agents never commit to main
  and never push.
- **Repo on the Mac:** `/Users/saravana/Saravana_Repos/agent-factory` (through the remote-device bridge:
  `$HOME/mnt/agent-factory`).
- **His local config edits:** uncommitted edits in `.agent-factory/config.yaml` (environment `local-ingress`,
  gateway `litellm` + subscription). With them in place, 19 tests fail on main. Never run `uv` or
  `scripts/check` inside his folder: it must stay clean, and tests there read his config.
- **How Saravana likes to work:** poll-style questions and concise answers; UI that is presentable and easy
  to use; each change states its value per part; he asks what value each option brings. When he answers
  "explain", give the issue, options and a recommendation in plain words, then ask again.

### The loop (cloud workspace ↔ Mac)
- The Mac's shell has no network (npm and pypi unreachable since 2026-10-18).
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
  Trivy DB download.
- **Reading live state:** stage `.factory-data/factory.db` (plus `-wal` and `-shm`) and `artifacts/<change>/`,
  then read them with Python's sqlite3 in the cloud (no `sqlite3` CLI there). A product's git is
  `GIT_DIR=.factory-data/products/<name>/.git`, branch `run/<change>`.
- **Cloud container:** never create a uid 10001 user (the CLI drops to it and can't write `.cache/check`);
  run the CLI with `AGENT_FACTORY_ALLOW_ROOT=1`. `pkill -f` / `pgrep -f` with a pattern in the command line
  kills the shell itself: use `ps -ef | grep next-server` and kill the pid.
- **Cloud Playwright:** the template pins Playwright 1.64.1 (chromium revision 1248), and its browser download
  is blocked in the cloud. Make a folder of symlinks `chromium-1248 → /opt/pw-browsers/chromium-1243` and
  `chromium_headless_shell-1248 → /opt/pw-browsers/chromium_headless_shell-1243`, then
  `PLAYWRIGHT_BROWSERS_PATH=<that folder> make e2e …`.
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
- **Name:** undecided ("Agent Software Factory" is the working name in the UI).
- **Secret store (agreed 2026-10-11, deferred until after J5):** [plans/secret-store.md](plans/secret-store.md).
