# ADR-0048: Closing the paths around the sandbox

**Status:** proposed · 2026-11-16 (order decided by Saravana 2026-11-16: the first group of the
[2026-11-15 assessment](../security/assessment-2026-11-15.md) comes before J4) · builds on
[ADR-0012](0012-auth-gateway-container.md) (the gateway), [ADR-0014](0014-agent-sandbox.md) (the agent sandbox),
[ADR-0018](0018-least-privilege-containers.md) (least privilege) and [ADR-0036](0036-model-gateway.md) (model gateway)

## Context
The sandbox (ADR-0014) holds agents and the commands they write on an internal network behind an
allowlist. The assessment found that agent-written code also runs in three places the sandbox doesn't
cover, and that the factory's own services trusted whoever reached them:

- a Dockerfile's `RUN` steps at Build, the image start check and the app pods run in dind on ordinary
  networks, with the open internet and a route to the compose network (S-02);
- the engine took identity headers from any caller (S-01, closed in ADR-0012's update);
- the UI and the apps were both on `localhost`, so apps were sent the factory's session cookie (S-04);
- the engine's own model credential sits in every sandbox unless the gateway mints a key per call (S-03).

## Decision
1. **dind keeps its containers off the compose network** (`cluster/dind-firewall.sh`, dind's entrypoint).
   Two rules that say the same thing, in two tables that are lost independently: `mangle FORWARD`, put in
   **before dockerd starts** (so no container ever runs without it; Docker never touches that table), and
   `DOCKER-USER` once dockerd has made the chain. Each drops every packet a container sends on a connection
   it opened to the compose subnet (conntrack direction ORIGINAL), whatever flags its `docker run` or
   `docker build` had, so a connection that got through while a rule was missing is cut when the rule
   returns. Replies to connections that came in pass (the engine calling an app on a published port). Only
   the model gateway's port stays reachable (`DIND_COMPOSE_ALLOW_PORTS`, default 4000), for the sandboxes'
   egress proxy. A watcher checks both rules every second and puts back what is missing. dind's health check
   (`dind-firewall.sh check`) reads the real rules and the watcher's liveness and last pass, never a marker
   file; the factory doesn't start on an unhealthy dind, and dind doesn't start dockerd at all if the first
   rule can't be installed (review of #182, 2026-11-16: the first version had one NEW-only rule, a 15-second
   poll and a marker file).
2. **The start check is boxed in** (`base_images.start_check`, pure). No capabilities, no privilege
   escalation, bounded memory and processes. With the sandbox image to ask from, the image runs on an
   `--internal` network with no published port and is asked by name from a second throw-away container; in
   dry-run or with the sandbox off it is published as before, with the same limits.
3. **App pods reach DNS and their own namespace, nothing else** (`providers/app_network.py`, pure). Both
   deploy providers apply one NetworkPolicy to the app's namespace before the release goes in. It is the
   engine's, not the chart's, so a change to the product's chart can't drop it. A blueprint whose apps must
   call something outside says `app_egress: open`, and the policy is removed.
   **The chart can't widen it either** (review of #182, 2026-11-16). Kubernetes adds policies together, so a
   second policy that allows egress would undo the engine's with the engine's intact; and a chart that
   binds its own service account to an existing role (`cluster-admin`, `edit`) lets a pod delete the policy
   later, after every deploy-time check (re-review, 2026-11-17). So under `restricted` the rendered chart
   (`helm template` in the release's namespace, hooks included; `app_network.widening`, pure) is held to an
   **allowlist** (`APP_KINDS`): Service, ConfigMap, Secret, ServiceAccount, PersistentVolumeClaim, Pod,
   Deployment, StatefulSet, Job, CronJob, Ingress, NetworkPolicy, HorizontalPodAutoscaler,
   PodDisruptionBudget, all in the release's own namespace. Anything else is refused by kind, whatever it
   contains: every RBAC object and binding, webhooks and admission policies, DaemonSets, CRDs, another
   plugin's policy kinds. Within the list, a NetworkPolicy with an egress rule and a pod on `hostNetwork`
   are refused. With no binding, a ServiceAccount the chart makes has no API rights. After the release the
   namespace is read back (`after_release`): a policy that allows egress besides the engine's, any
   RoleBinding in the namespace, and any ClusterRoleBinding naming a subject in it are removed and the
   deploy fails, because a template can render differently on the cluster (`lookup`). A render or an answer
   the engine can't read is refused, never taken as clean. Proved on a real kind cluster by
   `scripts/cluster-security-check.py` (CI job `cluster-security`), which installs an adversarial chart for
   real, bypassing the check, to show the threat and the read-back.
4. **The UI and the apps are different sites.** Apps on the local cluster are served on `127.0.0.1:<port>`
   (`local.off_the_factory_site`; a configured `http://localhost` is served the same way, and products already
   delivered are shown at the new address from the next start). The gateway sends a UI opened on `127.0.0.1`
   to `localhost`, and refuses a write to `/api` or `/auth` whose `Origin` is not the host it was sent to.
5. **A shared instance runs only with a virtual key per call** (`gateway.shared_refusal`, pure):
   `factory.shared: true` starts live only with `model_gateway.provider: litellm` and `credential: api_key`.

## Not decided here (still open)
- **Builds and the open internet.** A `RUN` step can no longer reach the factory, but it still has the open
  internet: the egress allowlist does not apply to it. BuildKit in dind refuses a custom build network
  (`network mode ... not supported by buildkit`, seen in CI 2026-11-16), so "build on the sandbox network
  through the proxy" is not a flag away. The options (a BuildKit builder with its own network, or
  `--network none` after a sandboxed dependency fetch, which changes both templates' Dockerfiles) need a
  design and live runs of both packs; the start check on the default bridge (sandbox off) and the scanner
  containers share this gap. The probe reports it as KNOWN.
- **Whether the cluster enforces the policy.** kind's network plugin enforces NetworkPolicy from v0.24 (the
  factory ships v0.27); another cluster's plugin may not. The policy is necessary, not proof: nothing in
  the factory tests enforcement yet.
- **An admission boundary for app namespaces.** The checks above are the engine reading the chart and the
  namespace at deploy time. They don't stop a workload that gets cluster rights some other way from
  changing policies later, and between the release and the read-back a policy that only appeared on the
  cluster is in force for a moment. The ceiling a chart cannot get past is in the cluster: a
  ValidatingAdmissionPolicy (or AdminNetworkPolicy where the plugin has it) that refuses egress policies in
  app namespaces. It needs the cluster bootstrap and a live kind run, so it goes with S-05 (Pod Security
  `restricted`), before J4. A `helm rollback` restores a revision that passed these checks when it was
  deployed; revisions from before this ADR were not checked.
- **Pod Security on app namespaces (S-05): decided 2026-11-24, in review on `security/s05-pod-security`.** Every
  deploy labels the namespace for Kubernetes' Pod Security admission at the level its rendered chart meets
  (`providers/pod_security.py`, pure apart from `prepare`): `restricted` for the packs' charts (fastapi-service 1.2.1,
  nextjs-web 1.6.1), `baseline` for charts made before them, refused below baseline before the release; `warn` and
  `audit` at `restricted`. The level only goes up by itself: a chart never lowers a namespace at `restricted` (a
  person can, on the cluster), and a change's temporary namespace counts production's level, so validation never
  passes a chart production refuses. Admission is the cluster's own control, so a value templated through
  `values.yaml`, a hook or a `lookup` can't get past it; the engine's check exists to refuse early with a reason.
  The admission policy for egress policies (the bullet above) is still open.

## Residual risks (stated, not accepted)
None of these is accepted by this ADR. An acceptance needs an owner, a reason, the workloads and data it
covers and an expiry, recorded here by Saravana; until then they are open findings.

| Residual | What it allows | What would close it |
|---|---|---|
| Builds and scanners have the open internet (S-02, fix 1) | A Dockerfile `RUN` step or a scanner container can send out whatever it can read: the build context, which is the product's worktree. No factory credential is in that context. | The build-network design above. |
| App pods reach the Kubernetes API server (seen on kind 2026-11-17, `cluster-security`) | The API server is on the nodes' own addresses, and what a NetworkPolicy does to traffic to a node is left to the network plugin: kindnet lets it through. A pod can talk to the API with whatever its account may do; the chart can't give it rights (the allowlist), and the job shows RBAC refusing the namespace's own account (403). | Turning off token mounting for app namespaces' accounts, and the admission policy above; or a network plugin that filters node traffic. |
| DNS is allowed from app pods | Data can leave in DNS queries through the cluster's resolver. The policy stops direct connections, not this channel. | A resolver for app namespaces that answers only cluster names (or logs and limits external lookups). |
| The firewall can be missing for up to about a second | Both rules would have to be removed at once (by root inside dind); connections opened meanwhile are cut when the rules return. Proved in CI by the probe. | Nothing planned; the window is the watcher's interval. |
| A policy that appears only on the cluster | In force between the release and the read-back (seconds). | The admission policy above. |
| `factory.shared` is a declaration | An instance that is shared without saying so still runs with the engine's own model credential in its sandboxes. | Auth and roles for shared instances (after J5): the engine then knows it is shared. Until then the runbook requires the setting for any instance more than one person uses. |
| Products delivered before S-05 run at `baseline` | Their pods may run as root and keep default capabilities inside the container; privileged pods, host namespaces, host paths and added capabilities are refused. | An upkeep change that adopts the template's security settings; the deploy then raises the namespace to `restricted` by itself. |
| Existing apps on a cluster target | Their namespaces get the policy at their next deploy, not at upgrade (the local cluster's get it at start, `secure_app_namespaces`). | Redeploy each, or apply the policy by hand; listed in `docs/upgrading.md`. |

## Consequences
- `make upgrade` restarts dind with the new entrypoint (the kind cluster and images are kept in its
  volume). App addresses move from `localhost:<port>` to `127.0.0.1:<port>`.
- An app that calls an outside service stops reaching it until its blueprint says `app_egress: open`.
- Anything run inside dind that needed the compose network (nothing the factory ships does) must go
  through `DIND_COMPOSE_ALLOW_PORTS`.
- A reverse proxy in front of the gateway must pass the `Host` the browser used, or writes are refused as
  cross-site.

## Best practices
Mapped to [practices.md](../practices.md) and the [threat model](../security/threat-model.md).

| Practice | How this decision meets it |
|---|---|
| §3 OWASP agentic: identity & privilege abuse | Code an agent wrote can't reach the engine, the auth service or the gateway from a build, a start check, a scanner or a pod; the session cookie never reaches an app; a page on another site can't write with a session. |
| §3 OWASP agentic: unexpected code execution | The start check runs the built image without capabilities, bounded, and where possible with no network way out; app pods have default-deny egress. |
| §1 Security & governance: least privilege | One firewall rule instead of a flag every caller must remember; the policy belongs to the engine, not to the chart the agent edits; a shared instance never puts a person's model credential in a sandbox. |
| §7 Security: fail closed | dind is unhealthy without the rule; a deploy fails if the policy can't be applied; gateway mode and a shared instance refuse to start when their condition isn't met. |
| §7 Security: evidence | `scripts/sandbox-paths-probe.sh` and `scripts/gateway-check.sh` run against the real stack in CI `e2e`, and the probe says plainly what is still open. |
