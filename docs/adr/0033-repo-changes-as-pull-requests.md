# ADR-0033: Changes to existing repositories, delivered as pull requests

**Status:** accepted · 2026-10-10 · builds on [ADR-0031](0031-existing-repos-assess.md) (assess) and [ADR-0030](0030-work-items.md) (work items)

## Context
An assessment tells a team what to change; the factory should then be able to make those
changes. A team's repository is theirs: the factory must work in their stack and
conventions, prove each change with the repo's own checks, and never change their default
branch itself.

## Decision
1. **Feature, bug and upkeep changes on repo products.** After the first assessment, a repo
   product takes `assess`, `feature`, `bug` and `upkeep` changes. The UI offers a change
   form on the product and a **Make this change** button on each recommended change.
2. **A second workflow per repo blueprint.** `change_workflow_template` seeds the workflow
   `<blueprint>-change` (`workflow-templates/repo-change`); assessments keep the blueprint's
   own workflow. `config.workflow_for(blueprint, kind)` picks one when a change starts.
3. **Stations:** onboard → implement (`repo-implement`) → test (`repo-test`) → review
   (`repo-review`) → change-risk → pull-request.
   - The developer works on the change's branch of the read-only clone; the engine commits.
   - **Test** runs the repo's own checks in the sandbox (`repo_change.test_commands`:
     `npm test`, a `verify`/`test` Makefile target, pytest, `go test`). A repo with no checks
     sends the change back: every change is proven by a test, and the first change to an
     untested repo adds them.
   - **Review** is judged by the engine (`repo_change.judge_review`): only `blocker`/`major`
     findings on files the change touched send it back; the rest become notes in the PR.
   - **Change risk** compares with the repo's branch (`origin/<branch>`), not `main`.
   - **Pull request** pushes `factory/<change>` and opens a PR against the repo's branch
     whose description carries the evidence (request, summary, tests, review, change risk),
     then sets an `agent-factory/change-risk` commit status. **A person merges**: the factory
     never merges its own pull requests.
4. **Credentials.** `existing_repos.write_token_ref` (default `env://REPO_GITHUB_TOKEN`) is a
   reference to a fine-grained token with Contents and Pull requests read/write on the
   repos. It is resolved for the step and reaches `git`/`gh` through the environment only.
   Without it the change is held before anything is pushed. In dry-run the PR is simulated.
5. **Node.js in the sandbox** (`node:22-bookworm-slim`) and `registry.npmjs.org` in the
   egress allowlist, so Node repositories' checks run where agent-written code runs.

## Consequences
- The assessment → change → pull request loop works on a team's real repository.
- The team's CI runs on the PR as usual; the factory's checks are evidence, not a gate on
  their repo. Reading the CI result back into the factory comes later.
- Repo products are still out of Outcomes (ADR-0031); measuring PR lead time and merge rate
  comes with the operate loop.
- Private repos still need a read token for cloning (later).
