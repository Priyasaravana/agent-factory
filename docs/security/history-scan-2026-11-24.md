# Secrets scan of the whole history (2026-11-24)

Phase A item 5 ([roadmap](../planning/roadmap.md), "Next, in order"). Done before the repository is promoted
for the v0.1.0 release: a secret committed once stays in the history even after the file changes.

## What was scanned
- A full (not shallow) bare clone of `Priyasaravana/agent-factory`, every branch on the remote:
  421 commits, about 14.7 MB of changes.
- Tool: gitleaks 8.28.0, default rules, `git` mode with `--log-opts=--all`, output redacted. The repo has no
  `.gitleaks.toml`, so nothing was excluded.

## Result: no real secret, nothing to rotate
7 findings, all of them test values or comments, all `generic-api-key`:

| File | Commits | What it is |
|---|---|---|
| `engine/tests/test_base_images.py` (lines 93, 100) | `43d5af84`, `66054c02` | A risk key next to the literal `"CVE-2026-31789"` |
| `engine/tests/test_repo_changes.py` (line 20) | `9e808ef6`, `47dbbe6e` | `TOKEN = "ghp_never_in_logs_1234567890"`, a fake token the test greps logs for |
| `.env.example` (line 9) | `f87a3fde` | The comment `# or an API key:` |

No token, key or password that has ever been valid was found, so no credential is rotated.

## Not covered
- Git LFS objects (the clone skipped LFS smudge) and anything outside git: issue and PR comments, CI logs, release
  assets, and the local `.env` and `.agent-factory/config.yaml` on the Mac, which are never committed.
- A rule set other than gitleaks' defaults. A CI secret scan on every push is part of S-08 (the factory's own CI
  has no security gates), which also decides whether to allowlist the three test values above.

## How to repeat it
```
git clone --bare --no-tags https://github.com/Priyasaravana/agent-factory af-history.git
gitleaks git af-history.git --log-opts="--all" --redact -f json -r history.json
```
