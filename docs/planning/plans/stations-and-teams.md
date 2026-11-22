# Plan: stations as team members, owned by teams, integrated with their tools

Status: direction and order decided by Saravana 2026-11-21 (architect session). Nothing here is built. J4 and J5
ran live on 2026-11-21, so the freeze has lifted; slice 0 comes first, slices 1–6 after the v0.1.0 release
(roadmap "Next, in order"). It refines phase **T. Team delivery** and composable-SDLC steps
2–4 ([composable-sdlc.md](composable-sdlc.md)) and builds on the station contract
([ADR-0037](../../adr/0037-station-contract.md)), check providers ([ADR-0035](../../adr/0035-check-providers.md))
and J4's Operate watch ([ADR-0049](../../adr/0049-run-maintain-retire.md)). Where this page and an ADR disagree,
the ADR wins.

## Why
Saravana's direction (2026-11-21): each station acts as a separate member of the team. It takes input and produces
output that another station can take as input. It is either built into the factory or integrated with an external
tool or service. Each station can be invoked on its own, and is owned by a team that works with it directly:
- a **design** station that draws on wikis, repos and existing knowledge, and asks experts, proposes a design; a
  reviewer critiques it, and they agree on a better solution;
- a **develop** station whose code is reviewed for modularity, reuse and security;
- a **test** station that can work on the output of a SaaS such as SonarQube;
- a **security** station that a security team talks to about practices (encryption at rest and in transit,
  dependencies, supply chain), and that adds recommendations or raises security-fix work, with third-party tools
  such as Snyk;
- an **operate** station whose aim is uptime and reliability under failure, fault tolerance and fast recovery,
  integrated with SaaS such as Grafana Cloud or Datadog.

Building and operating are kept in balance. A service or product is owned by a **team**, never by one person, so
ownership has no single point of failure: each station stands for a role, its responsible team and members, and
its tools.

## What exists and what's missing (main `56d7f16`)
| Idea | State |
|---|---|
| One station's output is another's input | ✅ ADR-0037: `needs`/`gives` typed, versioned artifacts, schema-checked at every handoff, sealed in the artifact ledger |
| Human mode per station (autonomous · notify · approve · discuss) | 🟡 designed (composable step 2); one-off gates only today |
| A team owns a station or a product | ❌ roles map to people per product; no Team, rota or escalation |
| A station as a standing entity (a team's queue, policies, knowledge, conversation across products) | ❌ a station exists only inside a change's run |
| Integrations at every station | 🟡 scan providers only (ADR-0035); no knowledge sources, verify or operate SaaS; no secret store |
| Designer and reviewer critique and agree | 🟡 spec review and architect alignment; no bounded critique loop |
| Operate equal to build | 🟡 J4: watch, incident, backups, drill; no responder, no external monitoring |
| Invoke one station on its own (design only → wiki / Jira) | ❌ composable steps 3–4 |
| One record per product that every role can read (what we own, who, what state, what needs attention) | 🟡 product page, readiness tier, work ledger, Outcomes; no role views, no source or sync time per fact |
| The factory's own continuity (products keep running and alerting while it is down; it can be restored) | 🟡 `make backup` / `make restore` (encrypted, `docs/upgrading.md`); no RPO / RTO, no tested factory restore; local products run in the factory's own dind |

## Decisions (Saravana, 2026-11-21)
1. **How stations hand off.** Stations communicate through typed, versioned artifacts and structured requests
   recorded in the ledger. Handoffs reference immutable artifact revisions and declare their contract version.
   Conversations inform work; recorded artifacts govern execution. Running each station as its own service is a
   deployment choice, not another handoff model.
2. **Who watches production: external first.** Detection and paging run in an always-on external system (Grafana
   Cloud first), fed with alerts and SLOs the factory pushes; the factory receives alerts by webhook, responds and
   keeps the record. The factory is never the only monitor of a product (it sleeps, restarts and upgrades).
3. **First integration per station, one each, to prove the pattern three times:** Snyk (secure), SonarQube /
   SonarCloud (verify), Grafana Cloud (operate).
4. **Order:** as below (0 → 6), with J4/J5 and the freeze first.

## The model
A **station** is a first-class entity:
1. **Contract:** what it needs and gives (ADR-0037), unchanged.
2. **Executor:** an agent, an engine check, a third-party integration or a person. The executor is replaceable
   behind the contract (the model runtime seam discussed 2026-11-05 is one more executor choice).
3. **Integrations:** providers chosen per org. A provider **normalises** its tool's output into the factory's
   report schema (severity, location, fix, accepted risk), so the engine's policy decides, never the vendor's
   quality-gate colour. Every integration has an open-source default (Trivy, Semgrep, SonarQube Community,
   Prometheus / Grafana OSS) so the factory works without licences.
4. **Owner team:** a Team entity (members, rota, escalation). RACI per station: the team is **responsible**; one
   person from the rota is **accountable** per decision; delegation, and a deadline that escalates.
5. **Mode:** autonomous / notify / approve / discuss.
6. **Workspace:** the owning team's standing view of its station across products: queue, policies (for example the
   security team's encryption and supply-chain standards), knowledge, conversations and findings.

A **product** has a **service team**: product owner, tech lead, security, QA and SRE roles, each mapped to a team.

### The product record (from the PR #188 review, 2026-11-22)
Station workspaces show a team its queue; the **product record** shows anyone with access one product's whole
life. It is the factory's own read view, built from what the engine already holds, **not a service catalog or
an IDP** (that stays a non-goal, [integrations.md](../../design/integrations.md)): a team with Backstage or Port
gets the record pushed there, with a link back.
- **One record per product:** lifecycle state (building, live, retiring, retired) and readiness tier; service team,
  rota and the accountable person now; repos and environments; dependencies; integrations with what each may
  read or send (data and permission scope); SLOs and runbooks; latest sealed evidence; open incidents and upkeep;
  recovery posture (RPO, RTO, last backup, last passed restore drill, open risks); improvement and retirement status.
- **Every fact shows where it came from and when:** sealed by the engine, imported from a tool (with its last
  sync time; stale past the tool's interval), or asserted by the owner. Imported and asserted facts never raise a
  readiness tier.
- **Role views of the same record**, filtered by permission: engineering and product (changes, ledger,
  roadmap), security (findings, accepted risks, data scopes), operations / SRE (SLOs, incidents, recovery
  posture), business / executive (state, owner, cost per change, risk summary).
- Built in slice 4 with the station workspaces; recovery posture is filled by slice 5's `recovery@1`.

## Rules (from the 2026-11-21 challenge)
- **Structured requests, recorded.** A station asks another for work with a request artifact (from, to, reason,
  artifacts it refers to, deadline). A request inside a running change follows the workflow's routes
  (`on_fail`, repair); a request across changes (a security fix, a bug from an incident) becomes a work item in the
  product's ledger, linked to what raised it. A station never calls back into another station's running work, so
  requests can't form loops.
- **Immutable revisions.** A handoff names the artifact's content digest and its contract version; a run keeps the
  versions it started with (ADR-0037 §2).
- **Critique is bounded and rubric-based.** Designer and reviewer get at most 2 rounds. The reviewer critiques against
  the pillars and the org's standards. The output is the agreed decisions **plus the open risks still disputed**,
  which go to the station's accountable person. Two agents agreeing is not evidence: the evaluation measures whether
  critiqued designs cause fewer repairs downstream.
- **People have deadlines.** A question to an expert or an approval waits with a deadline and a stated default,
  then escalates along the rota (the waiting-state model, `waiting.py`).
- **Credentials and data boundaries before SaaS.** No integration ships before the secret store (ADR-0038,
  [secret-store.md](secret-store.md)). Each integration declares what leaves the factory (code, findings,
  telemetry); sending a product's code to a SaaS is the owning security team's setting.
- **Build serves operate.** Build stations must give operability artifacts (`slo@1`, `runbook@1`, `alerts@1`,
  `dashboard@1`, and `recovery@1`: RPO, RTO, backup schedule and the restore drill that proves them); the
  Operate station holds a production-readiness veto over them. The production tier needs a restore drill that
  passed within the RPO's window.
- **The life is deliver, operate, recover, improve, retire.** Backup, restore and disaster recovery, and the
  improvement loop (incidents, upkeep and the ledger becoming changes), are named stages of a product's life,
  each with an owner and a measure, not parts of "operate".

## Order (decided 2026-11-21)
Each slice ends with a live run and sealed evidence, like the J-milestones.

| # | Slice | Why here |
|---|---|---|
| 0 | Incident response slice (the J4/J5 live run itself ✅ 2026-11-21) | Restores service without a development cycle; Respond is the first Operate station (below) |
| 1 | Secret store (ADR-0038) | Every SaaS integration needs it |
| 2 | Teams, rota, RACI and modes on every station (composable step 2) | Ownership before tools |
| 3 | Provider framework for every station; Snyk (secure), SonarQube / SonarCloud (verify), Grafana Cloud (operate: SLOs and alerts pushed, alerts back by webhook) | Proves the pattern three times, normalised |
| 4 | Station workspaces and discuss mode, security posture first; the product record and its role views | A team needs a home once it owns something; every role needs one place to see a product |
| 5 | Operability artifacts (`recovery@1` included) and the readiness veto | The build / operate balance, enforced |
| 6 | Start anywhere and station-as-a-service; design pack with knowledge sources (repo, wiki) and the bounded critique loop; stories exported to Jira / GitHub Issues | "Design only" for product owners (composable steps 3–4) |

## Slice 0: incident response (decided by Saravana 2026-11-21)
J4.6 today fixes an incident with a full change (design → … → deploy). Restoring service and fixing the root cause
are separated instead:
- **Respond** is an Operate operation the engine runs (like backup and drill, `operations.py`), not a station in a
  change: confirm → **snapshot first** (pods, events, failing pods' log tails, `helm history`, live vs recorded
  release) → restore → verify → classify. No model on the restore path.
- **Ladder**, each step at most once per incident, at most 3 actions per product per hour, never while a change
  deploys or the product is retiring, every action through ADR-0046's fail-closed checks:
  1. live state differs from `Product.release` (replicas, digest, revision) → re-apply the recorded release;
  2. state matches but pods crash or aren't ready → restart once;
  3. the release itself fails → roll back to the previous healthy release;
  4. data restore → always the owner, product name typed again, the backup's age shown as the data that will be lost;
  5. the dependency check fails → hold as `dependency`.
- **Diagnosis** is an ops-pack station template (phase `observe-act`, `report.incident-diagnosis@1`) that reads the
  engine's redacted snapshot file only: no kubeconfig and no route to the cluster in the sandbox (ADR-0048). One
  capped call; if the model is unavailable, restore still runs.
- **Code defect: the engine proves, the agent suggests.** Proven (survived a restart, cleared on rollback) → a bug
  change is raised, linked to the incident, validated off production. Operational (re-apply restored it, release
  unchanged) → recorded as such. Suspected (only the agent says so) → a bug suggestion on the Next iteration card.
- **States** `open → mitigating → restored → resolved` (+ reopened). Restored: k healthy readings **since the
  action**, read every 10 s while the incident is open (today's window since delivery still holds the outage
  readings). Resolved: the linked fix delivered and read healthy, or operational with no recurrence for 24 h.
  The same signature within that time reopens it and moves one step up the ladder.
- **DORA:** time to restore = first bad reading → restored; time to detect recorded separately; an incident counts
  against a change when rollback fixed it or it began within the watch window of that change's deploy.
- **Freeze:** an amendment to ADR-0049 §4, not a new ADR. Minimal slice (~1.5–2 days): snapshot first, re-apply,
  restart once, the restored window, the states, DORA timings, the incident ↔ change link. Rollback to the previous
  release, the diagnosis agent and raised bug changes come next.
- **Never undo a newer release (from the PR #188 review, 2026-11-22).** The factory may be restored from a
  backup older than the last deploy, so its `Product.release` can be stale. Re-apply and rollback compare
  first with the live `helm history`: a live revision newer than the recorded one holds the incident as `stale
  record` for the owner, never re-applies over it. After a factory restore, `operate.respond` runs as `ask` for
  every product until a watch reading has reconciled its recorded release with the live one. Part of the
  minimal slice.
- **To verify live:** Helm's three-way merge puts replicas back on a rollback to the same revision; if an HPA owns
  replicas, "scale to the release's replicas" is needed instead.

**Decided (Saravana, 2026-11-21):**
1. **When:** J4 and J5 had already run live on today's path, so slice 0 is the first item after the journey
   (roadmap "Next, in order", phase A), not a gate on J4.
2. **`operate.respond` default `auto`** for re-apply and restart, within the ladder's limits (each step once per
   incident, at most 3 actions per product per hour, fail-closed checks). An org may set `ask`.
3. **Automatic rollback to the previous healthy release when `schema_compat` says the change is additive.** A
   verdict of `unsure` or breaking always asks the owner, as data restore always does.

## Factory continuity (from the PR #188 review, 2026-11-22)
Team ownership and external monitoring remove two single points of failure (one person, the factory as the only
monitor). The factory itself is the third.
- **Invariant:** customer products keep running and stay monitored while the factory's control plane is down.
  Products run on their own clusters, and detection and paging run outside the factory (decision 2), so a factory
  outage stops changes and responses, never service or alerts. Products on the local kind cluster live inside the
  factory's own dind and stop with it. They are capped at the **pilot** tier; production needs a cluster the
  factory host doesn't run.
- **What must be restorable:** the factory database (products, changes, ledger, workflows), the evidence
  artifacts and manifests, the auth database, integration configuration and secret references (never values),
  and hidden scenarios: what `make backup` holds today, plus integration config once slice 3 adds it.
- **Recovery targets (proposed, set against a first drill):** RPO ≤ 1 hour (scheduled encrypted backups,
  stored off the factory host); RTO ≤ 1 hour to a running factory with its ledger verified against the sealed
  manifests.
- **Tested before automation:** `operate.respond` takes its `auto` default only on an install whose factory
  restore drill has passed (restore to a separate host or folder, manifests verified, records reconciled with
  the live releases). Until then it runs as `ask`. The drill is repeated after every upgrade that changes the
  data format.

## Open questions (later slices)
- Team model: one Team per role, or teams with several roles (a platform team owning operate and deliver)?
- Station workspaces across products: who may see another product's findings (security team yes, others by
  membership)?
- Station-as-a-service: same engine with an API entry per station, or stations deployed separately (decision 1 says
  this is deployment, not design)?
- Datadog as a second operate provider, and which pager (Grafana OnCall, PagerDuty) first.
