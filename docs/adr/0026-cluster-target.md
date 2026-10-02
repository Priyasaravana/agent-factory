# ADR-0026: Cluster target: any OCI registry + Helm to any cluster, one ingress host per app

**Status:** accepted · 2026-10-02 · phase 5 of [integrations.md](../design/integrations.md#9-delivery-plan)

## Context
Every app went to the local kind cluster: images side-loaded with `kind load`,
one node port per app (20 at most), reachable only on the laptop. That proved
the factory works, but nothing it built could run somewhere a team could use,
and the port limit capped how many apps (and evaluation cases, ADR-0025) could
be live at once. The provider seam (phase 1) was built for this; no second
provider existed.

## Decision
1. **Two generic providers**, not a cloud-specific pair first:
   - **`oci` registry:** tags and pushes the built image to any OCI registry
     (GHCR, Docker Hub, ECR with a token, Harbor, a local registry). The
     repository is set in config, as are the username and the password as a
     secret reference.
   - **`helm` deploy:** Helm to any cluster the factory can reach (a kubeconfig
     secret reference, or the factory's own).
     - Each app is served at **`<app>.<ingress_domain>`** through the cluster's
       IngressClass, with optional TLS from cert-manager.
     - Node ports aren't used, so there is no app limit, and orders on this
       target get no port.
     - An optional image pull secret covers private registries.
   - ECR and Argo CD come next. With these two in place they are small
     additions.
2. **Credentials never touch a command line, an event or a lasting file.**
   - The registry password goes to `docker login --password-stdin` from an
     environment variable, into a throw-away Docker config.
   - The kubeconfig and pull secret go to a temporary file or stdin, removed
     when the step ends.
   - Every use is audited as "used secret <ref> for <purpose>", and resolved
     values are redacted.
3. **The chart owns the shape; the engine sets the values.** The golden-path
   chart gains an optional Ingress, `service.type` and `imagePullSecrets`.
   Before deploying, the provider renders the chart. A chart without an Ingress
   fails with that evidence, so the devops agent adds one: older product repos
   heal themselves.
4. **Acceptance tests the real URL.**
   - The engine's health check and the independent verifier both use
     `<app>.<domain>`.
   - The sandbox reaches it only through the egress allowlist. Readiness
     degrades with the exact rule to add when `*.<domain>:<port>` is missing.
   - The egress proxy gains **routes** (`pattern:port=host:port`): it connects
     elsewhere while keeping the host name (Host header, SNI). Locally this
     sends `*.localtest.me:8180` to the ingress in dind.
5. **A local trial with no cloud needed:**
   - environment `local-ingress` = registry `kind-registry` (a registry
     container next to kind) + deploy `kind-ingress` (ingress-nginx in kind);
   - apps are served at `http://<app>.localtest.me:8180` on your Mac, because
     `*.localtest.me` resolves to 127.0.0.1;
   - set it up with `make reset-cluster` once (new kind settings), then
     `make local-ingress`, then set a product line's `environment: local-ingress`.
   - A real cluster is the same two integrations with real values (see the
     config comments).

## Consequences
- What the factory builds can run on a team's cluster, at a stable URL, with no
  app limit.
- The registry is a real supply-chain step: the pushed digest is recorded, and
  signing the image (cosign) and the evidence manifest attaches to it next.
- **Configuration grows:** a registry, a domain, an IngressClass, an egress rule
  and, for a remote cluster, a kubeconfig. Readiness checks each one and says
  what's missing.
- The local trial needs a one-time `make reset-cluster`, which removes running
  apps. The plain `local` environment is unchanged and stays the default.
- **Not yet:**
  - ECR login via IAM (a token works today);
  - Argo CD / GitOps;
  - per-app DNS automation (a wildcard record is expected);
  - the reliability, performance, cost and portability pillar signals (probes,
    graceful shutdown, resource requests, chart lint as readiness signals).
