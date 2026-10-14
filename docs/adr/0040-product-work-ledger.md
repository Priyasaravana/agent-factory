# ADR-0040: The product work ledger

**Status:** accepted · 2026-10-14 · builds on [ADR-0031](0031-existing-repos-assess.md) (assessment),
[ADR-0033](0033-repo-changes-as-pull-requests.md) (changes as pull requests) and the assessment rollup
(`rollup.py`).

## Context
Saravana tested the repo assessment on a real repository and found four problems:
- Issues, risks, test gaps and next changes lived inside each assessment's change, and only "Next changes" could start a change.
- A new assessment started from scratch: nothing recorded which change fixed what, what was still open, or what was deliberately left alone.
- A change's page didn't say plainly what it was asked, what it did and changed, and what it gathered.
- The activity log was long and listed oldest first.

## Decision
1. **One ledger per product** (`work_items` table, `ledger.py` pure). Every assessment is folded in when it delivers.
   - The ledger holds issues (from the factory rules and every scan), the assessor's risks and test gaps, the consolidated recommendations, and coverage gaps.
   - Each item has a stable id from its fingerprint (kind plus normalised title, or the station for a coverage gap).
   - The assessor rewords its judgement between runs, so a risk, test gap or recommendation also matches an earlier item when most of its words match.
   - Each item records its first iteration, every iteration it was seen in, and a history of what happened to it.
2. **Status, verified rather than assumed:**
   - `open` → `in_progress` when a change addresses it.
   - `in_progress` → `fixed` or back to `open` at the next assessment.
   - `wont_do` and `accepted_risk` (with an optional expiry) are people's decisions and need a reason. The reason, the person and the time are kept.
   - A fixed item that is found again **reappears** as open.
   - An accepted risk past its date opens again.
   - An **issue** is fixed only when the check that found it ran again with a real tool, or the factory rules ran, and didn't find it. A scan that fell back to the agent, was simulated or found nothing to read proves nothing.
   - A **risk, test gap or recommendation** is judgement, so its absence closes it only after a change addressed it. The history says "not reported again by the assessor".
3. **Any item can start a change.** `FeedbackInput.items` lists the work items.
   - The engine checks them, spells them out in the change request (and so in the pull request), records them on the change (`Change.work_items`) and marks them in progress.
   - A change that fails or is cancelled returns its items to open.
   - A delivered change leaves them in progress until an assessment verifies them.
4. **Each change shows its outcome** (`GET /api/changes/{id}/outcome`):
   - what it was asked;
   - the work items it addressed and what a later assessment found for each;
   - its summary and pull request;
   - the files it changed (`pull-request.json`);
   - what it gathered (the artifact ledger);
   - for an assessment, how many items were new, fixed or still present.
5. **The UI:**
   - The product page has a Work items section common to all iterations.
     - Tabs: Open, In progress, Fixed, and Won't do / accepted.
     - Filters by kind, severity and iteration, plus a search box.
     - Each item shows chips for the iterations it was seen in.
     - Select items to make a change, mark them won't do, accept the risk, or reopen them.
   - The assessment panel points to Work items, which is the one place to act.
   - The Activity log lists newest first, with a search box.

## Consequences
- Assessments become a running record of the repo instead of separate snapshots, and "fixed" means a later check confirmed it.
- Decisions not to act are explicit and auditable.
- Fingerprints are heuristic for judgement items: an assessor that rewrites a risk completely creates a new item. The old one stays open until someone closes or addresses it.
- Products built by the factory have no assessment yet, so their ledger is empty and the section is hidden. Feeding review and scan findings of factory changes into the ledger is a later step.
- `work_items` is a new table. It is dropped by `agent-factory reset` like the rest of the work, and no reset is needed to start using it.
