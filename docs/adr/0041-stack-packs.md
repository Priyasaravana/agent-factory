# ADR-0041: Stack packs: one pack per tech stack the factory builds

**Status:** accepted · 2026-10-16 · extends [ADR-0039](0039-lanes-phases.md) §2 (packs); takes the
build half of `blueprints:` ([ADR-0029](0029-products-changes-blueprints.md)) into packs.

## Context
The factory runs many lifecycles but builds one kind of app: a FastAPI service. What makes that app
good is spread over five places, and most of it is Python-specific:

| Today | Where | Stack-specific |
|---|---|---|
| Template | `templates/fastapi-service/` | yes |
| Golden-path skill | `plugin/skills/fastapi-golden-path/` | yes |
| Verify command, declared stack, port, chart | `blueprints.fastapi-service` in the config | mixed |
| Readiness signals | `readiness.py` (22) | 13 of 22 read Python files: ruff in `pyproject.toml`, `uv.lock`, `tests/test_*.py`, `app/*.py` for `/healthz`, `/metrics`, OpenTelemetry and JSON logs |
| Evaluation cases | `workflow-templates/standard/evals.yaml` | yes (three API services) |

So a second stack today means engine changes in five places. Two pieces already point the way:
- **Blueprints** pick the template, verify command and declared stack per product (`product.blueprint`). Intake already pauses on a stack conflict (`stack.py`).
- **Packs** (ADR-0039 §2) are admin-installed, reviewed folders with `pack.yaml` that add phases and station templates.

The agent sandbox already has Node 22 and npm, because the Claude CLI needs them.

## Decision

### 1. One pack format; a pack may add a stack
A **stack pack** is an ADR-0039 pack with a `stack:` section. It uses the same folder, the same admin install and the same review.
- A pack may add phases, stations, a stack, or any mix of these.
- The config key `phases.packs` becomes `packs:`; the old key is read as an alias.

```yaml
id: nextjs-web
version: 1.0.0
title: Next.js web app
stack:
  builds: web-app              # service | web-app | spa | full-stack | static-site | agent-service | worker
  summary: Next.js (App Router) + TypeScript, server-rendered, one container on Kubernetes
  technologies: [typescript, next.js, react, node.js, docker, helm, kubernetes]
  template: template/          # copied into a new product's repo
  skill: skills/nextjs-golden-path   # preloaded for the design, develop and fix agents
  toolchain: [node@22, chromium]     # what the sandbox image must provide (preflight)
  verify: make verify
  e2e: make e2e BASE_URL={url}
  port: 3000
  health: {live: /api/healthz, ready: /api/readyz}
  chart: deploy/chart
  egress: [registry.npmjs.org:443]   # added to the sandbox allowlist for this pack's products only
  detect: {files: [package.json], deps: [next]}   # team repos (§5)
  readiness: readiness.yaml    # how this stack proves each signal (§3)
  evals: evals.yaml            # this pack's evaluation cases (§6)
```

**The pack says what to build.** It is shipped, versioned and reviewed.
**The blueprint says where and how this install runs it.** A blueprint in the config names its pack (`pack: nextjs-web`) and keeps the install-specific settings:
- environment and node ports;
- the Trivy, SBOM and gitleaks commands;
- the minimum readiness level.

The config may override a pack field. An override is recorded in the change's evidence.

**Migration:**
- `fastapi-service` becomes pack #1 at `packs/fastapi-service/`, and its template and skill move in.
- The blueprint keeps its key, so `product.blueprint` still resolves. There is no data migration.

### 2. Chosen at intake, recorded on the product
- **New product:** the create form asks "What are you building?", with one card per installed stack pack (title, summary, `builds`). The choice is stored as `product.blueprint` (as today), and `pack@version` is recorded on the product.
- **Stack conflict:** when the request requires a technology another installed pack builds, the intake question offers "switch to <pack>". Switching is allowed only before the first build, while the product repo is still empty. After that, today's behaviour stays (`stack.question`).
- **Change to a factory product:** reuses the product's pack. Each change pins the pack version it starts on, like skills.
  - **A new pack version applies from the next change** and is shown on it.
  - An admin may pin a product to a version.
- **Who chooses:** requesters choose the stack. They still never choose the lane (ADR-0039).

### 3. The same readiness signals; each pack says how its stack proves them
**What doesn't change:** signal ids, levels, points, pillars (`tests/test_pillars.py`) and the Level 3 gate.

**What changes is how each signal is checked.** Stack-specific Python lambdas become declarative rules.
- **Stack-neutral signals stay in the engine:** `readme`, `agents_md`, `precommit`, `codeowners`, `dependency_updates`, `container_nonroot`, `verify_target`, `ci`, `design_docs`, `requirements_traced`, `secret_scan`.
- **Stack-specific signals take their check from the pack's `readiness.yaml`:** `linter`, `formatter`, `unit_tests`, `lockfile`, `coverage_gate`, `acceptance_tests`, `e2e_tests`, `health_endpoints`, `metrics`, `tracing`, `structured_logs`.
- **Rule kinds:** `file`, `glob`, `contains {file, text}`, `matches {file, regex}`, `all`, `any`.
  - Rules are pure checks on the files, as today.
  - They run in milliseconds and never execute anything.
- **Full coverage is required.** A pack that leaves a level 1–3 signal unmapped is refused at load, so "Level 3" means the same in every stack. There is no "not applicable": a stack that cannot prove a signal is not ready to be a pack.
- **Extra signals:** a pack may add its own, for example `a11y`. Each one names a pillar from `pillars.py` and a level, and counts only for that pack's products.
- **One convention:** every template exposes `make verify`, `make test` and `make e2e`.
  - `verify_target` and `ci` therefore stay stack-neutral.
  - Agents get the same instruction in every stack.

### 4. The first packs
| # | Pack | Builds | Key choices |
|---|---|---|---|
| 1 | `fastapi-service` | service | Today's template, extracted unchanged. |
| 2 | `nextjs-web` | web-app | App Router, TypeScript strict, `output: standalone`, Dockerfile `USER node`, `/api/healthz`, `/api/readyz`, `/api/metrics` (prom-client), pino JSON logs, `@vercel/otel`. |
| 3 | `react-spa` | spa | Vite + React + TypeScript, served by unprivileged nginx. nginx config gives `/healthz` and `/readyz` and a JSON `log_format`; an nginx exporter sidecar in the chart gives `/metrics`; the OpenTelemetry web SDK gives `tracing`. |

**Signals shared by packs 2 and 3:**
- **Lint and format:** eslint, with `prettier --check` in `make lint`.
- **Tests:** vitest, with a coverage threshold as `coverage_gate`.
- **Lockfile:** `package-lock.json`.
- **Acceptance and e2e:** Playwright specs, tagged with requirement ids for traceability.

**`a11y` (new; usability pillar, level 3; packs 2 and 3):**
- The Playwright e2e suite runs `@axe-core/playwright` on every route.
- Any serious or critical violation fails it.
- It is the first signal for the usability pillar.

**Sandbox:** Playwright needs Chromium, so the sandbox image gains an optional browser layer.
- A pack asks for it with `toolchain`.
- Preflight refuses a pack whose toolchain the sandbox image lacks.

### 5. Team repos
- **Detection:** on onboarding, the pack is detected from its `detect` rules, using `assess.detect_stack`.
- **A match gives guidance, not rules:**
  - the change's agents get the pack's skill, but the repo's own CI, AGENTS.md and conventions win;
  - the pack's signals appear in the assessment as advisory.
- **No match:** the change runs in generic mode, as today.
- **The pack replaces nothing** in a team repo's build.

### 6. Evaluation per pack
- **Cases:** each pack ships its own evaluation cases (`evals.yaml`, 3 cases).
- **A new pack, or a new pack version,** runs its own cases on the active New product lane. It is gated like a workflow version (`block`): nothing may regress against the previous pack version.
- **A new workflow version** runs:
  - all cases of the default pack;
  - **one case from each other installed pack.**

  This catches a workflow change that breaks another stack, at a cost that grows by one build per pack.
- **Done for packs 2 and 3:** 3 of 3 cases delivered at Level 3, with `a11y` passing.

### 7. Trust
- **Packs are a supply-chain input:** they carry templates, skills and commands that run in the sandbox.
  - Only an admin installs a pack, after reviewing it.
  - Each change records the pack's id, version and SHA-256 of its folder in its provenance and sealed evidence.
  - A skill inside a pack follows the pinning rules for imported skills.
- **Egress:** a pack's `egress` entries are shown at install and added only to the sandboxes of that pack's products, never factory-wide.

## Consequences
- **Adding a stack is adding a folder.** The engine changes only for a new rule kind or toolchain.
- **`readiness.py` shrinks** to the neutral signals plus a rule interpreter.
  - The FastAPI pack must reproduce today's results exactly: every readiness fixture keeps its level and points.
- **Files move:** `templates/fastapi-service/` and `plugin/skills/fastapi-golden-path/` go into `packs/fastapi-service/`.
  - `stations.skill_text` also searches the product's pack.
- **UI:**
  - stack cards on the create form;
  - the pack and its version on the product page;
  - Settings → Packs lists stacks with their egress and toolchain.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §1 Autonomy Maturity Model: every generated app starts at Level 3 | Every pack must prove every Level 1–3 signal; refused at load otherwise. |
| §1 Code quality | Stack logic leaves `readiness.py`; the engine keeps a small rule interpreter. |
| §2 DORA: quality internal platform | One golden path per stack: template, skill and checks together. |
| §3 OWASP agentic: supply chain | Admin install, version + SHA-256 in evidence, pinned skills. |
| §3 OWASP agentic: tool misuse | Pack egress is per product, shown at install. |
| §4 SLSA | Pack id, version and hash are part of each change's provenance. |
| §5 Evaluation harness | Per-pack suites; pack versions gated like workflow versions; workflow versions cover every pack. |
| §7 Quality pillars: usability uncovered | `a11y` with axe in Playwright, in packs 2 and 3. |

## Plan
1. **Pack foundation, with FastAPI extracted** (~3 days): `stack:` schema and loader, rule interpreter, blueprint → pack binding, skill lookup, pack id@version and hash in evidence. Done when the FastAPI evaluation is unchanged and readiness fixtures score the same.
2. **Stack cards and "switch to <pack>"** (~1 day): stack cards on the create form, and the intake question offers to switch.
3. **`nextjs-web`** (~3 days): template, `readiness.yaml` with `a11y`, skill, the sandbox's Chromium layer, 3 cases.
4. **`react-spa`** (~2 days): reuses the Node rules from step 3.
5. **Per-pack evaluation gating** (~1 day).
6. **Then:** full-stack (Next.js + FastAPI, typed client from the OpenAPI contract), static site, agent service, worker.
