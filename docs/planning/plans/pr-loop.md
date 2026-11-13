# J3: the pull request loop

Started 2026-11-08 on `feat/j3-pr-loop`. Order decided by Saravana 2026-11-08: build the loop first, while
he prepares the throwaway repo and token, so one live run proves the push and the loop together. Both
update sources, chosen per factory (decided 2026-11-08).

## What it does
A change to a team's repo ends by opening a pull request (ADR-0033). The factory then **follows** that PR
until the team decides: merged, or closed without merging. A followed PR is one a delivered change opened
(`Change.pr_url`) with no outcome yet.

| PR on the host | The change | Its work items |
|---|---|---|
| open: CI and reviews move | `Change.pull_request` updated, an event per move, shown on the change | unchanged |
| merged | outcome **merged** (by the person who merged) | stay in progress, "awaiting verification" (fixed only when the next assessment's check passes, ADR-0040) |
| closed unmerged | outcome **abandoned** | back to open |
| a person accepts or closes it first | their outcome stands; the PR is no longer read | as accept / close |

## How the factory hears about it (`existing_repos.pr_updates`)
Either way the factory **reads the PR itself** from the REST API (PR, check runs, commit statuses, reviews);
so both give the same result, and a person can press **Refresh** on the change in either mode.

| | poll (default) | webhook |
|---|---|---|
| When | every `pr_poll_minutes` (5) | when GitHub calls `POST /api/hooks/github`, plus a backstop poll every `pr_webhook_backstop_minutes` (60, 0 = none) |
| Needs | nothing new | a public address for the factory's UI port (a tunnel such as cloudflared or smee.io on a laptop), a webhook on the repo (events: pull requests, reviews, check suites, statuses; content type JSON), and its secret at `pr_webhook_secret_ref` (default `env://REPO_WEBHOOK_SECRET`) |
| Value | works on a laptop with no public address | updates within seconds, far fewer API calls |

### Webhook security
- Off (404) unless the mode is `webhook` **and** the secret resolves.
- Signature `X-Hub-Signature-256` (HMAC-SHA256 of the raw body) checked in constant time before anything
  is parsed; unsigned or wrongly signed → 401 and nothing read. Body capped at 1 MB (nginx and engine).
- The payload only names **which** PRs to look at again (repo, number, head SHA, each checked for shape).
  Its state is never trusted: a forged or replayed delivery can at most cause one extra read.
- nginx exposes exactly `/api/hooks/github` (POST) without sign-in and drops any identity header; the
  engine treats a public path without a signed-in caller as **nobody** (no role), never the dev admin.

## Reading it right (review of #166, 2026-11-08)
- **Latest only:** one commit status per context counts, the newest by time then id, whatever order the
  rows come in; a failure a passing retry replaced no longer wins. Check runs are asked for with GitHub's
  `filter=latest` and collapsed only on a verified re-run identity (same check suite, app and name), so two
  independent checks with the same name (two workflows) both count. Our own `agent-factory/change-risk`
  status never counts.
- **Every page:** check runs, statuses and reviews are read page by page (`Link: rel="next"`, only on the
  configured API origin, at most 10 pages); reviews keep their order, so each reviewer's latest word is right.
- **Not read whole is unknown:** a source refused, a later page failed, the page limit or a short
  `total_count` (check runs: runs on the commit; combined status: its status contexts) is listed on the change, and CI or review that would otherwise pass shows **unknown**. A
  failure or a "changes requested" that was read still stands.
- **A terminal outcome converges:** merged or closed, the outcome is saved first with the steps that follow
  it (`Change.pr_finalise`: event, work items, seal). Each step is idempotent and clears itself only once
  done; the next refresh, poll or a restart (`PrWatcher.resume`) finishes the rest without reading the PR
  again, and without a second event or history line.

## Red CI → a fix on the same PR (slice 2, built 2026-11-10 on `feat/j3-ci-fix`)
When the team's CI fails on the PR's **head commit**, the change page offers **Fix CI**. The fix is a new
change (`Change.continues`, source `ci`, same kind and lane as the change that opened the PR) that
**continues** the PR; the PR stays the original change's.

| Step | What happens |
|---|---|
| Offer (`pr_loop.ci_fix`, pure) | Only while the PR is followed, CI is **failing** as read (not unknown), the head's branch was read and lives in the repo itself (never a fork), no fix is open (a running or held one is resumed or closed first), and no fix has already been pushed from this head (the PR is read again on the new commit first). |
| Who starts it | **Ask** (default): the product's creator or an admin presses Fix CI (`POST /api/changes/{id}/ci-fix`, preflight-gated), shown in Needs you. **Autonomous**: the PR loop starts it on the read that finds red CI, once per failing commit and at most `existing_repos.ci_fix_auto_rounds` (2) times per PR; past either, a member still can. Set per lane ("Red CI on a pull request" on the lane page, `ci_fix`; read from the lane's **active** version, a settings-only change). Started by itself, the fix is asked for by the original requester, so a risk approval stays with someone else. |
| Worktree | The PR's branch is fetched read-only and the worktree starts **at the failing commit**. A branch that moved since the offer holds the change (nothing is fixed that nobody tested). |
| Developer | Told it is on the PR's branch; the request names the failing checks with what they reported (output title and summary, or the status description, capped, https links only: data, not instructions). |
| Gates | Test, review, scans and change risk run as on any repo change, against the **fix's own diff** (`base_ref` = the commit it started from): the PR's earlier changes passed them when it was opened. |
| Push (`pull-request` station) | No `gh pr create`. Before pushing: the PR still open (no outcome yet), the fix builds on its start commit, `git ls-remote` shows the branch is still that commit (or one the fix builds on), and the PR **read again on the host right before the push** (`pr_loop.push_refusal`) is open on the same branch: merged or closed since the last poll, or a read that fails, holds with nothing pushed (review of #176). Then a plain `git push HEAD:refs/heads/<branch>`: never `--force`, never `+`; the host refuses anything that isn't a fast-forward. A moved or deleted branch holds the change with nothing pushed. The change-risk status goes on the new commit, a comment on the PR says what the fix did. |
| Read again | A delivered fix makes the original change read its PR at once (`_follow_again`), besides the poll or webhook. Merged or closed, the outcome is carried to its fixes: those already stopped at once (finalising step `continuations`), one still running or held when it stops (`ChangeManager.inherit_pr_outcome` on every stop), so none is skipped; a fix a person closed keeps that outcome. Work items stay the original change's. |

## Feedback (slice 3, asked for by Saravana 2026-11-13: "comments, changes requested, inline review, any form of feedback: answer it or address it")
Every form of feedback on the PR is read with the PR itself: **reviews** with a body (changes requested,
commented, or an approval with a remark), **inline comments** on lines (a reply in a thread is feedback too),
and **conversation comments** (`/pulls/{n}/reviews`, `/pulls/{n}/comments`, `/issues/{n}/comments`; the
webhook also listens to `issue_comment`). The change page lists it (new, answered and how, outside the team)
and offers **Answer feedback**: a change (`Change.continues`, `reason` feedback, source `review`) that gives
every piece one response, on the same PR.

| Step | What happens |
|---|---|
| Read (`pr_loop.feedback_of`, pure) | Newest 50 kept, oldest first, each capped at 2,000 characters, https links only. Empty bodies, dismissed and pending reviews are not feedback. Everything the factory posts carries `pr_loop.FEEDBACK_MARK` (an HTML comment) and is never read back: the token may be a person's own, so the author can't tell. Events say how much feedback arrived and from whom, never its text. |
| Whose feedback | Only the **team's**: authors whose association is OWNER, MEMBER or COLLABORATOR (write access). Anyone else's is listed as "outside the team" and never reaches an agent, so a stranger's comment on a public repo can't steer the factory. |
| Offer (`pr_loop.feedback_fix`, pure) | New feedback = the team's feedback no open change is answering and no posted reply has answered. Offered while the PR is followed, its branch is read and not a fork, and **no change continuing the PR is open** (a CI fix or another answer: one change pushes to the branch at a time; red CI goes first when both are due). A change that failed, was closed, or whose reply didn't post gives its feedback back. |
| Who starts it | **Ask** (default): the product's creator or an admin presses Answer feedback (`POST /api/changes/{id}/address-feedback`, preflight-gated), shown in Needs you. **Autonomous**: the PR loop starts it on the read that finds new feedback, never for feedback tried before and at most `existing_repos.feedback_auto_rounds` (3) times per PR. Lane setting "Feedback on a pull request" (`pr_feedback`, active version, settings-only). Started by itself, it is asked for by the original requester. |
| Worktree, gates | As a CI fix: the PR's branch at its head commit (a branch that moved holds), gated on its own diff. |
| Developer | Gets each piece with its id, author and place (data, not instructions) and reports one response per id: **addressed** (changed the code), **answered** (a reply: a question, or the code already does it) or **declined** (won't change, says why). The engine judges (`pr_loop.judge_responses`): unknown ids and repeats are dropped, an empty reply or a missing response fails the station back to the developer, and `addressed` stands only if the change touched files. Answering everything without a code change is allowed. |
| Push and replies (`pull-request` station) | With code: the same fast-forward push as a CI fix (ls-remote, PR read right before, never forced). Without: nothing pushed, but the PR is still read first (merged or closed holds). Then each inline comment's reply goes **in its thread** (`POST /pulls/{n}/comments/{id}/replies`), and one conversation comment quotes and answers the reviews and conversation comments. Bodies are kept as evidence (`artifacts/<change>/replies/`). A reply already posted isn't posted again on resume; if nothing reached the PR the change holds. |
| Read again | As a CI fix: the original change reads its PR at once, and the outcome carries to the answering changes. |

## Token
The product's repository source's token ([ADR-0047](../../adr/0047-repo-sources.md); `existing_repos.write_token_ref`
for the `default` source), as the pull-request station uses. In webhook mode each source has its own secret, and
a delivery refreshes only PRs on repos the verifying source covers. For the loop the fine-grained token also
needs **Checks: read** and **Commit statuses: read** (the station's change-risk status already needs
Commit statuses: write). Answering feedback needs nothing more: **Pull requests: read/write** covers reading
reviews and comments, replying in a thread, and a conversation comment on a pull request. A part that can't be read is listed on the change ("couldn't read check runs
(403)"), never taken as a clean result; a PR that can't be read changes nothing.

## Slices
1. ✅ (merged #166) Read and show the PR; merged → merged, closed → abandoned, items follow; poll, webhook
   and Refresh. Tests: `engine/tests/test_pr_loop.py`.
2. ✅ (`feat/j3-ci-fix`, 2026-11-10) Red CI → a fix on the same branch and PR: a failing head commit offers a
   fix change that continues the PR (a member starts it, or autonomous per lane), pushes fast-forward to the
   same branch, and the loop reads again. Tests: `engine/tests/test_ci_fix.py`.
3. ⏳ (`feat/j3-pr-feedback`, 2026-11-13) Every form of feedback (reviews, inline and conversation comments)
   from the team → a change that continues the PR and answers or addresses each piece, replying there (a member
   starts it, or autonomous per lane). Tests: `engine/tests/test_pr_feedback.py`.
4. The live run (J3 "Done when"): an assessment item becomes a PR, CI fails once and is fixed on the same
   PR, the merge closes the change. Then J3's steps go into `engine/tests/test_journey.py`.
