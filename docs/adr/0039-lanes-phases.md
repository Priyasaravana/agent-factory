# ADR-0039: Lanes per kind of work, a phase registry with capability boundaries, gated promotion

**Status:** accepted · 2026-10-12 · amends [ADR-0037](0037-station-contract.md) §1, §4, §6
(one lane + windows → lanes; fixed columns → a phase registry). ADR-0038 stays reserved for the secret store.
Design: canvas "Workflow lanes and builder" (boards 1–7).

## Context
Testing 1b on the Mac showed the single standard lane is hard to read: a run shows all 20 stations,
half of them empty circles with no reason, under DevOps labels that repeat out of order. An end user
can't tell why Onboard, Repo scan, Assess and Report were skipped. The data model behind it (windows
plus `applies_when` rules such as "requirements: factory only", "code-review: not in verify-only")
is the cause, not the UI. Saravana also set requirements the ADR-0037 model doesn't cover:
- new **stations** and new **phases** (e.g. observability, "observe & act") added later without engine rework;
- **environment promotion** (dev → staging → prod), each step gated;
- a builder that can't build a wrong workflow (no delivery step inside Develop);
- orgs adopting only some phases (Design only, Development only, Test, Delivery).

## Decision

### 1. Lanes per kind of work (replaces one lane + windows)
A workflow is a **lane**: what it works on (`new-build` | `repo`), which change kinds it serves, and
its stations in phase columns. The factory standard is a **set of lanes**: New product, Product change,
Repo assessment, Repo change (board 1). A change picks its lane by (product target, change kind).
- **Factory default, org associations (settled 2026-10-12).** The factory ships default lanes. An org
  copies or builds its own and **associates** it with products ("payments-api: repo changes use Repo
  change + PCI"); every product without an association falls back to the factory default for its
  (works_on, kind). Requesters never choose. A new need (e.g. a PCI compliance review) is met by adding
  a station template to a copied workflow and associating that workflow with the products that need it.
- **Editing workflows is admin-only.** Every publish is a new version; runs keep the version they started on.
- **The factory default is always there (settled 2026-10-12).** The default lanes ship with the factory
  and every install has them. An admin may edit one: each publish is a new version, and **"back to
  factory default"** makes the shipped lane the active version again (as a new version, so history
  stays). To change how only some products run, **copy** a lane into an org workflow with its own
  versions and a record of which lane it came from (rebase offers, 1d), and assign it. Ways back when
  something isn't working: roll a workflow back to an earlier version; back to factory default;
  remove a product's assignment (it falls back to the default at once); or the org-wide
  `use_factory_defaults` switch. Runs in flight keep the version they started on. The default uses
  only built-in phases, stations and roles, so it runs on any install whatever the org has added.
- A run shows only its lane's stations. **Skips are only conditional** (`when: {has: deploy_target}`),
  each with a sentence that says what would make the station run (board 3).
- Windows, presets as windows, and most `applies_when` rules are removed. A station keeps the same name
  in every lane; its implementation follows the lane's works_on (Test = golden-path tests on a new
  build, the repo's own checks on a repo).

### 2. A phase registry with capability boundaries
Phases become ordered registry entries, not a fixed list in code (board 5):
`{id, title, order, allows: [capabilities], gives: [artifact types]}`.
- The seven built-in phases (discover, design, plan, develop, verify, deliver, operate) are fixed in
  order and can't be removed. Packs may **append** phases (e.g. `observe-act`, ops-pack); existing
  workflows gain an empty column, nothing reorders.
- **Capabilities** name what a station may do: `read-repo`, `write-docs`, `write-tickets`, `write-code`,
  `run-sandbox`, `run-scanners`, `push-image`, `deploy:<env>`, `promote`, `open-pr`, `read-metrics`,
  `read-logs`, `open-incident`, `rollback:<env>`. Each phase allows a set (Develop: read-repo,
  write-code, run-sandbox; Deliver: push-image, deploy:<env>, promote, open-pr; …).
- The org enables the phases it has adopted; the builder hides the rest.
- **Org columns (settled 2026-10-12).** An admin may add a column between built-in phases (e.g.
  "Compliance" after Verify, "Release approval" before Deliver). An org column is **look-and-decide
  only**: it may allow `read-repo`, `run-scanners`, `read-metrics`, `read-logs` and `approve`, and gives
  only `report.<kind>` or an approval. It can never write code, push images, deploy or open a PR, so it
  can't sit between the last code change and the mandatory controls and let something through. Admin-only
  and versioned; a workflow that doesn't use the column doesn't show it. A column that needs write or
  deploy capabilities (e.g. "Observe & act" with rollback) comes only from a reviewed pack.

### 3. Station templates declare their phase and the capabilities they use
`{id, phase, works_on, needs, gives, uses, executor, source}` where `source` is built-in, `pack@version`
(e.g. compliance-pack: PCI compliance review) or the org. Publish refuses a template whose `uses` falls
outside its phase's `allows` (a "build image" template declared in Develop is refused: `push-image`
belongs to Deliver). Custom agent steps get observe-only capabilities unless an admin grants more,
always inside the phase's boundary.

### 4. Three layers stop a wrong workflow
1. **Registry:** templates are checked against their phase (§3).
2. **Builder (Saravana's design, 2026-10-12):** each phase is a **dotted box**; its "Add station" offers
   only that phase's templates, filtered by the lane's works_on and the org's enabled phases. A station
   from Deliver can't be added to Develop: there is no path to do it (board 2). Checks across phases
   still run on every add and at publish, with a plain-language reason: inputs exist to the left
   (Deploy needs Build image), locked controls stay (test, secret scan, change-risk), a step uses no
   more than its phase allows.
3. **Runtime:** the engine grants a station only its phase's capabilities: tool presets, write access,
   registry and cluster credentials, tokens. A step drawn in the wrong place still couldn't act.

### 5. Phase-scoped workflows (adopt only some phases)
A lane's **scope** is a run of adjacent enabled phases (board 7). What it doesn't cover arrives as
entry artifacts from the org's own tools (schema-checked, sealed, shown as "provided by"), or leaves
as outputs. Rules:
- a scope that changes code must include Verify (the floor: test, secret scan, change-risk);
- a scope that delivers without Develop needs `image` (or `branch`) + `report.test` + `report.risk` as entry artifacts;
- presets: Design only, Development only (Develop + Verify), Test & secure, Delivery only, Full package.

### 6. Environment promotion, gated
Environments are ordered in config (`promotion: [dev, staging, prod]`); each names its delivery
integration and a **gate policy** (board 6):
`gate: {requires: [acceptance@previous, image-unchanged], approve: release-manager, separation_of_duty: true, window: "Mon-Thu 09-16"}`.
- Deliver renders an environment track; each promotion is a station with capability `deploy:<env>`, so
  prod credentials reach only the prod deploy, after its gate.
- The same image digest is promoted, never rebuilt.
- The floor adds what can't be removed: change-risk approval (categories 1–4) before prod.
- Gate evidence (approver, checks, digest) is sealed with the change, like risk approvals today.

## What stays from ADR-0037
Station contract, versioned artifacts and JSON Schemas, the per-change ledger, fit checks (column,
data, floor), roles and owners, org-level workflows (1b-2), fresh start (data generation 4: reset once).

## Consequences
- Each lane reads on its own; the run view shows only what runs; builders can't mix steps.
- New phases and station templates come from packs or admins as registry entries, checked at publish.
- Capability enforcement turns phase placement into a security boundary, not a label.
- Promotion needs per-environment credentials: build it after the secret store (ADR-0038).
- Build (estimates):
  - **1b-3a** lanes replace windows; standard as 4 lanes; product assignments with factory-default fallback; copy, back to factory default; run view by lane — done
  - **1b-3b** phase registry + capability boundary (publish and runtime); org look-and-decide columns; scope + entry artifacts — ~1.5 days
  - **1c** builder: dotted phase boxes with per-phase templates, drop-time checks, empty/copy, phase-scoped presets, product associations — ~3 days
  - **1d** lane rebase from the standard — ~1 day
  - **promotion** environment track, gates, per-env capabilities — ~3 days, after ADR-0038

## Settled
1. Factory default lanes; an org's own workflows are associated with products; requesters don't choose;
   workflow editing is admin-only (2026-10-12).
2. Builder: dotted phase boxes, each offering only its phase's station templates (2026-10-12).
3. Org columns: admins may add look-and-decide columns between built-in phases; columns with write or
   deploy capabilities only from reviewed packs (2026-10-12).
4. Factory default is always available: edits are new versions, "back to factory default" restores the
   shipped lane; copies become org workflows; fall back per product or org-wide at any time (2026-10-12).

## Open questions (for 1b-3b and promotion)
1. Capability enforcement in 1b-3: tool presets and credentials only (proposed), or also network egress per phase?
2. Promotion on the local setup: dev only until the cluster target is live, or simulate staging/prod namespaces in kind?
3. Who may add phases and station templates: admins only (proposed), or also packs installed by an admin?
