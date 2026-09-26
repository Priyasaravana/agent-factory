# ADR-0007: Lines and agent specs are versioned data per instance

**Status:** accepted · 2026-09-26

## Context
Each factory instance (for example, one per client) needs its own line: its
own agents, skills and stations. The MVP hard-coded roles in Python and kept the
stations in `config.yaml`, so nothing could be customised without code changes,
and a UI line builder or an improvement loop had nothing to edit.

## Decision
- An **agent spec** is a Markdown file with YAML frontmatter: model tier, tool
  preset, skills, turn budget and promised outputs. The body is the system prompt.
- A **line** is a list of stations that reference specs and routes.
- **Blueprints** are shipped in the repo. Each instance seeds v1 from a
  blueprint into its DB. Versions are immutable, exactly one is active, and
  **each run is pinned to its version**.
- The format for export and import is the same folder format as a blueprint. It
  is how lines are reviewed, shared between instances and committed to git.
- Tool **presets** are the permission ceiling, and the guardrail hooks still
  apply. Observe-only is a property of the preset, not of an agent's name.
- A **generic agent handler** runs any custom agent station under the same
  evidence contract as the built-in stations.

## Consequences
- The UI editing, line builder and improvement loop can all work on data.
- Old runs remain explainable, because their line version is kept forever.
- Secrets never go in lines. Golden-path templates stay in git, referenced by
  the product line.
