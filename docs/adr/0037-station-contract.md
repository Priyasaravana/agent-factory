# ADR-0037: One workflow container, one standard lane, windows per use case

**Status:** accepted · 2026-10-11 · §1 (one lane, windows), §4 and §6 amended by
[ADR-0039](0039-lanes-phases.md) (lanes per kind of work) · replaces the per-blueprint workflows of
[ADR-0028](0028-station-names-and-phases.md) and the separate repo lanes of
[ADR-0031](0031-existing-repos-assess.md)/[ADR-0033](0033-repo-changes-as-pull-requests.md); first step of the composable-SDLC plan.
Amended at acceptance (see *Amendments*): `report` sits in deliver; a repair that changes the
branch re-enters verify.

## Context
Today there are four templates (`default`, `api-security-review`, `assess`, `repo-change`) and one
workflow per blueprint. What a station reads and writes is implicit (handlers read files by path),
order is a hard-coded `STAGE_ORDER`, and the same job has two handlers depending on target
(`implement`/`repo-implement`, `test`/`repo-test`, `code-review`/`repo-review`,
`handover`/`pull-request`). Orgs adopt one phase at a time, want a person to own each step, and
must not be able to build a lane that ships untested code. The factory isn't in use by anyone
else yet, so there is nothing to stay compatible with.

## Decision

### 1. A workflow container with fixed columns
Every workflow is a **container** of seven phase columns, in a fixed order:

| Column | Purpose | Stations in the standard |
|---|---|---|
| `discover` | understand what exists | onboard, repo-scan, assess |
| `design` | what to build and how | requirements, design |
| `plan` | stories / tasks / tickets | — (stories + export in step 4) |
| `develop` | change the code | implement |
| `verify` | does it work, is it safe | test, quality-gate, secret/code/dependency/config scan, code-review, change-risk |
| `deliver` | let it out | build, deploy, deploy-repair (repair), acceptance, handover, report |
| `operate` | run and observe | — (operate loop, master plan phase 7) |

A station **fits** only if all three rules hold; the editor refuses the drop and publish refuses
the version otherwise:
1. **Column rule:** a station sits in its own column; free order within a column.
2. **Data rule:** every artifact it needs is given by a station to its left, or is an entry
   artifact of the window (§4). `deploy` before `build` doesn't fit: nothing gives `image`.
3. **Floor rule (mandatory controls, §5):** "deploy without test" passes the data rule (deploy
   doesn't read test results), so the floor catches it.

### 2. Artifacts: typed, schema-checked, in a ledger
Stations **need** and **give** versioned artifact types (`spec@1`). The engine keeps a per-change
**artifact ledger** `{type, version, ref (path/URL/commit), station, sha256}`, sealed with the
evidence ([ADR-0023](0023-evidence-manifest.md)).

| Type | What it is |
|---|---|
| `request` | the ask: text, ticket, feedback |
| `spec` | testable spec, numbered requirements, acceptance; holdout part sealed, never shown to builders |
| `design` | design doc, API contract, task plan |
| `stories` | stories with acceptance criteria (step 4) |
| `branch` | repo + ref with commits (factory worktree or team repo) |
| `report.<kind>` | evidence: repo, readiness, assessment, test, scan, review, risk, acceptance, change |
| `image` | image ref + SBOM + provenance |
| `url` | a running deployment |
| `pr` | a pull request (or handover tag) |

Each type has a **JSON Schema** (`schemas/artifacts/<type>.v<n>.json`; generated from the pydantic
models where they exist). It covers the manifest and machine-read content; prose files are checked
for presence. Checked at every handoff: an output that fails fails its station (routed by
`on_fail`); an entry artifact that fails is rejected before anything runs. A breaking change is a
new version, never an edit; a run keeps the versions it started with.

### 3. Station contract: shared contract, specialised implementations
A **station type** has one contract; implementations differ by target where they must.

```yaml
- id: implement
  type: implement                 # station type from the library
  phase: develop                  # fixed by the type for built-ins
  needs: [design@1 | request@1, branch@1]
  gives: [branch@1, report.change@1]
  executor: {agent: developer}    # agent spec | engine check | scan provider
  permissions: builder            # tool preset; observe-only types refuse write presets
  completion:                     # when the station counts as done
    outputs_valid: true           # gives pass their schemas
    verdict: engine               # engine | agent-verdict | person (mode approve)
  applies_when: {}                # applicability (§4); empty = always
  owner: tech-lead                # role id
  mode: notify                    # autonomous | notify | approve | discuss (step 2)
  on_fail: implement
```
Built-in types fix `phase`, `needs`, `gives` and the allowed `permissions` in code; a lane may
set executor, owner, mode, routing and policy fields (e.g. a scan's `fail_on`). Implementation is
chosen by the change's **target** (`factory` = product the factory owns, `repo` = a team's repo):

| Type | Needs → gives | factory impl | repo impl |
|---|---|---|---|
| onboard | branch → report.repo | (skipped: no repo yet) | today's onboard |
| repo-scan | branch, report.repo → report.readiness | — | today's repo-scan |
| assess / report | … → report.assessment | — | today's assess / report |
| requirements | request → spec | today's requirements | same |
| design | spec → design | today's design | same (optional for small repo changes) |
| implement | design \| request, branch → branch | today's implement | today's repo-implement |
| test | branch → report.test | golden-path tests + coverage | repo's own checks |
| quality-gate | branch → report.readiness | agent-readiness L3 | — (repo-scan covers it) |
| scan (×4) | branch → report.scan | policy `critical` | policy `high`, changed files only |
| code-review | branch, spec \| request → report.review | today's code-review | today's repo-review |
| change-risk | branch → report.risk | same | same |
| build → deploy → acceptance | branch → image → url → report.acceptance | today's | when the repo has a deploy target |
| deploy-repair | branch → branch (repair; re-enters verify) | today's deploy-repair | — |
| handover | branch (+image, url) → pr | commit, tag, PR | push branch, open PR (today's pull-request) |

### 4. One standard lane; each use case is a window over it
The **factory standard** is one lane holding every station above. A **window** picks where a run
enters and leaves, what it starts from, and applicability decides the rest:

| Window (change kind or preset) | Target | Enters at | Starts from | Leaves after |
|---|---|---|---|---|
| New product | factory | design | request | handover |
| Feature / bug / upkeep (factory product) | factory | design | request + branch | handover |
| Assess | repo | discover | branch | report |
| Repo feature / bug / upkeep | repo | discover | request + branch | handover (PR) |
| Design only (preset) | any | design | request | design (+ plan, step 4) |
| Verify only (preset) | repo | discover | branch | verify |
| Delivery (preset) | any | deliver | branch + report.test + report.risk | handover |
| Observe (preset) | any | operate | url | — (phase 7) |

- **Applicability rules** are data on the station (`applies_when: {target: repo}`,
  `{has: deploy_target}`, `{kind: [assess]}`), not code paths. Repo changes skip build/deploy/
  acceptance unless the repo has a deploy target; new products skip discover.
- **Every skipped station is recorded** in the run's evidence with its reason (`outside window`,
  `not applicable: no deploy target`), so a reviewer sees what didn't run and why.
- Windows are validated like lanes: the entry artifacts must satisfy the first station's needs,
  and a window that enters develop must not leave before verify.

### 5. Mandatory controls (policy floor), by scope and risk
Engine-enforced; no workflow, window, preset, mode or applicability rule can remove them:
- **Scope:** a window that runs any station giving `branch` must run `test`, the secret scan and
  `change-risk`, after the last `branch` change and before any station giving `image`, `url` or
  `pr`. Their failure routes back to develop or holds; never forward. This includes repair
  stations: one that changes the branch continues at (or before) the first control.
- **Risk:** change-risk's category decides extra controls (today: categories 1–4 wait for a
  second approver, ADR-0027; step 2 expresses this as `approve` mode + separation of duty).
- A window that changes no code (Assess, Design only, Verify only) has no scope floor.
- Delivery from outside (preset) needs `report.test` and `report.risk` as entry artifacts.

### 6. Custom workflows
- **Default: copy the standard.** The copy records `preset_of: {template, hash}`; when the
  standard changes, the editor offers a **rebase** (3-way merge at station granularity, conflicts
  shown, result is a draft).
- **Advanced: empty container.** Stations from the library are dropped into columns; the floor
  stations are locked in as soon as a station giving `branch` is added.
- **Custom stations** declare phase, versioned needs/gives, executor, permissions and completion
  criteria (the §3 schema, nothing implied). Custom agent stations default to observe-only and
  `gives: [report.<id>@1]`.
- **Publish validates** fit (§1), schemas, windows (§4), floor (§5), role ids and gate
  assignments; every run is pinned to the workflow version it started on (as today).
- Workflows belong to the org, not a blueprint; a product uses the standard unless it names
  another. Blueprints shrink to stack choice and become stack packs (step 5).

### 7. Fresh start
The four templates, `LEGACY_HANDLERS`, `line.yaml`, the `blueprint` alias, `STAGE_ORDER` and the
per-blueprint `WorkflowRegistry` are removed. The factory database is reset (workflows, products,
changes) and reseeded from `workflow-templates/standard/` plus `presets.yaml`. No migration code.

## Settled questions (Saravana, 2026-10-11)
- **Container:** columns + data + floor. **Existing workflows:** drop and start fresh.
- **One lane + windows** with validated entry artifacts, explicit applicability, recorded skips,
  controls by scope and risk, shared contracts with specialised implementations.
- **Custom:** copy the standard (version tracking, rebase) or an advanced empty container; both
  inherit the controls; custom stations declare everything; runs pinned.
- **Artifacts:** coarse types + JSON Schema, checked at every handoff.
- **Discuss threads:** per station run, pointing at the artifact; DB-stored; transcript and edit
  diff sealed into evidence on approve / send back; thread text is data, never instructions.
- **Roles:** built-in ids `product-owner`, `tech-lead`, `security`, `qa` + org roles in config;
  creator = product-owner only, others assigned explicitly; lanes reference role ids; gates check
  assignments (a change can't start if an approve/discuss station's role is unassigned);
  separation-of-duty rules as data (default: the requester can't approve change-risk); a
  single-person install relaxes SoD only with a config flag recorded in each change's evidence.

## Amendments (at acceptance, 2026-10-11)
- **`report` sits in deliver**, not discover. It folds the code, dependency and config scan
  findings (verify) into the assessment, so it must come after them; it is how an assessment
  leaves the lane, as `handover` is for code. The assess agent itself does not read scan results,
  so the assess lane runs onboard, repo-scan, assess (discover) → scans (verify) → report (deliver).
- **A repair that changes the branch re-enters verify.** `deploy-repair` commits chart, Dockerfile
  or app-config fixes; it now continues at `test` instead of `deploy`, so repaired code passes
  test, secret scan and change-risk again before the image is rebuilt (§5 "after the last
  `branch` change").

## Consequences
- One lane to test and evaluate instead of four; new use cases are windows and presets, not code.
- Merging the four handler pairs moves part of step 5 (stack packs) into step 1.
- Saravana's test data is lost by design (§7).
- Schema checks may fail stations that pass today; the build loosens a schema, not a handler,
  where an agent's output legitimately varies.
- A deploy repair now costs a verify pass (test, scans, review, change-risk, build) before the
  redeploy.
- Build split into PRs (estimates):
  1a. contract, ledger, JSON Schemas, publish checks (fit, floor, roles) — ~2 days
  1b. one standard lane: merged handlers, windows, applicability, skip records, reset + reseed — ~3 days
  1c. editor: container columns, drop-time fit checks, empty-container mode — ~2 days
  1d. preset rebase — ~1 day
- Then step 2 (modes, owners, SoD, discuss), step 3 (entry from ticket/doc/image).

## Implementation notes (1a)
- `contract.py` (pure): columns, artifact types and references, the station-type library
  (`STATION_TYPES`, today's handlers mapped by `TYPE_OF_HANDLER`), fit and floor checks, roles.
- `artifacts.py`: schema loading and validation, the ledger (`artifacts/<change>/ledger.json`).
  Schemas live in `engine/src/agent_factory/schemas/artifacts/`, written by hand (no pydantic
  models exist yet for these manifests).
- `engine/handoff.py`: entry artifacts recorded once per change; needs checked before a station
  runs (held if missing); gives collected and validated after it passes (invalid fails it).
- Until windows (1b), a workflow declares its entry artifacts (`entry:`, default
  `[request@1, branch@1]`): the engine provides `branch` itself (scaffolded worktree for a factory
  product, the clone for a repo). The assess template starts from `[branch@1]`.
- Stations keep `handler:` in 1a; the YAML `type:` key and the merged handlers come with 1b.

## Implementation notes (1b-1)
- `workflow-templates/standard/` replaces the four templates; `presets.yaml` holds the windows
  (`factory-new`, `factory-change`, `repo-assess`, `repo-change`) and presets (`design-only`,
  `verify-only`, `delivery`; Observe waits for operate stations). A change records its `window`.
- Stations name a `type`; `handler_for(target)` picks the implementation (a type without one for
  the target is skipped), `target_agents` the agent, `fail_on_by` a scan's policy per window or
  target, `applies_when` (`target`, `windows`, `not_windows`) the rest. `WorkflowDoc.plan(window,
  target)` gives the stations, reachable repairs and every skip with its reason; the engine
  records it as an event and `plan.json` and routes by it (a repair route outside the window holds).
- Publish checks fit per window (data, floor, the window's end counts as an exit); the column rule
  over the whole lane.
- Applicability needed beyond types: `requirements`/`design` factory only (repo changes skip them,
  as today); `code-review` not in `verify-only` (no request to review against); `deploy-repair` and
  `acceptance` not in `delivery` (no repair route, no spec there).
- Fresh start: `agent-factory reset [--confirm]` drops the work (products, changes, events,
  evaluations, workflows and their files); the engine refuses older data at start (`reset.check`,
  a `generation` stamp in the database).
- Left for 1b-2: one org workflow instead of one per blueprint, removing `LEGACY_HANDLERS`,
  `STAGE_ORDER`, `line.yaml`, the `blueprint` alias and `kind`/`handler` on stations.

## Implementation notes (1b-2)
- Workflows belong to the org: config `workflows:` (id → template, default `standard`) and
  `default_workflow`; every product runs the default whatever its blueprint. The blueprint keeps
  the stack, environment and delivery bindings (shown per blueprint on the workflow page);
  evaluations build with the first new-product blueprint (`cfg.eval_blueprint`).
- Removed: `LEGACY_HANDLERS` (and names from before ADR-0028), `STAGE_ORDER` (the column and data
  rules replace it), `line.yaml`, the `blueprint` alias on stored workflows, the per-blueprint
  `WorkflowRegistry` and the `default` → blueprint workflow rename.
- Data generation 3: databases from 1b-1 (workflows named after blueprints) need
  `agent-factory reset --confirm` once more.
- `kind`/`handler` on stations stay until 1c rebuilds the editor around types and columns
  (Saravana, 2026-10-12).
