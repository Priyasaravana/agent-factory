# Working on the agent-factory repo (for coding agents and humans)

- Engine: `engine/` (Python 3.12, uv). Tests are the contract: `make test`.
  Never call real models in tests; use `FakeAgentRunner` / `FakeExecutor`.
- Run checks quietly (`scripts/check`, see its header): output goes to `.cache/check/<step>.log`
  and you get one line per step plus only the failures. While developing, run
  `scripts/check changed` (tests touched by the branch) or `scripts/check test <files> -k <name>`;
  before a commit or PR, `scripts/check all` (= `make verify`, the same checks as CI). Read a
  log only when a step fails, and rerun a single failing test with `--tb=long` rather than
  reading the whole log. mypy is judged against `engine/.mypy-baseline.txt`: never add to it to
  hide a new error; re-record it only after fixing known ones.
- If you change `models.py` or `actions.py`, run `make openapi` and commit
  `web/openapi.json` and `web/src/api/schema.d.ts` together (CI checks drift).
- Workflows (stations + agent specs + reference docs) are data and belong to the org, not a
  blueprint (ADR-0039): a workflow is a **lane** for one kind of work (`works_on`: factory |
  repo, `kinds`). The factory default lanes are listed under `workflows:` in the config and seeded
  from `workflow-templates/standard/lanes/`; org workflows are copies, found in the DB and
  assigned to products per kind (`Product.workflows`). A change runs its product's assignment,
  else the factory default lane (`ChangeManager.lane_for`); requesters never choose. Versions are
  immutable; "back to factory default" re-imports the shipped lane as a new version. Prefer
  template, spec and skill changes over engine changes (see docs/workflows.md). Data from before
  the lanes is not migrated: `agent-factory reset --confirm` (`reset.py`).
- `plugin/skills/` holds only our own skills. Upstream skills (BuilderIO) are
  imported at a pinned commit via `default_skills` in the config; never copy
  them into the repo. Layer guidance through `skill_prompts` instead of editing them.
- The engineering standards skills (ADR-0032: Well-Architected pillars, DevSecOps,
  ISO 12207) are in `plugin/skills/` and mirrored in `.claude/skills/` for Claude Code:
  use them when you design, build or review here too. Edit the `plugin/skills` copy and
  run `make skills-sync` (`tests/test_engineering_skills.py` checks they match).
- Stations must never report success without evidence. Missing evidence means
  FAILED or HELD.
- Guardrails live in `engine/src/agent_factory/agents/hooks.py`. Add a test in
  `tests/test_hooks.py` for every rule.
- `auth/` is a separate service (own package, container and database). It must not
  import the engine, and the engine must not import it: they meet only at the
  gateway headers `X-Auth-User` / `X-Auth-Role` (see ADR-0012).
- Agents and agent-written code (tests, Makefiles) run only in sandboxes
  (`engine/src/agent_factory/sandbox/`, ADR-0014): use `ctx.agent(...)` and
  `ctx.cmd(..., untrusted=True)`. Privileged steps (docker build, kubectl, helm,
  git publish) stay in the engine. New egress goes through `sandbox.egress`.
- Generated repos must stay at agent-readiness Level 3 (`readiness.py`). A new
  golden path ships the same signals in its template.
- Stack packs (ADR-0041): what the factory builds comes from a **stack pack**, an ADR-0039 pack
  (`packs/<id>/pack.yaml`, listed under `packs:` in the config) with a `stack:` section: template,
  golden-path skill, verify command, technologies, port, chart and `readiness.yaml`. A blueprint
  names its pack (`pack:`) and keeps the install's own settings; a pack field the config sets anyway
  is an override, recorded in evidence. Signal ids, levels, points and pillars stay in
  `readiness.py`; the stack-specific signals (`STACK_SIGNALS`) are proved by the pack's declarative
  rules (`file`, `glob`, `contains`, `matches`, `all`, `any`: pure, nothing executed), and a pack
  that leaves one unproved is refused at load. Lanes stay stack-neutral: the product's pack skill is
  preloaded for the design, implement and deploy-repair agents (`STACK_SKILL_HANDLERS`), never listed
  in a lane. Each change records the pack's id, version and folder SHA-256 (`stack_pack` event,
  provenance, sealed manifest). Pack code is in `packs.py`; tests in `tests/test_packs.py`.
  Requesters pick the stack on the create form (`ConfigView.stacks`: one card per blueprint that builds
  new products; the product records `pack`). A stack conflict offers `Change.switch_to`
  (`stack.switch_options`, pure) only before the first build: the first change, with the product repo
  still only its template (`ChangeManager.switch_stack`, tests in `tests/test_stack_switch.py`).
  The engine also reads from the pack: the liveness path the deploy smoke check calls
  (`Blueprint.health_path`), the e2e command the acceptance station runs against the live app before
  the verifier (`_run_e2e`), how tests tag requirement ids (`requirement_tags` in `readiness.yaml`, read
  by `traceability.TagRule`; required), `egress` (a proxy per egress set, used only by that pack's
  products: `SandboxManager.proxy_name`) and `toolchain` (`sandbox/toolchain.py`: optional image layers
  such as `chromium` are built only when an installed pack asks; preflight refuses a pack whose
  toolchain the image lacks). Packs: `fastapi-service`, `nextjs-web` (tests in
  `tests/test_nextjs_pack.py`). A web pack's template pins `@playwright/test` to the sandbox's
  `PLAYWRIGHT_VERSION`.
  Adding a stack is adding a folder: change the engine only for a new rule kind or toolchain.
- Every delivery provider implements `check()` (fast, read-only readiness) and
  `undeploy()` (archive) besides its capability methods. New change-starting actions
  go through the preflight gate (`manager.preflight.gate`, ADR-0015).
- Changes to `images/`, Dockerfiles, `docker-compose.yml` or the golden path must
  keep CI `images` and `e2e` green (they test what ships, ADR-0016). The factory's
  Python (factory/auth images, CI) is one version; apps pick theirs from the
  sandbox's `APP_PYTHONS` (`tests/test_python_versions.py`).
- Generated apps are spec-driven (ADR-0017): every requirement in
  `docs/requirements.yaml` is covered by a scenario (`covers`) and a test tagged the
  stack's way (`@pytest.mark.req("R1")` in FastAPI, `@R1` in a test title in Next.js).
  Coverage checks live in `traceability.py`; keep them pure functions of the repo's files.
- No root in the factory's containers (ADR-0018). The factory image runs as uid
  10001 with no capabilities; the only root step is `cluster/init.sh` in the
  one-shot `factory-init` service. Dockerfile build steps that run the CLI set
  `AGENT_FACTORY_ALLOW_ROOT=1`. A new service gets `cap_drop: [ALL]` and
  `no-new-privileges`, and is added to `tests/test_least_privilege.py` and
  `scripts/privilege-check.sh`. Directories the engine creates for a sandbox go
  through `hand_to_sandbox`.
- Change status transitions are recorded by the state store itself (`change_transitions`,
  ADR-0019): always save changes through `store.save_change`. Outcome metrics are pure
  functions in `outcomes.py`; a new metric gets a definition in
  `docs/outcomes.md` and an exact-value test in `tests/test_outcomes.py`.
- The `code-review` station (ADR-0020) is judged by the engine (`review.judge`), not by
  the agent's own verdict. Keep that rule for any new judging station: the agent
  reports evidence, deterministic code decides.
- Nothing an agent suggests changes another agent's prompt without a person
  (ADR-0021): suggested learnings are vetted by `retro.vet`, accepted by an admin
  into the workflow draft, and apply only once published.
- Every agent call goes through `observe.AgentCall` (ADR-0022): station agents via
  `ctx.agent(...)`, engine-owned agents (like the retro) explicitly. Guardrails
  report denials through `on_deny`; never drop them.
- Every mutating action is classified in `tests/test_access.py`: admin-only (under
  `/api/workflows` or `/api/skills`, enforced by the identity middleware) or in
  `MEMBER_ACTIONS`. A new action must be added deliberately to one of the two.
  An action on an existing product or change also goes in `STEERING` and calls
  `_may_steer` (the product's creator or an admin).
- Change evidence is sealed by the engine when a change stops (ADR-0023). New evidence
  goes under `artifacts/<change>/` before the seal, so the manifest covers it; never
  write there after a change has stopped, and never put holdout text or secret
  values there.
- Every readiness signal has exactly one primary quality pillar (ADR-0024,
  `pillars.py`); a new signal also updates the mapping pinned in
  `tests/test_pillars.py` and practices §7. Pillars never change the Level gate.
- Workflow changes are measured by the evaluation harness (ADR-0025, `evals.py`):
  the verdict is the pure `evals.judge`; evaluation products carry `eval_run_id`
  and stay out of the products list and Outcomes. A new change-stopping path must
  still reach `ChangeManager._after_stop`.
- A blueprint declares its `stack`; intake reports what a request requires and
  `stack.conflicts` (pure) decides. A new blueprint declares its stack, and
  every requirement keeps a hidden scenario or a `no_live_check` reason
  (`_validate_spec`).
- Credentials a provider needs reach commands through the environment or stdin,
  never the command line, and temp files are removed in the same step
  (`providers/oci.py`, `providers/helm.py`, ADR-0026). `test_cluster_target.py`
  greps commands and events for leaked values.
- Station handlers are named after the DevOps phase they serve (ADR-0028,
  `workflow.HANDLER_INFO`: label, phase, hint). A new built-in handler gets an
  entry there. Names from before ADR-0028 are no longer read (ADR-0037 fresh start).
  Never rename event data keys (`readiness`, `review`) or evidence files to match.
- Change risk (ADR-0027): new products and feedback go through `ChangeManager.screen`
  (acceptable-use rules, refusals audited, never stored as products); diffs through
  the pure `risk.check_diff`. A new rule gets a test in `tests/test_change_risk.py`
  that also shows an ordinary change doesn't trip it. Approving a risky change is
  admin work and never by the requester except break-glass; nothing approves
  automatically (evaluations included). Test fixtures for refusals use operator
  rules with neutral wording, never real harmful requests.
- Vocabulary (ADR-0029): a **product** is one app, a **change** is one iteration of it,
  a **blueprint** is how a kind of app is built and run. Use these words in code,
  API, UI and docs. Stored names that must stay (`runs/` and `artifacts/` folders,
  `run/<id>` branches, the evidence manifest's `run`/`order` keys, `EvalRun`) are
  listed in the ADR. A renamed persisted field keeps its old name as a read alias.
- Work items (ADR-0030): every change has a `kind`, a `source` and `requested_by`; every
  product a `target`. Start changes through `ChangeManager.start_change` / `feedback`,
  which refuse kinds and targets that are not available yet (`AVAILABLE_KINDS`,
  `AVAILABLE_TARGETS`). Kind-specific agent instructions live in
  `stations.KIND_GUIDANCE`; keep `new` and `feature` prompts unchanged unless an
  evaluation measures the change.
- Existing repos (ADR-0031): onboarded by URL (`assess.parse_repo_url`, https on allowed
  hosts) and cloned read-only by `Workspace.sync_repo` (no hooks, submodules or push).
  Assessment stations never change the repository; the assessor is observe-only and
  judged by the pure `assess.judge`. Changes (ADR-0033) run the `repo-change` lane, are
  made on a branch of the clone and leave only as a pull request (`pull-request` station,
  token from `existing_repos.write_token_ref` via the environment); the factory never
  merges. Pure helpers live in `repo_change.py`. New signals and rules go in
  `assess.py` with a test in `tests/test_existing_repos.py` that also shows a well-kept
  repo doesn't trip them. The report station folds every station's output (`rollup.py`, pure): issues
  deduplicated across the rules and the scans with every source kept, recommendations ranked
  by severity then impact (assessor's plus one per scan check it didn't ask for), and scan
  coverage stated: a scan that didn't run, was simulated or was answered by the agent is a
  gap, never a clean result. Tests in `tests/test_rollup.py`.
- Product work ledger (ADR-0040): every delivered assessment is folded into the product's
  `work_items` by the pure `ledger.reconcile` (fingerprints, status, history). An issue is fixed
  only when its check really ran again; judgement items close only after a change addressed
  them. Changes address items through `FeedbackInput.items` (`ChangeManager.feedback`); won't
  do / accepted risk need a reason (`decide_work_items`, steering). Tests in `tests/test_ledger.py`.
- Scan stations (ADR-0034/0035): a `scan` check is named after what it checks (`check:`
  secrets | code | dependencies | config); the provider (local tool, github, snyk, agent) is
  chosen under `checks:` in the config or per station, with an agent fallback
  (`checks.plan`, pure). Tools are catalogue entries (`tools.TOOLS`, extended by `tools:`);
  parsers are pure (`tools.PARSERS`), and a new tool or parser gets a test in
  `tests/test_tools.py` on its real output format. Never keep or log a tool's raw output:
  only normalised findings, without matched text. Service tokens are references passed
  through the environment. Agent findings are judged by `checks.judge_agent` (real files,
  capped at high).
- Model access goes through the gateway seam (ADR-0036, `gateway.py`): `ClaudeAgentRunner`
  opens a gateway session per agent call (a virtual key behind a gateway) and closes it in
  a `finally`. A sandbox never gets a credential the session withholds
  (`SandboxSpec.withhold_env`); a refused key holds the call, never falls back to direct
  access. A new gateway is one class in `gateway.PROVIDERS`, tested with a fake HTTP
  function in `tests/test_gateway.py`.
- Station contract (ADR-0037): every station follows a type in `contract.STATION_TYPES` that fixes
  its column, `needs` and `gives` (versioned artifact types such as `spec@1`). Publishing checks
  container fit (column, data, floor) and owner role ids (`contract.fit_errors`, pure). When code
  changes, test, a secret scan and change-risk must run after the last branch change and before
  any `image`/`url`/`pr`; a repair that changes the branch re-enters verify. The engine records
  entry artifacts and each station's outputs in `artifacts/<change>/ledger.json` and checks them
  against `schemas/artifacts/<type>.v<n>.json` (`engine/handoff.py`): an invalid output fails its
  station. A breaking change to an artifact is a new schema version, never an edit; a new
  built-in handler gets a type and a collector, with a test in `tests/test_station_contract.py`.
  A station's handler follows its lane's `works_on` (`handler_for`); publish refuses a station
  without one, so a run shows only its lane's stations (`WorkflowDoc.plan`).
- Phases and capabilities (ADR-0039 §2–§5): columns come from the phase registry
  (`contract.PhaseRegistry`: the seven built-ins, org look-and-decide columns from `phases.columns`,
  pack phases from the packs under `packs:`). Each station uses capabilities inside its phase's `allows`
  (`contract.capability_errors`); a new built-in type declares `uses`. At runtime a station gets only
  its grant (`WorkflowDoc.grant`): agent tools through `ctx.agent`, credentials through
  `ctx.secret(ref, purpose, capability)`. A new credential use names the capability it serves; never
  call `ctx.secret` without one inside a station. Tests in `tests/test_phases.py`.
- The builder (ADR-0039 1c): what a phase box offers comes from `station_templates.py` (built-in
  types, scans per check, a custom step, pack stations). Keep it pure; the draft stores, publish
  still checks everything. A new built-in type is offered automatically in its column; a new preset
  gets a case in `tests/test_builder.py` showing it publishes on each default lane.
- Record significant decisions as ADRs in `docs/adr/`. Every ADR has a **Best practices** section mapping the decision to
  `docs/practices.md` (from ADR-0041 on); update practices.md in the same change when a decision
  closes or opens an item there.
