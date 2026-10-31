# Agent Factory: master plan (any app, any repo, itself)

Status: agreed direction · updated 2026-10-02 after the trust and business review · merges and supersedes
[archive/plan-any-stack-any-model.md](archive/plan-any-stack-any-model.md). What to build next is in
[roadmap.md](roadmap.md): since 2026-10-21 the golden journey decides the order, and the phase table in §4
is the long-term shape, not the schedule.

## 1. Vision
A software factory that **builds, runs and improves software**:
- new apps on any stack, **agentic apps** first-class, plus data pipelines;
- teams' **existing repos**: maintain, assess and uplift (migrating to agentic comes later);
- **itself**, from improvement requests that its owner approves.

It runs on its own where policy allows: intake from tickets, scheduled upkeep, fewer human gates. It also fits into any company: their tools as stations, their CI, their cluster, their models. It **refuses harmful work and makes risky changes visible**: what decides is the actual diff, not only the request.

## 2. Principles (unchanged, now applied everywhere)
1. **Everything is data:** workflows, stations, stack packs, skills, tool integrations and model profiles are versioned config, not engine forks.
2. **A deterministic engine routes; agents work inside stations.** Tools and checks decide pass/fail, not agent opinions.
3. **No change without evidence.** Every workflow, skill or model change is measured by the evaluation harness (ADR-0025). Every run is sealed (ADR-0023).
4. **People set policy.** Risk-based gates decide when a person is needed; everything else runs.
5. **Pluggable adapters** for tools, CI, ticket systems, registries, clusters and models.
6. **Trusted people, untrusted text.** Only signed-in members start work. Tickets, repo files and web pages are data, never instructions. Risky changes need a second person, and harmful apps are never built (§3J).
7. **Ready for, proven, owned** (decided 2026-10-31). Every delivered product states what it is ready for,
   proves that readiness, and has an owner responsible for what happens after delivery. Readiness is a tier
   (demo, pilot, production) the engine works out from the sealed evidence, never an agent's claim, and the
   product shows what the next tier still needs. The owner is a named person (§3J), accountable for incidents,
   upkeep and retirement.

## Vocabulary (decided, ADR-0028)
- **Stations** follow the DevOps loop: Plan (requirements, design) → Code (implement) → Test (test, quality-gate, code-review, change-risk) → Release (build) → Deploy (deploy, deploy-repair) → Validate (acceptance) → Handover; Operate/Monitor come with the operate loop. Shipped in `feat/station-phases`.
- **Blueprint** (was product line) → **Product** (was order: one app) → **Change** (was run: one iteration) → stations. These renames land with work items (phase 2), so data migrates once.

## 3. Building blocks

### A. Work items: one intake for every kind of work (new)
Today an "order" means "build a new app". It generalises to a **work item**:
- **target:** a new product, an existing repo, or the factory itself;
- **kind:** new, feature, bug, upkeep, assess, migrate, remove;
- **source:** UI, chat, a ticket (GitHub Issues, Jira, Linear), a schedule, or an improvement request.

Each kind maps to a workflow. Existing repos, ticket intake and self-improvement all build on this one model. Every work item has a **requester** (a member) and every product a **named owner** (§3J).

### B. Targets
- **New product:** a stack pack's template, as today.
- **Existing repo:** onboard through a GitHub App or token.
  - The factory clones the repo and detects the stack.
  - If the repo has no AGENTS.md, the factory writes one.
  - The team's own CI is the verify step.
  - Every change is a PR with evidence; the team merges.
- **The factory itself:** the same as an existing repo, with stricter rules (§3I).

### C. Stations: agent, check, and the new **tool** station
Today tests, coverage and bandit run inside `verify`, and gitleaks, Trivy and SBOM inside readiness and package. They work, but you can't see, configure or swap them. A **tool station** makes them first-class.
- **One contract:**
  - input: a repo, an image or a URL;
  - output: normalised findings (SARIF-style), each with a severity;
  - a policy decides pass/fail, for example "fail on high and above, warn on medium";
  - the findings are sealed evidence.
- **Three adapters behind the same contract:**
  - **container:** a tool image run in the sandbox;
  - **MCP:** a tool exposed as an MCP server;
  - **CI/SaaS:** trigger the team's pipeline or SonarCloud/Snyk and read the result back.
- **Starter catalogue:**

  | Area | Tools |
  |---|---|
  | Testing | unit + coverage, integration, contract, e2e/browser (Playwright), load (k6) |
  | Security | SAST (Semgrep), dependencies (OSV-Scanner / Snyk), secrets (gitleaks), container (Trivy), IaC (Checkov), DAST (ZAP baseline against the deployed app), licences |
  | Quality | SonarQube / SonarCloud |
  | Change risk | the change-risk check (§3J) |
- **Agent variants:** a station can instead run an *agent* on a chosen model profile. Examples are a security-review agent on a local model, or a test-gap agent. That agent reports evidence, and the engine judges, as with the review station.
- **Station library:** stations are reusable entries that the lane builder drags in and every workflow can use.

### D. Skills library inside the factory (decided)
- **Library:** skills are stored and versioned in the factory: our own, imported ones (BuilderIO etc., pinned) and ones you create. Workflows pin skill versions, as today. Export to Git is optional.
- **Skill studio, create by chatting:**
  1. You describe what you want.
  2. A skill-author agent drafts the SKILL.md (and references), asks clarifying questions and tests the skill on a sample task in the sandbox.
  3. You review and save it as version 1.
  4. Editing works the same way: chat a change, see a diff, save a new version.
- **Self-improving skills:** the retro proposes skill *diffs* after repeated failures, not just short lessons. Each diff is evaluated automatically against the active version on the workflow's suite. A person accepts it, seeing the diff and the measured effect.
- **Usage data:** which skills were loaded per run, next to outcomes. You can see which skills help and which go unused.

### E. Stack packs, including **agentic packs**
A pack is a folder: a Level 3 template, `pack.yaml` (stack, verify, toolchain, egress, test-tag pattern, readiness checks), a golden-path skill and an evaluation suite. The FastAPI stack becomes pack #1.

**Agentic packs** (all four kinds you chose):

| Pack | What the generated app is |
|---|---|
| **Agent service** | An API around an agent (Claude Agent SDK) with tools/MCP, guardrails, cost limits and tracing |
| **Knowledge assistant (RAG)** | Ingestion, pgvector retrieval, answers with citations, and an evaluation set of questions |
| **Multi-agent workflow** | An orchestrator with roles and durable state: the factory's own pattern, packaged for clients |
| **Agent inside an existing app** | The existing-repo flow plus the agent-service pattern as a module |

**What agentic apps need that normal apps don't:**
- **A new readiness group, "AI safety & evals"**, with checks for:
  - an eval suite in the repo;
  - prompts and guardrails as files;
  - tracing of LLM calls;
  - prompt-injection tests;
  - spend limits;
  - no model keys in code.
- **Acceptance by scenario evals:** deterministic checks plus rubric-judged answers. A judge model scores against a written rubric, and the engine applies thresholds.
- **The app's own model access:** generated apps use their own model configuration through secret references, never the factory's token.

Non-agentic packs come after the first agentic one: Python worker / data pipeline, TypeScript web app, Go service, Java service.

### F. Model profiles: any model per stage
As planned:
- profiles name an endpoint and a model; local models are reached through Ollama's Anthropic-compatible API or a LiteLLM gateway;
- each profile is smoke-tested for tool use;
- models are compared side by side in evaluations;
- a profile can fall back to another.

The same profiles are available to generated agentic apps. A product or SaaS for other people uses **API keys**, never a personal Claude subscription token.

### G. DevOps practices for everything the factory makes
1. **Trunk + PR per change:**
   - every iteration is a branch and a PR in the product repo, with the pack's CI, branch protection, release tags and a changelog;
   - the factory's evidence is posted on the PR.
2. **Pipelines as code:** each pack ships its CI/CD (GitHub Actions first, GitLab later), running the same verify, tool stations and deploy. Apps then keep working without the factory.
3. **Environments and promotion:**
   - dev → staging → prod per product line, each mapped to providers (cluster, registry);
   - promotion gates (tests, DAST, change risk, approvals) and rollback to the previous release.
4. **Observability and SLOs:**
   - packs ship dashboards, SLOs and alert rules;
   - an **operate loop** watches the apps the factory owns, turns an alert into an incident work item, and proposes the fix as a PR;
   - this also covers the reliability and performance pillars, which are uncovered today.

### H. Running on its own
- **Ticket intake:** GitHub Issues first, then Jira and Linear through adapters or MCP. Labels map to work kinds, and results are posted back.
  - Issues written by a GitHub user **linked to a factory member** start automatically.
  - Everyone else's wait in a **"needs a member"** queue until a member accepts them.
  - Ticket text is data, never instructions.
- **Scheduled upkeep** for every app it owns:
  - dependency updates;
  - security rescans;
  - re-evaluation for model and skill drift;
  - config drift checks.
- **Risk-based gate policies:** for example, prod deploys, failed evaluations, risky change-risk findings and safety-core changes need a person; low-risk upkeep PRs can auto-merge when CI is green, if the team allows it.
- **Deploy anywhere:**
  - a Helm chart for the factory itself;
  - Postgres instead of SQLite;
  - object storage for evidence;
  - more than one worker.

### I. Self-improvement (decided: owner-approved PRs to its own repo)
An improvement request (UI, chat, ticket, or the retro noticing an engine-level pattern) becomes a work item on the factory's own repo:
1. The factory plans the change, codes it in a sandbox, and runs its own test suite, evaluations and the change-risk check.
2. It opens a PR with evidence.
3. **The factory's owner approves and merges.** The factory never merges its own changes.

Limits:
- **The safety core is out of bounds.** The acceptable-use list, the change-risk check, approvals and auth can only be changed by a PR a person wrote. If a self-improvement diff touches them, the run is held and the change is dropped.
- Changes to workflows, skills and prompts keep going through the evaluation gate.
- The data-level learning loop (retro lessons, skill diffs, both human-accepted and evaluated) stays as it is, with its per-workflow off switch.

### J. Trust and change-risk policy (new, decided)
**Who can ask.** Only signed-in members start work; external tickets need a member to accept them (§3H).

**What is checked.** The request at intake, and the **actual diff**, which is authoritative. A harmless-sounding request whose diff weakens security is treated by what the diff does.

**Six categories, two tiers:**

| # | Category | Example | Response |
|---|---|---|---|
| 1 | Weakening security | remove auth, disable TLS, open CORS to `*`, drop rate limits | a **second admin** approves with a written reason |
| 2 | Removing safety nets | delete tests, lower coverage gate, remove logging/audit, skip CI checks | a second admin approves with a reason |
| 3 | Destructive data changes | drop tables/columns, mass delete, irreversible migrations | a second admin approves with a reason |
| 4 | Exfiltration / backdoors | new outbound endpoints, hidden admin routes, hard-coded credentials, data sent to unknown hosts | a second admin approves with a reason |
| 5 | Abuse apps | phishing, malware, credential harvesting, spam, covert surveillance of individuals | **always refused**, with the matched rule recorded |
| 6 | Legitimate large removals | "remove the billing feature" | the **product owner** confirms an exact removal list before building |

Rules:
- **Acceptable-use list:** the factory ships a default list for category 5. Operators can add rules but never remove the defaults.
- **Second approver:** an admin other than the requester. On a single-admin install, a **break-glass self-approval** is allowed with a mandatory reason and a cooling-off delay (default 1 hour), and it is flagged in the evidence.
- **Product owner:** each product has a named owner, by default its creator, and an admin can reassign it. For category 6 the factory lists what will be removed (requirements, endpoints, tables, tests). The owner confirms that list, and the diff is checked against it. Anything removed beyond the list escalates to categories 1–4.
- **One check, three places:**
  1. a **change-risk station** after review and before package, which holds the run with its findings;
  2. a **required PR status check** in existing repos;
  3. a gate at **promotion to prod**.
- **Deterministic first.** The change-risk check is rules over the diff (paths, patterns, removed tests, migrations, new egress, auth decorators, config flags), with an optional agent reviewer whose findings the engine judges.
- **Evidence.** Every refusal, finding, approval, break-glass and owner confirmation is a decision event in the sealed evidence (ADR-0023).

## 4. Roadmap (long-term shape; the order of work is in roadmap.md)
Each phase is a few PRs, each with its value and "done when". Order chosen so later phases reuse earlier ones.

| Phase | Theme | Main pieces | Done when |
|---|---|---|---|
| 0 | Finish cluster target | Local-ingress live check; image-freshness check in `make local-ingress` | A live order runs on `local-ingress` |
| 1 | **Change-risk guard** | Acceptable-use refusal at intake; change-risk station over the diff; second-approver and break-glass gate; product owners and removal confirmation; evidence events (ADR-0027) | A risky test order is held until a second approval; an abuse order is refused; a confirmed removal passes |
| 2 | **Foundations for "any"** | Work items + targets; stack-pack foundation (FastAPI = pack #1); tool-station contract with the container adapter; first visible stations: tests, Semgrep, OSV, gitleaks, Trivy, ZAP baseline | The FastAPI evaluation is unchanged, with security findings as evidence per station |
| 3 | **Existing repos: maintain + assess** | Onboard a repo, detect its stack, assessment report (pillars, security, test gaps); issues → PRs using the team's CI; change risk as a required PR check | A real repo gets an assessment and an accepted PR |
| 4 | **Skill studio** | In-factory library, create and edit by chat, test-run, versions, usage data; retro skill diffs with evaluated acceptance | A skill made by chatting is used by a workflow and its effect is measured |
| 5 | **Agent service pack** | First agentic pack; "AI safety & evals" readiness; rubric-judged acceptance | 3/3 agentic test orders delivered at Level 3 |
| 6 | **Model profiles** | Gateway (LiteLLM) + Ollama; per-agent profiles; model comparison in evaluations | A local model runs a stage, chosen by evidence |
| 7 | **DevOps for products** | PR per change + pipelines as code in packs; environments and promotion (with the change-risk gate); dashboards, SLOs and alerts | An app promotes dev → prod through gates and runs its own CI |
| 8 | **Runs on its own** | GitHub Issues intake with member linking and the "needs a member" queue; scheduled upkeep; gate policies | A member's ticket becomes a merged PR with no UI use |
| 9 | **Self-improvement** | The factory as a target; safety core out of bounds; owner approval | An improvement request becomes a factory PR the owner merges |
| 10 | **More packs and adapters** | RAG and multi-agent packs, agent inside existing apps; data pipeline, TS web, Go, Java packs; MCP and CI/SaaS adapters; operate loop; factory Helm chart + Postgres | Each pack passes its suite; one tool runs through each adapter |
| later | Migrate to agentic | Analyse an app, plan the migration, then do it step by step | (after phase 10) |

Why this order:
- The change-risk guard (phase 1) comes before anything that widens who or what can start work: existing repos, tickets and self-improvement all depend on it.
- Phase 2 unlocks everything else.
- Existing repos (phase 3) is the biggest "fits anywhere" value, and its PR flow is exactly what self-improvement (phase 9) and ticket intake (phase 8) reuse.
- Skills (phase 4) and models (phase 6) make every later pack cheaper and better.
- The first agentic pack (phase 5) comes early because it's the differentiator.

## 5. Risks, stated plainly
- **Breadth over depth.** Ten phases is months of work. Each phase must ship something used live before the next starts.
- **Agentic acceptance is fuzzier than HTTP checks.** Rubric judging needs its own calibration: known-good and known-bad answers in each suite.
- **Self-modification.** Owner approval, the out-of-bounds safety core, and the factory's test suite and evaluations are non-negotiable.
- **Rules over a diff miss things.** The change-risk check catches common patterns, not every clever attack. The second-person rule and the sealed evidence are the backstop; the rule set grows from real findings.
- **Break-glass becomes a habit on solo installs.** The delay and the evidence flag make it visible; Outcomes should count it.
- **Running against external repos means real credentials and real codebases.** Least-privilege tokens per repo, everything else in the sandbox, PRs only (no direct pushes).
- **Local models are weaker at long agentic work.** The evaluation harness decides where they're good enough.

## 6. Product and business (decided)
- **Priorities:** credibility and leads for the consulting practice, and the factory as its own delivery tool, first. Product revenue later.
- **Time:** part-time, growing. So: **self-hosted first, paid setup and support through consulting; no SaaS yet.** A hosted offer is revisited when there is a team to run it (on-call, security reviews, SOC 2/GDPR).
- **Model:** **open core.** The engine is open source under **Apache-2.0**; paid enterprise features later (for example SSO/SCIM, audit export, multi-tenant, premium packs and adapters).
- **IP:** built on own time and equipment; the contract has been checked.
- **Before publishing:**
  - check the licences of everything bundled (imported skills, templates, Agent SDK/CLI terms);
  - ship the acceptable-use list and the change-risk guard in the first public release;
  - use API keys, never a personal subscription token, for anything other people run;
  - pick the name (see open decisions) and check trademarks.

## 7. Open decisions
1. **Name.** "Agent Factory" is crowded. Candidates: Autowright, Forgeline, Kilnworks, Throughline, or "Agentic Software Factory (ASF)" with "your software vending machine" as the tagline. Trademark check before choosing. (The UI says "Agent Software Factory" since 2026-10-26.)
2. First ticket system: GitHub Issues (assumed).
3. The test repo for phase 3: one of your own, or a sample open-source app?
4. For deploy anywhere: Postgres for the factory's state (assumed), when we get there.
5. Earlier plan decisions still open: LiteLLM gateway, Next.js for the TS web pack, a Postgres-backed worker queue.
6. The break-glass cooling-off delay (1 hour proposed) and whether an install can turn break-glass off.
