# Plan: the factory as a control plane beside a company's estate (environments, credentials, teams, audit, continuity)

Status: **reference, proposed** (research 2026-11-27, kept at Saravana's request 2026-11-27 for reference). Nothing
here is decided: the decisions are listed under "Decisions only Saravana can make". Phase P and phase R are proposed
names; the roadmap's "Next, in order" is unchanged until he agrees.

Saravana's goals (2026-11-27): (1) deploy into and integrate with existing dev/test/stage/prod environments, with
different tools per environment and stations as the integration points for environment and tool access; (2) no
permanent tokens kept in the factory, short-lived scoped tokens only when needed; (3) later, stations and interactions
restricted by role, each station acting as a team, or autonomous with a responsible owner where the company has no
team; (4) products visible only to the teams entitled to them; (5) log standards, audit trails, encryption; (6) the
factory is not a bottleneck or single point of failure: without it every app keeps running, serving and is manageable
by other means, with no business impact; (7) the factory is the place to see and manage a product's whole lifecycle
without creating more dependency.

Since the research: S-06 (#214, in review 2026-11-26) moves secrets to files (`NAME_FILE`) and adds a
`github-app://` backend that mints GitHub App installation tokens, which closes part of goal 2 and of phase T slice 1
below. Check the "Credentials" section against main once #214 is merged.

## Summary
The Agent Software Factory is partly heading toward the bigger goal. Its seams are the right ones: deploy providers behind an interface, credentials passed as references and resolved just in time, capability grants per station, promotion of the exact tested image digest, signed evidence anyone can verify, and a factory that only opens PRs and never merges. But three of the seven goals have no scheduled home. **Multi-environment delivery (dev, test, stage, prod with different tools in each) is not in phases A–E, T or I**. It sits under "Other deferred work". **Product visibility by team has no design beyond one line in phase T**. **"The factory is not a single point of failure" is stated as an invariant but is not yet true**: the factory is today the only deployer (it pushes with its own kubeconfig), the only holder of the "last healthy release" record, the only monitor and the only place backups are kept. Every serious peer (Argo CD, Flux, Kargo, Humanitec, Harness, Spacelift) solves this the same way. A thin control plane decides. The customer's own pull-based agents or outbound-only runners act, using credentials that never leave the customer's network. Desired state lives in Git, so an outage freezes changes but stops nothing. Phases C–E are full, so before v0.1.0 the honest move is to commit to that architecture on paper: three ADRs, a handful of invariants, and the cheap pieces already in flight (S-06, the O1 log contract, SSO group claims, KMS signing, a factory-off check in the 20 Dec rehearsal). Build it in phase T plus two new phases after it: environments and promotion (phase P) and continuity and runners (phase R).

## The seams are right, but three of seven goals have no scheduled home

The factory already models an environment, but only as a mapping from four capabilities to integrations: `registry`, `scan`, `deploy`, `publish` ([config.py](../../../engine/src/agent_factory/config.py)). Each blueprint picks exactly one, and both shipped environments are local kind variants ([config.yaml](../../../.agent-factory/config.yaml)). The model has no order, no promotion between environments and no approvals per environment. `policies.deploy.targets` is declared with the comment "production deploy is never implied", but no code reads it. The `helm` and `oci` providers can reach any cluster and registry given a kubeconfig and a token (ADR-0026). ADR-0046 adds a temporary validation release, promotion by digest and verified rollback ([ADR-0046](../../adr/0046-validate-promote-rollback.md)). The design doc already sketches named `dev`/`staging` environments, a `deploy/argocd` GitOps provider, `approvals: [deploy]` per environment and a "handoff mode" in which the company's CI deploys and the factory runs acceptance against the resulting URL. Phases 5b and 6 of that doc are unticked and unscheduled ([integrations.md](../../design/integrations.md)).

The scorecard below sets what exists against what is planned and what is missing. It is the baseline for the plan in the later sections.

| Goal | What exists today | Where the roadmap puts it | Gap with no home |
|---|---|---|---|
| 1. Fit into existing dev/test/stage/prod, different tools per environment | Provider seam; one environment per blueprint; push deploys via kubeconfig; ADR-0046 validate → promote → rollback | "Other deferred work" (gated promotion, ADR-0039 Q2; Argo CD) | Environment chain, gates per environment, GitOps or handoff deploy, outbound runner |
| 2. No permanent tokens | Refs `env://`, `file://`, `aws-sm://`, `vault://`; LiteLLM virtual key per agent call (only when `litellm` mode is on); long-lived tokens in a plaintext `.env` (S-06 open) | S-06 in phase A; secret store as T slice 1; GitHub App "later" | Minting short-lived credentials (GitHub App, K8s TokenRequest, cloud STS), factory workload identity |
| 3. Stations gated by role, team or accountable owner | Admin and member roles only; station `owner` role id checked at publish, not enforced at run time | SSO in D; teams, rota, RACI and modes in T slice 2 | Binding people or teams to stations at run time; SCIM |
| 4. Product visibility by team | Every member sees every product; steering limited to creator, owner or admin | "Team permissions per product" in T | Read authorization for every route and side channel |
| 5. Logs, audit, encryption | Change events, transitions, signed evidence (ADR-0050), auth audit table; plain-text logs; HTTP at nginx; data unencrypted at rest | O1–O7 telemetry proposed for C; SIEM, KMS and compliance pack in D | One audit stream across the factory; TLS by default; encryption at rest |
| 6. No single point of failure | Apps make no runtime call into the factory; Helm state stays in-cluster | External monitoring (T slice 3); factory restore drill; Postgres "after v0.1.0" | Factory-off guarantee for deploy, rollback and backups |
| 7. Lifecycle view without new dependency | Product page, attention, ledger, Operate, signed retire; standalone repos with chart and CI | UI redesign in D; product record pushed to Backstage/Port in T slice 4 | Repo-held catalog descriptor; deploy that works without the factory |

The pattern is clear. Where the factory touches its own internals (evidence, guardrails, sandboxes, judging), it is already enterprise-grade. Where it touches a company's estate (clusters, clouds, identity, other teams), it still behaves like one trusted host that holds every key. That is the shape to change. Most of the change is configuration and contract, not new engines.

## Peers keep the deployer out of the factory and the factory out of runtime

Every mature tool separates the **environment** (a named stage with protection rules, scoped secrets and a target) from the **mechanism that deploys to it**. GitHub environments carry up to six required reviewers, a "prevent self-review" switch, wait timers, branch filters, and secrets that a job cannot read until a reviewer approves. GitHub Apps can also act as custom protection rules, where an external service approves or rejects the deployment ([GitHub Docs](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)). That hook matters for the factory: it can be the **evidence gate on an environment it does not deploy to**. One commercial catch: on Free, Pro and Team plans, reviewers and custom rules work only on public repos, so private repos need Enterprise. GitLab has environment tiers, protected environments and environment-scoped variables, and it blocks self-approval by default ([GitLab environments](https://docs.gitlab.com/ci/environments/); [GitLab deployment approvals](https://docs.gitlab.com/ci/environments/deployment_approvals/)). In Humanitec, every environment is linked to **exactly one runner** by rules ([Humanitec runners](https://developer.humanitec.com/platform-orchestrator/docs/configure/runners/overview)). Harness places delegates per network or environment ([Harness delegates](https://developer.harness.io/harness-platform/use-harness-platform/delegates/delegate/delegate-concepts/delegate-overview.md)). In each case the binding of "which tool, which credential, which target" belongs to the environment. The factory's `Environment` already has that shape, minus order, gates and scope.

**Kargo is the closest template for the factory's promotion logic.** It "moves changes between stages but does not deploy them"; deploying "is left to a GitOps agent like Argo CD" ([Kargo concepts](https://docs.kargo.io/user-guide/core-concepts/)). Its **Freight** is an immutable bundle of image and manifest revisions that move together. A Stage accepts Freight only "after being verified in the upstream Stage(s)". Promotion steps are ordinary `git-commit` and `git-push` operations to a stage branch, and auto-promotion is switched on per project "as a security measure" ([Kargo stages](https://docs.kargo.io/user-guide/how-to-guides/working-with-stages)). The factory's sealed, signed change manifest (image digest, chart, config hash, evidence) is Freight in all but name. ADR-0046's "promote the exact tested digest" is Kargo's rule for a single stage. Extending it to a chain is a modelling change, not an invention.

The second lesson is **pull over push**. The OpenGitOps principles say agents "pull the desired state" and "continuously reconcile" ([OpenGitOps](https://opengitops.dev/)). Argo CD is "largely stateless": its state is Kubernetes objects in etcd, and its Redis cache "can be safely rebuilt without service disruption" ([Argo CD HA](https://argo-cd.readthedocs.io/en/stable/operator-manual/high_availability/)). GitLab goes further and says push-based CI/CD to a cluster "has a weaker security model" and should not be used for production ([GitLab agent](https://docs.gitlab.com/user/clusters/agent/)). When a GitOps controller is down, running pods keep serving, because kubelet and the controller manager keep them up. You lose sync and drift correction. It is a change freeze, not an outage. No vendor states that outright for a total outage; it follows from how the Kubernetes control loop works. For the factory, this decides goal 6. If the factory commits desired state to an environment repo and the cluster's own Argo CD or Flux applies it, then a dead factory cannot block a rollback (`git revert` works), the factory never holds production cluster credentials, and a compromised factory can at worst *propose* a change that branch protection then stops.

The third lesson is **outbound-only runners for networks the factory cannot reach**. Spacelift workers need only outbound HTTPS on port 443. The private key is generated locally and only a CSR leaves the network, and the worker assumes the customer's own IAM role. Spacelift is also retiring its MQTT transport in favour of HTTP long-poll by **30 March 2027** ([Spacelift worker pools](https://docs.spacelift.io/concepts/worker-pools)). That is a useful steer that long-poll is the simpler, proxy-friendly choice. The Harness delegate dials out over HTTPS/WSS and "performs all operations" itself ([Harness](https://developer.harness.io/harness-platform/use-harness-platform/delegates/delegate/delegate-concepts/delegate-overview.md)). In 2026 Humanitec moved its drivers into the in-cluster Operator. The stated reason was that calling the SaaS had created "an external deploy-time dependency". Now provisioning runs "without sending any data outside your network" ([Humanitec blog](https://humanitec.com/blog/a-simpler-and-more-secure-architecture-with-built-in-drivers)). That is a vendor removing itself from the deploy path, which is exactly goal 6. Backstage and Port add the matching rule for the lifecycle view. The catalog is a **read-only view over sources that live elsewhere**: Backstage has a `readonly` mirror mode and removes orphans when their source disappears ([Backstage catalog config](https://backstage.io/docs/features/software-catalog/configuration)), and Port's reconciliation deletes entities no longer in the source ([Port sync](https://docs.port.io/build-your-software-catalog/sync-data-to-catalog/)). OpsLevel locks in the UI any field defined in the repo's `opslevel.yml`, and keeps the data if the file is deleted ([OpsLevel](https://docs.opslevel.com/docs/opslevel-yml)).

Put together, these give the target architecture. The factory decides, gathers evidence and shows the lifecycle. Git holds desired state per environment. The customer's GitOps agent, CI or runner acts with the customer's credentials. Each environment declares which of those it uses. That is how "stations are the integration points for environments and tools" becomes concrete. A deploy or promote station does not deploy. It calls the environment's adapter, and the adapter is one of four:

- **gitops**: commit to the environment repo, then read back sync and health.
- **handoff**: open a PR or trigger the team's pipeline, read status, and act as the GitHub custom protection rule.
- **runner**: an outbound worker in the customer network runs the helm or terraform step.
- **direct**: today's helm push, kept for `local` and pilots.

## Credentials should be minted per station call, not stored

The factory's `ctx.secret(ref, purpose, capability)` seam is the right shape. What has to change is what a "ref" means. Today it points to a stored value. In the target design it resolves to a **mint operation**: issuer, audience, scope and TTL. Every target the factory touches now supports this:

- **GitHub App installation tokens** "expire after 1 hour". Each token can be narrowed to listed repositories and a subset of permissions ([GitHub Docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)). A PR station can therefore get a token for one repo with `contents:write` and `pull_requests:write`, and revoke it in its `finally`.
- **AWS `AssumeRoleWithWebIdentity`** issues sessions of 900–43,200 seconds. A session policy can only narrow the role, and up to 50 session tags can carry `product` and `change` for ABAC and CloudTrail ([AWS STS](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRoleWithWebIdentity.html)).
- **Azure federated credentials** need an exact issuer and subject match, allow no wildcards, cap each identity at 20, and take minutes to replicate ([Microsoft Learn](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations)). This argues for one federated subject per factory *role* (build, deploy, operate), with per-product narrowing done through Azure RBAC rather than one trust per product.
- **Kubernetes** `kubectl create token` mints a bound, time-limited token for a service account scoped to one namespace, with no kubeconfig stored ([kubectl create token](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/kubectl_create_token/)).
- **ECR** is the weak spot. Its token lives 12 hours and reaches any registry the principal can reach ([ECR API](https://docs.aws.amazon.com/AmazonECR/latest/APIReference/API_GetAuthorizationToken.html)), so the scope has to come from the short-lived parent session, and the token must stay in the privileged engine step, never in a sandbox.

HCP Terraform shows the end state. It issues an OIDC token per plan and apply that encodes the workspace and run phase, and the temporary cloud credentials "are discarded when the run environment is torn down" ([HashiCorp dynamic credentials](https://developer.hashicorp.com/terraform/cloud-docs/dynamic-provider-credentials)).

The root of trust cannot be removed, only shrunk. When the factory runs on EKS, AKS or GKE, the platform's workload identity is the root, and the `IntegrationAuth.identity: "irsa"` field is already declared for it (declared but unused). On compose or a laptop, the cleanest root is for **the factory to be its own OIDC issuer**. It signs a short JWT per station call with claims such as `sub=factory:<install>:station:<handler>`, `product`, `change`, `capability`, and `aud=<target>`. It publishes its JWKS somewhere clouds can fetch, and keeps the signing key non-exportable (KMS, or sealed by OpenBao). One residual long-lived secret remains: the GitHub App private key, because GitHub has no inbound OIDC for Apps. It belongs in KMS or behind a small broker so the engine never holds the PEM. Snyk and similar SaaS scanners probably remain static service tokens in a vault. I found no evidence that Snyk supports OIDC.

The agent-security consensus supports this direction:

- OWASP's December 2025 Agentic Top 10 lists Identity and Privilege Abuse (ASI03). Its mitigations include "a unique, bounded identity with short-lived credentials" and "re-authorization for privilege escalation" ([OWASP GenAI](https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/); [Teleport summary](https://goteleport.com/blog/owasp-top-10-agentic-applications)).
- The 2026-07-28 MCP authorization spec requires tokens bound to an audience and says servers "MUST NOT accept or transit any other tokens" ([MCP spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)).
- NIST's NCCoE has an active agent identity and authorization project working toward an SP 1800 guide ([NCCoE](https://pages.nist.gov/nccoe-ai-identity/)).

The factory's sandbox rule (agents hold no target credential; privileged steps stay in the engine, ADR-0014) already meets the hardest part. Two details need care when the work is done:

1. **Redaction.** Since 27 April 2026 GitHub has been rolling out a stateless token format (`ghs_APPID_JWT`) that may not be 40 characters long ([GitHub Docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)). Redaction and the leak greps in `test_cluster_target.py` must not assume the old length.
2. **TTL versus step length.** A 15-minute AWS floor means "TTL equal to the task" really means re-minting before each privileged command, not one credential per change.

Each mint should also be an audited `credential_issued` event that names the source, capability, audience and TTL, never the token. Putting `change_id` into session tags or `sourceIdentity` lets the cloud's own audit log point back to the factory's sealed evidence.

## Teams, visibility and audit fit one relationship model

The access goals (stations owned by teams or accountable people, products visible only to entitled teams) are relationships, not attributes: org, then team, then product, then change, then station, with a responsible person at each level. That is ReBAC in the Zanzibar style, with a thin role layer and a few attribute rules such as "approver is not the requester". **OpenFGA** reached CNCF incubation on 11 November 2025. It has an official Python SDK and native `ListObjects` and `ListUsers` queries, which answer "which products may I see" and "who may approve this station" ([CNCF](https://www.cncf.io/blog/2025/11/11/openfga-becomes-a-cncf-incubating-project/)). SpiceDB is equally capable, though its `LookupResources` gets heavy above 10k results ([AuthZed](https://authzed.com/docs/spicedb/concepts/querying-data)). Cedar and Cerbos are stateless, so list filtering has to be built into the app ([Auth0 comparison](https://auth0.com/blog/rebac-abac-openfga-cedar/); [Cerbos](https://docs.cerbos.dev/cerbos/latest/recipes/filtering-resources)). At the factory's likely scale (hundreds to thousands of products per org), the right first step is **a pure Python ReBAC module in the engine**: `team_members`, `product_teams` and `station_owners` tables, list filtering as SQL joins, and relation names chosen to match an OpenFGA model so a later switch is a swap. This keeps "deterministic code decides" (ADR-0020) and the pure-function test style. It also respects ADR-0012: team membership must not become a gateway header. The engine looks it up by the proved user id.

The platforms agree on the details worth copying:

- **Ownership is separate from permission.** Port has Admin, Moderator and Member, with teams owning assets ([Port RBAC](https://docs.port.io/sso-rbac/rbac/)). Harness bindings are additive: "a role by itself grants nothing" ([Harness RBAC](https://developer.harness.io/docs/platform/role-based-access-control/rbac-in-harness)). Backstage returns *conditional* decisions that the owning plugin turns into a database query ([Backstage permissions](https://backstage.io/docs/permissions/concepts)).
- **GitLab's approval rules are the model for station gates:** each person counts once even across several groups, self-approval is blocked by default, the required count must be below the eligible pool, and a deleted approver is reassigned to Maintainers with an audit event ([GitLab approvals](https://docs.gitlab.com/ci/environments/deployment_approvals/)).
- **An autonomous station with no team** gets a required `accountable_user`. The agent is a non-human principal that can never satisfy an approval. If the owner is deprovisioned, the station holds and appears on the admin's attention list rather than silently running without an owner. That matches the EU AI Act Art. 14 expectation that a named person can "disregard, override, or reverse" output and halt it safely ([AI Act Art. 14](https://artificialintelligenceact.eu/article/14/)). It also matches the ISO 42001 auditor expectation of "override records", not just a policy ([CSA](https://cloudsecurityalliance.org/blog/2026/07/21/iso-42001-the-importance-of-knowing-your-role-before-building-your-ai-system)).

Visibility is where it is easiest to leak. OWASP rates broken object-level authorization as the top API risk ([OWASP WSTG](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/12-API_Testing/02-API_Broken_Object_Level_Authorization)). Today `list_products` filters only archived and evaluation products ([actions.py](../../../engine/src/agent_factory/actions.py)). Read authorization therefore needs to:

- filter in the query, never after pagination;
- return 404 for products the user cannot see;
- cover events, attention, outcomes, notifications, metric labels and slugs in hostnames.

The repo already has the enforcement pattern: `tests/test_access.py` classifies every mutating action. Adding a matching read classification is the invariant to commit to.

Audit uses the same principal model. ISO 27002 8.15 asks for user, activity, UTC time from a common source, source address and event type in every record. It also asks that logs be protected from tampering and deletion, including by admins ([UpGuard 8.15](https://www.upguard.com/compliance/iso-27001/8-15.md)). The DORA RTS and NIS2 implementing rules add detection of logging failures and logging of "activation, stopping and pausing" of the logs themselves ([DORA RTS Art. 12](https://www.springlex.eu/en/packages/dora/rts-rmf-regulation/article-12/); [NIS2 summary](https://nisd2.eu/en/wiki/implementation/nis2-logging)). The incumbents gate streaming, not generation. GitLab audit streaming is Ultimate-only, but every payload carries an `id` for deduplication ([GitLab streaming](https://docs.gitlab.com/administration/compliance/audit_event_streaming/)). Harness streams to S3, GCS, Splunk and Sumo Logic, with a two-year in-product limit ([Harness](https://developer.harness.io/docs/platform/governance/audit-trail/audit-streaming)). GitHub now streams **Copilot agent session events** to Purview, so agent actions have become audit material ([GitHub streaming](https://docs.github.com/en/enterprise-cloud@latest/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise/streaming-the-audit-log-for-your-enterprise)).

The factory already records every agent call and guardrail denial (ADR-0022), which puts it ahead of the incumbents once it streams them. The split that fits its open-core plan:

- **Open core:** a complete, signed, append-only audit log, generated for free. Shape it like OCSF API Activity ([OCSF](https://github.com/ocsf/ocsf-docs/blob/main/overview/understanding-ocsf.md)), with actor, `on_behalf_of`, scope, outcome and trace id.
- **Phase D (licensed):** SIEM connectors, long retention and customer KMS.

For telemetry, keep audit separate from OTel. OTel is sampled and may drop data; audit must not. Also keep the GenAI conventions' default of not capturing content: `NO_CONTENT` unless opted in, with large content stored externally and referenced ([OTel GenAI semconv](https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-spans.md)). Those conventions are still at Development status, so pin the version.

Two plain gaps should not wait for enterprise tiers:

1. **TLS.** nginx listens on HTTP 8080 and the secure-cookie flag is off by default ([nginx.conf](../../../web/nginx.conf)).
2. **Encryption at rest.** SQLite, repos, holdout and evidence are unencrypted at rest; only backups are age-encrypted.

GitLab Dedicated's AES-256 KMS envelope encryption with customer-owned keys is the public reference for the top tier ([GitLab Dedicated encryption](https://docs.gitlab.com/administration/dedicated/encryption/)).

## What fits before 31 December, and what waits

On 2026-11-27, Phase C closes on 4 December, D on 14 December, and the rehearsal is on 20 December. No environment chain, credential broker or team model can be built and tested in that window without displacing the open-core seam, SSO, SIEM, KMS or the UI redesign. Pretending otherwise would put the release at risk. What *does* fit is a small set of **commitments**. They make v0.1.0 point in the right direction, they cost hours rather than days, and most of them attach to work already scheduled.

| Phase | Add (cheap, foundational) | Why now |
|---|---|---|
| A (close out) | Close S-06 with `file://` as the documented default and `.env` used for development only; fix S-16 (token in git URL) if still open | It is already in A; the public release must not ship a plaintext-`.env` default |
| B (done) | No change. ADR-0050's signing key path should be written so it can later be held in KMS | D's KMS signing builds on it |
| C (by 4 Dec) | Make the O1 log contract carry the audit fields (`actor{type,id,on_behalf_of}`, `scope{org,product,change}`, `outcome`, `trace_id`) even if export comes later; add `org_id` to the extension seam's tenancy hooks; write **three ADRs**: environments and promotion (Kargo-style chain, gitops/handoff/runner/direct adapters), credentials as mints (factory OIDC issuer, GitHub App, no static fallback), and factory continuity (the factory-off invariant, its drill, and the rule that no rollback path waits on the factory) | ADRs are hours; the O1 schema is far cheaper to set now than to migrate later; the seam is the boundary paid features plug into |
| D (by 14 Dec) | SSO: keep IdP group claims in `auth/` (no mapping yet); KMS: one key abstraction that covers signing now and envelope encryption later; SIEM export: emit the O1/OCSF shape with a dedupe id and "stream unhealthy" events; decide the viewer role (about 1 day; slip it if D is tight) | Each one reuses work already in D and avoids a second schema |
| E (rehearsal 20 Dec, public 31 Dec) | Add a **factory-off check** to the rehearsal: stop the factory stack and confirm that a product on a non-`local` cluster still serves, pods reschedule (image from a non-factory registry), the repo's own CI still builds, and an operator rolls back to the previous release without the factory (on `direct`, `helm rollback` with the customer's own kubeconfig, which also tests that charts re-apply without engine-injected values); ship `catalog-info.yaml` in both pack templates if there is room; SECURITY.md states TLS-in-front as a requirement until native TLS lands | It shows the invariant in public instead of just claiming it; `catalog-info.yaml` is a template file, not engine work |

Three invariants belong in AGENTS.md before release. They are written rules with tests pending, so they bind later work without building anything now:

1. **No credential is stored where it could be minted.** `ctx.secret` never falls back from a mint to a stored value, and the gateway already follows this rule.
2. **Every route and side channel has a read classification.** It mirrors `tests/test_access.py`; until phase T it is "all members", but the classification exists.
3. **The factory is never in an app's runtime path, and nothing about a non-`local` product needs the factory to keep serving.** That covers registry, secrets, ingress, auth and monitoring, and rollback: every environment the factory gates keeps a
   customer-controlled, audited rollback path that does not wait on the factory. `local.off_the_factory_site` already applies this rule to hostnames.

After v0.1.0, phase T should be **reordered and widened rather than replaced**:

1. **Slice 1 becomes the credential broker, not only a secret store.** It mints GitHub App installation tokens per station call (repo- and permission-scoped, revoked in `finally`), K8s TokenRequest for namespace-bound deploy service accounts, and STS, Entra or GCP sessions keyed to the factory's OIDC issuer or platform workload identity, with `credential_issued` audit events. OpenBao (2.6.x, with a Kubernetes JWT provider and KMS seal plugins, [OpenBao 2.6](https://openbao.org/community/release-notes/2-6-0/)) holds what cannot be minted.
2. **Slice 2 (teams, rota, RACI, modes) gains product visibility and SCIM.** The in-engine ReBAC tables, list filtering in SQL, 404 for hidden products, and IdP groups pushed through SCIM into `auth/` and consumed by the engine.
3. **Slice 3 (external monitoring) and slice 5 (`recovery@1`) stay as planned.** They are the monitoring half of goal 6.
4. **Slice 4's product record pushes to Backstage/Port from the repo descriptor.** It does not mirror and own.

Then two new phases:

- **Phase P, environments and promotion** (after T slice 1, because environment-scoped credentials need the broker). It covers ordered environments per product, the Kargo-style chain on top of ADR-0046's digest promotion, gates per environment that reuse the team model, the `gitops` adapter (Argo CD or Flux reading an environment repo), and `handoff` mode, with the factory registered as a GitHub custom deployment protection rule.
  **Rollback must not wait on the factory.** A custom protection rule waits up to 30 days for the factory's answer and
  then fails the job, so a rollback into the same gated environment would be blocked by the very outage it is meant to
  survive, and admin bypass is not a plan (an environment may forbid it, and nothing limits what a bypassed job ships)
  ([GitHub protection rules](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/configure-custom-protection-rules)).
  Handoff mode therefore ships a **customer-controlled rollback path**: a separate rollback environment per gated
  environment (say `prod-rollback`), same target, *without* the factory's rule, guarded by the customer's own required
  reviewers and restricted to one rollback workflow on a protected branch. That workflow can only redeploy a digest
  listed in the healthy-release record mirrored into Git (phase R), so it rolls back and never ships new code. The audit
  is the customer's: GitHub's deployment history plus the reviewer's approval. When the factory returns it reads those
  deployments and records each one as an out-of-band rollback on the product (ledger and evidence), never as a
  factory change. The `gitops` adapter has the same trap: the factory must not be a required status check on the
  environment repo's revert path, which stays under the customer's branch protection.
- **Phase R, continuity and runners.** It covers the outbound long-poll runner for private networks, Postgres and more than one concurrent change, the healthy-release record mirrored into Git (so rollback works without the factory, through the customer-controlled
  rollback path from phase P), product backups written to customer storage, and the factory restore drill against RPO and RTO of one hour or less. The factory-off drill grows with it: with the
  factory stopped, an operator performs a real rollback through each adapter's customer path (handoff: the rollback
  environment; gitops: a `git revert`), the app ends on the previous healthy digest, and the factory, once back,
  records the rollback it did not make.

Phase I keeps its place. Its outcome metrics should add the 2026 DORA definitions, including **deployment rework rate** and failed deployment recovery time ([DORA guide](https://dora.dev/guides/dora-metrics/)).

### Decisions only Saravana can make

1. **Push or pull as the default for non-`local` environments.** I recommend GitOps pull, with `direct` kept for `local` and pilots, because it is what makes goal 6 true.
2. **Build a Kargo-style chain in the engine, or integrate Kargo itself.** Building fits the evidence model and pure judging. Integrating saves code but adds a dependency for customers who do not run Argo CD.
3. **The root of trust on self-hosted installs.** The factory as OIDC issuer (needs a reachable JWKS) or platform workload identity only (simpler, but excludes laptop and compose deployments from cloud federation).
4. **Authorization engine.** In-engine ReBAC now with an OpenFGA-compatible model (recommended), or OpenFGA as its own service from day one.
5. **Where the viewer role lands.** D or T.
6. **The open-core line for audit and encryption.** I recommend that generation, the signed log, OCSF export and TLS are open, and that streaming connectors, retention, KMS/BYOK and compliance packs are paid.
7. **Whether phase P comes before or after T slices 2–6.** Enterprise pilots will ask for environments before they ask for rota and escalation.
8. **Whether to ship native TLS in E or document TLS-in-front.**

Before acting, check five things the notes could not settle:

- whether a per-product environment override exists in code (the design doc says it does; only `Blueprint.environment` was found);
- the exact wording of ADR-0039 Q2 on gated promotion;
- whether S-16 is fixed on main;
- whether the generated charts can be re-applied without values only the engine injects (if not, the factory-off drill fails at the first rollback);
- naming: the telemetry slices are O1–O7 since PR #218, though a local checkout may still say T1–T7, which collides with phase T.

## Conclusion

The factory's real lock-in is not in artefacts. Generated repos already carry a chart, CI and verifiable evidence. The lock-in is that the factory *acts*: it deploys, holds the rollback record, monitors and keeps backups. Every peer that sells to enterprises has learned to decide centrally and act locally. That one principle settles goals 1, 2 and 6 together. A station that only commits to an environment repo, or asks a runner in the customer's network to act, needs no production credential, so short-lived tokens become natural rather than bolted on. The same station cannot block a running app or a `git revert`, so "no single point of failure" becomes a property of the architecture rather than a promise.

The practical consequence for the next five weeks is narrow: v0.1.0 should not try to deliver this, but it must not contradict it. Three ADRs, three invariants, an audit-ready log schema in O1, and one factory-off check in the 20 December rehearsal cost very little. They let the public release say truthfully that the factory is built to sit beside a company's estate as a control plane, and they make phases T, P and R build on that design instead of reworking it.
