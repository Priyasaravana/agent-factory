# Scaling path

v0 is built for one person on one laptop. These seams are already in the code,
so each change below replaces an implementation rather than rewriting the engine.

| Seam | v0 (now) | Team / platform | Where |
|---|---|---|---|
| State | SQLite (JSON docs) | Postgres, same `StateStore` protocol | `state/` |
| Command execution | `LocalExecutor` in the factory container → dind | `KubernetesJobExecutor`: one sandboxed pod per step (gVisor/Kata) | `executor.py` |
| Agent runtime | `ClaudeAgentRunner`, in-process | same runner inside per-run worker pods | `agents/runner.py` |
| Workflow durability | persisted state machine, manual resume | a worker queue; Temporal (Python SDK) if you need retries across hosts | `engine/pipeline.py` |
| Model auth | subscription token | API key / Bedrock, spend limits per run | `.env`, ADR-0005 |
| Deploy target | kind in dind, NodePorts | any cluster via the same Helm charts, Gateway API, a registry | `templates/*/deploy` |
| Concurrency | `max_concurrent_runs: 1` | N workers, one namespace per run | `config.yaml` |
| Observability | event log + UI timeline | OpenTelemetry traces, cost per run/station | events → OTel exporter |
| Access | localhost only | SSO in front of web/API, per-user orders | web + API gateway |

## Adoption surface (what others change without touching Python)
- `.agent-factory/config.yaml`: stations, routes, policies, budgets, models, skills per role
- `templates/<product-line>/`: a new golden path (e.g. Next.js, Go service)
- `prompts/<role>.md`: role behaviour
- `plugin/skills/<name>/SKILL.md` (our own) or `default_skills` imports: portable skills in the open skills format,
  usable in Claude Code or Codex too
- Plugins for new station types or providers (future: Python entry points)
