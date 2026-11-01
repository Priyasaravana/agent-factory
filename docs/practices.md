# Best practices we measure the factory against

Status: living reference · last reviewed 2026-10-18 (stack packs: FastAPI and Next.js).
Every ADR has a **Best practices** section that maps its decision to the sections below (from ADR-0041 on).
Legend: ✅ in place · 🟡 partial · ⬜ not yet · → the phase that closes it.

The factory is judged twice:
- as a **producer**: are the apps it generates production-grade?
- as a **system**: is the factory itself safe, observable and improving?

## 1. Autonomy Maturity Model (Factory.ai, "Software Factory" white paper)
This is a vendor paper, so we use its model and ignore its outcome numbers.
It scores *repositories* across eight pillars. Level 3 is the production floor.
The goal is **every generated app starts at Level 3**, not Level 5 everywhere.

| Pillar | Generated apps | Factory repo |
|---|---|---|
| Style & validation | ✅ ruff lint + format, pre-commit config | ✅ ruff, mypy, tsc |
| Build | ✅ uv.lock, Dockerfile, deterministic build | ✅ |
| Testing | ✅ pytest, coverage ≥ 80%, acceptance tests, e2e smoke suite, run against a preview in the sandbox before any image (Next.js, ADR-0043) and against the live app (holdout stays hidden by design) | ✅ engine + auth tests, Playwright |
| Docs & instructions | ✅ AGENTS.md, README | ✅ AGENTS.md, ADRs, runbook |
| Environment & sandbox | ✅ reproducible; agents and `make verify` run in a sandbox (ADR-0014) | ✅ compose; sandbox per agent session |
| Code quality | ✅ small golden-path modules | 🟡 `actions.py` is ~1,800 lines |
| Observability | ✅ JSON logs with request ids, /metrics, OpenTelemetry hook | 🟡 event log per change, egress log, per-agent-call records, transcripts and denials (ADR-0022); no OpenTelemetry export yet |
| Security & governance | ✅ Trivy, gitleaks, CODEOWNERS, Dependabot, SBOM + provenance · ⬜ branch protection (on publish) | ✅ secret refs + redaction, auth + audit, role checks, hooks, sandbox, threat model, sealed evidence per change (ADR-0023), capability grants per station (ADR-0039) |

**Processes:** ✅ signal→build→verify→deploy→accept→feedback loop with human gates · ✅ preflight before any model usage ·
🟡 model tiers per agent (no provider fallback) · ✅ lanes per kind of work: new product, product change, repo assessment, repo change as a PR (ADR-0031/0033/0039) · ⬜ intake from issues/Jira ·
⬜ staged rollout and rollback · ⬜ parallel / DAG decomposition · ⬜ incident response.

**Headline metric:** share of delivered apps at Level 3+. ✅ The Quality gate station scores every change (21 signals + secret scan, each tagged with a quality pillar, §7) and fails the change below Level 3. ✅ The Outcomes page aggregates it (live apps at Level 3, requirements verified live).

## 2. DORA: four keys and the 2025 AI Capabilities Model
- **Measure outcomes, not activity:**
  - lead time (change requested → delivered);
  - change failure rate (changes HELD or failed after deploy);
  - time to restore;
  - deploy frequency.
  - Add **autonomy ratio** (delivered with no *unplanned* human touch; fix loops are agents fixing their own work and are reported as effort) and **cost per delivered change** (failed changes' spend included).
  - ✅ → Outcomes page, from a status transition log (ADR-0019, [definitions](outcomes.md)).
- **Small batches:** ✅ feedback iterations are small changes. Keep them small (cap the change-request scope).
- **Strong version control:** ✅ versioned workflows and pinned skills · ⬜ every generated app gets its own repo history by default.
- **Quality internal platform:** ✅ golden path and blueprint workflows · ✅ one golden path per stack: FastAPI service and Next.js web app (ADR-0041) · ✅ design decisions as a pack catalogue the engine checks, one ADR per answer in the product, and a pillar roadmap whose deferrals become ledger items (ADR-0044) · ⬜ React SPA and full-stack packs.
- **Clear AI policy:** 🟡 guardrail hooks. Write down which actions agents may never take (see §3).

## 3. OWASP Top 10 for Agentic Applications (2026)
Maps directly to our agents. See [security/threat-model.md](security/threat-model.md).
- **Goal hijack / prompt injection** (via requirements, feedback, imported skills):
  - 🟡 skills are reviewed and pinned;
  - 🟡 trusted people, untrusted text: only signed-in members start work, the diff decides the risk (ADR-0027) · ⬜ mark requirements and feedback as untrusted data inside prompts.
- **Tool misuse & unexpected code execution:** ✅ sandbox with egress allowlist, and hooks as a second layer.
- **Identity & privilege abuse:** ✅ agents hold no credentials beyond the model token and cannot reach the engine · ✅ with the LiteLLM gateway, a virtual key per agent call instead of the model token (ADR-0036; `direct` stays the default) · ✅ each station gets only its phase's capabilities (ADR-0039).
- **Supply chain (skills, templates, packages):** ✅ pinned commits, consent for scripts, SBOM + provenance · ⬜ pack id, version and hash in provenance (ADR-0041) · ⬜ image signing (with a registry).
- **Memory/context poisoning:** 🟡 earlier-iteration docs are fed back. Keep holdout scenarios out of builder context (✅).
- **Cascading failures & rogue agents:** ✅ deterministic ChangeManager, loop limits, usage limits, HOLD on missing evidence · ✅ failures classed as work, infra or decision: infra retried in place with backoff and a cap, never sent to the developer; time limits per agent call and inside a station (ADR-0043).
- **Human-agent trust:** ✅ evidence shown for every decision; people approve publish.

## 4. Supply chain: SLSA and OpenSSF Scorecard
- **Generated images:** ✅ SBOM (syft) + provenance record in the package station · ✅ image CVEs read as findings, routed by origin (app bump to the developer, base image or no fix to a person), recorded in the work ledger with accepted risks that expire (ADR-0042) · ⬜ scheduled rescans of live images · ⬜ signature (cosign) once images go to a registry.
- **Factory repo:** ✅ OpenSSF Scorecard in CI (weekly + on main), least-privilege workflow tokens, Dependabot for uv, npm, Actions and Docker (Python base images patch-only) · ✅ CI builds and tests the shipped images and runs a dry-run end-to-end smoke test (ADR-0016) · ✅ branch protection: a ruleset on main requires the `engine`, `auth`, `web`, `golden-path`, `images` and `e2e` checks · ⬜ pin Actions to commit SHAs (they use version tags).

## 5. Agent engineering
- ✅ Deterministic orchestration and LLM work only inside stations ("workflows before agents").
- ✅ Smallest tool set per role; skills preloaded only where measured.
- ✅ **Evaluation harness** (ADR-0025): a fixed suite of products per workflow, run on every new version against the active one (pass rate, autonomy, Level 3, verified live, cost, loops, lead time). `block` keeps a version a candidate until nothing regressed; overrides need a recorded reason; rollback is never gated · ⬜ repeated runs per case, scheduled re-evaluation for model drift.
- 🟡 Trace every agent call: ✅ per-call records (turns, time, cost, tools, denials) and redacted transcripts, with effort by station on Outcomes (ADR-0022); ⬜ export as OpenTelemetry GenAI spans.

## 6. Enterprise governance (when a company adopts it)
- NIST SSDF (secure development practices) and ISO/IEC 42001 (AI management system) are the frameworks auditors will ask about.
- Our audit log, versioned workflows, approvals and evidence records are the raw material. ⬜ Map them when a real customer needs it; don't build for it now.

## 7. Quality pillars (ADR-0024)

Ten pillars: the Well-Architected pillars plus the ISO/IEC 25010 qualities that Well-Architected misses. As in §1, they judge the factory twice: the apps it produces, and the factory itself.

The Autonomy Maturity Model (§1) scores the repository; these pillars score the running product. Every readiness signal carries one primary pillar, and the Level 3 gate is unchanged. A pillar with no signals is **uncovered**, never 100%. See them on **Outcomes → Quality pillars of what is live**, on each change, and in its sealed evidence (`pillars.json`).

### Signal → pillar mapping
Readiness signals only (21 + secret scan), pinned by `tests/test_pillars.py`. Trivy, SBOM, provenance and the review are station evidence: they appear in the evidence pillar index by file, but are not counted as signals.

| Pillar | Signals |
|---|---|
| security | `secret_scan`, `container_nonroot`, `codeowners`, `dependency_updates` |
| reliability | `health_endpoints`, `module_contracts` (pack signal: Next.js; every module's adapters pass its contract tests, ADR-0044) |
| performance | — uncovered |
| operability | `structured_logs`, `metrics`, `tracing` |
| cost | — uncovered |
| interoperability | `design_docs` (spec, design, OpenAPI contract) |
| usability | `a11y` (pack signal: Next.js; axe on every route, serious or critical fails) |
| maintainability | `readme`, `linter`, `formatter`, `unit_tests`, `agents_md`, `verify_target`, `precommit`, `ci`, `coverage_gate`, `modules_isolated` (pack signal: Next.js; adapters private to their module, checked by dependency-cruiser in `make lint`, ADR-0044) |
| portability | `lockfile` |
| compliance | `acceptance_tests`, `requirements_traced`, `e2e_tests` |

### Generated apps

| Pillar | State | Have | Next signals (proposed ids) → phase |
|---|---|---|---|
| Security | ✅ | secret scan, non-root container, CODEOWNERS, Dependabot; hardened runtime images by default with approved fallbacks and factory-wide base-image risk decisions (ADR-0045); sign-in and roles on protected writes in Next.js products whose decisions call for them (local accounts, scrypt, httpOnly session cookie, first admin from a generated Secret, ADR-0044); Trivy findings routed by origin and kept in the work ledger (ADR-0042), SBOM + provenance in the package station | `image_signed`, `branch_protection` → phase 5 |
| Reliability | 🟡 | liveness and readiness endpoints · `module_contracts` in the Next.js pack: auth, authz, logging, metrics and tracing behind ports, each adapter (no-op included) passing the module's contract tests (ADR-0044) | `probes`, `graceful_shutdown`, `resource_limits` → phase 5 (helm) |
| Performance & scalability | ⬜ | deferrals stated per product with a trigger, in `docs/roadmap.md` and the work ledger (ADR-0044) | `load_smoke` (k6 in verify), `autoscaling` → phase 5 |
| Operability & observability | 🟡 | JSON logs, /metrics, OpenTelemetry hook | `runbook`, `slo_alerts` (starter SLOs and alert rules) |
| Cost | ⬜ | factory-side cost per change only; deferrals stated per product with a trigger (ADR-0044) | `resource_requests` (runtime cost estimable) → phase 5 |
| Interoperability | 🟡 | OpenAPI contract present (`design_docs`) | `openapi_valid` (spec published and valid) |
| Usability | 🟡 | `a11y` in the Next.js pack: axe in the Playwright e2e suite, run against every deployment (ADR-0041) · design system (Tailwind v4 tokens per `look`, shadcn/ui components, app shell), a designer writing `docs/ui.md`, route screenshots (light/dark, desktop/phone) as evidence and a UI review against the pack's rubric, reported only (ADR-0044 §4, nextjs-web 1.4.0) · services have no UI, so it stays uncovered there | UI review blocking once the rubric is calibrated; `a11y` in the React SPA pack |
| Maintainability | ✅ | ruff, pre-commit, coverage ≥ 80%, CI, AGENTS.md, small modules · ports and adapters in the Next.js pack, `modules_isolated` enforced by lint (ADR-0044) | modules in the FastAPI pack (after J5) |
| Portability | 🟡 | lockfile, Dockerfile, app chooses its Python, Helm chart to any cluster (ADR-0026) · two stacks: FastAPI and Next.js (ADR-0041) | `chart_lint`; more stacks → React SPA, full-stack packs |
| Compliance & evidence | ✅ | acceptance tests, traceability, e2e, review evidence, SBOM, provenance, sealed evidence with a pillar index (ADR-0023) | — |

### The factory itself

| Pillar | State | Notes |
|---|---|---|
| Security | ✅ | secret refs + redaction, auth + audit, access rules, sandbox + egress allowlist, no root, threat model, model gateway with a virtual key per call (ADR-0036, optional), capability grants per station (ADR-0039) |
| Reliability | 🟡 | `make backup` / `restore` / `upgrade`, loop and usage limits, SQLite reads under the lock · ✅ failure classes with retries and backoff, time limits in agent time (ADR-0043) · ⬜ single node, no HA, Postgres deferred |
| Performance & scalability | ⬜ | no measured capacity · parallel tasks (DAG) later |
| Operability & observability | 🟡 | event log, per-call records, transcripts, Outcomes, runbook · ⬜ OpenTelemetry GenAI export |
| Cost | ✅ | cost per delivered change (failed changes included), usage limits, model tiers per agent |
| Interoperability | 🟡 | OpenAPI-first API, API tokens · ⬜ MCP integrations, issue intake, Backstage/Port plugin |
| Usability | 🟡 | new UI, themes, ⌘K · product and change pages: a "Needs you" panel with the pending actions (engine-decided, `GET /api/attention`), collapsible panels with one-line summaries, events levelled error/warn/info/debug by the engine; no sideways scroll at 390 px · ⬜ accessibility check on the factory UI |
| Maintainability | 🟡 | ruff, mypy (with a baseline), tsc, engine + e2e tests · `actions.py` is ~1,800 lines |
| Portability | 🟡 | docker compose for the factory · ✅ generated apps to any registry and cluster (ADR-0026) · ⬜ live check on `local-ingress` |
| Compliance & evidence | ✅ | audit log, versioned workflows, approvals, sealed evidence manifest (ADR-0023) · ⬜ SSDF / ISO 42001 mapping on demand |

**Headline:** generated apps are strong on security, maintainability and evidence, partial on reliability, operability, interoperability, portability and usability (web apps only), and uncovered on performance and cost. They come in two stacks with the same Level 3 (ADR-0041): FastAPI services and Next.js web apps, whose `a11y` check is the first usability signal.

## Roadmap
- **Done:**
  - sandbox change: per-session agent sandbox with an egress allowlist; `make verify` sandboxed; Level 3 template; Quality gate station; SBOM + provenance; threat model.
  - readiness + preflight change: provider `check()`; readiness page and header pill; product/change preflight; archive; 20 app ports; OpenSSF Scorecard; Dependabot; least-privilege CI.
  - upgrade-safety change: CI builds all images and tests the engine on the image's Python; the sandbox verifies the golden path offline; a dry-run end-to-end browser smoke test; engine on the next Python (advisory); a weekly run; app Python decoupled from the factory's; `make backup` / `restore` / `upgrade`.
  - spec-driven change: numbered requirements; requirement → scenario → test → live-result traceability with a Level 3 signal; optional spec review gate (off by default); spec diff on feedback; bring your own spec (ADR-0017).
  - least-privilege change: no root in the factory's containers (one-shot offline init step; factory, auth and web without capabilities); runtime proof in CI; privileged dind documented as an accepted risk (ADR-0018).
  - outcomes change: status transition log; Outcomes page with deliveries, lead time, change failure rate, recovery, autonomy, cost per delivered change, Level 3 share, requirements verified live, where the time goes and who the factory is waiting on (ADR-0019).
  - spec review change: built-in review station after verify/readiness; the reviewer reports on every requirement and the engine judges (partial/missing or blocker/major findings go back to build; incomplete reviews are held); review evidence per requirement in the traceability view (ADR-0020).
  - learning change: after changes that needed help, an observe-only retro suggests lessons per agent; the engine vets them (no check-weakening, no duplicates, evidence required); an admin accepts them into the workflow draft; they apply only when published (ADR-0021).
  - agent observability change: every guardrail denial recorded as a decision; one record per agent call (turns, time, cost, tools, denials); redacted transcripts per call; Agent calls panel and effort by station; the retro learns from denials and call stats (ADR-0022).
  - evidence manifest change: every stopped change sealed (events, spec, review, traceability, SBOM, provenance, transcripts, each with SHA-256); re-verified on every view; one-zip download for the product's creator or an admin; checkable with `sha256sum -c` (ADR-0023).
  - quality pillars change: ten pillars (Well-Architected + ISO/IEC 25010); every readiness signal tagged with one pillar; readiness report, change page, Outcomes and the sealed evidence grouped by pillar; uncovered pillars shown as uncovered (ADR-0024).
  - evaluation harness change: a fixed evaluation suite per workflow; every new version measured against the active one before it serves real products; the `block` gate keeps it a candidate until nothing regressed, with recorded overrides (ADR-0025).
  - since 2026-10-01:
    - cluster target with OCI registry and Helm (ADR-0026);
    - change-risk policy (ADR-0027);
    - products, changes and blueprints (ADR-0029);
    - work items (ADR-0030);
    - existing repos: assessment (ADR-0031) and changes as pull requests (ADR-0033);
    - engineering standards as skills (ADR-0032);
    - tool and scan stations (ADR-0034/0035);
    - model gateway (ADR-0036);
    - station contract (ADR-0037);
    - lanes, phase registry, capability boundaries and the builder (ADR-0039);
    - product work ledger (ADR-0040).
- **Next:** ✅ failure classes (ADR-0043 PR 1); end-to-end tests before deploy (ADR-0043 PR 2).
- **After that:** stack packs (ADR-0041):
  - ✅ FastAPI extracted as pack #1;
  - ✅ Next.js web app with `a11y`;
  - React SPA;
  - per-pack evaluation;
  - then full-stack.
- **Now (J2, ADR-0046):** ✅ rollback to the last healthy release, diagnostics first (slice 1); ✅ validation in a
  temporary environment and promotion of the tested image, digest-checked (slices 2–3, on the `helm` target;
  `local` validates in place); 🟡 deployment strategy per product: in place or blue/green, two colours on one
  database with an instant switch back (slice 4).
- **Then:**
  - live check of the cluster target on `local-ingress`;
  - secret store, then gated promotion;
  - ECR + Argo CD.
- **Later:**
  - intake from GitHub issues;
  - staged rollout beyond blue/green (canary, traffic shares);
  - image signing;
  - provider fallback;
  - parallel tasks.

## Candidates to consider (from Warp's Cloud Software Factory)
Highest value only; each fits data or hooks we already have.
1. ✅ **"Waiting on whom" metric** (done, Outcomes page): split every change's time into agent-working vs waiting-for-a-human, and show the factory as a board by state. Warp's own dashboard shows humans, not agents, are the bottleneck. → part of the Outcomes page.
2. ✅ **Spec-driven development** (done, ADR-0017):
   - an optional spec review gate after design, with approve, request changes or edit (per workflow: always, first iteration or off; **off by default**);
   - numbered requirements (`docs/requirements.yaml`) plus the product spec and technical design (`docs/spec.md`, `docs/design.md`);
   - requirement → scenario → test → acceptance traceability, with a readiness signal;
   - feedback updates the spec first, and the gate shows the spec diff;
   - a product can bring its own spec.

   Warp calls spec review the highest-leverage checkpoint.
3. ✅ **Self-improving loop** (done for learnings, ADR-0021): after feedback or a fix loop, an agent proposes changes to the relevant skill or learnings as a workflow draft that an admin approves. Corrections then become better future changes.
4. ✅ **Code review station** (done, ADR-0020): a reviewer agent checks the diff against spec and design before packaging and records its findings as evidence. Part of the default template.

## Sources
- Factory.ai, *Software Factory: An Autonomy Maturity Model for the enterprise* (white paper, 2026)
- [DORA: State of AI-assisted Software Development 2025](https://dora.dev/dora-report-2025/) · [2025 DORA AI Capabilities Model](https://services.google.com/fh/files/misc/2025_dora_ai_capabilities_model.pdf)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [SLSA (OpenSSF)](https://openssf.org/projects/slsa/)
- [Warp: The Cloud Software Factory Build Guide](https://www.warp.dev/blog/software-factory-build-guide) · [build.warp.dev](https://build.warp.dev)
