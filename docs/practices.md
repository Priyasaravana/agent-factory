# Best practices we measure the factory against

Status: living reference · last reviewed 2026-09-29 (after the upgrade-safety change).
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
| Testing | ✅ pytest, coverage ≥ 80%, acceptance tests, e2e smoke suite (holdout stays hidden by design) | ✅ engine + auth tests, Playwright |
| Docs & instructions | ✅ AGENTS.md, README | ✅ AGENTS.md, ADRs, runbook |
| Environment & sandbox | ✅ reproducible; agents and `make verify` run in a sandbox (ADR-0014) | ✅ compose; sandbox per agent session |
| Code quality | ✅ small golden-path modules | 🟡 `actions.py` > 500 lines |
| Observability | ✅ JSON logs with request ids, /metrics, OpenTelemetry hook | 🟡 event log per run and egress log; no metrics/tracing yet |
| Security & governance | ✅ Trivy, gitleaks, CODEOWNERS, Dependabot, SBOM + provenance · ⬜ branch protection (on publish) | ✅ secret refs + redaction, auth + audit, role checks, hooks, sandbox, threat model |

**Processes:** ✅ signal→build→verify→deploy→accept→feedback loop with human gates · ✅ preflight before any model usage ·
🟡 model tiers per agent (no provider fallback) · ⬜ intake from issues/Jira ·
⬜ staged rollout and rollback · ⬜ parallel / DAG decomposition · ⬜ incident response.

**Headline metric:** share of delivered apps at Level 3+. ✅ The Readiness station scores every run (20 signals + secret scan) and fails the run below Level 3. ✅ The Outcomes page aggregates it (live apps at Level 3, requirements verified live).

## 2. DORA: four keys and the 2025 AI Capabilities Model
- **Measure outcomes, not activity:**
  - lead time (order → delivered);
  - change failure rate (runs HELD or failed after deploy);
  - time to restore;
  - deploy frequency.
  - Add **autonomy ratio** (delivered with no *unplanned* human touch; fix loops are agents fixing their own work and are reported as effort) and **cost per delivered change** (failed runs' spend included).
  - ✅ → Outcomes page, from a status transition log (ADR-0019, [definitions](outcomes.md)).
- **Small batches:** ✅ feedback iterations are small changes. Keep them small (cap the change-request scope).
- **Strong version control:** ✅ versioned workflows and pinned skills · ⬜ every generated app gets its own repo history by default.
- **Quality internal platform:** ✅ golden path and product-line workflows.
- **Clear AI policy:** 🟡 guardrail hooks. Write down which actions agents may never take (see §3).

## 3. OWASP Top 10 for Agentic Applications (2026)
Maps directly to our agents. See [security/threat-model.md](security/threat-model.md).
- **Goal hijack / prompt injection** (via requirements, feedback, imported skills):
  - 🟡 skills are reviewed and pinned;
  - ⬜ treat requirements and feedback as untrusted in prompts.
- **Tool misuse & unexpected code execution:** ✅ sandbox with egress allowlist, and hooks as a second layer.
- **Identity & privilege abuse:** ✅ agents hold no credentials beyond the model token and cannot reach the engine or gateway.
- **Supply chain (skills, templates, packages):** ✅ pinned commits, consent for scripts, SBOM + provenance · ⬜ image signing (with a registry).
- **Memory/context poisoning:** 🟡 earlier-iteration docs are fed back. Keep holdout scenarios out of builder context (✅).
- **Cascading failures & rogue agents:** ✅ deterministic RunManager, loop limits, usage limits, HOLD on missing evidence.
- **Human-agent trust:** ✅ evidence shown for every decision; people approve publish.

## 4. Supply chain: SLSA and OpenSSF Scorecard
- **Generated images:** ✅ SBOM (syft) + provenance record in the package station · ⬜ signature (cosign) once images go to a registry.
- **Factory repo:** ✅ OpenSSF Scorecard in CI (weekly + on main), least-privilege workflow tokens, Dependabot for uv, npm, Actions and Docker (Python base images patch-only) · ✅ CI builds and tests the shipped images and runs a dry-run end-to-end smoke test (ADR-0016) · ⬜ pin Actions to commit SHAs, branch protection with required checks on main.

## 5. Agent engineering
- ✅ Deterministic orchestration and LLM work only inside stations ("workflows before agents").
- ✅ Smallest tool set per role; skills preloaded only where measured.
- ⬜ **Evaluation harness:** a fixed set of orders run per workflow version. Track pass rate, cost, loops and lead time. Gate publishing a workflow version on no regression.
- ⬜ Trace every agent turn (OpenTelemetry GenAI conventions) for debugging and cost attribution.

## 6. Enterprise governance (when a company adopts it)
- NIST SSDF (secure development practices) and ISO/IEC 42001 (AI management system) are the frameworks auditors will ask about.
- Our audit log, versioned workflows, approvals and evidence records are the raw material. ⬜ Map them when a real customer needs it; don't build for it now.

## Roadmap
- **Done:**
  - sandbox change: per-session agent sandbox with an egress allowlist; `make verify` sandboxed; Level 3 template; Readiness station; SBOM + provenance; threat model.
  - readiness + preflight change: provider `check()`; readiness page and header pill; order/iteration preflight; archive; 20 app ports; OpenSSF Scorecard; Dependabot; least-privilege CI.
  - upgrade-safety change: CI builds all images and tests the engine on the image's Python; the sandbox verifies the golden path offline; a dry-run end-to-end browser smoke test; engine on the next Python (advisory); a weekly run; app Python decoupled from the factory's; `make backup` / `restore` / `upgrade`.
  - spec-driven change: numbered requirements; requirement → scenario → test → live-result traceability with a Level 3 signal; optional spec review gate (off by default); spec diff on feedback; bring your own spec (ADR-0017).
  - least-privilege change: no root in the factory's containers (one-shot offline init step; factory, auth and web without capabilities); runtime proof in CI; privileged dind documented as an accepted risk (ADR-0018).
  - outcomes change: status transition log; Outcomes page with deliveries, lead time, change failure rate, recovery, autonomy, cost per delivered change, Level 3 share, requirements verified live, where the time goes and who the factory is waiting on (ADR-0019).
  - spec review change: built-in review station after verify/readiness; the reviewer reports on every requirement and the engine judges (partial/missing or blocker/major findings go back to build; incomplete reviews are held); review evidence per requirement in the traceability view (ADR-0020).
- **Next:**
  - evaluation harness gating workflow publishes;
  - model token behind an auth proxy.
- **Then:** phase 5, generic `registry/oci` + `deploy/helm` providers (any dev cluster, ingress hosts instead of NodePorts), then ECR + Argo CD.
- **Later:**
  - intake from GitHub issues;
  - staged rollout and rollback;
  - image signing;
  - provider fallback;
  - parallel tasks.

## Candidates to consider (from Warp's Cloud Software Factory)
Highest value only; each fits data or hooks we already have.
1. ✅ **"Waiting on whom" metric** (done, Outcomes page): split every run's time into agent-working vs waiting-for-a-human, and show the factory as a board by state. Warp's own dashboard shows humans, not agents, are the bottleneck. → part of the Outcomes page.
2. ✅ **Spec-driven development** (done, ADR-0017):
   - an optional spec review gate after design, with approve, request changes or edit (per workflow: always, first iteration or off; **off by default**);
   - numbered requirements (`docs/requirements.yaml`) plus the product spec and technical design (`docs/spec.md`, `docs/design.md`);
   - requirement → scenario → test → acceptance traceability, with a readiness signal;
   - feedback updates the spec first, and the gate shows the spec diff;
   - an order can bring its own spec.

   Warp calls spec review the highest-leverage checkpoint.
3. **Self-improving loop**: after feedback or a fix loop, an agent proposes changes to the relevant skill or learnings as a workflow draft that an admin approves. Corrections then become better future runs.
4. ✅ **Review station** (done, ADR-0020): a reviewer agent checks the diff against spec and design before packaging and records its findings as evidence. Part of the default template.

## Sources
- Factory.ai, *Software Factory: An Autonomy Maturity Model for the enterprise* (white paper, 2026)
- [DORA: State of AI-assisted Software Development 2025](https://dora.dev/dora-report-2025/) · [2025 DORA AI Capabilities Model](https://services.google.com/fh/files/misc/2025_dora_ai_capabilities_model.pdf)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [SLSA (OpenSSF)](https://openssf.org/projects/slsa/)
- [Warp: The Cloud Software Factory Build Guide](https://www.warp.dev/blog/software-factory-build-guide) · [build.warp.dev](https://build.warp.dev)
