# Vision and goals

Status: **proposed 2026-11-21** (architect session, product-owner view), for Saravana's decision. Until he decides,
[master-plan.md](master-plan.md) §1 stays the agreed vision; once he decides, this page replaces it and the
master plan points here. Evidence behind every claim below:
[../research/market-landscape-2026-11-21.md](../research/market-landscape-2026-11-21.md) (market and competitors,
sources dated) and the repo itself (what is proven).

## Vision
**Teams let agents build and run their software, because they can prove at any moment what was done, why, and
that it worked.**

## Mission
A self-hosted factory in which every stage of a product's life (design, develop, test, secure, deliver, operate,
retire) is a **station owned by a team**, worked by agents and by the tools that team already uses, and **judged
by an engine that seals the evidence**. The record stays with the team that owns the product.

**Tagline:** *Own the evidence, not the agent.*

## Positioning
For **engineering leaders in security-conscious and regulated organisations** who want agents to deliver and run
software but can't let them act unchecked, the factory is a **self-hosted, open-core control and evidence plane
for the whole product life**. It orchestrates agents and third-party tools through team-owned stations, and it
seals an engine-judged record from the first request to retirement.

Unlike coding agents (Cursor, Devin, Factory.ai, Codex) and SaaS DevOps platforms (GitHub Agent HQ, GitLab Duo,
Harness), which stop at "a person approves the PR" and keep their audit logs inside each vendor, the factory judges
the **outcome**, not the agent's account of it. It covers operate and retire, and the client keeps the record.

Why now (forecasts, not measurements):
- DORA 2025 finds AI raises throughput while delivery instability keeps rising.
- 60% of developers block agents from unapproved system changes (Stack Overflow, May 2026).
- Gartner (18 Nov 2026) forecasts:
  - 70% of teams on eval-driven development by 2028;
  - 70% of enterprises sandboxing agentic workflows by 2030;
  - 60% of regulated enterprises moving AI workloads to local infrastructure by 2028.
- The UK NCSC's "vibe coding spectrum" (Jun 2026) ties oversight to the risk of the code, which is what the change-risk
  policy and readiness tiers do.

## Who it is for
| Who | What they get | How they meet it |
|---|---|---|
| **Engineering leader / CTO** (buyer) | Agents delivering and running software under policy, with evidence an auditor can read; cost per change known | A consulting engagement that leaves the factory installed |
| **Station-owning teams**: product, security, QA, SRE (users) | Their station: its queue, policies, tools and conversation, across every product | One station preset first (for example Test + Security), then more |
| **Product owner** (user) | Design and stories without touching code (design-only preset) | Start-at-any-phase entry |
| **The consulting practice** (channel, first customer) | Its own delivery tool and the credibility of a working, open product | Every engagement |

## What makes it different
1. **The engine judges, the agents work.** Hidden scenarios the builder never sees, failure classes, diff-based change
   risk, a safety floor nobody can remove, and readiness tiers (demo, pilot, production) computed from evidence,
   never claimed by an agent.
2. **One sealed record across the whole life**, including **operate and retire**. No competitor in the research
   claims either.
3. **Stations owned by teams.** A team is responsible and one person from its rota is accountable per decision, so
   no single person is a point of failure.
4. **Integrate, don't rebuild.** Snyk, SonarQube, Grafana Cloud and other vendors' agents work as stations. Their
   findings are normalised so the engine's policy decides. Production is watched externally first.
5. **Self-hosted, open core (Apache-2.0), any model**, with data boundaries the owning team sets.

## Principles
The seven principles in [master-plan.md](master-plan.md) §2 stand, with one change proposed:
- **Principle 7, "Ready for, proven, owned": owned by a team, not a named person.** Each product has a service
  team. Each role (product owner, tech lead, security, QA, SRE) maps to a team. The team is responsible, and one
  person from its rota is accountable for each decision, with a deadline that escalates
  ([plans/stations-and-teams.md](plans/stations-and-teams.md)). This resolves the conflict between principle 7
  ("a named person") and the 2026-11-21 direction ("owned by a team, never by one person").

## What it is not (non-goals)
- **Not a better coding agent.** It drives coding agents; it doesn't compete with Cursor or Devin on writing code.
- **Not an app host or a vibe-coding builder.** Products run on the client's clusters, not on ours.
- **Not an observability or root-cause engine.** Grafana, Datadog and the AI SRE tools detect and investigate. The
  factory restores service from recorded releases, raises fixes and keeps the record.
- **Not a developer portal or IDP.** It plugs into one if a team has it.
- **Not hosted SaaS** until there is a team to run one (master plan §6).
- **Never autonomous without policy.** The policy floor, change risk and the refusal of harmful work stay on.

## North-star metric
**Proven changes per month:** delivered changes whose requirements were all verified live by hidden scenarios,
that reached their stated readiness tier, and caused no incident in the 7 days after delivery. It counts what
the factory exists for (work that is done and shown to be done), not activity. Its parts come from
[../outcomes.md](../outcomes.md) and ADR-0049's incidents.

## Goals
**The first open-source release, v0.1.0, is public by 2026-12-31** (decided by Saravana 2026-11-21). AI builds and
reviews; Saravana validates the outcome of each goal. More integrations follow after the release. The week-by-week
plan, the open core / licensed / SaaS split and the release blockers are in
[plans/open-source-release.md](plans/open-source-release.md).

**By 2026-12-31: v0.1.0**
- **G0. Baselines (by 2026-11-29).** Nothing in [../outcomes.md](../outcomes.md) has a recorded baseline. Record
  autonomy, change failure rate, lead time p50/p90, cost per delivered change (failures included), requirements
  verified live and share at Level 3 from the live runs so far.
- **G1. Prove the whole life (by 2026-11-29).**
  - J4 and J5 run live; the golden journey is complete and the freeze lifts.
  - One product reaches the **production** tier, with an alerting availability target and a passed restore drill.
  - An incident is restored without a development cycle, in **under 5 minutes** for drift and crash classes (slice 0
    in [plans/stations-and-teams.md](plans/stations-and-teams.md)).
  - A retired product leaves a sealed decommission record.
- **G2. Make "sealed" verifiable by someone else (by 2026-12-06).**
  - The security assessment's high findings are closed, and its open mediums are scheduled.
  - Evidence manifests are **signed**, not only hashed.
  - A **verifier** anyone can run against a downloaded evidence bundle.
- **G3. Open core and licensed edition from one repo (by 2026-12-20).**
  - The LICENSE and the licensed folder, with the CI guard.
  - The extension seam and the licence key.
  - The first paid features: SSO, audit export to a SIEM, customer KMS signing, a compliance report pack.
- **G4. Release (by 2026-12-31).**
  - Licence audit, name and trademark, DCO, docs.
  - A release rehearsal from the public images.
  - v0.1.0 tagged and signed.
  - The SaaS design written.

**After v0.1.0 (2027, dates set then)**
- One external pilot: UK regulated or public sector through consulting, one station preset (Test + Security) with
  SonarQube or Snyk, production watched by Grafana Cloud, change failure rate and cost per change reported before
  and after.
- Teams with rota and RACI beyond one team, the secret store and the provider framework.
- Concurrent changes and Postgres; station workspaces and discuss mode.
- Any model, qualified per station.
- Single-tenant hosting through consulting, then multi-tenant SaaS.
- The first outside contribution.

Targets to hold across the goals (proposed; set against the G0 baselines):

| Measure | Target |
|---|---|
| Requirements verified live, per delivery | 100% |
| Autonomy (no unplanned human touch) | ≥ 80% of deliveries |
| Change failure rate | ≤ 15% |
| Cost per delivered change, failures included | ≤ $10 in model spend (both meters agreeing) |
| Lead time p50, feature change | ≤ 60 minutes |
| Time to restore, operational incidents | < 5 minutes |

## Risks to the vision
- **GitHub absorbs the governance plane.** Agentic Workflows (GA 2026-10-28) and Agent HQ already provide policy,
  audit and sandboxing. The answer is what GitHub doesn't do: operate and retire, engine-judged verdicts, and
  self-hosted evidence that spans vendors. Supporting a second forge (GitLab) reduces the dependency.
- **OpenHands or GitLab moves into the same quadrant.** OpenHands already owns "self-hosted open-source agent".
  The factory must not compete on that; it competes on the lifecycle and the evidence.
- **The name.** "Agent Software Factory" collides with Factory.ai, Tessl ("build your software factory"), Port's
  "AI Software Factory Summit" and Warp. Choose before the first public tag (G4).
- **Trust gaps in a trust product.** One incident defines a brand (Replit, July 2025). Nothing is tagged before G2.
- **Capacity.** One person validating AI-built work, against companies valued in billions. The goals stay narrow: one quadrant, one
  pilot, evidence first.

## Challenges to the current direction (for Saravana)
1. **Narrow the vision.** The master plan's "any app, any repo, itself; agentic apps first-class" is three products.
   The evidence favours one: team-owned stations under one evidence record. Agentic packs and self-improvement
   become later packs, not the headline.
2. **Land with a preset, not the full package.** The 2026-10 decision made the full package the first focus. Buyers
   adopt one stage at a time: Grafana grew from 2.3 to 4.4 products per customer in two years, and 68% of developers
   prefer predictable setups. The full package stays the demo; a pilot should start with one preset.
3. **Proof before breadth.** Every public claim should point to a sealed live run. Today "runs and retires
   software" is dry-run only, and "for teams" is a plan.

## Decisions for Saravana
1. Adopt this vision, mission and tagline (or amend), replacing master plan §1.
2. Principle 7: owned by a team, with one accountable person per decision.
3. The first customer profile: UK regulated or public sector through consulting, or another?
4. The target values (the release date is decided: 2026-12-31).
5. Land with a preset for pilots (challenge 2), keeping the full package as the demo.
