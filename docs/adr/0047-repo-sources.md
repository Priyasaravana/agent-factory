# ADR-0047: Repository sources: team repos from many hosts, orgs and tokens

**Status:** proposed · 2026-11-09 (asked for by Saravana 2026-11-09: onboarding a repo must never mean
rebuilding the factory, and repos come from different orgs and sources) · builds on
[ADR-0031](0031-existing-repos-assess.md) (onboard and assess), [ADR-0033](0033-repo-changes-as-pull-requests.md)
(changes as pull requests) and [ADR-0026](0026-cluster-target.md) (credentials through the environment)

## Context
A factory instance has exactly one `existing_repos` block: one `write_token_ref`, one `api_url`, one
`pr_webhook_secret_ref`, for every team repo. In practice the repos a factory looks after live in several
GitHub orgs (each with its own fine-grained token, because a fine-grained token has one resource owner),
sometimes on a GitHub Enterprise host, and some of them are private. Today:
- every repo shares one token, so the token must reach every org: the widest token wins;
- a second org or a GitHub Enterprise host means editing config and restarting;
- private repos can't be assessed at all: the clone is anonymous;
- the webhook has one secret, so a delivery signed for one org can name another org's PRs.

Adding a repo is already data (the **Products** page). What is missing is the layer between a repo and the
credentials that reach it.

## Decision
1. **A repository source** (`repo_sources.RepoSource`) says how the factory reaches one set of repos: a
   `provider` (`github`: github.com or GitHub Enterprise; nothing else yet), the `host`, its REST `api_url`,
   the `owners` (orgs or users) it covers, `access` (`read`: assess only; `write`: also push a branch and open
   pull requests), a `token_ref` and a `webhook_secret_ref`. Both secrets are **references**, never values
   (`env://`, `file://`, `vault://`, `aws-sm://`), resolved just in time for one step.
2. **Three layers, three owners.**
   | Layer | What | Where | Who changes it |
   |---|---|---|---|
   | Safety floor | `existing_repos.allowed_hosts`; `existing_repos.source_token_refs` (which secret references an admin-made source may name, default `env://REPO_*`) | config | the factory owner, with a restart |
   | Sources | the sources | config (`existing_repos.sources`, shown read-only) **and** the DB (made on **Integrations → Repository sources**) | admins, no restart |
   | Repos | URL, branch, source | the DB, from the **Products** page | any signed-in member |
   The existing single-token settings stay as the **default source** (`existing_repos.default_source`, on by
   default: every allowed host, any owner, `write_token_ref`, `api_url`, `pr_webhook_secret_ref`), so an
   install that sets nothing new behaves as before.
3. **Which source a repo uses.** Onboarding takes an optional `source`; without one the factory picks the
   only source that covers the URL's host and owner, preferring a source that names the owner over one that
   covers any owner; two that name it is an error asking the person to choose. The product records it
   (`Product.repo_source`). **The scope is checked again at every use** (clone, push, pull request, reading
   the PR): a source that was deleted, narrowed or moved to another host holds the change with the reason; it
   never falls back to another token.
4. **Security rules** (threats and what stops them):
   - *A token reaches an org it was not meant for*: a source covers only its `owners`; admin-made sources must
     name them (`*`, any owner, is config only).
   - *A token is sent to another host*: the source's host must be on `allowed_hosts`, and its `api_url`
     must be `https://` on that same host or `api.<host>`; next pages are followed only on that origin
     (`pr_loop.same_origin`, as before).
   - *An admin points a source at another credential* (the model key, the publish token): admin-made sources
     may only name references matching `source_token_refs`; config sources are the owner's own.
   - *The token leaks through the clone*: a private clone gets the token as an `http.<https://host/>.extraHeader`
     in `GIT_CONFIG_*` environment variables, never on the command line or in `.git/config`, with
     `http.followRedirects=false`; nothing of it reaches the worktree the sandbox mounts. Every resolved value
     (and its header form) goes to the redactor. A token the host refuses for a public repo falls back to an
     anonymous read, and the clone event says the token was refused, so a wrong token is visible, not silent.
   - *The token reaches agents*: clone, push, pull request and reading the PR run in the engine, never in a
     sandbox (ADR-0014).
   - *A read-only org gets pushed to*: a `read` source holds the pull-request station before anything is
     pushed.
   - *A forged webhook names another org's PRs*: each source has its own webhook secret; a delivery is
     accepted only when one source's secret verifies it, and then refreshes only PRs of repos that source
     covers.
   - *A source in use disappears*: deleting a source that a product still uses is refused; archive the
     product or move it to another source first (admin, on the product).
   - *Who did what*: creating, changing and deleting a source is admin-only and kept on the source (who,
     when, what changed; references only, never values).
   - *Members see more than they need*: members get the source's id, title, host, owners and access, which the
     onboarding form needs; admins also see the references and whether each one resolves.
5. **GitHub Enterprise.** A source on another host sets `GH_HOST` and `GH_ENTERPRISE_TOKEN` for `gh`, and
   names the repo as `host/owner/name`; `api_url` is `https://<host>/api/v3`.
6. **Not yet:** GitLab, Bitbucket and Azure DevOps (a provider each: clone, push, PR and PR reading differ);
   GitHub App installation tokens (short-lived, per org: the better credential once the secret store lands,
   ADR-0038); onboarding many repos in one go (each starts a paid assessment).

## Consequences
- A new org or a GitHub Enterprise host is an admin form, not a config edit and restart; a new repo is a URL.
- Each org gets its own least-privilege token; a `read` source lets a team be assessed before granting write.
- Private repos can be assessed and changed.
- Config still decides what the factory may reach at all: an admin can't widen the hosts or name arbitrary
  credentials from the UI.
- Products onboarded before this ADR have no `repo_source`: at their next use they resolve by the rule in §3
  (the default source unless a source names their owner), the answer is recorded on the product and an event
  says which source was chosen; from then on §3's "checked again, never falls back" applies.

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §3 OWASP agentic: identity & privilege abuse | One least-privilege token per org instead of one token for every repo; a `read` source assesses without write access; agents never hold any of them. |
| §1 Security & governance: secrets as references | Sources hold references only; admin-made ones are limited to the config's patterns; values are resolved per step, sent through the environment, redacted, never stored. |
| §1 Security & governance: separation of duties | The config owner sets the floor (hosts, references); admins manage sources within it; members only choose among sources that cover their repo. Every source edit is recorded. |
| §7 Security: fail closed | A source that no longer covers a repo holds the change; it never falls back to another token. Unknown hosts and other API hosts are refused. |
| §7 Security: webhook authenticity | Per-source secrets, constant-time checks, and a verified delivery only reaches PRs that source covers. |

