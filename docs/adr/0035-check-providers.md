# ADR-0035: Scan stations are named after their check; the provider is a choice

**Status:** accepted · 2026-10-11 · refines [ADR-0034](0034-tool-stations.md) (tool stations)

## Context
ADR-0034 made each scanner a station named after the tool (Semgrep, OSV-Scanner,
gitleaks, Trivy). Teams differ in what they have: some pay for Snyk or use GitHub's
code scanning and Dependabot, some have nothing beyond free tools, and some environments
can't pull the tool images. A workflow shouldn't change when the tool does, and a missing
tool shouldn't stop a change when something else can answer.

## Decision
1. **A scan station is named after its check:** `handler: scan` with `check:` `secrets`
   (Secret scan), `code` (Code scan), `dependencies` (Dependency scan) or `config`
   (Config scan). Station ids in the templates: `secret-scan`, `code-scan`,
   `dependency-scan`, `config-scan`.
2. **Providers** (`checks.py`), chosen per check once for the factory under
   `checks.providers` in the config, or per station with `provider:`:
   - `local` (default): the open-source tool from the catalogue (ADR-0034), in dind;
     free and deterministic. `checks.local_tools` picks a different catalogue entry.
   - `snyk`: Snyk Code, Open Source and IaC (no secrets check), SARIF output.
     `SNYK_TOKEN` reaches the container by name (`docker run -e SNYK_TOKEN`).
   - `github`: the repository's open code scanning, Dependabot and secret scanning
     alerts, read with `gh api`. `--jq` keeps only rule, severity, title, file and line,
     so a secret-scanning alert's value never leaves gh. Only for products with a GitHub
     repository. The alerts describe the default branch, not the change, so on a pull
     request they are reported **for information** and never block it (GitHub scans
     the PR itself once it's open).
   - `agent`: the observe-only `checker` agent named by the station. It is judged by the
     engine like a tool: a finding must cite a file that exists, and severity is capped
     at `high` because a critical needs a tool's evidence. Reports carry
     `confidence: agent`.
3. **Fallback:** when the provider can't answer (no token, not a repository, the feature
   is off, the tool didn't run), the station tries the agent (`checks.fallback: agent`,
   the default) and records why in `fallback_from`. With `fallback: none` it holds the
   change, as ADR-0034 did.
4. **One judgement:** every provider's findings are normalised and judged by the same
   `fail_on` policy and scope rules (`tools.judge`). The report names the check, the
   provider and the tool or service that answered (`via`).
5. **Credentials** are references (`checks.snyk_token_ref`, `checks.github_token_ref`,
   default `env://SNYK_TOKEN` and `env://REPO_GITHUB_TOKEN`), resolved for the step and
   passed through the environment, never the command line. Raw output is never kept.
6. **Compatibility:** stations saved with `handler: tool` and `tool:` read as `scan`
   stations of that tool's check and keep running that tool. Evidence stays in
   `artifacts/<change>/tools/` and events keep the `tool` key.

## Consequences
- Switching a team to Snyk or GitHub, or to the agent, is a config change; workflows
  don't change.
- A change no longer stops because a scanner image can't be pulled, but its report shows
  that an agent answered instead, at lower confidence.
- GitHub's alerts can't gate a pull request; teams that want that rely on GitHub's own
  required checks on the PR.
- Snyk needs network access from dind to Snyk, and its dependency scan works best with a
  lockfile. Other services (SonarQube, Wiz …) follow the same shape: catalogue entries
  with a parser, a credential reference and a provider name.
- The workflow editor sets a scan station's check, provider and policy.
