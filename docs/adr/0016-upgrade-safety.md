# ADR-0016: Upgrades are tested on what we ship; app Python is independent of the factory's

**Status:** accepted · 2026-09-29 · guide: [upgrading.md](../upgrading.md)

## Context
Dependabot moved the factory and sandbox images from Python 3.12 to 3.14. Every
CI check passed, yet every live run would have failed:
- the sandbox ran the generated app's tests with the image's own Python;
- the golden path pins 3.12;
- the sandbox may not download interpreters.

CI tested the source on the runner's Python. It never built the images or ran
the stack.

## Decision
1. **Decouple the app's Python from the factory's.**
   - The sandbox image carries interpreters for apps (`APP_PYTHONS`, default
     3.12 3.13 3.14, installed with `uv python install` at build time).
   - An app's `.python-version` selects one, offline.
   - The factory's own Python (factory and auth images, CI) is a separate,
     single version.
   - `test_python_versions.py` enforces both rules.
2. **CI tests what we ship:**
   - `images` builds every image, runs the engine tests inside the factory
     image, and verifies the golden path in the sandbox image as uid 10001
     with downloads off;
   - `e2e` runs the whole stack in dry-run: compose, the sandbox isolation
     probe, and a Playwright smoke test from sign-in to archive. It keeps
     screenshots and logs as evidence;
   - `engine-next-python` is advisory, on 3.13 and 3.14;
   - a weekly scheduled run catches drift without PRs.
3. **Dependabot policy:** Python base images get patch updates only. A Python
   upgrade is one deliberate PR.
4. **Operations:**
   - `make backup` takes online SQLite snapshots of the factory and auth
     databases, plus `.factory-data`;
   - `make restore` keeps the previous data aside;
   - `make upgrade` runs backup → pull → rebuild → wait for health → sandbox
     check.

## Consequences
- A base-image or dependency bump that breaks the stack fails on its PR, with
  screenshots, before anyone runs `make up`.
- CI takes longer (images plus e2e, about 15–20 minutes) but costs no model
  usage.
- Apps can adopt a newer Python without upgrading the factory, and the factory
  can upgrade without breaking existing apps.
- Still out of scope:
  - live model runs in CI (the evaluation harness, later);
  - automatic merging (needs branch protection with required checks first).
