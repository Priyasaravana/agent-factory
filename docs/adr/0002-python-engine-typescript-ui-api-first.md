# ADR-0002: Python engine, TypeScript UI, API-first

**Status:** accepted · 2026-09-25

## Decision
- **Engine and API:** Python 3.12, FastAPI and the Claude Agent SDK (Python).
  The engine is I/O-bound (model calls, docker, helm), which asyncio handles
  well. Python is also the lingua franca of AI and platform engineers.
- **UI:** TypeScript with React, Vite and TanStack Query. The richer UI
  ecosystem matters because the UI is the product surface for adopters.
- **Contract:** the engine's OpenAPI document (`web/openapi.json`) is the
  contract. UI types are generated from it with `openapi-typescript`. CI fails
  if either one drifts.
- **Shared actions** (from BuilderIO agent-native): each capability is defined
  once in `engine/src/agent_factory/actions.py`. That definition becomes both
  an HTTP route and, when `agent_tool=True`, an MCP tool for agents. The UI and
  agents therefore share one action layer, with the same validation.

## Rejected
- **Adopting the agent-native framework directly.** It is TypeScript-only, and
  its Factory template is coupled to Builder.io services (vault, Dispatch,
  `@builderio-bot`).
- **HTMX server-rendered UI.** Simpler, but it would limit adoption and make a
  later product UI a rewrite.
