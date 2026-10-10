# ADR-0034: Tool stations: security and quality tools as visible, configurable stations

**Status:** accepted · 2026-10-10 · relates to [ADR-0027](0027-change-risk-policy.md) (change risk), [ADR-0033](0033-repo-changes-as-pull-requests.md) (PRs)

## Context
Scanners ran hidden inside other stations (gitleaks in the Quality gate, Trivy in Build).
Nobody could see which tool found what, set a policy per tool, or add a tool without
engine code. The master plan's phase 2 asks for one tool-station contract.

## Decision
1. **One contract** (`tools.py`): a `tool` check station names a tool from the catalogue
   and a policy, `fail_on` (`critical` | `high` | `medium` | `low` | `none`; default `high`).
   - The tool runs in dind against the worktree, mounted read-only.
   - Its output is parsed into normalised findings (tool, rule, severity, title, file, line).
     Output that isn't a report means the tool didn't run, and the change is held.
   - Findings at or above `fail_on` fail the station and are routed to its `on_fail`
     (the developer); lower ones are warnings.
   - Only the normalised findings are kept, in `artifacts/<change>/tools/<station>.json`.
     Raw output never reaches the event log or the evidence: a secret scanner's report can
     hold the secret.
2. **Catalogue as data:** Semgrep (SAST, SARIF), OSV-Scanner (dependencies), gitleaks
   (secrets), Trivy config (Dockerfile and Kubernetes). `tools:` in the config overrides an
   entry (e.g. pins a reviewed digest) or adds one that uses a known parser.
3. **Scope:** on a change to an existing repo, only findings in the files the change
   touched count; the rest are reported as outside and never block (a PR isn't held
   responsible for problems it didn't make).
4. **Policies in the templates:**
   - **default** (new apps): sast, dependencies, iac after the Quality gate, failing on
     **critical** only until we have seen what each tool reports on generated apps.
   - **repo-change:** secrets, sast, dependencies, iac after the repo's tests, failing on
     **high** in the changed files.
   - **assess:** sast, dependencies, iac with `fail_on: none`; findings go into the report.
5. **Visible:** each tool is its own station (labelled by tool), a Tool findings panel on
   every change (`GET /api/changes/{id}/tools`), tool lines in the pull request description,
   and a Tools section in the assessment report.
6. In dry-run tools are simulated; the fake executor answers like a clean scan.

## Consequences
- Security evidence is per tool and per policy, and adding a scanner is configuration.
- Tool images are pulled into dind on first use (Semgrep's is large); moving tags until a
  digest is reviewed and pinned under `tools:`, like the existing scanners.
- Semgrep's `p/default` rules are fetched from the Semgrep registry at run time.
- The existing scans in the Quality gate (gitleaks) and Build (Trivy image) stay; folding
  them into tool stations is a later cleanup.
- Workflows pick the stations up when re-drafted from their templates.
