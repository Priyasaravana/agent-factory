# Plan: secret store (ADR-0038)

Status: agreed with Saravana 2026-10-11; deferred until after J5 ([roadmap](../roadmap.md)). ADR-0038 is
reserved for it and written when the work starts.

## Why
Secrets live in a plaintext `.env` and reach containers as env vars (visible in `docker inspect`); no audit, no
rotation, no team sharing. Config already holds references only (`env://SNYK_TOKEN`), so a store is a new scheme.

## Decisions
- **Pluggable resolver** by reference scheme, same pattern as the model gateway: `env://` (today), `file://`
  (mounted secret files), `vault://path#key`, `aws-sm://`, `gcp-sm://`, `azure-kv://`.
- **Local store: OpenBao** (MPL-2.0 Vault fork; Vault itself is BSL) as an optional compose profile `secrets`.
  The same Vault-API provider serves a remote HashiCorp Vault / OpenBao.
- **Remote providers, all four first:** Vault/OpenBao, AWS Secrets Manager (IAM role), GCP Secret Manager
  (workload identity), Azure Key Vault (managed identity).
- Rules: engine resolves just in time, never caches or stores values; every value registered with the redactor;
  sandboxes never get store access; prefer short-lived credentials (LiteLLM virtual keys, GitHub App tokens later).

## Honest limits
- Bootstrap: one credential to reach the store remains (OpenBao unseal/token locally); cloud runtime identity removes
  it when deployed. `.env` shrinks to that credential.
- The engine still holds values in memory at use; Docker-socket access on the host still reaches them.
- Third-party containers (Postgres, LiteLLM) read secrets at start: Postgres via `*_FILE`; LiteLLM: verify file or
  secret-manager support before relying on it (else an entrypoint fetch).

## Size
Resolver + env/file + OpenBao profile + Vault provider ~2 days; AWS, GCP, Azure ~0.5 day each (~3.5 days total).
Preflight readiness row "Secrets" (store reachable, every configured ref resolves) included.
