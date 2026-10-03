# Design: integrations, credentials and the factory control plane

Status: **accepted** · phases 1 (provider seam) and 2 (secret references) implemented · Decision record: [ADR-0011](../adr/0011-pluggable-delivery-control-plane.md)

## 1. Goals and non-goals

**Goals**
- Run the same workflow locally (kind) or against real infrastructure (registry,
  EKS/Argo CD, scanners, test tools, GitHub) by changing config, not code.
- No long-lived secrets on disk, in the DB, in logs, or anywhere an agent runs.
- Know an integration is ready **before** a change spends model tokens.
- Fit into a company's existing platform: its CI/CD, secret manager and portal.

**Non-goals (this iteration)**
- A general IDP: service catalog, scorecards, ownership, docs portal.
- Building on existing codebases (brownfield). That's a separate decision.
- Multi-tenant SaaS, SSO/RBAC. We only design so they can be added.

## 2. Concepts

| Concept | What it is | Example |
|---|---|---|
| **Integration** | A configured external system, with a readiness state | `ecr-main`, `argocd-dev`, `github-org` |
| **Provider** | Code that implements one capability for one kind of system | `registry/ecr`, `deploy/argocd`, `scan/trivy-server` |
| **Capability** | What a station needs | `registry`, `deploy`, `scan`, `test`, `publish`, `ci` |
| **Environment** | Named target that binds capabilities to integrations | `local`, `dev`, `staging` |
| **SecretRef** | Pointer to a credential, resolved just in time | `aws-sm://factory/ecr-push`, `vault://kv/argocd#token`, `env://GITHUB_TOKEN` (dev only) |
| **Readiness** | Result of a provider's `check()` | `ready` / `degraded` / `failed`, with reasons |

### Config sketch (`.agent-factory/config.yaml`)
```yaml
integrations:
  ecr-main:
    provider: registry/ecr
    settings: {account: "123456789012", region: eu-west-2, repository_prefix: factory/}
    auth: {identity: irsa}                 # no secret at all: pod role
  argocd-dev:
    provider: deploy/argocd
    settings: {server: argocd.dev.internal, project: factory, env_repo: org/env-dev, path: apps/}
    auth: {secretRef: "aws-sm://factory/argocd-dev#token"}
  github-org:
    provider: publish/github
    settings: {owner: my-org, visibility: private}
    auth: {secretRef: "aws-sm://factory/github-app"}   # GitHub App, not a PAT
  local:
    provider: local                        # today's behaviour: kind + dind + Trivy

environments:
  local: {registry: local, deploy: local, scan: local, publish: local}
  dev:
    registry: ecr-main
    deploy: argocd-dev
    scan: local                            # Trivy in the sandbox is fine to start
    publish: github-org
    approvals: [deploy]                    # a person approves before deploy

blueprints:
  fastapi-service:
    environment: local                     # per blueprint; can be overridden per product
```

## 3. Provider contract

```python
class Provider(Protocol):
    capability: str                        # "registry" | "deploy" | "scan" | ...
    def check(self, ctx) -> Readiness: ...     # reachable, identity, permissions (dry run), quota
    async def run(self, ctx, request) -> StepResult: ...   # the station's work, with evidence
```

- Stations keep their evidence contract: a provider returns what it did plus
  proof (image digest, scan report, Argo sync status, health URL). No evidence
  means FAILED or HELD, as today.
- Providers are Python entry points (`agent_factory.providers`), so a company can
  add its own without forking the engine.
- First providers: `local` (wraps today's code), `registry/ecr`,
  `deploy/argocd` (GitOps: commit to the env repo, wait for sync and health),
  `publish/github` (GitHub App, PRs after the first delivery).

## 4. Credentials

1. **Resolution:** the engine resolves a SecretRef in memory, right before the
   provider call, then drops it. It is never written to the DB, logs, events or
   disk. Values are redacted from command output.
2. **Prefer no secret at all:**
   - IRSA or OIDC federation for AWS;
   - a GitHub App (short-lived installation tokens) instead of PATs;
   - Vault dynamic credentials where available.
3. **Least privilege per integration:**
   - the ECR role can push `factory/*` only;
   - the Argo token can sync one project;
   - the GitHub App is installed on selected repos.
4. **Backends:** `aws-sm://` (Secrets Manager), `vault://`, `k8s://`
   (secret in the factory namespace), and `env://` for local development only.
   The UI shows the source, never the value.
5. **Rotation:** nothing is cached beyond one step, so a rotation takes effect
   on the next step.

## 5. Agent sandbox

- **One sandbox container per change.** It mounts only the change worktree. It has no
  engine secrets, no Docker socket or TLS keys, and no `/data` beyond the
  worktree.
- **Egress allowlist:** the model API, package indexes and docs sites. It
  cannot reach the cluster, the registry or GitHub.
- **Model credential:** only the model token is present. Later it could go
  through a local auth proxy so the token never enters the sandbox.
- **Engine vs agents:** the engine keeps privileged work, including
  build/push/deploy and git publish. Agents get results back as text or files.
- **Hooks remain** as defence in depth and for behaviour rules (e.g. no
  `git push`).
- **Holdout scenarios** are never mounted into builder sandboxes, only into the
  acceptance sandbox, read-only.

Open: Docker-in-dind sandbox containers vs Claude Code's built-in sandbox mode vs
a k8s Job per change. We start with a container per change in dind (it works locally
and maps to a k8s Job later).

## 6. Readiness and preflight

- `GET /api/integrations` returns each integration with its provider, source of
  auth (not the value), last check, state and reasons.
- `POST /api/integrations/{id}/check` runs a check on demand. Checks also run
  every 15 minutes and at startup.
- **Product preflight:** when a product is created, every integration its
  environment needs must be `ready`. Otherwise the product is refused with the
  failing checks, before any model call.
- Example checks:
  - ECR: `sts:GetCallerIdentity`, `ecr:DescribeRepositories` on the prefix.
  - Argo CD: app/project get, and write access to the env repo.
  - GitHub: App installation covers the target repo, and the repo is empty or
    factory-owned.
- UI: an **Integrations** page (green/amber/red per environment) and a readiness
  badge in the top bar.

## 7. Handoff mode (plug into existing CI/CD)

For companies with an established pipeline:
1. Deliver opens a PR in the company repo, instead of the factory building and
   deploying.
2. A `ci` provider (GitHub Actions / GitLab / Jenkins) watches the pipeline for
   that PR: build, scan, deploy to a preview or dev environment.
3. The factory runs acceptance (the hidden scenarios) against the deployed URL,
   reports on the PR, and holds for human feedback as today.

The factory then needs only: PR-level git access, read access to pipeline
status, and network access to the preview URL.

## 8. Control plane scope (what the UI/API owns)

- **In:**
  - integrations and readiness;
  - environments and promotion rules;
  - SecretRefs (references only);
  - policies and approvals (e.g. deploy to `dev` needs a person);
  - audit log (who changed an integration or policy, and who approved);
  - changes, cost and success metrics.
- **Out:**
  - service catalog, ownership, scorecards, docs;
  - general self-service. Those belong to the company's portal.
- **Later:** a Backstage/Port plugin or template action ("new service from
  requirements"), which calls the factory API and reads catalog context.

## 9. Delivery plan

Each phase is a separate PR with its own acceptance check.

| Phase | Scope | Done when |
|---|---|---|
| 1. Provider seam ✅ | `Provider` interface (`engine/src/agent_factory/providers/`); today's code in `local`; `integrations` + `environments` in config | Local changes issue the same commands; a test environment routes registry/deploy to another provider and acceptance tests its URL |
| 2. Secrets ✅ | SecretRef resolver (`env://`, `aws-sm://`, `k8s://`), redaction, audit | No secret in DB, events or logs (a test greps a change for known values) |
| 3. Sandbox ✅ | Agent sessions and `make verify` in throw-away containers without secrets, egress allowlist ([ADR-0014](../adr/0014-agent-sandbox.md)) | The earlier env/cert bypasses fail inside the sandbox; a live change passes |
| 4. Readiness ✅ | `check()` + `undeploy()`, readiness page, product preflight, archive ([ADR-0015](../adr/0015-readiness-and-preflight.md)) | A broken integration blocks a product in seconds, with reasons |
| 5a. Cluster target ✅ | generic `registry/oci` + `deploy/helm`, an ingress host per app, egress routes, local trial ([ADR-0026](../adr/0026-cluster-target.md)) | A live change on `local-ingress` pushes to a registry, deploys behind the ingress and passes acceptance |
| 5b. Cloud paths | ECR login via IAM, `deploy/argocd` (GitOps), GitHub App publish, image signing | A live change deploys to a dev EKS via Argo CD and passes acceptance |
| 6. Handoff mode | `ci/github-actions` provider; acceptance against the preview URL | A change lands as a PR, the company pipeline deploys it, the factory verifies it |

## 10. Open questions for review

1. **First remote target:** ECR + Argo CD on EKS (assumed), or plain Helm to
   EKS first?
2. **Secret manager:** AWS Secrets Manager (assumed), Vault, or both from the
   start?
3. **Where the factory runs** for a company: on their EKS (IRSA available), or
   still on a laptop, with OIDC or assumed roles?
4. **Sandbox technology:** container per change (assumed), Claude Code sandbox
   mode, or k8s Jobs?
5. **Approval gates:** which steps always need a person (deploy to shared
   environments? first publish?).
