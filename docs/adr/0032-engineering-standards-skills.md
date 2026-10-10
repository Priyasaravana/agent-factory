# ADR-0032: Engineering standards as skills, for the factory's agents and for us

**Status:** accepted · 2026-10-10 · relates to [ADR-0024](0024-quality-pillars.md) (quality pillars), [ADR-0031](0031-existing-repos-assess.md) (assess)

## Context
The factory's agents build to the golden path, but nothing told them *which standards*
to design, build, review and deploy against. People working on this repo with Claude
Code had the same gap. Nine skills now capture those standards: the six AWS
Well-Architected pillars (each aligned with its ISO standards, with targets, controls,
evidence and anti-patterns), a router across them, DevSecOps practices (ISO/IEC 27034,
shift left and right, immutable infrastructure) and the ISO/IEC/IEEE 12207 lifecycle.

## Decision
1. **One set of files, two users.** The skills live in `plugin/skills/` (shipped in the
   factory image like our other skills) and are mirrored in `.claude/skills/` for Claude
   Code. Copies, not symlinks: Claude Code's support for symlinked skill folders is
   undocumented. `make skills-sync` copies; a test fails when the copies differ.
2. **By stage, on demand.** Each agent lists the skills of its station (docs/skills.md):
   intake gets the lifecycle and the router (measurable non-functional requirements),
   the architect gets every pillar and writes the Pillar check into docs/design.md, the
   developer DevSecOps and security, the reviewer security, reliability and performance,
   devops DevSecOps, reliability and operability, the assessor every pillar. They are
   never preloaded, and the acceptance agent gets none.
3. **In proportion.** The skills are written for full cloud solutions. `skill_prompts`
   tells agents to apply them in proportion to the request, keep to the blueprint's
   platform (no unrequested cloud services: recommend them instead), and never treat a
   pillar gap the request doesn't cover as a blocker. CI-level practices the stations
   already run (scans, SBOM, provenance) are not re-added by agents.
4. **Assessments file risks under a pillar** (optional `pillar` on each risk; unknown
   values are dropped by `assess.judge`).
5. **Through the normal gates.** The templates change; running workflows don't. A
   blueprint picks the skills up when its workflow is re-drafted from the template and
   published, and `eval_gate: block` measures that version before it serves real work.

## Consequences
- Design docs carry a Pillar check; reviews and assessments speak the same language as
  the quality pillars (ADR-0024).
- More context per agent call when a skill loads (each is under 8 KB); the evaluation
  harness shows the effect on cost and pass rate before activation.
- The skills are our own and ship with the repo under its licence.
