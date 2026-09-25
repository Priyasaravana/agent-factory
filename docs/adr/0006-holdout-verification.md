# ADR-0006: Independent acceptance with holdout scenarios

**Status:** accepted · 2026-09-25

## Context
Agents learn to satisfy the tests they can see. With the only human gate after
deployment, confidence has to come from verification, not from reviewing every line.

## Decision
- Intake writes two sets of scenarios: **acceptance** scenarios (visible, in the
  repo) and **holdout** scenarios (stored under `.factory-data/holdout/`, outside
  every worktree).
- PreToolUse hooks deny any builder-agent access to the holdout path.
- The **Acceptance** station runs a separate verifier agent. It uses the
  agent-watchdog skill and is observe-only: it may call the live app, read
  files, and run `kubectl get/logs`. It runs the holdout scenarios against the
  deployed app with real HTTP calls.
- When a scenario fails, the builder receives only the **observed behaviour**,
  never the scenario text. That lets it fix the defect without overfitting to
  the hidden checks.

## Consequences
- An iteration costs one more agent session (the verifier) in return for much
  stronger evidence.
- Holdout quality depends on intake. Review holdouts in `.factory-data` if a
  product matters.
