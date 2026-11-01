# Agent Software Factory: north star (why we build it, and for whom)

Status: **proposed 2026-11-01**, for Saravana to agree. Researched 2026-11-01; sources at the end.
This page answers *why* and *for whom*. The golden journey in [roadmap.md](roadmap.md) is *how we prove it*;
the principles and building blocks are in [master-plan.md](master-plan.md). Nothing here reorders the roadmap.

## One line
**AI made writing code cheap. We make shipping and running it trustworthy: every change ready for something,
proven, and owned.**

## The problem (what the evidence says, 2025–2026)
1. **Code is no longer the bottleneck; everything after it is.** 90% of software professionals now use AI
   (DORA 2025). In a March 2026 survey of 831 engineers, 90% of teams hit problems with AI-generated code;
   the top bottlenecks are manual review (52%), security testing (51%) and rework (48%). AI moves work
   downstream instead of removing it (Black Duck 2026).
2. **Speed without stability.** DORA calls AI an amplifier: strong teams get faster, while for most teams
   faster delivery is offset by a higher change failure rate. Only about a quarter of people trust AI a lot.
   Experienced developers in a controlled study were 19% slower with AI while believing they were 20% faster
   (METR 2025): perceived speed is not delivered value.
3. **Governance lags adoption.** Only 30% of teams fully govern their AI coding tools, a quarter have no
   policy, and governed teams are far more likely to see real gains (Black Duck 2026).
4. **Prototypes don't become products.** AI app builders get to a demo fast, but a scan of 1,400+ live
   vibe-coded apps found 58% with a critical vulnerability, and AI-written code carries more flaws
   (Escape.tech, Veracode, via Manchester Digital). The rebuild to production often costs more than the
   prototype did.
5. **The lifecycle stops at deploy.** Assistants and app builders help write and ship; almost nothing owns
   what happens next: rollback, incidents, upkeep, CVEs, retirement. Our own critique (2026-10-21) found the
   same gap in the factory, which is why the golden journey ends at end of life.

**The problem in one sentence:** organisations can now produce code faster than they can trust, ship and
look after it, so AI's speed turns into review queues, security debt and orphaned apps.

## For whom
| Who | Their pain | What they get | How they get it now |
|---|---|---|---|
| **1. Engineering leaders in UK/EU scale-ups and mid-market firms** (CTO, head of engineering or platform; roughly 10–200 engineers) | AI is adopted but ungoverned; review and security are the bottleneck; no proof AI is paying off; can't send code or data to a US SaaS | A self-hosted delivery line that turns requests and tickets into PRs and releases with sealed evidence, their CI and cluster as stations, and DORA-style outcome numbers | **Consulting**: paid setup, packs and support on their infrastructure (master plan §6) |
| **2. Product owners and founders with a prototype** (often non-engineers; SMEs and B2B services, where UK AI adoption grew from 35% to 54% in a year, BCC 2026) | A working demo that isn't safe, tested or operable, and no team to run it | The same app rebuilt or uplifted to a stated tier (demo, pilot, production) with proof, and an owner for what comes after | **Consulting delivery**: we run the factory for them |
| **3. The practice itself** (Saravana, Priya, future associates) | Part-time hours; must deliver client work fast and show it is sound | The factory as our own delivery tool and the live demo that wins the work | Daily use; it improves itself through PRs its owner approves |
| **4. Later: platform teams who want to own their factory** | Agent sprawl, cost and access with no single control point | The open-core engine (Apache-2.0) plus paid enterprise features | Open source now; product revenue after the journey is proven |

**Not for (now):** large enterprises buying a hosted, SOC 2-certified platform (Factory, Port, GitHub and
others serve them; we don't fight there yet), and hobbyists who want the fastest demo (app builders win that).

## The value we provide
1. **Trust, shown not claimed.** Every change carries sealed evidence (tests, scans, review, change risk)
   and states what it is ready for; the engine judges, never an agent's opinion (principles 2, 3 and 7).
2. **The whole life of a product.** Launch, iterate, roll back, run, maintain and retire, not just generate.
3. **Fits what they already have.** Their repos, tickets, CI, registry, cluster and models are stations
   and adapters; self-hosted and model-agnostic, so code and data stay where they choose.
4. **People set policy, agents do the work.** Risk-based gates bring a named owner in only where it
   matters; everything else runs on its own.
5. **Measured outcomes.** Lead time, change failure rate, autonomy and cost per delivered change
   ([docs/outcomes.md](../outcomes.md)) answer "is AI paying off?" with numbers.

### Business value
- **For clients:** faster lead time without a higher change failure rate; less senior time in review and
  security triage; prototypes that reach production instead of being rebuilt; an audit trail for governance.
- **For the practice:** credibility and leads first, a delivery tool that lets a small team deliver like a
  larger one, then open-core revenue (master plan §6).

## North star metric (proposed)
**Proven changes kept in service per month:** changes that reached their stated readiness tier with sealed
evidence, have a named owner, and were not rolled back or reverted within 30 days.

Why this one: it only grows if the factory delivers (deliveries), safely (change failure rate and rollback),
with proof (sealed evidence) and ownership (principle 7). It can't be gamed by generating more code.

**Inputs we steer by** (already defined in [docs/outcomes.md](../outcomes.md)): lead time, change failure
rate, autonomy, cost per delivered change. **Commercial check:** live client or demo journeys that lead to
paid work.

## For the factory itself
The factory is its own first customer and is held to the same bar: its changes arrive as PRs with evidence,
its owner approves them, and its Outcomes page reports the same numbers. If the factory can't keep itself
proven and owned, it can't credibly do it for anyone else.

## Reality check
- The space is crowded and well funded: Factory sells an end-to-end "software factory" to large
  enterprises, and Port sells the platform-team version. Competing on breadth would lose.
- Our edge is narrow and real: **open, self-hosted, evidence-first, whole lifecycle, sold with hands-on
  delivery** for organisations too small for enterprise platforms and too cautious for app builders.
- That edge only counts once it is visible: the golden journey running live end to end is the proof.
  Until J5, this north star is a promise, not a claim.

## Sources (researched 2026-11-01)
- [DORA 2025 State of AI-assisted Software Development](https://blog.google/technology/developers/dora-report-2025/) and [commentary on its clusters](https://blog.robbowley.net/2025/10/01/dora-2025-ai-assisted-dev-report-some-benefit-most-dont/)
- [Black Duck, The State of AI-Powered Software Development (2026), via IT Security Guru](https://www.itsecurityguru.org/2026/06/10/nine-in-ten-dev-teams-hit-by-ai-code-bottlenecks-as-governance-lags-behind-adoption/)
- [METR study on experienced developers, via The Decoder](https://the-decoder.com/ai-coding-can-make-developers-slower-even-if-they-feel-faster/)
- [Manchester Digital: from vibe-coded prototype to production](https://www.manchesterdigital.com/post/foresight-mobile/your-vibe-coded-prototype-works-now-comes-the-hard-part)
- [BCC: AI adoption among UK SMEs, 2026](https://www.staffingindustry.com/news/global-daily-news/ai-adoption-among-uk-smes-climbs-to-54-in-2026)
- [Factory: from coding agents to software factories](https://factory.com/news/software-factory)
- [Port: what an AI software factory is and who owns it](https://www.port.io/blog/ai-software-factory)
