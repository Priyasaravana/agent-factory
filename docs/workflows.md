# Workflows, agents and templates

A **workflow** is the ordered set of stations a product goes through, and the
agents that do the work at each agent station. **Each blueprint has its own
workflow.** A product runs the active workflow of its blueprint.

| Where | What |
|---|---|
| `workflow-templates/<name>/` (repo) | Built-in templates: `workflow.yaml`, `agents/<id>.md`, `docs/<id>.md` |
| A GitHub repo folder | The same format, imported into a draft and pinned to a commit |
| Factory DB | Per blueprint: immutable, numbered **workflow versions**, one active, plus one editable **draft** |
| Each change | Pinned to `(workflow, version)` for its whole life |

On first start, each blueprint's workflow is seeded from the template named
in `.agent-factory/config.yaml` (`blueprints.<id>.workflow_template`) as
**v1**. After that the DB is the source of truth. A later change to the shipped
template never overwrites your workflow; the UI shows "template updated"
instead.

Built-in templates:

| Template | Stations |
|---|---|
| `default` | intake → design → build → verify → readiness → **review** → package → deploy (→ deploy_fix) → acceptance → deliver |
| `api-security-review` | as `default`, plus an observe-only **security-review** after build, with a secure-coding standard |

## Agent spec (`agents/<id>.md`)

```markdown
---
id: security-reviewer
description: Reviews the change for OWASP issues before packaging
model: judgment           # tier from config.models (judgment | default | fast) or a specific model
tools: reviewer           # preset, see below
extra_tools: []           # only WebFetch / WebSearch may be added
disallowed_tools: []      # narrow the preset further
skills: [factory-station-contract, agent-watchdog]
preload_skills: [agent-watchdog]   # always in the system prompt; others load on demand
max_turns: 40
produces: []              # files that must exist afterwards (checked by the engine)
context_docs: [secure-coding]   # reference docs from the workflow's library
previous_iterations: 1    # recall N earlier iterations of this product (0–5)
learnings: |              # human-approved lessons (max 4000 chars)
  Flag any endpoint without input validation.
---
You are the security reviewer. ...
```

| Preset | Tools | Notes |
|---|---|---|
| observer | Read, Glob, Grep | read-only |
| reviewer | Read, Glob, Grep, Bash | Bash limited to observe-only commands (curl, cat, kubectl get/logs…) |
| author | Read, Write, Edit, Glob, Grep | writes docs/code, no shell |
| operator | author + Bash | e.g. deploy repair |
| builder | operator + Task | may delegate to a cheap sub-agent |

The guardrail hooks apply to every preset. No spec can grant `git push`,
access to credentials, writes outside the change worktree, or holdout access.

## Roles: what each station needs from its agent

Publishing is **blocked** when an agent can't do its station's job safely:

| Station handler | Requirement | Why |
|---|---|---|
| intake | must not write files | the engine records the spec; intake only reads |
| design | must be able to write files | it produces the design docs |
| build, deploy_fix | must write files and use the shell | they change code and run checks |
| acceptance | observe-only, with shell (curl) | it sees the hidden scenarios, so it must never write |
| review | observe-only, with shell (read-only git) | it judges the change; fixing is the builder's job |

**Warnings** don't block publishing. They are raised for:
- a recommended skill that's missing (e.g. `agent-watchdog` at acceptance);
- the fast model tier on a judgment station;
- a "review" station whose agent can write;
- a custom station with no `on_fail` route;
- unused agents.

## Context an agent receives

| Setting | What the agent gets |
|---|---|
| `prompt` | The job. |
| `context_docs` | Team standards from the workflow's **reference docs**. Max 20,000 chars each and 40,000 per agent. |
| `learnings` | Human-approved lessons, used instead of free-form memory: versioned and reproducible. |
| `previous_iterations` | For each earlier iteration of the same product: what was asked, the outcome, and the decisions. |
| skills | Loaded on demand (the agent decides from each skill's description), or **preloaded** (`preload_skills`: the skill's instructions go into the system prompt, so they are always followed). Built-in or imported from GitHub and pinned per version (see [skills.md](skills.md)). |

## Workflow file (`workflow.yaml`)

```yaml
stations:
  - {id: requirements,    kind: agent, agent: intake}
  - {id: design,          kind: agent, agent: architect}
  - {id: implement,       kind: agent, agent: developer}
  - {id: security-review, kind: agent, agent: security-reviewer, on_fail: implement}   # custom
  - {id: test,            kind: check, on_fail: implement}
  # ...
```

**Stations follow the DevOps loop** ([ADR-0028](adr/0028-station-names-and-phases.md)).
The lane and every change's timeline group them by phase:

| Phase | Built-in stations | What they do |
|---|---|---|
| Plan | `requirements` (agent), `design` (agent) | the request becomes a spec, numbered requirements, scenarios and a design |
| Code | `implement` (agent) | writes the code and its tests |
| Test | `test`, `quality-gate` (checks), `code-review` (agent), `change-risk` (check) | tests, lint and coverage; agent-readiness Level 3 and a secret scan; review of the change against the spec; rules over the diff ([change risk](#change-risk)) |
| Release | `build` (check) | builds, scans and pushes the image, with SBOM and provenance |
| Deploy | `deploy` (check), `deploy-repair` (agent, repair only) | deploys to the blueprint's environment; repairs a failed deploy |
| Validate | `acceptance` (agent) | runs the hidden scenarios against the live app |
| Handover | `handover` (check) | commits, tags, opens the PR and hands over the app |

Operate and monitor come with the operate loop. A custom station sits in the
phase of the station before it; set `phase:` to place it elsewhere.

**Older versions keep their station ids** (`intake`, `build`, `verify`, `readiness`,
`review`, `package`, `deploy_fix`, `deliver`): changes and evidence are pinned to
them. They resolve to today's handlers, and the UI shows today's names. Note that
`build` used to be the coding agent; a check named `build` is the image build.
To adopt the new ids, start a draft from the `default` template ("template updated").

- `kind: check` stations are implemented by the engine: `test`, `quality-gate`,
  `build`, `deploy`, `handover`.
- Agent stations named like a built-in handler (`requirements`, `design`, `implement`,
  `code-review`, `deploy-repair`, `acceptance`) use that handler. Any other agent station uses the
  **generic handler**: the engine requires a structured verdict (`passed`,
  `summary`, `findings`) plus the files listed in `produces`. A failed verdict
  sends the findings to `on_fail`.
- Repair stations (`only_on_fail: true`) are reached only through `on_fail`,
  and go to `next` afterwards.
- Built-in steps must keep the order requirements → design → implement → code-review →
  build → deploy → acceptance → handover, because each one uses the previous one's output.
- Other validation errors: invalid station ids, unknown routes, agents, handlers, skills or docs;
  unreachable stations; oversized docs.

Older `line.yaml` files are still read.

## Spec-driven development

Details and rationale: [ADR-0017](adr/0017-spec-driven-development.md).

**What intake produces.** Intake turns the request into three things:
- numbered requirements in `docs/requirements.yaml` (`R1`, `R2`, …);
- a product spec, `docs/spec.md`;
- acceptance and hidden scenarios, each listing the requirements it `covers`.

Design then writes the technical design (`docs/design.md`). The implement station
tags tests with `@pytest.mark.req("R1")`.

**What the factory checks.**
- Each requirement must be covered by at least one scenario. Intake gets one
  correction attempt, then the change is held.
- The Quality gate station checks that every requirement has a scenario and a
  tagged test (`requirements_traced`).
- Acceptance records which hidden scenarios verified which requirement live.

**Seeing it.** The change page's **Specification** panel has tabs for
Requirements, Product spec, Technical design, API and **Traceability**
(requirement → scenarios → tagged tests → live result). On later iterations,
chips such as `+R3 ~R1` show how feedback changed the requirements.

**Spec review gate.** Set it in `workflow.yaml` (`spec_review: "off" | "first" | "always"`,
default `"off"`; quote the value) or in the editor's **Spec review gate** card.
When the gate applies, the change stops after design with **spec ready for review**.
The product's creator or an admin then chooses:
- **Approve & build:** the change continues to build.
- **Request changes:** the change goes back through intake and design with your
  notes.
- **Edit:** fix the product spec or technical design directly; the edit is
  committed under your name.

`first` gates only iteration 1. `always` also gates every feedback iteration.

**Bring your own spec.** On the **New product** form, choose **I have a spec**. Paste the
spec or load a `.md` file. Its numbered items become R1, R2, … with their
wording kept.

### What a blueprint can build
Each blueprint declares its `stack` in `.agent-factory/config.yaml`
(`fastapi-service`: python, fastapi, postgres, sqlalchemy, docker, helm, kubernetes).
If a product *requires* something else (Node.js, Go, MongoDB, a React UI…), intake
quotes it and the change pauses with a question instead of building Python anyway.
Answer "build it with python" to go ahead, or cancel and create the product on a blueprint
that supports it. Mentions that aren't requirements ("a React app will call this
API") are only noted.

### Every requirement is checked live
Each requirement needs at least one hidden scenario that checks it on the running
app, including health/metrics endpoints and persistence. Only a requirement that
truly can't be observed over HTTP may instead carry `no_live_check: <reason>`,
which the Traceability tab shows.

## Learning from runs

After a change that **needed help**, the factory suggests short lessons for the agent
whose work caused it ([ADR-0021](adr/0021-learning-from-runs.md)). A change needed
help if it had a fix loop, a hold a person resolved, or blocking intake questions.

1. A retro agent reads the recorded evidence. It is observe-only and sandboxed,
   and runs after delivery, so it never delays a change. It suggests up to 3 lessons.
2. The engine discards a suggestion that names an unknown agent, is too short or
   too long, cites no evidence, repeats an existing or pending lesson, or would
   weaken a check (e.g. "skip flaky tests"). Discarded ones are listed on the
   change's `retro:` event.
3. The rest appear under **Suggested learnings** on the workflow page, each with
   its reason and evidence.
4. An **admin** accepts one (it is added to that agent's learnings in the
   **draft**) or rejects it.
5. The lesson reaches agents only when the draft is **published**. To undo it,
   activate the previous version.

Turn it off per workflow with `learn_from_runs: false` in `workflow.yaml`, or the
**Learn from runs** switch in the editor.

<a id="spec-review-station"></a>

## Code review station

A station with id `code-review` (formerly `review`) runs the built-in spec review
([ADR-0020](adr/0020-spec-review-station.md)). Put it after `test` and
`quality-gate`, so the review only looks at code that already passes its tests,
and route `on_fail` to `implement`.

**What the reviewer gets:**
- every numbered requirement;
- what changed in this iteration (`git diff --stat main...HEAD`);
- the change request and any spec review notes;
- on a re-review, its own earlier blocking items, so it checks they were fixed.

**What it reports:** for **every** requirement, `implemented`, `partial` or
`missing` plus where it is implemented and tested. It also lists findings as
`blocker`, `major` or `minor`.

**How the engine judges the report:**

| Report | Result |
|---|---|
| every requirement implemented; only minor findings | passes (minor findings are recorded) |
| a requirement partial or missing, or a blocker/major finding | back to `implement`, with the findings as evidence |
| a requirement skipped or an unknown id cited | the reviewer retries once; then the change is **held** for a person |

**Where to see it:** the Specification panel's **Traceability** tab has a
*review* column per requirement and the findings list. The evidence file is
`artifacts/<change>/review.json`.

**Existing workflows:** they keep their version and show "template updated".
To adopt the review, open **Edit workflow** and either start again from the
`default` template, or add a `code-review` station after `quality-gate` using a
`reviewer` agent with `tools: reviewer`.

## Change risk

Details and rationale: [ADR-0027](adr/0027-change-risk-policy.md).

**At the door.** Every new product and every piece of feedback is checked against the
acceptable-use rules before anything is built. A match is refused with the rule
that matched (HTTP 422); nothing is created, and the refusal is appended to
`audit/refusals.jsonl` in the data folder. The shipped rules can't be removed;
`change_risk.acceptable_use` in the config adds your own.

**On the diff.** The `change-risk` check reads this iteration's diff against
`main` and looks for:

| Category | Examples of what it finds |
|---|---|
| 1 · weakens security | an auth dependency removed, CORS opened to `*`, TLS verification off, root or privileged containers, debug on, rate limiting removed |
| 2 · removes a safety net | a test file deleted, fewer tests than before, tests skipped, the coverage gate lowered, CI no longer running `make verify` |
| 3 · destroys data | `DROP TABLE` / `DROP COLUMN` / `TRUNCATE`, `DELETE FROM` without `WHERE`, `drop_all` |
| 4 · exfiltration or backdoor | a call to an outside host not in `change_risk.allowed_hosts`, `eval`/`exec`/`shell=True`, debug or shell routes, a credential in the code |

Tests and docs don't count for categories 1 and 4. Some patterns (`# noqa`,
removed log calls) are **notes**: recorded, never held.

**When something is found** the change waits (`awaiting_risk_approval`) and the change
page shows each finding with its file and line:
- **Approve:** an admin *other than the requester* gives a reason, and the change
  continues. On a single-admin install the requester may approve their own as
  **break-glass** after `cooling_off_minutes`; it is flagged in the evidence.
  An approval covers exactly those findings: if the change moves on and finds
  something new, it waits again.
- **Send back:** anyone who can steer the product gives a reason; the change goes back
  to the station's `on_fail` (`implement`) with the findings and the reason.

Evidence: `artifacts/<change>/change-risk.json`, plus a decision event for every
check, approval and send-back. Evaluation cases are never approved automatically:
a case that trips the rules counts as held.

Not yet: confirmed removal lists for large removals (category 6), the same check
as a required PR status check and at promotion to prod (master plan phases 3 and 7).

## Evaluation gate

A workflow carries a fixed **evaluation suite**: up to 8 small products, in `evals.yaml`
next to `workflow.yaml`, edited under **Edit workflow → Evaluation gate**. Every new
version is measured on it against the active version ([ADR-0025](adr/0025-evaluation-harness.md)):

- **Metrics:** pass rate, autonomy (no unplanned help), Level 3 share,
  requirements verified live, cost per delivery, fix loops per case and median
  lead time.
- **`eval_gate: block`** (the default template): publishing creates a
  *candidate*. It activates by itself when nothing regressed. If it regressed,
  the workflow page shows why. Fix the draft (it is kept) and publish again, or
  activate anyway with a recorded reason.
- **`warn`:** activate at once, evaluate and report. **`off`:** run evaluations by
  hand from the workflow page.
- Rolling back to an older version is never gated.

```yaml
# workflow-templates/<name>/evals.yaml
- id: bookmarks
  title: Bookmarks service
  requirements: Save bookmarks with a URL, a title and tags; list and filter by tag.
  answers: ["Use your best judgement and record it as an assumption."]  # if intake asks
```

Each case is a full build, test and deploy, so keep the suite small and
representative. Evaluation products are hidden from the products list and Outcomes,
and archived when the evaluation ends.

## Editing in the UI

**Workflows → (blueprint) → Edit workflow** opens the blueprint's draft.
The draft saves as you go and never runs.

- **Start from a template:** a built-in template, or a folder in a GitHub repo
  (`owner/repo` + path + ref, pinned to the exact commit). Private repos use
  `SKILLS_GITHUB_TOKEN`.
- **Lane:** the stations left to right. Drag from the **palette** to insert a
  custom agent step, a built-in agent step that isn't used yet, or a check
  (verify, package, deploy, deliver); you can also click to append. Drag the ⠿
  handle to reorder, × to remove (routes pointing at it are cleared). On each
  card you set the agent, **on fail** (where a failure goes back to) and
  **repair only** plus **then** (for stations reached only on failure).
  Problems appear on the card itself as you edit. Built-in steps must stay in
  the order intake → design → build → package → deploy → acceptance → deliver;
  verify and custom steps can go anywhere.
- **Agents:** create, edit, duplicate and delete. The settings are the model,
  tool preset and narrowing, skills, reference docs, earlier-iteration recall,
  learnings, turn budget, promised outputs and prompt.
- **Reference docs:** create, edit and delete.
- **Spec review gate:** Off, First iteration or Every iteration (see above).
- **Publish** validates the draft (problems and suggestions are listed live)
  and stores it as the next active version. **Discard** drops the draft.

## CLI (inside the factory container)

```bash
docker compose exec -u factory factory agent-factory workflow list
docker compose exec -u factory factory agent-factory workflow templates
docker compose exec -u factory factory agent-factory workflow --id fastapi-service versions
docker compose exec -u factory factory agent-factory workflow --id fastapi-service export --out /data/wf-edit
docker compose exec -u factory factory agent-factory workflow --id fastapi-service import /data/wf-edit --note "tweak"
docker compose exec -u factory factory agent-factory workflow --id fastapi-service activate 1      # rollback
```

## Upgrading from "lines"

Earlier builds called this a "line" with a single `default` line. On first
start after upgrading:
- the tables are renamed;
- the `default` line becomes the workflow of the first blueprint (history
  kept, v1/v2… unchanged);
- old changes keep their pinned version.
