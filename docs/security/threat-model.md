# Threat model: agents in the factory

Status: living document · last reviewed 2026-09-30.
It is structured on the [OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/).

## Assets and trust boundaries
- **Credentials:**
  - the model token (sandbox + engine);
  - `GITHUB_TOKEN` and other secret references (engine only, resolved just in time);
  - the dind TLS keys and kubeconfig (engine only).
- **Integrity of delivered software:**
  - the product repos;
  - the images;
  - the evidence records (events, readiness scorecards, SBOM, provenance), sealed
    per run with SHA-256 hashes (ADR-0023).
- **Independence of verification:** the holdout scenarios, which the builder
  must never see.
- **Boundaries:**
  - browser → gateway (auth) → engine;
  - engine → sandbox (untrusted);
  - sandbox → egress allowlist → internet;
  - engine → dind (privileged).

Untrusted inputs: requirements and feedback text, imported skills and templates,
package registries, and anything an agent writes (code, tests, charts).

## Privileges of the factory's own containers (ADR-0018)

| Container | Runs as | Capabilities | Notes |
|---|---|---|---|
| factory (API + engine) | uid 10001 | none (`cap_drop: ALL`, `no-new-privileges`) | Refuses to start as root. `docker compose exec` opens a non-root shell. |
| factory-init | root, once per `up` | CHOWN, FOWNER, DAC_OVERRIDE only; no network | Hands dind's client certs and `/data` to uid 10001, then exits. |
| auth | uid 10002 | none | Own database; never sees the factory's `.env`. |
| web (gateway) | uid 101 (unprivileged nginx) | none | The only published UI port, bound to 127.0.0.1. |
| agent sandboxes | uid 10001 | none; read-only image | ADR-0014. |
| **dind** | **root, privileged** | all | **Accepted risk**, see below. |

**Accepted risk: privileged dind.** Running a kind cluster inside Docker needs a
privileged container. A container escape from dind would reach the Docker
Desktop VM (or the Linux host).
- **Mitigations:**
  - dind publishes only the app ports, and only on 127.0.0.1;
  - its API needs mutual TLS, and only the factory holds the client key;
  - agents never reach it: sandboxes are on an internal network with no route to `docker:2376`, which `make sandbox-check` proves;
  - nothing untrusted runs in dind with privileges: sandboxes and app pods run as non-root.
- **Removal path:**
  - phase 5 delivers to a real cluster (`deploy/helm`), so dind is needed only for local development;
  - rootless dind or Sysbox is an option for teams that need the local cluster on shared hosts.

## Risks and controls

| # | Risk | How it applies here | Controls | Open |
|---|---|---|---|---|
| 1 | Goal hijack / prompt injection | Requirements, feedback, skill text or a package README tell an agent to exfiltrate or skip checks | Sandbox has nothing to exfiltrate but the model token and the worktree, and egress is allowlisted. Deterministic stations re-check everything (verify, readiness, scan, acceptance); an agent's claim is never evidence | Mark requirements/feedback as untrusted in prompts; model token behind an auth proxy |
| 2 | Tool misuse | Bash used to delete, push, or reach the cluster | Sandbox: read-only image, worktree-only writes, no Docker/cluster. Hooks deny `git push`, destructive commands and credential reads. Observe-only roles limited to read commands | — |
| 3 | Identity & privilege abuse | Agent acts as a factory user or admin; a compromised service escalates to root | Agents cannot reach the engine or gateway (internal network). Identity only via the auth gateway (ADR-0012). Changing workflows and skills is admin-only; steering an order (feedback, answers, resume, cancel, spec decisions, archive, transcripts) is for its creator or an admin, pinned per action by `test_access.py`. No factory service runs as root or holds capabilities (ADR-0018) | Privileged dind (accepted, see above) |
| 4 | Supply chain | Malicious skill, template or package | Skills and templates pinned to commits and reviewed before install (scripts need consent). Lockfiles; Trivy scan; SBOM + provenance per image; Dependabot in generated repos | Image signing (with the registry provider); digest-pinned scanner images |
| 5 | Unexpected code execution | Agent-written tests or build scripts run with engine privileges | `make verify` runs in a sandbox (ADR-0014). Docker builds run in dind with no secrets | — |
| 6 | Memory & context poisoning | Earlier iterations, learnings or docs steer later runs | Learnings are human-approved; context docs are versioned with the workflow; iteration history is summarised from engine records. Suggested learnings (ADR-0021) come from an observe-only retro, are vetted (no check-weakening lessons, evidence required), and reach agents only after an admin accepts them into a draft that is then published as a new version | Review learnings in the UI before publishing (workflow changes are admin-only: ADR-0012 middleware, pinned per action by `test_access.py`) |
| 7 | Insecure inter-agent communication | One agent's output misleads another | Agents never talk directly: the engine passes evidence only. The builder gets observed behaviour, never holdout text | — |
| 8 | Cascading failures | Fix loops burn budget or corrupt the repo; a run starts against a broken dependency | Attempt and loop budgets, wall-clock limit, usage-limit pause, HOLD on missing evidence, fresh worktree per run. Preflight refuses orders and iterations while a guarding check fails (ADR-0015) | — |
| 9 | Human-agent trust exploitation | A plausible summary hides a failure | Every decision carries evidence (commands, outputs, scorecards). Each stopped run's evidence is sealed with SHA-256 hashes and re-verified on every view; changed, missing or added files are shown (ADR-0023). Publishing and merging follow policy; the feedback gate needs a person | Approval gate before shared-environment deploys (remote providers); signed manifests (someone with write access to both the data folder and the database could re-seal) |
| 10 | Rogue agents | An agent keeps working outside its task, or keeps trying forbidden actions | Sandboxes are per session and removed afterwards; runs cancel their containers; archiving removes the app and any leftover sandboxes, with an audit event; `max_turns` per spec. Every guardrail denial is a recorded decision, every agent call has a record and a redacted transcript, and Outcomes counts denials (ADR-0022) | Per-run egress log in the run view; OpenTelemetry export |

## How to verify
- `make sandbox-check` runs the isolation probe inside a real sandbox.
- A run's **Agent calls** panel lists every agent call and every guardrail
  denial, with a redacted transcript per call (ADR-0022).
- A run's **Evidence** panel says whether its sealed evidence is intact; a
  downloaded bundle checks with `sha256sum -c SHA256SUMS` (ADR-0023).
- `make privilege-check` proves that factory, auth and web run as non-root with
  no capabilities and `no-new-privileges`, that factory-init succeeded, and that
  only dind is privileged. CI e2e runs it on every change.
- `make test` covers:
  - hooks (`test_hooks.py`);
  - secret redaction (`test_secret_refs.py`);
  - the sandbox spec and egress proxy (`test_sandbox.py`);
  - readiness and SBOM (`test_readiness.py`);
  - preflight, archive and ports (`test_preflight.py`);
  - least privilege of the shipped compose file and images (`test_least_privilege.py`, `test_runtime_user.py`).
- CI runs OpenSSF Scorecard on the repo weekly (`.github/workflows/scorecard.yml`).
- `docker compose exec dind docker logs factory-egress` shows every allowed and
  denied connection.
