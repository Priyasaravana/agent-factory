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
1. **dind keeps its containers off the compose network** (`cluster/dind-firewall.sh`, dind's entrypoint). One
   rule in `DOCKER-USER` drops every new connection a container inside dind opens to the compose subnet,
   whatever flags its `docker run` or `docker build` had. Replies to connections that came in pass (the engine
   calling an app on a published port). Only the model gateway's port stays reachable
   (`DIND_COMPOSE_ALLOW_PORTS`, default 4000), for the sandboxes' egress proxy. dind is healthy only while
   the rule is in place, so the factory doesn't start without it.
2. **The start check is boxed in** (`base_images.start_check`, pure). No capabilities, no privilege
   escalation, bounded memory and processes. With the sandbox image to ask from, the image runs on an
   `--internal` network with no published port and is asked by name from a second throw-away container; in
   dry-run or with the sandbox off it is published as before, with the same limits.
3. **App pods reach DNS and their own namespace, nothing else** (`providers/app_network.py`, pure). Both
   deploy providers apply one NetworkPolicy to the app's namespace before the release goes in. It is the
   engine's, not the chart's, so a change to the product's chart can't drop it. A blueprint whose apps must
   call something outside says `app_egress: open`, and the policy is removed.
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
- Pod Security `restricted` on app namespaces is S-05, placed before J4.

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
