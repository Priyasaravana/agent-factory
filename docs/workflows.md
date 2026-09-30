# Workflows, agents and templates

A **workflow** is the ordered set of stations an order goes through, and the
agents that do the work at each agent station. **Each product line has its own
workflow.** An order runs the active workflow of its product line.

| Where | What |
|---|---|
| `workflow-templates/<name>/` (repo) | Built-in templates: `workflow.yaml`, `agents/<id>.md`, `docs/<id>.md` |
| A GitHub repo folder | The same format, imported into a draft and pinned to a commit |
| Factory DB | Per product line: immutable, numbered **workflow versions**, one active, plus one editable **draft** |
| Each run | Pinned to `(workflow, version)` for its whole life |

On first start, each product line's workflow is seeded from the template named
in `.agent-factory/config.yaml` (`product_lines.<id>.workflow_template`) as
**v1**. After that the DB is the source of truth. A later change to the shipped
template never overwrites your workflow; the UI shows "template updated"
instead.

Built-in templates:

| Template | Stations |
|---|---|
| `default` | intake → design → build → verify → package → deploy (→ deploy_fix) → acceptance → deliver |
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
access to credentials, writes outside the run worktree, or holdout access.

## Roles: what each station needs from its agent

Publishing is **blocked** when an agent can't do its station's job safely:

| Station handler | Requirement | Why |
|---|---|---|
| intake | must not write files | the engine records the spec; intake only reads |
| design | must be able to write files | it produces the design docs |
| build, deploy_fix | must write files and use the shell | they change code and run checks |
| acceptance | observe-only, with shell (curl) | it sees the hidden scenarios, so it must never write |

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
  - {id: intake,          kind: agent, agent: intake}
  - {id: design,          kind: agent, agent: architect}
  - {id: build,           kind: agent, agent: developer}
  - {id: security-review, kind: agent, agent: security-reviewer, on_fail: build}   # custom
  - {id: verify,          kind: check, on_fail: build}
  # ...
```

- `kind: check` stations are implemented by the engine: `verify`, `package`,
  `deploy`, `deliver`.
- Agent stations named like a built-in handler (`intake`, `design`, `build`,
  `deploy_fix`, `acceptance`) use that handler. Any other agent station uses the
  **generic handler**: the engine requires a structured verdict (`passed`,
  `summary`, `findings`) plus the files listed in `produces`. A failed verdict
  sends the findings to `on_fail`.
- Repair stations (`only_on_fail: true`) are reached only through `on_fail`,
  and go to `next` afterwards.
- Built-in steps must keep the order intake → design → build → package → deploy →
  acceptance → deliver, because each one uses the previous one's output.
- Other validation errors: invalid station ids, unknown routes, agents, handlers, skills or docs;
  unreachable stations; oversized docs.

Older `line.yaml` files are still read.

## Spec-driven development

Details and rationale: [ADR-0017](adr/0017-spec-driven-development.md).

**What intake produces.** Intake turns the order into three things:
- numbered requirements in `docs/requirements.yaml` (`R1`, `R2`, …);
- a product spec, `docs/spec.md`;
- acceptance and hidden scenarios, each listing the requirements it `covers`.

Design then writes the technical design (`docs/design.md`). The build station
tags tests with `@pytest.mark.req("R1")`.

**What the factory checks.**
- Each requirement must be covered by at least one scenario. Intake gets one
  correction attempt, then the run is held.
- The Readiness station checks that every requirement has a scenario and a
  tagged test (`requirements_traced`).
- Acceptance records which hidden scenarios verified which requirement live.

**Seeing it.** The run page's **Specification** panel has tabs for
Requirements, Product spec, Technical design, API and **Traceability**
(requirement → scenarios → tagged tests → live result). On later iterations,
chips such as `+R3 ~R1` show how feedback changed the requirements.

**Spec review gate.** Set it in `workflow.yaml` (`spec_review: "off" | "first" | "always"`,
default `"off"`; quote the value) or in the editor's **Spec review gate** card.
When the gate applies, the run stops after design with **spec ready for review**.
The order's creator or an admin then chooses:
- **Approve & build:** the run continues to build.
- **Request changes:** the run goes back through intake and design with your
  notes.
- **Edit:** fix the product spec or technical design directly; the edit is
  committed under your name.

`first` gates only iteration 1. `always` also gates every feedback iteration.

**Bring your own spec.** On the order form, choose **I have a spec**. Paste the
spec or load a `.md` file. Its numbered items become R1, R2, … with their
wording kept.

## Editing in the UI

**Workflows → (product line) → Edit workflow** opens the product line's draft.
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
docker compose exec factory agent-factory workflow list
docker compose exec factory agent-factory workflow templates
docker compose exec factory agent-factory workflow --id fastapi-service versions
docker compose exec factory agent-factory workflow --id fastapi-service export --out /data/wf-edit
docker compose exec factory agent-factory workflow --id fastapi-service import /data/wf-edit --note "tweak"
docker compose exec factory agent-factory workflow --id fastapi-service activate 1      # rollback
```

## Upgrading from "lines"

Earlier builds called this a "line" with a single `default` line. On first
start after upgrading:
- the tables are renamed;
- the `default` line becomes the workflow of the first product line (history
  kept, v1/v2… unchanged);
- old runs keep their pinned version.
