# Security assessment: Agent Software Factory

Date: 2026-11-15 · Commit assessed: `main` @ `43b36d3`

## Summary

The factory's own containers and the agent sandbox are well built: non-root, no capabilities, no host
Docker socket, an internal sandbox network with an allowlist proxy, an auth gateway in its own container,
hashed sessions and tokens, pinned scanners, and tests that pin most of this.

The gaps are at the edges the sandbox doesn't cover. **Agent-written code also runs in three places
outside the sandbox: `docker build`, the image start check, and the deployed app pods.** All three sit on
dind's default network, which has open internet access and can reach the engine's API, and the engine
accepts identity headers from anyone who can reach it. That combination is the main thing to fix.

| Severity | Count | Findings |
|---|---|---|
| High | 4 | S-01 to S-04 |
| Medium | 7 | S-05 to S-11 |
| Low | 7 | S-12 to S-18 |

Severity is rated for a shared or team instance, which is where the roadmap is heading (J3 team repos,
members, repository sources). On today's single-user laptop, S-01 to S-03 still apply because the attacker
is not a person on the network: it is code an agent wrote or a package it installed.

## Scope and method

- **Read:** engine, auth service, web gateway config, sandbox and egress proxy, compose file, cluster
  bootstrap, Dockerfiles, CI workflows, both stack packs' charts, the threat model (last reviewed
  2026-09-30), ADRs 0012, 0014, 0018, and `plans/secret-store.md`.
- **Scanners run:** gitleaks (30 commits, the clone's depth), bandit on `engine/src` and `auth/src`,
  pip-audit on both lockfiles, `npm audit` on `web` and the Next.js template.
- **Not done:** nothing was run live. No Docker here, so the network findings (S-01, S-02) come from
  reading the code and compose file, not from a probe. Each has a proposed test that would prove or
  disprove it. Full git history, a Trivy scan of the built images, and the deployed apps' own security
  were not assessed.

## What is already solid (keep)

- Least privilege on factory, auth and web containers, checked by `make privilege-check` and tests.
- Sandbox: read-only image, worktree-only writes, internal network, engine secrets never forwarded
  (`sandbox/spec.py` `_NEVER`), secrets kept out of argv.
- Auth: scrypt, constant-time compare, timing-safe unknown user, only hashes of sessions and tokens
  stored, last-admin protection, forced password change, audit log.
- nginx overwrites `X-Auth-*` from the browser; the webhook path strips them and checks an HMAC with
  `compare_digest` and a body cap.
- Repository sources re-checked at every use; git tokens through the environment in `workspace.py`.
- Trivy and syft pinned by digest; Dependabot on all ecosystems; OpenSSF Scorecard weekly.
- No `dangerouslySetInnerHTML` in the web UI; React 19 blocks `javascript:` URLs.

## Findings

### High

**S-01 · The engine trusts identity headers from any caller on its network**
- **Status: fixed on `fix/s01-gateway-proof` (2026-11-16)**, the shared secret (ADR-0012, "the gateway's
  proof"); the probe is `scripts/gateway-check.sh`, in `make sandbox-check` and CI `e2e`. The separate
  network stays with S-02.
- Evidence: `engine/src/agent_factory/identity.py:68` reads `X-Auth-User` / `X-Auth-Role` with no proof
  they came from the gateway. The engine listens on `0.0.0.0:8000` on the compose network, which dind is
  also on.
- Impact: anything that can open a connection to `factory:8000` and send `X-Auth-Role: admin` is an
  admin: it can publish workflows, install skills, add repository sources, approve risky changes. With
  S-02 that includes agent-written code.
- Fix: a shared secret between nginx and the engine (a header nginx adds from a file, the engine
  compares in constant time and refuses without it), generated at first start. Better later: put the
  engine on its own compose network that only `web` joins, with dind reached over a second network.
- Test: from a container on dind's default bridge, `curl -H 'X-Auth-User: x' -H 'X-Auth-Role: admin'
  http://<factory-ip>:8000/api/products` must fail. Add it to `make sandbox-check`.
- Effort: ~0.5 day.

**S-02 · Builds, the start check and app pods run agent-written code outside the sandbox network**
- **Status: partly fixed on `fix/sandbox-paths` (2026-11-16, ADR-0048).** Done: fix 4 (the rule in dind,
  proved in CI `e2e` by `scripts/sandbox-paths-probe.sh`, which lifts it for a moment and shows the path
  open), fix 2 (the start check), fix 3 (the NetworkPolicy, applied by the engine; enforcement not tested
  live). **Open: fix 1.** BuildKit in dind refuses a custom build network, so a `RUN` step still has the
  open internet; it can no longer reach the engine. See ADR-0048, "Not decided here".
- Evidence: `engine/stations.py:1209` and `:1211` run `docker build --pull` with no `--network`;
  `:1345` runs the built image with `docker run -d --rm -p` and no hardening flags; neither chart has a
  NetworkPolicy and app namespaces carry no Pod Security labels (`providers/helm.py:270`).
- Impact: a Dockerfile `RUN` step, an npm `postinstall` script inside the build, or the deployed app
  itself has unrestricted internet (the egress allowlist does not apply) and a route to the engine, the
  auth service and the model gateway. This is the path that makes S-01 reachable, and it is an
  exfiltration route for anything in the worktree. The threat model's row 5 ("Docker builds run in dind
  with no secrets") is true but doesn't cover the network.
- Fix, in order of value:
  1. Build on a network whose only exit is the egress proxy (BuildKit `--network` plus proxy build
     args), or `--network none` after a sandboxed dependency fetch.
  2. Start check: `--cap-drop ALL --security-opt no-new-privileges --read-only --memory --pids-limit`
     on the internal network.
  3. Default-deny egress NetworkPolicy in each app namespace (kindnet in kind v0.27 enforces it),
     allowing DNS and what the pack declares.
  4. An iptables rule in dind (`DOCKER-USER`) dropping traffic from container bridges to the compose
     subnet, so the block doesn't depend on every caller passing the right flag.
- Effort: ~2 days.

**S-03 · The Claude subscription token is readable inside every agent sandbox**
- **Status: the refusal is on `fix/sandbox-paths` (2026-11-16):** `factory.shared: true` starts live only
  with the gateway in api_key mode. The auth-injecting proxy and the secret-in-tree check stay open.
- Evidence: `sandbox/spec.py:34` forwards `CLAUDE_CODE_OAUTH_TOKEN`; in gateway `subscription` mode
  (`gateway.py:95-107`) it is still forwarded. Hooks block the variable's *name* in commands
  (`agents/hooks.py`), which `/proc/self/environ` or any script sidesteps.
- Impact: a long-lived personal credential sits beside code and packages the agent installs. Exits
  exist on the allowlist: `api.anthropic.com` (upload with another account's key), `registry.npmjs.org`
  (publish), or a commit into the product repo that the factory then pushes.
- Fix: for anything beyond your own laptop, gateway `api_key` mode only (the sandbox then holds a
  per-call virtual key), and refuse to start a shared instance in `direct` or `subscription` mode. For
  subscription use, the open item already in the threat model: an auth-injecting proxy so the token
  stays in the engine. Add a check that fails publish if a known secret value appears in the tree.
- Effort: ~1 day for the proxy; the refusal is ~1 hour.

**S-04 · The factory session cookie is sent to generated apps on `localhost`**
- **Status: fixed on `fix/sandbox-paths` (2026-11-16, ADR-0048):** apps on `127.0.0.1`, the UI kept on
  `localhost`, and an `Origin` check at the gateway (`scripts/gateway-check.sh` in CI `e2e`). The cookie
  keeps its name: `__Host-` needs `Secure`, which plain-http localhost can't rely on in every browser.
- Evidence: the UI is `http://localhost:8080`, apps are `http://localhost:8081-8100`
  (`docker-compose.yml:24`, `config.py:32`). Cookies are scoped by host, not port, and the cookie is
  host-only for `localhost` (`auth/app.py:203-211`).
- Impact: every request your browser makes to a generated app carries `factory_session`. An app (or a
  dependency in it) that logs or forwards request headers has your admin session. The app's pages are
  also same-site with the factory, so `SameSite=Lax` gives no CSRF protection against them.
- Fix: serve the UI and apps on different sites. Simplest: make the ingress host
  (`<app>.localtest.me:8180`) the only app URL shown, and name the cookie `__Host-factory_session`.
  Add an `Origin` check on state-changing `/api` and `/auth` requests.
- Effort: ~0.5 day.

### Medium

**S-05 · Pod privileges are checked by a regex, not enforced by the cluster**
- Evidence: `risk.py:248` matches literal strings such as `privileged: true`. `hostPath`, `hostPID`,
  added capabilities, or a value templated through `values.yaml` pass. A privileged pod on a kind node
  is root on a privileged dind.
- Fix: label app namespaces `pod-security.kubernetes.io/enforce=restricted` when the provider creates
  them; keep the regex as the early warning. ~0.5 day. Both charts already set
  `runAsNonRoot` and `allowPrivilegeEscalation: false`; neither sets `seccompProfile: RuntimeDefault`
  or `capabilities.drop: [ALL]`, which `restricted` requires, so add those to both templates first.
- **Status (2026-11-24, `security/s05-pod-security`): fixed, in review.** Both deploy providers label the app's
  namespace for Pod Security admission before every release (`providers/pod_security.py`): `restricted` when every
  pod the chart renders meets it (the packs' charts do from fastapi-service 1.2.1 and nextjs-web 1.6.1, postgres
  included as uid 70), `baseline` for a chart made before them, so delivered products keep deploying with host
  namespaces, host paths, privileged pods and added capabilities refused; a pod below baseline is refused before
  anything is installed. `warn` and `audit` are always `restricted`. A namespace at `restricted` is never lowered by
  a chart, and a change's own namespace counts production's level. Apps already running on the local cluster get
  `baseline` at start where their namespace enforces nothing. The regex stays as the early warning. **Not run
  live:** a real kind deploy of both packs under `restricted` (postgres as uid 70 on kind's local-path volumes
  especially).

**S-06 · Secrets in a plaintext `.env`, handed whole to the engine**
- Known and planned (ADR-0038, deferred until after J5). Raised here because J3 adds per-org repo
  tokens and webhook secrets, and `GITHUB_TOKEN` has Administration on the publishing org.
- Fix: pull forward the smallest slice: `file://` references from mounted secret files (already
  supported by the resolver) as the documented default, and short-lived GitHub App installation tokens
  in place of long-lived fine-grained ones. ~1 day; the store itself can stay after J5.
- **Status (2026-11-25, `security/s06-secret-files`): fixed, in review.** Tokens are files by default: `env://NAME`
  reads the file `NAME_FILE` names (Docker's convention), `.env.example` ships `*_FILE=/run/secrets/…` for every
  token, compose mounts the gitignored `.secrets/` read-only at `/run/secrets` (never in a build, `.dockerignore`),
  and `make secrets-files` moves an install's existing values out of `.env` once (old file kept 0600 in `backups/`;
  an existing different file is never overwritten). `github-app://<app>/<installation>[#repo,…]` mints a GitHub App
  installation token per use (an hour; reused until ten minutes before expiry, in memory only), signed with the
  app's private key from a mounted file, over https with no redirects, optionally narrowed to named repositories.
  Tests: `tests/test_secret_files.py`. Not covered: the model credentials (`CLAUDE_CODE_OAUTH_TOKEN`,
  `ANTHROPIC_API_KEY`) and `LITELLM_MASTER_KEY` are still read from the environment by the agent runner and the
  gateway; the secret store (ADR-0038) stays after v0.1.0.

**S-07 · Images are not built from locked or verified inputs**
- Evidence: `images/factory/Dockerfile:46` and `images/auth/Dockerfile:10` run `uv pip install .`,
  ignoring `uv.lock`, so the shipped versions are whatever resolves that day and are not the ones
  pip-audit checked. kind, kubectl and helm are downloaded with no checksum (`:25-31`). Base images
  and `uv:0.8` are tags, `docker:27-dind` and `postgres:17-alpine` float, and LiteLLM defaults to
  `main-stable` (`docker-compose.yml:122`).
- Fix: `uv sync --frozen`; `sha256sum -c` on each download; digests on every `FROM` and compose image
  (Dependabot already updates them); a pinned LiteLLM version as the default. Extends the existing
  "Upgradable selections" backlog item. ~1 day.
- **Status (2026-11-25, `security/s07-locked-images`): fixed, in review.** The factory and auth images install
  `uv export --frozen` output with `--require-hashes`, then the project with `--no-deps`, and `uv pip check`; CI's
  `images` job dry-runs each image against its lockfile and fails on any difference. Every `FROM`, `COPY --from` and
  compose image carries its digest next to its tag (LiteLLM's `main-stable` included, so the default no longer
  floats). An install's own LiteLLM is `LITELLM_REPO` plus `LITELLM_DIGEST`: compose's reference always ends in `@`, so a
  tag alone is not an image, and `pin-images.py --check-compose` (before every start, `.env` applied) refuses anything
  but `@sha256:<64 hex>`; kind, kubectl and helm are checked against checksums pinned in the Dockerfile, never fetched beside the
  binary. `scripts/pin-images.py --check` gates it in CI and `tests/test_locked_images.py`; `--resolve` fails when a
  pinned checksum isn't the published one. Dependabot moves the compose digests too (`docker-compose` ecosystem).
  Not covered: the apt packages in the images (Debian's signed repositories, unpinned versions) and the sandbox's
  `uv python install` (uv checks its own downloads).

**S-08 · The factory's own CI has no security gates**
- Evidence: `ci.yml` runs lint, tests and builds. The only scan is Trivy on the *template* images,
  advisory (`continue-on-error`). Actions are on tags (`@v7`), already in the backlog.
- Fix: add gitleaks, `pip-audit`, `npm audit --omit=dev`, CodeQL (or Semgrep) and a blocking Trivy
  scan of the factory, auth, web and sandbox images; pin Actions to SHAs. ~0.5 day.

**S-09 · API tokens never expire and carry the user's full role**
- Evidence: `auth/store.py` `tokens` has no expiry or scope; an admin's token is an admin forever.
- Fix: expiry (default 90 days), an optional read-only scope, and "last used" surfaced with a stale
  warning. ~0.5 day.

**S-10 · No security headers or request limits at the gateway**
- Evidence: `web/nginx.conf` sets only `Cache-Control`. No CSP, `X-Frame-Options` /
  `frame-ancestors`, `X-Content-Type-Options`, `Referrer-Policy`; no `limit_req` on `/auth/login` or
  the public webhook.
- Fix: the header set plus a rate limit on `/auth/login` and `/api/hooks/github`. ~2 hours.

**S-11 · Untrusted text reaches agent prompts unmarked**
- Already in the backlog ("requirements, feedback and align-flow text marked as untrusted"). J3 widens
  it: team repo contents, PR review comments and CI logs now reach agents. The align prompt already
  frames its input well (`design_station.py:462`); the rest should match.
- Fix: one helper that wraps every external text in a labelled block, used by all prompt builders, and
  a test that fails on a prompt builder that doesn't use it. ~1 day. Do it inside J3.

### Low

| ID | Finding | Evidence | Fix |
|---|---|---|---|
| S-12 | Engine defaults to no auth | `settings.py:16` `auth_mode` defaults to `off`; the CLI binds `0.0.0.0` | Default `gateway`; `off` only with an explicit flag and a loopback bind |
| S-13 | Sign-in throttle is per username, in memory | `auth/app.py:27`, `Throttle` | Add a per-IP limit (nginx, with S-10); cap the dict; anyone can lock the admin out for 60 s at a time |
| S-14 | Password and session policy is thin | min length 10 only; scrypt `n=2**14`; 12 h sessions, no idle timeout; `AUTH_COOKIE_SECURE=false` by default; no MFA or SSO | Raise scrypt to `2**17`; idle timeout; refuse `cookie_secure=false` on a non-loopback host; SSO via the header contract when instances are shared |
| S-15 | Auth API docs are public | `auth/app.py:148`: `/auth/docs` and `/auth/openapi.json` need no sign-in | Disable or put behind `auth_request` |
| S-16 | Skill import puts the token in a git URL on the command line | `github.py:51` | Reuse `workspace.token_env` |
| S-17 | Evidence seals and backups are unprotected at rest | Seals are hashes only (ADR-0023, open in the threat model); `scripts/backup.sh` writes `auth.db`, product repos and holdout scenarios unencrypted | Sign manifests with a key outside `/data`; encrypt backups (`age`) |
| S-18 | A personal email address is committed | `.env.example` `GIT_AUTHOR_EMAIL`, in a public repo | Replace with a placeholder |

## Scanner results

| Tool | Result |
|---|---|
| gitleaks | 1 hit, a deliberate test value (`engine/tests/test_repo_changes.py:20`). No real secrets in the last 30 commits. |
| bandit | No high. 12 medium, all reviewed: bind-all inside containers, SQL built from placeholders or fixed table names, `urlopen` on configured URLs. No action. |
| pip-audit | `opentelemetry-api` 1.45.0 (engine) and 1.45.1 (auth): CVE-2026-114603, CVE-2026-126360. Fixed in 1.45.2. Bump both locks. |
| npm audit | 0 in `web` and the Next.js template (production dependencies). |

## Proposed roadmap placement

Ordering and anything that touches the freeze is Saravana's call (planning README rule 4). None of these
needs a new ADR or a new control surface except where noted.

1. **Now, before more J3 live runs (~3.5 days): "Close the paths around the sandbox".**
   S-01, S-02, S-04, and the S-03 refusal. These are the findings where agent-written code reaches
   something it shouldn't, and J3 adds team repos, whose content is less trusted than your own
   requirements.
2. **Inside J3 (~1.5 days):** S-11 (it is J3's own input), S-06's GitHub App tokens if the PR loop is
   going to hold write tokens for several orgs.
3. **Before J4, with "Upgradable selections" (~2.5 days):** S-05, S-07, S-08, S-10, and the
   `opentelemetry-api` bump (do that one today, it is a lockfile change).
4. **With auth and roles for shared instances (after J5):** S-03's proxy, S-09, S-12 to S-15, S-17,
   and the rest of S-06 with ADR-0038.
5. **Any time, minutes each:** S-16, S-18.

Also update `docs/security/threat-model.md`: add the build, start-check and app-pod boundary to "Trust
boundaries", correct row 5, and add the browser boundary between the factory UI and generated apps.

## Progress since the assessment
- **2026-11-19 (PR #186, from its review):** S-14 in part: sessions end after `AUTH_IDLE_MINUTES` (60) unused,
  and sign-in is refused when the session cookie would cross the network in clear (`AUTH_COOKIE_SECURE=false`
  on a host other than this machine; the compose file binds the UI to 127.0.0.1). Still open: scrypt cost,
  MFA or SSO. S-17 in part: `make backup` encrypts with age and refuses a plaintext backup unless
  `BACKUP_PLAINTEXT=1`; backups are owner-readable only. Still open: signing evidence manifests with a key
  outside `/data`. Also from the review: a retired product's final export is deleted after
  `retire.keep_export_days` (90), with the deletion recorded (ADR-0049).
- **2026-11-24 (`security/s08-s10-s11`):** S-08 closed: `.github/workflows/security.yml` scans every commit for
  secrets (gitleaks, pinned and checksum-verified; `.gitleaks.toml` allows three exact test values found by the
  [history scan](history-scan-2026-11-24.md)), audits the engine and auth lockfiles (pip-audit) and the UI's
  runtime packages (npm audit), and runs CodeQL; the `images` job fails on a fixable CRITICAL in the factory,
  auth, web or sandbox image; every action is pinned to a commit. S-10 closed: one header set on every response
  (CSP with no inline scripts, `frame-ancestors 'none'`, nosniff, Referrer-Policy, Permissions-Policy, COOP),
  and a per-address limit on sign-in and the webhook (429). S-13's per-address part comes with it; behind
  Docker's port mapping every browser on the host shares one address, so the burst is generous. S-11 closed:
  `untrusted.py` marks external text in every prompt, and a test fails on a prompt that doesn't. Files an agent
  reads itself are covered only by the rule in its system prompt.
