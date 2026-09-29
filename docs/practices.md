# Best practices we measure the factory against

Status: living reference · last reviewed 2026-09-29 (after the sandbox + Level 3 change).
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

**Processes:** ✅ signal→build→verify→deploy→accept→feedback loop with human gates ·
🟡 model tiers per agent (no provider fallback) · ⬜ intake from issues/Jira ·
⬜ staged rollout and rollback · ⬜ parallel / DAG decomposition · ⬜ incident response.

**Headline metric:** share of delivered apps at Level 3+. ✅ The Readiness station scores every run (20 signals + secret scan) and fails the run below Level 3. ⬜ The Outcomes page will aggregate it.

## 2. DORA: four keys and the 2025 AI Capabilities Model
- **Measure outcomes, not activity:**
  - lead time (order → delivered);
  - change failure rate (runs HELD or failed after deploy);
  - time to restore;
  - deploy frequency.
  - Add **autonomy ratio** (delivered with no questions and no fix loops) and **cost per delivered change**.
  - ⬜ → Outcomes page (built from existing run data).
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
- **Factory repo:** ⬜ run OpenSSF Scorecard in CI (branch protection, pinned actions, token permissions, dependency updates).

## 5. Agent engineering
- ✅ Deterministic orchestration and LLM work only inside stations ("workflows before agents").
- ✅ Smallest tool set per role; skills preloaded only where measured.
- ⬜ **Evaluation harness:** a fixed set of orders run per workflow version. Track pass rate, cost, loops and lead time. Gate publishing a workflow version on no regression.
- ⬜ Trace every agent turn (OpenTelemetry GenAI conventions) for debugging and cost attribution.

## 6. Enterprise governance (when a company adopts it)
- NIST SSDF (secure development practices) and ISO/IEC 42001 (AI management system) are the frameworks auditors will ask about.
- Our audit log, versioned workflows, approvals and evidence records are the raw material. ⬜ Map them when a real customer needs it; don't build for it now.

## Roadmap
- **Done (sandbox change):**
  - per-session agent sandbox with an egress allowlist;
  - `make verify` sandboxed;
  - Level 3 template;
  - Readiness station;
  - SBOM + provenance;
  - threat model.
- **Next:**
  - Outcomes page (DORA + autonomy ratio + cost per change + share of apps at Level 3);
  - evaluation harness gating workflow publishes;
  - OpenSSF Scorecard in CI;
  - model token behind an auth proxy.
- **Later:**
  - intake from GitHub issues;
  - staged rollout and rollback with remote providers;
  - image signing;
  - provider fallback;
  - parallel tasks.

## Sources
- Factory.ai, *Software Factory: An Autonomy Maturity Model for the enterprise* (white paper, 2026)
- [DORA: State of AI-assisted Software Development 2025](https://dora.dev/dora-report-2025/) · [2025 DORA AI Capabilities Model](https://services.google.com/fh/files/misc/2025_dora_ai_capabilities_model.pdf)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [SLSA (OpenSSF)](https://openssf.org/projects/slsa/)
