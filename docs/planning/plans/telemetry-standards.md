# Plan: logs, metrics and traces on open standards (factory and products)

Status: recorded 2026-11-24 at Saravana's request, so it isn't missed; revised the same day for his two reviews on PR #198:
- redaction as a tested boundary, collector isolation and credentials, metric semantics during migration;
- egress enforcement across clusters, existing collectors, duplicate log ingestion, setup and usability.

Revised 2026-11-27 for the second review of #218 (held for a tenant boundary):
- every signal carries a trusted factory id and product id; readback selects by them, never by `service.name`,
  and a destination must be isolated by the backend (tenant-scoped credentials or a dedicated destination) (§3a);
- readback reconciles only objects this factory and release own; anything else that weakens isolation holds the
  deploy and is named for the platform operator, never deleted (§3).

Revised 2026-11-26 for the review of PR #218 (requested changes before implementation):
- telemetry egress is owned by the factory, never by the app's chart, and read back against the expected policy (§3);
- an adapter supports production only once its live enforcement check passes (§3);
- Test connection says what it proved, hop by hop, and keeps test time apart from real-data freshness (§6);
- metrics are scraped at the pack's declared path, and `existing` mode names who delivers them (§3, §4).

 **Slot decided by
Saravana 2026-11-26: all of it (O1–O7) in v0.1.0 phase C** (roadmap "Next, in order"). The slices were T1–T7 until
2026-11-26; renamed O1–O7 so they don't clash with phase T.

Related:
- Builds on agent observability ([ADR-0022](../../adr/0022-agent-observability.md)), which calls an exporter "a
  small, additive step".
- Builds on the external-first monitoring decided in [stations-and-teams.md](stations-and-teams.md).
- Replaces the roadmap backlog line "OpenTelemetry GenAI export".

## Why
Teams must be able to plug the factory, and every product it builds, into the tools they already run (Grafana,
Datadog, Elastic, Splunk, New Relic) without code changes and without a vendor SDK in any app. Swapping vendors
should mean changing one collector config. This is part of the positioning ([../vision.md](../vision.md)) and of
staying free of vendor lock-in.

## Where we are (main `233d89c`)
**Products: a good base, three gaps.** Both packs ship observability, and readiness checks keep it
(`packs/*/readiness.yaml`; `assess.py` checks the same on team repos).

| | fastapi-service | nextjs-web |
|---|---|---|
| Logs | JSON lines on stdout (`template/app/observability.py`) | pino JSON on stdout (`template/src/modules/logging/adapters/pino.ts`) |
| Metrics | Prometheus `/metrics` | Prometheus `/api/metrics` |
| Traces | OpenTelemetry over OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set | `@vercel/otel` over OTLP, same switch |

1. **Logs aren't joined to traces.** No line carries `trace_id` / `span_id`.
2. **Log field names are our own.**
   - FastAPI writes `ts` (epoch seconds), `level`, `msg`, `request_id`.
   - pino writes its defaults plus `service`.
   - Neither follows the OpenTelemetry log data model, so each backend needs a mapping.
3. **Metrics are our own:** a request counter `http_requests_total` with a `status` label. The watch reads it
   (`operate.request_counts`, named in `pack.yaml` `metrics.counter` / `status_label`).

The pack charts ship no NetworkPolicy. The engine applies its own, `agent-factory-egress` (DNS and the namespace only),
to every app namespace, and refuses a chart that adds egress rules or kinds outside `APP_KINDS`
(`providers/app_network.py`).

**The factory itself: not on the standards.**
- `agent-factory serve` uses plain Python `logging` with no formatter, so its logs are default text lines.
- No OpenTelemetry, and no `/metrics`.
- Its rich telemetry (one `agent_call` record per call, JSONL transcripts, denials; ADR-0022) lives only in its
  own ledger and UI.

## Design
### 1. One log contract
- **Format:** JSON on the OpenTelemetry log data model:
  - `timestamp` (RFC 3339), `severity_text`, `severity_number`, `body`;
  - `trace_id`, `span_id`, `trace_flags`;
  - resource attributes `service.name`, `service.version`, `deployment.environment.name`;
  - everything else under attributes (`request_id`, route, status).
- **Applies to** the factory and every product.
- **Trace context** is injected by the instrumentation: OpenTelemetry's logging instrumentation in Python,
  `@opentelemetry/instrumentation-pino` in Node.
- **Versioned:** the contract is a JSON schema in the repo.
- **Two outputs, one ingestion path:**
  - **stdout**, always, so `kubectl logs` works;
  - **OTLP** (the OpenTelemetry logs bridge: a Python logging handler, a pino OpenTelemetry transport), so a
    product-scoped collector needs no access to node log files (§3).
- **Duplicate-ingestion policy: every log record reaches the backend through exactly one path.** The product's
  setting `telemetry.logs.ingest` says which:
  - `otlp`: the logs bridge exports, and nothing is expected to collect stdout;
  - `cluster-agent`: the cluster's own log agent already collects stdout, so the logs bridge is **off**. The JSON
    lines on stdout carry `trace_id` / `span_id`, so correlation still works.
- **The factory asks once** ("Does your cluster already collect container logs?", §6) and never guesses. Traces
  and metrics have one path anyway.

### 2. Redaction is a tested boundary for every exported signal
**Covered fields:**
- log `body` and every log attribute;
- span name and attributes, span events (including exceptions: `exception.message`, `exception.stacktrace`) and
  span status messages;
- resource attributes;
- metric labels.

**Where it runs:** inside the process, before anything is batched, written or sent.
- **Logs:** a redacting processor ahead of both outputs (the stdout formatter and the OTLP log processor).
- **Spans:** a redacting span processor that applies on start and on attribute set, plus a redacting exporter
  wrapper as the last step before the wire, so batches never hold raw values.
- **Metrics:** labels come from fixed allow-lists (§4); no free-text label exists.

**Allow-lists:**
- **The factory's spans and logs** export only declared attribute keys; anything else is dropped.
- **Resource attributes** come from a fixed list.
- **Baggage** is never copied into attributes or exported. Incoming baggage is dropped at the factory's edge.

**What is redacted:**
- the `REDACTOR`'s resolved secret values (`secret_refs.py`);
- token patterns (bearer, API keys, the gateway's virtual keys);
- prompt and tool-result content. It is never put in telemetry: OpenTelemetry's GenAI content capture stays off,
  and transcripts stay in the ledger.
- In products: the `Authorization` and `Cookie` headers, `password`-like fields and URL query strings, from the
  pack's request logging.

**Fail closed:**
- If redaction raises, or meets a value it can't vet (an unexpected type, an oversized value), the record is
  **dropped**. A log line becomes a minimal `telemetry_redaction_failed` line with no payload, and a span is not
  exported.
- The drop is counted on `telemetry_redaction_failures_total` and recorded as a factory event.
- Never a fallback to the raw value.

**Defence in depth, not the primary control:** the collector's `redaction` and `transform` processors block known
credential keys and drop `gen_ai` content attributes, in case an instrumentation library adds something.

### 3. Collector modes, isolation and credentials
**Two modes, chosen per product** (`telemetry.collector.mode`; §6 shows how the choice is offered):

| | `existing`: use the organisation's collector | `bundled`: a product-scoped collector |
|---|---|---|
| For | Teams with a shared OTLP gateway or agent (most companies with observability) | Self-contained setups, pilots, the local cluster |
| What the factory deploys | **No collector.** The product's pods export OTLP straight to the organisation's endpoint | One collector Deployment in the product's namespace, a factory-owned release |
| Who runs it | The platform team: their collector, their egress, their backend credentials | The factory, with the profile below |
| Egress control | Engine-owned: the product's pods may reach only the organisation collector, by pod and namespace selector when it runs in the cluster, or by CIDR when it doesn't | Engine-owned: the exporter's egress, enforced as below |

- **Default:** `existing` when the team gives an endpoint. `bundled` with a debug exporter on the local cluster.
  Off until the team chooses, so nothing leaves the cluster by surprise.

**Who owns what (review of #218).** The product's chart is agent-written, and the deploy guard
(`providers/app_network.py`) already refuses a chart that ships egress rules or any kind outside `APP_KINDS`, and
removes unexpected egress policies after the release. That stays exactly as it is. So:
- **The app's chart renders nothing for telemetry** beyond the `OTEL_*` environment on its pods. No collector, no
  egress policy, no vendor resource (`CiliumNetworkPolicy`, `FQDNNetworkPolicy`, `EgressFirewall`, `ServiceEntry`, …).
- **The bundled collector is a factory-owned release** from a chart the factory ships (not the pack template),
  installed by the engine beside the product's release, with values the engine writes. An agent never edits it.
- **The telemetry egress policy is engine-owned**, like `agent-factory-egress` today: the engine renders it from the
  product's `telemetry` settings through the chosen adapter, labels it `app.kubernetes.io/managed-by: agent-factory`,
  and applies it before the release. `egress_steps` gains the telemetry step; the chart guard (`widening`) is
  unchanged.
- **Readback against the expected policy, reconciling only what this factory and release own** (review of #218,
  second round). After every release (and on the watch), `after_release` reads back the objects of every adapter kind
  the cluster serves, not only `networking.k8s.io` NetworkPolicies, and sorts each one by owner:
  - **This factory's own objects** (labels `app.kubernetes.io/managed-by: agent-factory` and
    `agent-factory.io/factory-id: <this factory's id>` (§3a), in the product's namespace): compared with what the engine
    applied (spec hash in an annotation, checked against a fresh render). Drift is re-applied and the deploy fails.
  - **This release's objects** (Helm's `meta.helm.sh/release-name` / `release-namespace` naming `ctx.release_name`
    and the product's namespace): the chart guard should have refused any egress object at render time; one that
    appears anyway (a template that renders differently on the cluster) is removed and the deploy fails, as today.
  - **Everything else is not ours, and is never modified or deleted:** another factory's objects, the platform
    team's policies in the namespace, and every cluster-scoped object (`CiliumClusterwideNetworkPolicy`,
    `AdminNetworkPolicy` / `BaselineAdminNetworkPolicy`, mesh-wide Istio config). The engine only reads them. If
    one weakens the product's isolation (it selects the product's pods or namespace and allows egress beyond the
    expected set, or passes / overrides the deny), the deploy **holds** (`StationResult.required`, a `decision`
    failure) and names the object (kind, namespace, name, its owner labels) and what it opens, for the platform
    operator to change or accept. Resume re-reads; it never removes the object.
  - **A kind the factory can't read** (no RBAC for it) is reported, and that adapter counts as preview on this
    cluster: isolation that can't be read can't be proven.
  - Each adapter gets cases in `tests/test_app_network.py`: own policy matching, own policy drifted, a release
    object removed, a foreign namespaced object that widens (held, not deleted), a cluster-scoped one that widens
    (held, not deleted), a foreign one that doesn't widen (ignored), and another factory's identically named
    policy (ignored).
  - The existing `after_release` removals in the product's namespace (NetworkPolicies, RoleBindings) and of
    ClusterRoleBindings predate this rule; O4 brings them under the same owner test, so a ClusterRoleBinding the
    factory didn't make is held and named, not deleted.

**Metrics delivery has one named owner per mode** (each signal reaches the backend through exactly one path, as
logs do in §1):
- **The scrape path is the pack's own** `operate.metrics.path`, never a hard-coded `/metrics`: `fastapi-service`
  serves `/metrics`, `nextjs-web` serves `/api/metrics`. The factory renders the scrape target from the pack.
- **`bundled`:** the factory's collector scrapes the product's Service at that path and exports.
- **`existing`:** `telemetry.metrics.delivery` says who delivers, asked once in setup (§6), never guessed:
  - `push` (default): the pods export the same instruments over OTLP (the SDK's periodic metric reader) to the
    organisation's endpoint; `/metrics` stays for the factory's watch, which is not a backend path;
  - `org-scrape`: the organisation's collector scrapes the product. The factory renders the scrape annotations at the
    pack's path, adds an ingress rule from the organisation collector's selector or CIDR to the metrics port, and
    shows "metrics: scraped by your collector" until Test connection verifies them at the destination.

**Bundled-mode topology:**
- One collector **Deployment in the product's namespace**, never a node-level DaemonSet.
- It receives OTLP (logs, traces) from the product's pods and scrapes the product's own Service at the pack's metrics
  path.
- With logs over OTLP (§1), it needs no `hostPath`, no `filelog` receiver and no access to any other workload's
  logs. A node-level log agent stays the cluster platform team's choice; the factory never deploys one.
- **The factory itself:** a collector service in its compose stack, reached only on the compose network, with the
  same two modes.

**Least privilege** (defaults of the factory's collector chart; the chart check fails on any departure):
- **Container:** non-root (`runAsNonRoot`, fixed UID), `readOnlyRootFilesystem`, all capabilities dropped,
  `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`.
- **Kubernetes access:** no `hostPath`, no host network or ports, and `automountServiceAccountToken: false`. The
  scrape target is the product's Service by name, so there's no Kubernetes API discovery and no RBAC.
- **Ingress:** a NetworkPolicy allows OTLP (4317/4318) only from the product's own pods. This is portable: it
  uses pod selectors. The receivers listen on the pod IP, never on a host port.
- **Egress to the backend: an explicit enforcement mechanism, chosen and checked** (`telemetry.egress.enforcement`),
  rendered and applied by the engine, never by a chart (see "Who owns what" above).
  The standard NetworkPolicy API selects destinations only by pod or namespace selector or IP CIDR; it has no
  hostname selector, and enforcement depends on the cluster's network plugin. A backend such as
  `otlp-gateway.grafana.net` resolves to changing IPs, so a plain NetworkPolicy can't express "only this host". The
  chart supports these mechanisms:

  The mechanism is an engine adapter: each one renders its own policy, and all share the same render-time check,
  the same readback and the same live enforcement check (below). Supported, in order of how widely they are deployed:

  | Mechanism | How | When |
  |---|---|---|
  | `fqdn-cilium` | `CiliumNetworkPolicy` with `toFQDNs` (`matchName` for the backend host) and a DNS rule for kube-dns, beside the standard policy that denies all other egress | Clusters running Cilium, including AKS with Azure CNI powered by Cilium (FQDN filtering needs the paid Advanced Container Networking Services). The chart checks for the `cilium.io/v2` API |
  | `fqdn-gke` | `FQDNNetworkPolicy` (`networking.gke.io/v1alpha1`) | GKE with Dataplane V2. The chart checks for the API; at most 50 addresses per policy |
  | `fqdn-openshift` | `EgressFirewall` (`k8s.ovn.org/v1`, one named `default` per namespace) with `dnsName` rules | OpenShift / OVN-Kubernetes. Red Hat recommends DNS names only for hosts whose addresses change rarely (re-polled by TTL, default 30 min), so the enforcement check re-runs on the watch |
  | `fqdn-calico` | `NetworkPolicy` with `destination.domains` | Calico editions that offer DNS policy (documented for Calico Cloud and Enterprise). The chart checks the field is accepted |
  | `fqdn-antrea` | Antrea `ClusterNetworkPolicy` with an `fqdn` egress peer | Clusters running Antrea |
  | `mesh-istio` | `outboundTrafficPolicy: REGISTRY_ONLY`, a `ServiceEntry` for the backend host, optionally through an egress gateway | Clusters already running Istio |
  | `egress-proxy` | The collector's only egress is the organisation's egress proxy (selector or CIDR). Exporters use it through the standard `HTTPS_PROXY` / `NO_PROXY` settings, and the proxy holds the hostname allow-list | Organisations that already run an egress proxy; works on any cluster |
  | `cidr` | A standard NetworkPolicy `ipBlock` list the team supplies (a backend's published ranges, or a private endpoint / PrivateLink address) | Backends with stable addresses; works on any plugin that enforces NetworkPolicy |
  | `external` | Enforcement outside the cluster: a cloud firewall (AWS Network Firewall, Azure Firewall FQDN rules, Google Cloud NGFW) or a corporate firewall. The engine renders deny-other-egress plus port 443; the team declares the firewall | Common on EKS, whose VPC CNI network policies have no hostname rules, and in regulated estates. Accepted only once the live enforcement check passes |
  | `unenforced` | No egress restriction beyond DNS and port 443 | Only with `telemetry.egress.acknowledgeUnenforced: true` |

  - **The portable future:** the Kubernetes network-policy working group's FQDN egress selector (NPEP-133,
    `DomainNames` in `ClusterNetworkPolicy`) is still experimental. When it reaches the standard channel and the
    main plugins implement it, it becomes the default, and the plugin-specific adapters remain for older clusters.
  - **Setup only offers what applies:** the factory detects which APIs the cluster serves and lists only those
    mechanisms, plus the universal ones (`egress-proxy`, `cidr`, `external`).

- **Fails closed:**
  - With no mechanism set, the engine **refuses to deploy telemetry**, with a message listing the mechanisms this
    cluster supports.
  - A plugin-specific mechanism whose API the cluster doesn't serve also refuses, and says which API is missing.
  - `unenforced` deploys, but the factory records it in the product's evidence and shows it on the product page
    as "telemetry egress not enforced". It blocks the **production** readiness tier (pilot is allowed), and it can
    be changed in one step.
- **Proven live, on every deploy and on the watch.** A served API proves the CRD is installed, not that the plugin
  enforces it, and one denied request proves little. So the engine runs an enforcement check from a short-lived
  pod with the collector's labels, after the policy is applied and read back:
  - **must succeed:** the backend host on its port (or, for `egress-proxy`, the proxy);
  - **must fail, each one:** another public hostname; a public IP literal (bypasses DNS-based rules); the
    backend's own resolved IP on another port; the cloud metadata address `169.254.169.254`; a pod in another
    namespace; and, for `egress-proxy`, a direct connection that skips the proxy.
  - The result (each case, pass or fail, the adapter, the cluster's plugin and version) goes into the evidence, so
    "enforced" is a measured fact, not a setting. Any must-fail case that connects fails the deploy, the same as
    a widening policy.
- **Production support depends on that check, per adapter.** An adapter is **supported for production** only
  once its live enforcement check has passed on a real cluster of that type (recorded in a support table in the
  docs: adapter, plugin, version, date, the evidence bundle). Until then it is **preview**: it may run, it shows
  "egress enforcement in preview", and like `unenforced` it blocks the production readiness tier. v0.1.0 claims
  production support only for the adapters O7 proves live; the rest ship as preview.
- **Local cluster:** the exporter is `debug`, so there's no backend egress at all. Deny-all egress (except DNS) is
  enforceable with standard NetworkPolicy, given a plugin that enforces it; the enforcement check proves that too.
- **Resources:** requests and limits are set; the `memory_limiter` processor and a bounded sending queue stop a
  noisy app from taking the collector down.
- **Image:** a minimal distribution built with the OpenTelemetry Collector Builder, holding only the components
  used (otlp and prometheus receivers; memory_limiter, batch, redaction, transform processors; otlp, otlphttp,
  prometheusremotewrite, debug exporters). It is pinned by digest and runs no unused extensions.

**TLS:**
- **Exporters to a backend** require TLS (`insecure: false`); the chart check fails otherwise.
- **In-namespace traffic** from app to collector is limited by the NetworkPolicy; mTLS is used where the cluster
  has a service mesh.

**Credentials:**
- Never in chart values or the collector config.
- The chart takes the **name of an existing Kubernetes Secret** (`telemetry.exporter.credentialsSecret`). The
  collector reads it as environment variables and the config refers to `${env:…}`.
- The factory creates that Secret at deploy from the product's secret refs (the secret store), and the evidence
  records the ref, never the value.
- The chart check fails if any value under `telemetry` looks like a literal credential.

### 3a. The tenant boundary (review of #218, second round)
The first hosted offer is one isolated factory per customer, and multi-tenant self-service comes later
([open-source-release.md](open-source-release.md#saas)). Factories may still share services: a telemetry backend, a
cluster, later a control plane. So telemetry carries a trusted identity, and isolation is enforced by the backend,
never by a query filter alone.
- **Identity on every signal.** The engine sets two resource attributes on every product and on the factory itself:
  `agent_factory.factory.id` (generated once at the factory's first start, kept in the store's meta, never chosen by
  a person, a chart or an app) and `agent_factory.product.id` (`Product.id`, stable across renames). `service.name`
  stays a readable name and is **never a lookup key**: two customers can both have a product called `kudos`.
- **Trusted, not app-asserted.** In `bundled` mode the factory's collector **overwrites** both attributes
  (resource processor, `upsert`) on everything it receives, so an app can't send as another product. In `existing`
  mode the pods set them from the engine's environment and the organisation's collector is theirs; the attributes
  then select within the tenant, and only the backend's isolation (below) separates tenants.
- **Export and readback bound to the tenant.** Every query the factory makes (real-data freshness, Test connection,
  trace lookup from an evidence manifest) selects by factory id and product id (plus `agent_factory.test_id` for
  tests), never by `service.name`. And every destination is isolated by the backend itself, one of:
  - **tenant-scoped credentials:** the export token writes only to this factory's tenant (a Grafana Cloud stack, a
    Datadog org, a Honeycomb environment, a multi-tenant Loki / Tempo / Mimir tenant behind an authenticating gateway
    that binds the tenant to the token, never a bare `X-Scope-OrgID` header), and the read token reads only from it;
  - **a dedicated destination:** a backend, or bundled-mode store, used by this factory alone.
  - Each query adapter declares which it relies on (`isolation: tenant-credential | dedicated`). A destination with
    neither, such as a read token that can see several factories' data, is **not used for readback**: Test
    connection stops at "Collector accepted", freshness shows "not verifiable from here", and setup says why.
- **The standalone install changes nothing visible.** It has one factory id from day one; its own context is the
  tenant. A tenant switcher and a shared control plane stay in the SaaS phase.
- **Tested with two factories on one backend** (`tests/test_telemetry_tenancy.py`, against a fake backend that
  enforces tenant-scoped tokens): factories A and B each have a product named `kudos`. A's freshness ignores B's
  data; A's Test connection never verifies on B's test records, even with a colliding test id; A's trace lookup of a
  trace id from B's manifest returns not found; A's read token on B's tenant is refused by the backend; an adapter
  without a declared isolation is refused for readback; and in `bundled` mode an app that sets another product's id
  has it overwritten.

### 4. Metric semantics are kept through the migration
- **New metric:** `http.server.request.duration`, the stable OpenTelemetry HTTP convention. It is a **histogram in
  seconds**, not a renamed counter, so the migration keeps request counting explicit.
  - Bucket boundaries: 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 s (the spec's
    advisory).
  - Attributes: `http.request.method` (unknown methods become `_OTHER`), `url.scheme`, `http.response.status_code`,
    `http.route` (the framework's route template only, never the raw path; left unset when unmatched) and
    `error.type`.
  - In Prometheus format it appears as `http_server_request_duration_seconds_bucket`, `_sum` and `_count`.
- **What the watch reads:**
  - Requests per status come from the histogram's **`_count`**, a counter of completed requests, the same
    semantics as today's `http_requests_total`.
  - The new pack version sets `pack.yaml` `metrics.counter: http_server_request_duration_seconds_count` and
    `status_label: http_response_status_code`. `operate.request_counts` already sums a counter over its label
    sets, so the availability arithmetic is unchanged.
- **Coexistence:**
  - Pack version N+1 emits **both**: today's counter unchanged, plus the new histogram. Its `pack.yaml` points the
    watch at the new `_count`.
  - Products still on version N keep today's counter, and the watch reads whatever their `pack.yaml` names.
  - The latency histogram the FastAPI pack exposes today (`http_request_duration_seconds`) is kept alongside for
    the same one version.
- **Retiring the old counter** (version N+2) needs all three:
  - every live product the factory watches reports the new series (a factory check lists the ones that don't);
  - dashboards and alerts the factory ships read the new names;
  - a product upgraded to N+1 has passed a watch period on the new series.
- **Bounded cardinality** (acceptance, in the pack tests and readiness):
  - 1,000 requests to distinct raw paths (`/items/1` … `/items/1000`) and one request with method `FOO` add no
    series beyond the declared route templates.
  - `FOO` is recorded as `_OTHER`.
  - No label carries a user id, query string or free text.
  - The label set per series is exactly the attributes listed above.
- **Delivery is proven with real requests, not a synthetic metric** (a test metric can arrive while request metrics
  are missing, e.g. a scrape at the wrong path). For **both packs in both modes** (`bundled`, and `existing` with
  each `metrics.delivery`), N real requests to the app's own routes must show up at the destination as
  `http_server_request_duration_seconds_count` (and, during coexistence, `http_requests_total`) increased by N for
  this factory's and product's ids (§3a), scraped or pushed from the pack's declared path.

### 5. The factory becomes a well-behaved service
- **Logs:** JSON under the contract.
- **`/metrics`:** changes running and queued, station durations, agent calls, cost, denials, gateway spend, waits,
  and `telemetry_redaction_failures_total`.
- **Traces:** change → station → agent call → tool call.
  - Agent spans use OpenTelemetry's `gen_ai.*` conventions (`gen_ai.operation.name`, `gen_ai.request.model`,
    `gen_ai.usage.input_tokens` / `output_tokens`).
  - Those conventions were still "Development" in late 2026, so the semconv version is pinned and renames are
    absorbed in the collector.
- **Export** is off by default, and on with the standard `OTEL_*` variables.

### 6. Setup and everyday use (usability)
**Setup: one card on the product's page, three plain choices:**
- **Send to our existing collector:** an endpoint, plus an optional Secret name for its token.
- **Run a collector for this product:** a backend picker (Grafana Cloud, Datadog, Elastic, Honeycomb, other
  OTLP), the endpoint, the Secret name, and the egress mechanism.
- **Off** (the default).

**Questions in the product's own words, one at a time, each with a recommended answer:**
- "Does your cluster already collect container logs?" sets `logs.ingest`.
- "How should we limit where telemetry may go?" offers the egress mechanisms that this cluster supports. The
  factory detects Cilium and existing proxies where it can, and lists only what applies, each marked supported or
  preview (§3).
- In `existing` mode only: "Does your collector scrape metrics, or should the app send them?" sets
  `metrics.delivery` (`push` recommended).

**Test connection says what it proved, hop by hop.** An OTLP acknowledgement covers one hop: a collector can accept
all three signals while its export to the backend fails. So each signal (log, span, metric, each tagged with one
`agent_factory.test_id`) shows up to three steps, and never claims more than it checked:
1. **Sent:** the app-side exporter connected (DNS, TLS, egress). A failure names the step.
2. **Collector accepted (time):** the OTLP response was a success with no `partial_success` rejections. A
   rejection count, or an error status, is a failure at this hop.
3. **Verified at destination (time):** only after the factory reads the test records back from the backend
   (a query adapter per backend in the picker, using a read-only token named by its own Secret reference and
   capability, never the write token) or an equivalent confirmation the backend offers. In `bundled` mode the
   factory also reads its collector's own exporter counters (`otelcol_exporter_sent_*` /
   `otelcol_exporter_send_failed_*`) so a backend rejection is named at the right hop ("Collector accepted ✓ ·
   backend rejected: 401") even without a query. A backend with no query adapter shows "Collector accepted;
   not verifiable from here: check <backend link>", never a green "verified".
- The result, step by step, is saved as evidence.

**Test time and real-data freshness are separate.** A passing test proves the path once; it says nothing about the
app's own data now.
- **Last test:** when Test connection last ran, and how far it got.
- **Last real data:** the newest record for this factory's id and the product's id at the destination (§3a), excluding
  test records; when no query adapter exists, the newest non-test export from the bundled collector's counters, labelled
  "sent, not confirmed at destination". `existing` mode with no query adapter has no real-data line and says so.

**Status, at a glance on the product page** (app health and telemetry health are separate lines, so a collector
outage never reads as an app outage):
- "Telemetry: sending to Grafana Cloud via your collector · real data 2 minutes ago · last test passed yesterday".
- Or a single amber line with the fix, such as "egress not enforced: choose a mechanism", "egress enforcement in
  preview", or "no real data for 15 minutes: test connection".

**The same choices for the factory itself,** in its settings.

**Docs:** one short page per common backend (endpoint, token, egress), written as steps.

### 7. Evidence stays the record
- Telemetry is a copy for operations; the sealed ledger stays the source of truth.
- Each change's trace id goes into its evidence manifest, so evidence and traces can be joined.
- Hidden scenarios never leave the factory.

## Slices (AI build and check time)
Estimates are agent build time plus automated checks, not Saravana's time. His part is one live validation at the
end.

| # | Slice | AI build | Checks | Done when |
|---|---|---|---|---|
| O1 | Log contract (JSON schema); both packs on it, with trace context; OTLP logs bridge; the `logs.ingest` switch (one ingestion path) | ~2.5 h | ~45 min (pack unit and e2e tests) | A schema test passes on log lines from both packs; a line written inside a request span carries its `trace_id`; with `ingest: otlp` each line reaches a fake OTLP receiver exactly once; with `ingest: cluster-agent` none does and stdout lines still carry trace ids |
| O2 | Factory JSON logs on the contract + `/metrics` | ~1 h | ~15 min | The engine's log lines pass the schema test; `/metrics` passes `promtool check metrics` |
| O3 | Factory traces (`gen_ai.*`); trace id in the evidence manifest; **redaction boundary** (§2) in the factory and both packs | ~3.5 h | ~45 min | Serialized OTLP payloads captured at the exporter boundary (a fake OTLP server), plus stdout log lines, contain none of the planted values: a resolved secret, a virtual key, a bearer token, prompt text, tool-result text, an exception whose message holds a secret, baggage holding a secret, and an environment value in a resource attribute. A forced redactor failure drops the record and increments `telemetry_redaction_failures_total`. Undeclared attribute keys are absent |
| O4 | Both collector modes (`existing`, `bundled`) for products and the factory; the bundled collector as a factory-owned release (security settings, ingress NetworkPolicy, Secret by reference, TLS, minimal pinned image); **engine-owned egress adapters** with API detection, fail-closed rendering and readback against the expected policy; the live enforcement check; metrics scraped at the pack's path, and `metrics.delivery` in `existing` mode | ~8 h | ~2 h (`otelcol validate`; chart tests on the collector chart for each mode; adapter render and owner-scoped readback tests; the enforcement check on kind) | `otelcol validate` passes on every shipped config. `existing` deploys no collector, points the pods at the given endpoint and limits their egress to it. Collector chart tests fail on a relaxed security setting, a `hostPath`, a mounted token, missing limits, a missing ingress policy, `insecure: true`, or a literal credential. **A product chart that renders any telemetry egress rule or adapter kind is still refused by `widening`** (a case per adapter kind in `tests/test_app_network.py`). The engine refuses with no egress mechanism, and with a plugin-specific mechanism whose API is absent; `unenforced` deploys only when acknowledged and blocks the production tier, and so does a preview adapter. Readback passes on the factory's own policy and re-applies a drifted one; removes an egress object of this release; **holds, naming it, and never deletes** a foreign namespaced or cluster-scoped object that widens the product's egress; ignores a foreign one that doesn't, and another factory's policy. The bundled collector overwrites `agent_factory.factory.id` and `agent_factory.product.id` (§3a). On kind, the enforcement check's allowed case connects and every must-fail case (other host, IP literal, other port, metadata address, other namespace) is refused; a deliberately open policy makes the deploy fail. The scrape target is `/metrics` for `fastapi-service` and `/api/metrics` for `nextjs-web`, read from `pack.yaml`. On kind with a fake backend, N real requests show up as the request count up by N for both packs in `bundled`, `existing` + `push` and `existing` + `org-scrape` |
| O5 | `http.server.request.duration` alongside the old counter in pack version N+1; the watch reads `_count`; readiness and `assess.py` updated; retirement check | ~1.5 h | ~45 min | The watch gives the same availability from the old counter and from the new `_count` on the same traffic; the cardinality test (§4) passes; readiness passes on N and N+1 products; the factory lists products still on the old counter |
| O6 | Setup card, guided questions, hop-by-hop Test connection with backend query adapters, the separate last-test and real-data lines, status line, per-backend docs (§6) | ~4 h | ~1.5 h (UI tests, an accessibility check, a screenshot review of the card at phone and desktop widths; Test connection against a fake collector and a fake backend; the two-factory tenancy test) | A new user can choose each mode and run Test connection without leaving the product page. Against fakes: an accepted-and-stored signal shows "Verified at destination"; a `partial_success` rejection fails at "Collector accepted"; **a collector that accepts while the backend rejects** shows "Collector accepted ✓" and names the backend hop as failed; a backend with no query adapter never shows "verified". The last-test time and the real-data time are shown separately, and test records never count as real data. Every query selects by factory id and product id, and the two-factory test in §3a passes; an adapter without a declared isolation is refused for readback |
| O7 | Live validation (Saravana) | — | ~1 h 30 min of a live run | From the UI: one `fastapi-service` and one `nextjs-web` product, each in `bundled` mode and then in `existing` mode (pointed at an in-cluster stand-in for the organisation's collector), and the factory in `existing` mode. Test connection reaches "Verified at destination" for each. **Failure case:** with the bundled exporter's backend token revoked, the collector still accepts and the page names the backend hop as failed. **Metrics:** after N real requests per product, the destination shows the request count up by N for both packs in both modes. A log line's trace id matches the evidence manifest. The live enforcement check passes on this cluster's adapter (allowed host reached, every must-fail case refused, another namespace can't reach the product's collector), and that adapter is recorded as supported for production. **Tenancy:** a second, throwaway factory sends to the same backend with its own tenant-scoped token and a product of the same name; neither factory's freshness, Test connection or trace lookup sees the other's data |

Total: about 20.5 h of AI build and about 6 h of automated checks, plus the one live validation (revised from
17 h and 4.5 h for the reviews of #218: the tenant boundary and owner-scoped readback, engine-owned egress with readback, the enforcement check, hop-by-hop Test
connection, metrics delivery per pack and mode).

## Not in scope
- Running an observability backend for clients. They bring their own; the factory only ships collector configs.
- A node-level log agent. That is the cluster platform team's choice; the factory never deploys one.
- Profiles (the OpenTelemetry profiling signal).

## Sources
- [OpenTelemetry HTTP metrics conventions](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)
  (`http.server.request.duration`: stable histogram in seconds, advisory buckets, `_OTHER`, low-cardinality
  `http.route`).
- [Handling sensitive data](https://opentelemetry.io/docs/security/handling-sensitive-data/).
- [Collector hosting best practices](https://opentelemetry.io/docs/security/hosting-best-practices/): no privileged
  access unless the data is in a privileged location; narrow volume mounts; authenticate server-like components;
  protect config files holding credentials.
- [Kubernetes NetworkPolicies](https://kubernetes.io/docs/concepts/services-networking/network-policies/):
  destinations by pod or namespace selector or IP block; the cluster's network plugin must enforce them.
- [Cilium DNS-based policies](https://docs.cilium.io/en/stable/security/dns/) (`toFQDNs` with `matchName` /
  `matchPattern`, enforced through Cilium's DNS proxy).
- [GKE FQDN network policies](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/fqdn-network-policies),
  [AKS FQDN filtering](https://learn.microsoft.com/en-us/azure/aks/container-network-security-fqdn-filtering-concepts),
  [OpenShift EgressFirewall](https://docs.redhat.com/en/documentation/openshift_container_platform/latest/html/network_security/egress-firewall),
  [Calico DNS policy](https://docs.tigera.io/calico-cloud/network-policy/domain-based-policy),
  [NPEP-133 FQDN egress selector](https://network-policy-api.sigs.k8s.io/npeps/npep-133-fqdn-egress-selector/).
- [Collector configuration best practices](https://opentelemetry.io/docs/security/config-best-practices/):
  encryption and authentication, secrets kept out of config via environment variables, minimal components.
