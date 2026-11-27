# Plan: stack packs, breadth without losing the evidence

Status: **reference, proposed** (discussion 2026-11-26, kept at Saravana's request 2026-11-27). Nothing here is
decided; the questions at the end are Saravana's call. Packs today: `fastapi-service` and `nextjs-web` (ADR-0041),
plus `ops-pack`.

## The question (Saravana, 2026-11-26)
Two packs is very little for today's market. The aim is to support every kind of development and every technology,
web UI included. AI-driven development already writes any stack on the go: is the pack model an advantage or a
disadvantage?

## Answer: an advantage as the proof layer, a disadvantage only as a gate
- **The factory doesn't sell code, it sells evidence.** A model can write Elixir or Kotlin. What the factory promises
  (tests tagged to requirements, Level 3 readiness, a scanned image, a chart that meets Pod Security `restricted`, a
  health path, e2e against the live app, backup and restore commands) comes from the pack, not the model. Without a
  pack the factory can't prove anything, and "never report success without evidence" makes it hold: correct, but to a
  user it looks like "unsupported".
- **Models are much weaker off the mainstream.** Across 3,011 problems, top models passed over 60% first time in
  Python, Java and JavaScript but often under 25% in Erlang or Racket; on hard problems 50–63% against 0–6%. Full-stack
  CRUD tasks took 1–2 correction rounds on mainstream stacks and 5–10 on niche ones; Go and Rust held up reasonably
  ([arXiv 2509.23261](https://arxiv.org/html/2509.23261v2)). In the factory every extra round is a fix loop: time,
  spend and more holds.
- **The market leaders narrow on purpose.** Lovable generates one stack (React, TypeScript, Tailwind, shadcn/ui,
  Vite, Supabase) ([MindStudio](https://www.mindstudio.ai/blog/what-is-lovable)); Bolt and v0 also stay on mainstream
  JavaScript stacks ([Lovable comparison](https://lovable.dev/guides/bolt-vs-replit-vs-lovable)). Narrow is how they
  get reliable results.
- **Platform engineering is going the same way for agents:** the agent does the judgement work, deterministic checks
  gate anything that matters, start with one bounded path and widen it
  ([Datadog](https://datadoghq.com/blog/golden-paths-for-ai-agents);
  [CNCF](https://www.cncf.io/blog/2026/07/21/platform-engineering-for-the-agentic-enterprise-managing-applications-resources-and-ai-agents/)).
  A pack is exactly that path.

## Where packs hurt today
- Each pack is hand-built (`nextjs-web` took several slices), so this doesn't scale to "every technology".
- A stack without a pack has nowhere to go: a stack conflict, not a lesser but honest route.
- The gap is in kinds of software as much as count: mobile apps, libraries and CLIs, data pipelines and infrastructure
  code don't end in a Helm deploy. They need new deploy targets (`AVAILABLE_TARGETS`) in the engine, not only packs.
- No composite apps from several packs (web, API and worker together); full-stack Next.js + FastAPI is in the backlog.

## Proposal: three tiers
1. **Certified packs** with every guarantee. Few, chosen by what the first customers (UK/EU scale-ups) run: probably
   Spring Boot (Java/Kotlin), a Go service, .NET, a static React/Vite single-page app, a Python worker or agent
   service, and the full-stack combination.
2. **Draft packs written by the factory.** For a stack with no pack, the architect agent drafts one (template,
   readiness rules, verify command, chart); the pack evaluation judges it and an admin publishes it. Nothing an agent
   writes takes effect without a person (ADR-0021's rule, applied to packs). This is the "every tech on the go" answer
   without giving up the evidence: adding a stack is already adding a folder.
3. **Any stack, best effort, for team repos.** Detection already gives guidance (ADR-0041 §5). With no matching pack,
   take the verify command from the repo's own CI and say plainly in the evidence what wasn't proven, instead of
   refusing.

**Packs are also the open-source lever.** Ship a pack authoring guide and an `agent-factory pack check` command with
v0.1.0, so the community adds packs the way Terraform providers and Backstage templates grew.

## Proposed timing
- Phase D is full (paid features and the UI). Only the authoring guide and `pack check` would join phase E.
- A **phase for packs after v0.1.0** (January): draft-pack generation, 3–4 certified packs chosen with the pilot
  customer, composite apps. The non-web kinds follow as their own deploy targets.
- Phase names are not settled: [enterprise-integration.md](enterprise-integration.md) also proposes phases P and R.

## Questions for Saravana
1. Which stacks become certified first?
2. Should a stack with no pack wait for its draft pack, or build best-effort while the pack is drafted alongside?
3. Do the authoring guide and `pack check` go into phase E?
4. Where does the packs phase sit against phase T and the proposed phases P (environments) and R (continuity)?
