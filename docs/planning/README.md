# Planning: start here

This folder is **the only place the factory's plan lives**. Any person or agent (any model, any
account, any tool) can pick up development from a clone of this repo and nothing else.

## Read in this order
1. [roadmap.md](roadmap.md): what's next, in order, and what's done. The one page for "what do I do now".
2. [handoff.md](handoff.md): the current working state (what's live, what's open) and how we work:
   environment, checks, commits, lessons learned.
3. [master-plan.md](master-plan.md): the long-term vision, principles and vocabulary.
4. [critique-2026-10-21.md](critique-2026-10-21.md): why the roadmap follows the golden journey.
5. As needed: [plans/](plans/) (designs agreed but not built yet) and the ADRs in [../adr/](../adr/).
   [archive/](archive/) is history only: never act on it.

The canonical copies of everything else are elsewhere in the repo, never duplicated here:

| What | Where |
|---|---|
| Decisions (ADRs, drafts included) | `docs/adr/` (a draft is an ADR with status `proposed`) |
| Best practices we measure against | `docs/practices.md` |
| How to work on the code | `AGENTS.md` (`CLAUDE.md` imports it) |
| Outcome metric definitions | `docs/outcomes.md` |

## Rules that keep the plan from drifting
1. **One copy.** Plans, ADRs and handoff notes live only in this repo. No mirrors in chat projects,
   wikis or drives: at most a pointer to this folder. If you find a copy elsewhere, the repo wins.
2. **The plan changes in the same PR as the work.** A PR that finishes, starts or reshapes a roadmap
   item updates `roadmap.md` (and `handoff.md` "Current state") in that PR. The PR template asks.
3. **Git resolves concurrent edits.** Two sessions editing the roadmap get a merge conflict on rebase,
   not a silent overwrite. Resolve it by keeping both changes; never rewrite the page from a stale copy.
4. **Reshaping the plan is Saravana's call.** Any session may tick items, add status, and add backlog
   entries. Reordering milestones, changing a "Done when", or lifting the freeze needs his agreement,
   recorded with its date ("decided 2026-10-26").
5. **Dates, not "today".** Write `2026-10-26`, never "today" or "this week": the reader may be months later.
6. **Checked in CI.** `engine/tests/test_planning_docs.py` fails when a link here points nowhere, an ADR
   is mentioned that doesn't exist (except the reserved numbers below), a page says it mirrors another,
   or anything references the old chat-project paths.

## Reserved ADR numbers
ADR-0038 (secret store: planned in [plans/secret-store.md](plans/secret-store.md), not written yet).

## Starting a session (any agent)
1. Read this page, then `roadmap.md` and `handoff.md`.
2. Pick the first unticked item under "Next, in order" unless Saravana names another.
3. Work on a branch; follow `AGENTS.md`; update the plan in the same branch (rule 2).
4. Before you stop: replace `handoff.md` "Current state" with what you leave behind.
