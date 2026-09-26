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

The UI's **The line** page shows the active version, every agent spec and the
version history, and lets you activate an older version. Editing in the UI is
the next slice.
