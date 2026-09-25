# Working on the agent-factory repo (for coding agents and humans)

- Engine: `engine/` (Python 3.12, uv). Tests are the contract: `make test`.
  Never call real models in tests; use `FakeAgentRunner` / `FakeExecutor`.
- If you change `models.py` or `actions.py`, run `make openapi` and commit
  `web/openapi.json` and `web/src/api/schema.d.ts` together (CI checks drift).
- The line lives in `.agent-factory/config.yaml`. Prefer config/template/prompt
  changes over engine changes.
- Vendored skills in `plugin/skills/` are unmodified upstream copies. Layer
  guidance through `skill_prompts` instead of editing them.
- Stations must never report success without evidence. Missing evidence means
  FAILED or HELD.
- Guardrails live in `engine/src/agent_factory/agents/hooks.py`. Add a test in
  `tests/test_hooks.py` for every rule.
- Record significant decisions as ADRs in `docs/adr/`.
