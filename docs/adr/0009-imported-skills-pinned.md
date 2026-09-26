# ADR-0009: Skills imported from GitHub, pinned per workflow version

**Status:** accepted · 2026-09-26

## Context
Teams keep skills in their own repos (public or private) and want to use them
without rebuilding the factory image. Skills can contain scripts, and a run must
stay reproducible.

## Decision
- Fetch skill folders with plain git (sparse, shallow, pinned to a sha). A
  read-only `SKILLS_GITHUB_TOKEN` is used for private repos and is never
  persisted.
- **Review before install:** the preview shows all files, flags scripts and
  diffs against the installed commit. Install takes the reviewed sha, not the
  moving ref, and requires explicit consent for scripts.
- Store every installed commit in the DB; one is current per name. Workflow
  versions record `skill_pins` at publish time.
- Before a run, the pinned skills are written to a content-addressed local
  plugin (`imported-skills`) in the data dir. The SDK runner loads it alongside
  the factory plugin.
- Imports cannot shadow built-in skills. Removal is blocked while a workflow
  uses the skill, and removed commits are kept for older versions.

## Consequences
- Updating a skill never changes a running or published workflow silently. It
  takes a publish, which the editor surfaces as "pending skill updates".
- Guardrail hooks, not skill review, remain the enforcement boundary for what
  agents can run.
- Skills are text-only (no binaries), max 200 files / 2 MB.
