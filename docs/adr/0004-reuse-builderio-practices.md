# ADR-0004: Reuse BuilderIO Factory practices instead of inventing our own

**Status:** accepted · 2026-09-25

## Context
BuilderIO/skills ships an experimental *Factory* skill set for turning signals
into policy-gated changes, along with general agent skills. BuilderIO/agent-native
is a TypeScript agent+UI framework. Neither one covers greenfield builds
(spec → code → image → deploy). Both encode practices worth keeping.

## Decision
We adopt the following conventions:

| Practice | Where |
|---|---|
| `.agent-factory/config.yaml` as the single factory config | config loader, UI |
| Independent policy per action (implement/deploy/publish/merge/recover). One never implies another. | `policies` |
| Missing or partial evidence means a **hold**, never a plausible success | station contract, pipeline |
| `skill_prompts` overlays instead of forking skills | `config.skill_overlay` |
| Fresh worktree per run | `Workspace.create_worktree` |
| Recover only with the original authorization. Restarts mark runs interrupted. | `recover_on_startup` |
| Shared actions for UI and agents (agent-native) | `actions.py` |

We **vendor unmodified** skills pinned at commit `a74a3a0`: plow-ahead,
agent-watchdog, efficient-frontier, read-the-damn-docs, quick-recap,
stay-within-limits, factory-recover. See `plugin/skills/VENDORED.md`.

## Phase 2
The post-deploy loop can adopt `factory-collect`, `factory-lookback`,
`factory-babysit-pr` and `factory-ship`. Your feedback in the UI becomes a
configured *source*.
