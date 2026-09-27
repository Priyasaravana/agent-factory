# ADR-0010: Upstream skills are imported defaults, not vendored copies

**Status:** accepted · 2026-09-27 · supersedes the vendoring part of ADR-0004

## Context
We copied seven BuilderIO skills into `plugin/skills/`. That meant keeping
upstream files in our repo, upgrading them by hand, and running two mechanisms
(vendored and imported) for the same kind of thing.

## Decision
- `plugin/skills/` holds only the factory's own skills.
- `default_skills` in the config lists upstream skills by repo, paths and a
  **pinned sha**. Reviewing a change to that pin in a PR is the review for
  default installs, so no interactive script consent is needed for them.
- The image build caches those folders at the pin (`agent-factory skills
  cache`), so first start needs no network. Outside Docker, the pinned commit
  is fetched from GitHub.
- On first start, defaults are installed as ordinary imported skills. After
  that, humans own them: they are never reinstalled or overwritten on restart.
- Workflow versions created before pins existed resolve imported skills to the
  installed commit.

## Consequences
- One skill mechanism, one update path (Skills page: check for update, diff,
  update), and no upstream files in the repo.
- A first start outside Docker without GitHub access fails with a clear
  message naming the missing default skills.
