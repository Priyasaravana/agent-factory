# ADR-0001: Build a software factory, not a single orchestrator agent

**Status:** accepted · 2026-09-25

## Context
The goal is a system where people only supply requirements and feedback, and
agents build and deploy the software. One option is a single "orchestrator" LLM
that decides what to do next. The other is a production line with fixed
stations and quality gates.

## Decision
Build a **factory**. The line is reusable (config, templates, prompts, skills,
gates) and the apps are its products. Orchestration is **hybrid**: a
deterministic state machine owns routing, budgets and gates, and LLM agents
work freely *inside* each station.

## Consequences
- Runs are repeatable, resumable and auditable. Every transition is persisted.
- Adding a product type means adding a template and a config entry, not engine code.
- The upfront cost is in templates, checks and verification, not prompts. For a
  single one-off app the factory would be overkill. It pays off through reuse.
- The line only works on standardised product lines. "Build anything" is out of scope.
