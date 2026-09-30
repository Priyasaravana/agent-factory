# Working on the agent-factory repo (for coding agents and humans)

- Engine: `engine/` (Python 3.12, uv). Tests are the contract: `make test`.
  Never call real models in tests; use `FakeAgentRunner` / `FakeExecutor`.
- If you change `models.py` or `actions.py`, run `make openapi` and commit
  `web/openapi.json` and `web/src/api/schema.d.ts` together (CI checks drift).
- Workflows (stations + agent specs + reference docs) are data: one per product
  line, seeded from `workflow-templates/<name>/`, stored as immutable versions in
  the DB. Runs are pinned to (workflow, version). Prefer template, spec and skill
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
  `undeploy()` (archive) besides its capability methods. New run-starting actions
  go through the preflight gate (`manager.preflight.gate`, ADR-0015).
- Changes to `images/`, Dockerfiles, `docker-compose.yml` or the golden path must
  keep CI `images` and `e2e` green (they test what ships, ADR-0016). The factory's
  Python (factory/auth images, CI) is one version; apps pick theirs from the
  sandbox's `APP_PYTHONS` (`tests/test_python_versions.py`).
- Generated apps are spec-driven (ADR-0017): every requirement in
  `docs/requirements.yaml` is covered by a scenario (`covers`) and a test tagged
  `@pytest.mark.req("R1")`. Coverage checks live in `traceability.py`; keep
  them pure functions of the repo's files.
- Data under `/data` belongs to the runtime user (uid 10001). Exec into the
  factory container with `docker compose exec -u factory factory …`; the
  `agent-factory` console script also drops root itself (`cli.run`). Directories
  the engine creates for a sandbox go through `hand_to_sandbox`.
- Record significant decisions as ADRs in `docs/adr/`.
