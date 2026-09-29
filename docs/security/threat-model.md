# Threat model: agents in the factory

Status: living document · last reviewed 2026-09-29.
It is structured on the [OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/).

## Assets and trust boundaries
- **Credentials:**
  - the model token (sandbox + engine);
  - `GITHUB_TOKEN` and other secret references (engine only, resolved just in time);
  - the dind TLS keys and kubeconfig (engine only).
- **Integrity of delivered software:**
  - the product repos;
  - the images;
  - the evidence records (events, readiness scorecards, SBOM, provenance).
- **Independence of verification:** the holdout scenarios, which the builder
  must never see.
- **Boundaries:**
  - browser → gateway (auth) → engine;
  - engine → sandbox (untrusted);
  - sandbox → egress allowlist → internet;
  - engine → dind (privileged).

Untrusted inputs: requirements and feedback text, imported skills and templates,
package registries, and anything an agent writes (code, tests, charts).

## Risks and controls

| # | Risk | How it applies here | Controls | Open |
|---|---|---|---|---|
| 1 | Goal hijack / prompt injection | Requirements, feedback, skill text or a package README tell an agent to exfiltrate or skip checks | Sandbox has nothing to exfiltrate but the model token and the worktree, and egress is allowlisted. Deterministic stations re-check everything (verify, readiness, scan, acceptance); an agent's claim is never evidence | Mark requirements/feedback as untrusted in prompts; model token behind an auth proxy |
| 2 | Tool misuse | Bash used to delete, push, or reach the cluster | Sandbox: read-only image, worktree-only writes, no Docker/cluster. Hooks deny `git push`, destructive commands and credential reads. Observe-only roles limited to read commands | — |
| 3 | Identity & privilege abuse | Agent acts as a factory user or admin | Agents cannot reach the engine or gateway (internal network). Identity only via the auth gateway (ADR-0012) | — |
| 4 | Supply chain | Malicious skill, template or package | Skills and templates pinned to commits and reviewed before install (scripts need consent). Lockfiles; Trivy scan; SBOM + provenance per image; Dependabot in generated repos | Image signing (with the registry provider); digest-pinned scanner images |
| 5 | Unexpected code execution | Agent-written tests or build scripts run with engine privileges | `make verify` runs in a sandbox (ADR-0014). Docker builds run in dind with no secrets | — |
| 6 | Memory & context poisoning | Earlier iterations, learnings or docs steer later runs | Learnings are human-approved; context docs are versioned with the workflow; iteration history is summarised from engine records | Review learnings in the UI before publishing a workflow |
| 7 | Insecure inter-agent communication | One agent's output misleads another | Agents never talk directly: the engine passes evidence only. The builder gets observed behaviour, never holdout text | — |
| 8 | Cascading failures | Fix loops burn budget or corrupt the repo | Attempt and loop budgets, wall-clock limit, usage-limit pause, HOLD on missing evidence, fresh worktree per run | — |
| 9 | Human-agent trust exploitation | A plausible summary hides a failure | Every decision carries evidence (commands, outputs, scorecards). Publishing and merging follow policy; the feedback gate needs a person | Approval gate before shared-environment deploys (remote providers) |
| 10 | Rogue agents | An agent keeps working outside its task | Sandboxes are per session and removed afterwards; runs cancel their containers; `max_turns` per spec | Per-run egress log in the run view |

## How to verify
- `make sandbox-check` runs the isolation probe inside a real sandbox.
- `make test` covers:
  - hooks (`test_hooks.py`);
  - secret redaction (`test_secret_refs.py`);
  - the sandbox spec and egress proxy (`test_sandbox.py`);
  - readiness and SBOM (`test_readiness.py`).
- `docker compose exec dind docker logs factory-egress` shows every allowed and
  denied connection.
