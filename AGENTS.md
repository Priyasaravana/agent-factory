# Working on the agent-factory repo (for coding agents and humans)

- Engine: `engine/` (Python 3.12, uv). Tests are the contract: `make test`.
  Never call real models in tests; use `FakeAgentRunner` / `FakeExecutor`.
- If you change `models.py` or `actions.py`, run `make openapi` and commit
  `web/openapi.json` and `web/src/api/schema.d.ts` together (CI checks drift).
- Workflows (stations + agent specs + reference docs) are data: one per
  blueprint, seeded from `workflow-templates/<name>/`, stored as immutable versions in
  the DB. Changes are pinned to (workflow, version). Prefer template, spec and skill
  changes over engine changes (see docs/workflows.md).
- `plugin/skills/` holds only our own skills. Upstream skills (BuilderIO) are
  imported at a pinned commit via `default_skills` in the config; never copy
  them into the repo. Layer guidance through `skill_prompts` instead of editing them.
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
- Every delivery provider implements `check()` (fast, read-only readiness) and
  `undeploy()` (archive) besides its capability methods. New change-starting actions
  go through the preflight gate (`manager.preflight.gate`, ADR-0015).
- Changes to `images/`, Dockerfiles, `docker-compose.yml` or the golden path must
  keep CI `images` and `e2e` green (they test what ships, ADR-0016). The factory's
  Python (factory/auth images, CI) is one version; apps pick theirs from the
  sandbox's `APP_PYTHONS` (`tests/test_python_versions.py`).
- Generated apps are spec-driven (ADR-0017): every requirement in
  `docs/requirements.yaml` is covered by a scenario (`covers`) and a test tagged
  `@pytest.mark.req("R1")`. Coverage checks live in `traceability.py`; keep
  them pure functions of the repo's files.
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
  entry there; old names stay readable through `LEGACY_HANDLERS`, because stored
  versions, changes and evidence keep their station ids. Never rename event data
  keys (`readiness`, `review`) or evidence files to match.
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
  Repo stations (`workflow.REPO_HANDLERS`) never change the repository; the assessor is
  observe-only and judged by the pure `assess.judge`. New signals and rules go in
  `assess.py` with a test in `tests/test_existing_repos.py` that also shows a well-kept
  repo doesn't trip them.
- Record significant decisions as ADRs in `docs/adr/`.
