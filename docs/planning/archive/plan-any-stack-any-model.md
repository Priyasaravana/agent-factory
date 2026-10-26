# Plan: any stack, any model per stage, skills that improve themselves

Archived: **merged into [../master-plan.md](../master-plan.md)** on 2026-10-02, which keeps everything below and adds
work items, tool stations, a skill studio, agentic packs, DevOps practices, autonomy and self-improvement.

## Summary of what moved
- **Stack packs:** a template, `pack.yaml`, a golden-path skill and evals per stack (master plan §3E; built as ADR-0041).
- **Model profiles:** Ollama's Anthropic-compatible API or a LiteLLM gateway, chosen per agent and smoke-tested, with model comparison in evaluations (master plan §3F).
- **Skills improved by evidence:** retro skill diffs, evaluated before a person accepts them (master plan §3D). The skills library lives inside the factory.
- **Pack order:** agentic packs first, then Python worker / data pipeline, TS web (Next.js), Go and Java packs. (Superseded: Next.js was built second, 2026-10-18.)

## Decisions that were open (carried into the master plan §7)
1. LiteLLM gateway as a factory service (recommended; built as ADR-0036).
2. Next.js for the TypeScript web pack (recommended; built).
3. A Postgres-backed worker queue (recommended).
4. Skill auto-accept: a person always decides for now (recommended).
