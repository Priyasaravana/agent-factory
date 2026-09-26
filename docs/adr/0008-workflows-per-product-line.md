# ADR-0008: "Workflow" per product line, templates, role checks

**Status:** accepted · 2026-09-26

## Context
"Line" was unfamiliar to users; "workflow" is the expected term. Different
products need different flows (e.g. a security-reviewed API versus a plain one),
and changes to one product's flow must not affect another. Workflows should
start from standard templates, and publishing must not allow an agent whose
tools don't fit its station.

## Decision
- Rename line to **workflow** everywhere visible (UI, API, CLI, docs,
  `workflow-templates/`). Data is migrated in place: the tables are renamed, and
  the old `default` line becomes the first product line's workflow.
- **One workflow per product line.** The workflow id is the product line id.
  Each workflow has its own versions and draft. An order runs its product
  line's active workflow, and each run is pinned to `(workflow_id, version)`.
- **Templates:** built-in templates live in `workflow-templates/`. A draft can
  start from a built-in template, or from a GitHub folder fetched with git at
  a pinned commit (a separate read-only token for private repos).
- **Role checks:** each engine handler declares what its agent must, or must
  not, be able to do (write files, use the shell). Violations block publishing.
  Advice (missing recommended skills, fast tier on judgment work, reviewers that
  can write, unused agents) is shown as non-blocking warnings.

## Consequences
- Adding a product line with a different workflow is now a config entry plus a
  template.
- The workflow is still a sequence with failure routes, not a free-form graph.
  The builder UI enforces that.
