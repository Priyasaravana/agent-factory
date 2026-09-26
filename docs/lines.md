# Lines and agent specs

A **line** is the ordered set of stations a run goes through. Each agent
station runs an **agent spec**. Lines are data, not code:

| Where | What |
|---|---|
| `blueprints/<name>/` (repo, read-only) | `line.yaml` + `agents/<id>.md`. The starting point shipped with the factory. |
| Factory DB (per instance) | Immutable, numbered **line versions**. One of them is active. |
| Each run | Pinned to the line version it started with. Later edits never change a run in flight. |

On first start the instance copies the blueprint in as **v1**. After that the
DB is the source of truth; a later blueprint change never overwrites it (the UI
shows "blueprint updated").

## Agent spec (`agents/<id>.md`)

```markdown
---
id: security-reviewer
description: Reviews the change for OWASP issues before packaging
model: default            # tier from config.models (judgment | default | fast) or an alias
tools: reviewer           # preset, see below
extra_tools: []           # only WebFetch / WebSearch may be added
disallowed_tools: []      # narrow the preset further
skills: [factory-station-contract]
max_turns: 30
produces: []              # files that must exist afterwards (checked by the engine)
context_docs: [security-standards]   # reference docs from the line's library
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
Only the acceptance station sees the holdout scenarios, and its agent must use
an observe-only preset.

## Context an agent receives

Every part of an agent's context comes from the pinned line version or from the
order's own history, so a run can always be explained afterwards.

| Setting | What the agent gets |
|---|---|
| `prompt` | The job. |
| `context_docs` | Team standards from the line's **reference docs** (`docs/<id>.md` in a blueprint). Max 20,000 chars each and 40,000 per agent. |
| `learnings` | A short, human-approved list of lessons. It replaces free-form agent memory: it is versioned, reviewable and reproducible. |
| `previous_iterations` | For each earlier iteration of the same product: what was asked, the outcome, and the recorded decisions. |
| skills | Loaded on demand from `plugin/skills/`. |

The factory also always gives agents the shared station contract and, where
relevant, the failure evidence routed to them.

## Line (`line.yaml`)

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
- Agent stations whose id matches a built-in handler (`intake`, `design`,
  `build`, `deploy_fix`, `acceptance`) use that handler. Any other agent station
  uses the **generic handler**. The spec's prompt defines the job, and the
  engine requires a structured verdict (`passed`, `summary`, `findings`) plus
  the files listed in `produces`. A failed verdict sends the findings to
  `on_fail`.
- Repair stations (`only_on_fail: true`) are reached only through `on_fail`,
  and go to `next` afterwards.

A line is validated before it is stored. These are rejected: unknown routes,
agents, handlers or skills; an acceptance agent that can write; and a repair
station without `next`.

## Managing versions (CLI, inside the factory container)

```bash
docker compose exec factory agent-factory line versions
docker compose exec factory agent-factory line export --out /data/line-edit
#   edit .factory-data/line-edit/line.yaml and agents/*.md on your Mac
docker compose exec factory agent-factory line import /data/line-edit --note "add security-review after build"
docker compose exec factory agent-factory line activate 1        # rollback
```

## Editing in the UI

**The line → Edit line** opens a **draft**: one working copy per line, saved as
you go, which never runs.

- **Agents:** create, edit, duplicate and delete. You can set the model (tier or
  a specific model), tool preset and narrowing, skills, reference docs,
  earlier-iteration recall, learnings, turn budget, promised outputs and prompt.
  Ids can't be renamed (duplicate instead). An agent that a station uses can't
  be deleted.
- **Stations → agents:** choose which agent runs each agent station. Check
  stations have no agent.
- **Reference docs:** create, edit and delete. A doc attached to an agent can't
  be deleted.
- **Publish** validates the draft (the problems are listed live) and stores it
  as the next active version. **Discard** drops the draft.

Adding, removing and reordering stations comes in the next slice.
