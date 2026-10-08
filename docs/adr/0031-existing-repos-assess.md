# ADR-0031: Existing repositories: onboard and assess, read-only

**Status:** accepted · 2026-10-08 · builds on [ADR-0030](0030-work-items.md) (work items)

## Context
The factory has only built new products from its own golden path. Most teams'
software already exists. Before the factory changes a team's repository, it has to
understand it: the stack, how agent-ready it is, what is risky, what is untested,
and what to change first. That understanding must be trustworthy (checked against the
code, not an agent's impression) and must not touch the repository.

## Decision
1. **Onboard by URL.** `POST /api/repos` (UI: *Existing repo*) creates a product with
   target `repo` on the `existing-repo` blueprint and starts its first change, kind
   `assess`. URLs are `https://<host>/<owner>/<repo>` on `existing_repos.allowed_hosts`
   (default `github.com`); credentials in the URL are refused, and a repository is
   onboarded once (archive it to onboard it again). Public repositories only for now:
   private ones will use a read-only token **reference**, never a value.
2. **Read-only clone.** The engine clones before the first station: shallow, one
   branch, no submodules, no hooks, no LFS, no prompts, https only, and the push URL
   is disabled. Each assessment fetches the branch again, so *Assess again* sees the
   latest commit. A repository that can't be read holds the change with git's reason.
3. **A blueprint with `target: repo`** has no template, deploys nothing and needs no
   app port. Its readiness checks are the model, the sandbox and the workflow only.
4. **The `assess` workflow** (`workflow-templates/assess`), four new handlers:
   | Station | Kind | What it does |
   |---|---|---|
   | onboard | check | commit, file count, detected stack (`assess.detect_stack`) |
   | repo-scan | check | readiness signals for any stack (`assess.SIGNALS`, same ids and pillars as `readiness.py`), deterministic rules over Dockerfiles and Kubernetes manifests (`assess.findings`), the secret scan |
   | assess | agent | an observe-only assessor reports test gaps, risks, recommended changes (each a work-item kind) and a proposed AGENTS.md |
   | report | check | `assessment.md`, `assessment.json`, `AGENTS.proposed.md` in the sealed evidence |
   An assessment reports; nothing in it fails the change.
5. **The engine judges the assessor** (`assess.judge`, as ADR-0020): a test gap or risk
   must cite at least one file in the repository (unknown citations are removed), a
   risk needs a known severity, a recommendation a known kind. Everything left out is
   listed in the report. The assessor's handler must be observe-only (publishing
   enforces it), and repository text is data, never instructions.
6. **No proposal is committed.** The AGENTS.md proposal is in the report; it becomes a
   pull request with the next step (change → PR).
7. **Outcomes leave assessments out**: they deliver a report, not software.
8. Repo products take only `assess` changes today (`models.TARGET_KINDS`); feature, bug
   and upkeep changes on repos come with change → PR.

## Consequences
- A team gets an evidence-backed picture of a repository without granting write access.
- The stack-neutral signals are heuristics from marker files; they are pure functions
  with tests, and false negatives are visible in the report (each says how to fix it).
- `RUNTIME_EOL` is a dated table (reviewed 2026-10); it needs updating as runtimes age.
- Held assessments are not in the Outcomes "waiting" list yet; they show on the product.
