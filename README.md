# Agent Factory

A local **software factory**. You give it requirements, and later feedback.
AI agents do everything in between: they specify, design, build, test,
package, deploy to Kubernetes and independently verify the result.

Everything runs in containers. Your laptop only needs Docker.

```
requirements ──► intake ► design ► build ► verify ► package ► deploy ► acceptance ► deliver ──► running app
   (you)          agent    agent   agent    check    check     check     agent        check       + your feedback
                                     ▲        │         │        │  ▲       │
                                     └────────┴─────────┘        ▼  │       │
                                        evidence of failure   deploy_fix    │
                                                               (agent)      │
                                     ◄──────────── observed behaviour ──────┘
```

- **Hybrid orchestration.** A deterministic pipeline (the *line*) moves work
  between stations. Agents built on the Claude Agent SDK do the judgment work
  *inside* the stations. An LLM never picks the route.
- **Two human touchpoints.** You give requirements (and answer questions only
  when intake has no safe assumption). After deploy, you give feedback.
- **Evidence over claims.** Checks are deterministic. Acceptance is done by an
  independent verifier agent. It runs **holdout scenarios** that the builder
  never sees against the live app.
- **Sandboxed agents.** Every agent session, and every run of code the agents
  wrote, gets a throw-away container that can write only its worktree, holds no
  factory secrets, and reaches only an egress allowlist
  ([ADR-0014](docs/adr/0014-agent-sandbox.md)). Check it with `make sandbox-check`.
- **Level 3 by default.** Generated apps start at agent-readiness Level 3:
  - CI, CODEOWNERS and pre-commit;
  - JSON logs, metrics and tracing;
  - a secret scan, SBOM and provenance.

  A Readiness station holds every run to that bar ([practices](docs/practices.md)).
- **Checked before it starts.** Readiness checks cover the model, sandbox,
  Docker, cluster, free ports and credentials. They refuse an order in seconds,
  before any model usage, if something it needs is broken
  ([ADR-0015](docs/adr/0015-readiness-and-preflight.md)).
- **API-first.** A Python engine (FastAPI) exposes an OpenAPI contract. The
  TypeScript UI (React + Vite, Tailwind + Radix components, ⌘K command palette,
  dark/light themes) uses types generated from that contract.

## Quick start

```bash
cp .env.example .env        # FACTORY_MODE=dry-run by default
make up                     # builds and starts dind + factory + web
open http://localhost:8080  # sign in (see docs/runbook.md), submit an order, watch the workflow run
```

**Dry-run** simulates the agents and commands. Git is still real. Use it to
try the UI, gates, fix loops and resume without spending any model usage.

### Going live

1. Choose one model auth in `.env`. See [ADR-0005](docs/adr/0005-model-access.md).
   - **Subscription:** run `claude setup-token` on your Mac and paste the
     token into `CLAUDE_CODE_OAUTH_TOKEN`.
   - **API key:** set `ANTHROPIC_API_KEY`.
2. To publish generated apps to GitHub, set `GITHUB_TOKEN`, `GIT_AUTHOR_NAME`
   and `GIT_AUTHOR_EMAIL`.
3. Set `FACTORY_MODE=live`, then `make up`. The first start creates the kind
   cluster inside the dind container, which takes about 2 minutes.
4. Submit an order. The delivered app is served on `http://localhost:8081`
   (the second product on `:8082`, and so on, up to 20 apps on `:8100`).
   Archive orders you no longer need to free their port.

See [docs/runbook.md](docs/runbook.md) for the first live run and troubleshooting.

## What's where

| Path | What it is |
|---|---|
| `.agent-factory/config.yaml` | Factory settings: models, product lines, policies, budgets, gates, skill guidance. |
| `workflow-templates/` | **Workflow templates**: `workflow.yaml` (stations, routes), `agents/*.md` (one spec per agent), `docs/*.md` (reference docs). Each product line's workflow is seeded from one and then edited in the UI; see [docs/workflows.md](docs/workflows.md). |
| `engine/` | Python engine and API: state machine, stations, agent runner, guardrail hooks. |
| `web/` | TypeScript UI. `openapi.json` is the contract; `src/api/schema.d.ts` is generated from it. |
| `templates/` | Golden paths. `fastapi-service` is a FastAPI + Postgres app with a Helm chart. |
| `prompts/` | The shared station contract every agent receives. |
| `plugin/` | The factory's own skills. BuilderIO skills are imported on first start (pinned in `default_skills` in the config), and more can be imported in the UI; see [docs/skills.md](docs/skills.md). |
| `cluster/` | kind-in-dind config, bootstrap and container entrypoint. |
| `images/factory/` | Factory runtime image: engine plus docker CLI, kind, kubectl, helm, gh, uv. The vulnerability scanner runs as a container in dind. |
| `docs/` | Architecture, ADRs, scaling path, runbook. |

## Design docs

- [Architecture](docs/architecture.md)
- [ADRs](docs/adr/): the factory model, stack, isolation, reuse of Builder
  practices, model access, holdout verification
- [Scaling path](docs/scaling.md): seams already in the code, and what changes for team use
- [Best practices we measure against](docs/practices.md) and the [threat model](docs/security/threat-model.md)

## Development

```bash
make test        # engine tests: full workflow in dry-run, hooks, API (no model usage)
make lint
make openapi     # after changing engine models/actions: regenerate contract + UI types
cd web && npm run dev   # UI dev server on :5173, proxied to the engine on :8000
```

Credits: the Factory conventions and the default skills come from
[BuilderIO/skills](https://github.com/BuilderIO/skills) (MIT); they are imported
at a pinned commit (see `default_skills` in `.agent-factory/config.yaml`), not
copied into this repo. The shared-actions pattern comes from
[BuilderIO/agent-native](https://github.com/BuilderIO/agent-native).
