# ADR-0027: Change-risk policy: trusted people, untrusted text, the diff decides

**Status:** accepted · 2026-10-03 · phase 1 of the master plan (before existing repos, ticket intake and self-improvement) · guide: [workflows.md](../workflows.md#change-risk)

## Context
The factory builds and changes software on request. So far every request came
from a signed-in member through the UI, and every change landed in a repo the
factory created. The plan widens both: existing repos, GitHub Issues intake,
scheduled upkeep and the factory improving itself. Wider intake means three new
ways for harmful work to get in:

- someone asks for something harmful outright (a phishing site);
- a harmless-sounding request ("simplify login", "clean up old tables") becomes
  a diff that weakens security or destroys data;
- text the factory reads (a ticket, a README, a web page) tries to steer the
  agents.

The review station (ADR-0020) judges whether a change is *correct*. Nothing
today judges whether it is *risky*, and nothing records who accepted the risk.

## Decision
1. **Trusted people, untrusted text.**
   - Only signed-in members start work (identity from the auth gateway, ADR-0012:
     headers `X-Auth-User` / `X-Auth-Role`).
   - Ticket intake (later) starts work automatically only for issues written by
     a GitHub user linked to a member. Other issues wait in a "needs a member"
     queue until a member accepts them.
   - Tickets, repo files, web pages and tool output are data. They reach agents
     wrapped as quoted material, never as instructions, and a station never
     takes an action because such text asked for it.

2. **Six categories, two tiers.**

   | # | Category | Tier |
   |---|---|---|
   | 1 | Weakening security (auth, TLS, CORS, rate limits, permissions) | needs a second approver |
   | 2 | Removing safety nets (tests, coverage gate, logging/audit, CI checks) | needs a second approver |
   | 3 | Destructive data changes (drop/truncate, mass delete, irreversible migrations) | needs a second approver |
   | 4 | Exfiltration or backdoors (new outbound hosts, hidden routes, hard-coded credentials) | needs a second approver |
   | 5 | Abuse apps (phishing, malware, credential harvesting, spam, covert surveillance of individuals) | **always refused** |
   | 6 | Legitimate large removals ("remove the billing feature") | the product owner confirms an exact removal list |

3. **The request is screened; the diff decides.**
   - **At intake**, the request is matched against the acceptable-use list
     (category 5, refused with the matched rule) and classified for 1–4 and 6.
     A category-6 request gets a **removal list** (requirements, endpoints,
     tables, tests) that the product owner confirms before building.
   - **After review, before package**, a **change-risk station** checks the
     run's diff against the base. Findings in categories 1–4 hold the run in a
     new `awaiting_risk_approval` state. For a confirmed removal, the diff must
     stay within the confirmed list; anything beyond it is a category 1–4
     finding.
   - The check is **deterministic rules over the diff**: paths, patterns,
     removed or skipped tests, lowered gates, migrations, new egress hosts and
     URLs, auth decorators and middleware, security-relevant config flags. An
     optional agent reviewer may add findings, and the engine judges them as it
     does review findings (ADR-0020). A request classified as harmless never lowers what
     the diff check reports.

4. **Who approves.**
   - **Second approver:** an admin other than the requester, with a written
     reason. The requester can never approve their own risky change.
   - **Break-glass on a single-admin install:** when the requester is the only
     admin (including `AUTH_MODE=off`), they may approve their own after a
     cooling-off delay (default 1 hour) with a mandatory reason. The approval is
     flagged as break-glass in the evidence and counted in Outcomes.
   - **Product owner:** every product has a named owner, by default whoever
     created it; an admin can reassign it. The owner confirms category-6
     removal lists.

5. **One check, three places.** The same rules run:
   - as the change-risk station in factory runs;
   - as a required status check on PRs in existing repos (phase 3);
   - as a gate when promoting to prod (phase 7).

6. **The acceptable-use list ships with the factory.** Operators can add rules
   in config; the shipped rules can't be removed or disabled.

7. **Self-improvement can't touch the safety core.** The acceptable-use list,
   the change-risk rules, the approval logic and auth are protected paths. A
   factory-authored diff that touches them is held and the change is dropped;
   only a person-written PR changes them. Every other factory change is a PR
   that the factory's owner approves and merges.

8. **Everything is evidence.** Refusals (with the matched rule), findings,
   removal lists and owner confirmations, approvals and break-glass approvals
   (who, when, reason) are decision events, sealed with the run (ADR-0023).

## Consequences
- Harmful apps are refused before any tokens are spent on building them, and
  the refusal can be explained by the rule that matched.
- Risk is judged on what the change does, so rewording a request doesn't get a
  risky diff through.
- Every risky change has a named person who accepted it and a reason, in
  tamper-evident evidence.
- Existing repos, tickets and self-improvement can be opened up on top of this
  instead of each inventing its own safety rule.
- Cost: one more station per run (deterministic and fast), and some runs wait
  for a person. On a solo install the wait is the cooling-off delay.

## Limits, stated plainly
- Rules over a diff catch common patterns, not every clever attack. The
  second-person rule and the sealed evidence are the backstop, and the rule set
  grows from real findings and evaluation cases.
- Classifying a request in words can be wrong in both directions. That's why the
  diff, not the request, is authoritative.
- Break-glass can become a habit. The delay, the flag and the Outcomes count
  make it visible; an install can turn it off.

## Alternatives considered
- **Judge only the request.** Cheap, but a reworded request gets through.
- **Judge only the diff.** Abuse apps would be built before being caught, and
  removals wouldn't have a confirmed scope to compare against.
- **An agent decides risk.** In this factory a deterministic engine owns gates
  and agents work inside stations (ADR-0001). An agent may add findings, but
  the rules and the approver decide.
- **Self-improvement without limits, owner approves.** Rejected: one approved
  PR could weaken every later safety decision.

## Implementation (2026-10-03)
Built in this order; the rest follows the master plan.

- **Done:** acceptable-use screening of orders and feedback (`RunManager.screen`,
  shipped rules in `risk.DEFAULT_AUP`, operator rules in `change_risk.acceptable_use`,
  refusals in `audit/refusals.jsonl`); the `change-risk` station (Test phase, after
  `code-review`) with deterministic rules for categories 1–4 (`risk.check_diff`);
  the `awaiting_risk_approval` state; approval by an admin other than the requester
  (`/risk/approve`, admin-only path), break-glass with a cooling-off delay, and
  send-back to the repair route; evidence in `change-risk.json` and decision events.
- **An approval covers a digest** of the hold findings (rule, file, line text):
  if the change moves on and the findings change, it waits again.
- **Refusals don't create orders.** A refused request leaves nothing to build or
  seal, so the audit file is its record rather than a run.
- **Next:** category 6 (product owners and confirmed removal lists), an optional
  agent reviewer adding findings, the check as a required PR status check (phase 3)
  and at promotion to prod (phase 7). Until a workflow is re-drafted from the
  template it has no `change-risk` station; the editor and preflight warn.
