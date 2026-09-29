# ADR-0015: Readiness checks, order preflight, archiving and 20 app ports

**Status:** accepted · 2026-09-29 · design: [integrations.md §6](../design/integrations.md)

## Context
Until now, a run found out that something was missing only when it reached the
failing station: no model credential, dind or the cluster down, the sandbox not
built, no free app port. By then it had already spent model tokens on intake,
design and build. The Integrations page ran provider checks live on every page
load. Nothing gated an order. Five app ports filled up quickly, and delivered
apps could not be removed without shell access.

## Decision
1. **Readiness checks** (`engine/preflight.py`). One view per product line,
   covering:

   | Area | What is checked |
   |---|---|
   | agents | model credential; agent sandbox |
   | integrations | each integration's provider `check()`, plus whether its credential reference resolves |
   | deploy | a free app port that the cluster actually maps |
   | scan | scanner configured |
   | workflow | the active workflow has the readiness station |

   For `local`, `check()` means: dind answers, the kind API is `/readyz`, and
   which NodePorts the cluster maps. Checks are read-only, take seconds, and
   return ready, degraded or failed with reasons.
2. **When checks run:** at startup, every `preflight.interval_minutes` (15),
   and on demand (`POST /api/preflight`, the **Check now** button).
   - Results are cached. Integration checks are reused for
     `preflight.max_age_seconds` (60).
   - In-memory checks (model, sandbox, ports) are recomputed on every read,
     so the header pill and gates are never stale.
3. **Preflight gate:**
   - New orders, feedback (new iteration) and resume run the gate first.
   - A **failed** check that guards the action refuses it with HTTP 409 and
     the failing checks listed, before any work or model usage.
   - **Degraded** never blocks. Examples: sandbox still preparing, no publish
     credential, workflow without readiness.
   - A publish-only integration never blocks delivery.
   - The app-port check guards new orders only.
4. **Archive** (`POST /api/orders/{id}/archive`, creator or admin):
   - The deploy provider's `undeploy()` runs; for `local` that is helm
     uninstall and delete the namespace.
   - Remaining sandboxes are removed, the port is freed, and an audit event
     is added to the order's latest run.
   - The product repo, runs, events and evidence are kept. Archived orders
     accept no new iterations.
   - If undeploy fails, the order and its port stay as they were.
5. **20 app ports:** NodePorts 30080–30099 → `localhost:8081–8100`. They are
   set in kind-config, compose, `node_ports: 30080-30099` (a range is now
   accepted) and the egress allowlist.
   - kind cannot add port mappings to a running cluster, so ports are handed
     out only if the cluster really maps them.
   - The readiness check says when `make reset-cluster` would add the rest.

## Consequences
- A broken dependency costs seconds, not a run: "no free port", "cluster
  down" or "no model credential" show before intake starts.
- Every provider type must implement `check()` and `undeploy()`. Remote
  providers (phase 5) plug into the same page and gate.
- The header shows one **readiness** pill; the Integrations page shows every
  check with its reasons and what it blocks.
- Existing installs keep 5 working ports until `make reset-cluster`. Apps
  deployed before the reset must be re-delivered, via a new order or feedback.
