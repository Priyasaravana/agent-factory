# ADR-0043: Failure classes, and end-to-end tests before deploy

**Status:** accepted · 2026-10-21 (§1–§4, §6, §7 delivered in PR 1; §5 is PR 2) · builds on [ADR-0019](0019-outcome-metrics.md) (outcomes),
[ADR-0022](0022-agent-observability.md) (agent calls), [ADR-0036](0036-model-gateway.md) (gateway),
[ADR-0041](0041-stack-packs.md) (stack packs) and [ADR-0042](0042-image-findings.md) (image findings)

## Context
The first live Next.js product, Guestbook (`guestbook-2`, change `df3bdaaeb306`), was delivered
at Level 3 with all 8 requirements verified live. It took two days and stopped five times, though
its code was rarely wrong:

1. **The model key expired during a review, twice.** A gateway virtual key lives `key_minutes`
   (180) of wall-clock time. The Mac slept for most of the call, the key expired, and the call
   ended with `401 Expired Key`. The engine treats every station failure the same: it routed the
   401 to the developer as a code failure. Both times the developer replied that nothing in the
   repo was wrong and ran `make verify`. Those two empty tries used up implement's attempt budget
   (3), and the change was held.
2. **End-to-end tests failed after deploy, twice** (a selector clash, then the smoke tests). The
   developer can't run the e2e suite: it runs only against the live app, after build and deploy.
   Each fix was a guess, checked by a full build → deploy → acceptance loop.
3. **A base-image critical CVE held the build** (ADR-0042, as designed). The change was resumed
   twice before the risk was accepted. Each resume ran the build again and held again, with the
   same message.
4. **The reviewer was denied 16 harmless commands** (`cd`, `sed -n`, `sort`, and a `grep` whose
   quoted `\|` the guardrail split as a pipe). Each denial cost a turn; the reviewer was the most
   expensive role ($1.31 of $3.45).
5. **Scanners sometimes read the worktree, not the committed-files copy.** When `git ls-files`
   fails, `committed_files` falls back to the worktree without saying so. One code scan went from
   7 medium to 1 high and 48 medium, from `node_modules` and `.next`.

Today a failure has one route: `on_fail`, to a repair station. Retries, timeouts and budgets
exist (`max_attempts_per_station`, `max_loops_per_change`, `change_wall_clock_minutes`, command
timeouts, `max_turns`, `call_budget_usd`), but none of them asks what kind of failure it is.
The time budget is checked only between stations, and an agent call has no time limit.

## Decision

### 1. Every failure has a class
A failed `StationResult` carries a `failure_class`:

| Class | Means | Examples | Route |
|---|---|---|---|
| `work` | the product is wrong | tests, readiness, scans, review, e2e, holdout scenarios | `on_fail`, as today |
| `infra` | the factory or its dependencies failed; the same step may pass if repeated | expired or refused model key, model API 429/5xx/overloaded, gateway unreachable, connection reset, sandbox not ready, image pull, kind or dind not ready, a privileged command timing out | retry in place (§2), then hold |
| `decision` | only a person can unblock it | an accepted risk, a base-image CVE, a missing secret, a risky diff | hold with a required action (§4) |

The class comes from the pure `failures.classify` over what the step returned (an `AgentResult`
error, a `CommandResult` with its exit code, output and `timed_out` flag, and which executor ran
it). Its rules are a table, each with a test that also shows an ordinary work failure does not
match. **Anything the rules don't recognise is `work`**, today's behaviour, so the classifier can
only remove wrong routes, never add one. Agent-written commands (`make verify`, `make e2e`) that
time out are `work`: a hung test is the product's fault.

### 2. Infra failures are retried in place, then held
- The retry happens where the failure happened: `ctx.agent(...)` and privileged `ctx.cmd(...)`
  retry their own call, so the rest of the station's work is kept. A retried agent call continues
  in the same worktree.
- Exponential backoff with full jitter and a cap: `budgets.infra_retries` (default 3) and
  `budgets.infra_backoff_seconds` (default `[20, 90, 300]`). An expired or refused key gets a
  fresh key and one immediate retry before backoff.
- Retries never count against the station's attempt budget or the fix-loop budget, and never
  reach the developer as failure evidence.
- After the last retry the change is **held**, naming the class and the cause ("model gateway
  unreachable after 3 retries"), with the provider's `check()` result attached. Resuming retries
  the step.
- Every retry is a `retry` event (class, cause, attempt, wait). Infra failures never become a
  change's failure evidence, so the retro doesn't learn code lessons from them.

### 3. Time limits that hold inside a station
- **An expired key is renewed, not reported.** The gateway still issues a key per agent call
  for `key_minutes` of wall-clock time, which a sleeping Mac can use up. An expired or refused key
  is `infra` (`key_expired`, `auth_failed`): the call is retried, and every retry opens a new
  gateway session with a fresh key; an expired one is retried at once, without backoff. *(As built:
  renewing the key inside a running call, on host wake, was dropped: the CLI session can't take a
  new key mid-call, and the immediate retry gives the same result.)*
- **An agent call has a time limit:** `budgets.agent_call_minutes` (default 45) of agent time,
  host sleep excluded. A call that sends nothing for `budgets.agent_stall_minutes` (default 15, as built:
  above the CLI's 10-minute ceiling for a single Bash tool call, so a long `make verify` inside the
  call isn't taken for a stall) is stalled: it is cancelled and retried as `infra`. A call that keeps working past its limit is
  cancelled and the change held: running the same agent again would not help.
- **The change's time budget holds inside a station.** The deadline is passed to the station:
  `ctx.agent` and `ctx.cmd` cap their timeouts to what is left, and at the deadline the running
  call is cancelled and the change held, resumable, as today between stations. The budget itself
  moves to agent time (the monotonic clock), so host sleep no longer uses it up either.

### 4. Decision holds say what to do, and resume checks it was done
A `decision` hold records a **required action** (`Change.required_action`). The change page
shows it ("Before you resume: …"). A resume whose action isn't done is refused with the same
instruction; it does not start the station again or seal new evidence. *(As built: the one kind
is `accept_risk`, for the image holds of ADR-0042 that the work ledger can unblock. Risky diffs
already wait in their own state for a second admin (ADR-0027), and no station holds for a missing
secret today. A repeated app fix holds without an action: a person looks, then resumes.)*

### 5. End-to-end tests run before deploy
- A stack pack may declare `preview`: a command that starts the app inside the sandbox, without
  the cluster, and the URL it serves (`nextjs-web`: `make preview` on port 3000 with its
  in-memory Postgres; `fastapi-service`: `make preview` with SQLite). Packs version this as a
  minor bump.
- A new built-in station type, **`preview-e2e`**, in the verify phase after `test`: it starts the
  preview, waits for its liveness path, runs the pack's `e2e` command with `{url}` set to the
  preview, and stops it. A failure is `work` and goes to implement with the test output, before
  any image is built.
- The golden-path skills tell the developer to run `make preview` and `make e2e` itself before it
  reports done.
- Acceptance still runs the e2e suite and the hidden scenarios against the **live** app: the
  preview proves the code, the live run proves the deployment.
- The new-product and product-change lanes get the station; a pack without `preview` skips it
  with that reason in the plan. Team-repo lanes don't: their CI is theirs.

### 6. Smaller fixes from the same run
- **Scanner scope is never silent.** If the committed-files copy can't be made, the scan still
  reads the whole worktree (never less than asked), and says so in a `scan_scope` event with git's
  error. *(As built: looking for the cause found a worse bug. The file listing was read from the
  command's output, which the executor cuts to its last 6,000 characters, so a large repo's scans
  silently skipped its first files. The listing now goes to a file. Guestbook's own fallback left
  no trace of git's error; the event will name it next time.)*
- **The observe-only guardrail parses commands properly.** Commands are split with a shell-aware
  parser (a quoted `\|` is not a pipe), and reviewers may also use `cd` inside the worktree,
  `sed -n` (no `-i`, no `w`), `sort`, `uniq`, `wc`, `cut` and `find` (no `-exec`, `-delete` or
  `-fprint`). Each rule gets a case in `tests/test_hooks.py`, with the write it still refuses.

### 7. Outcomes
Retries are effort, like fix loops: Outcomes shows infra retries and the time they took per
station. On the "waiting on" board, changes held for `infra` wait on the system, not the
requester, and a decision hold shows its required action. *(As built: the time split still counts
all held time as waiting on a person; the transition log records statuses, not hold classes.)*
Autonomy is unchanged: a retry is not a human touch. Each metric gets a definition in
`docs/outcomes.md` and an exact-value test.

## Delivery
- **PR 1, failure classes** (§1–§4, §6, §7; ~2 days): `failures.py` (pure) with
  `tests/test_failures.py`, the call-site retries and limits, required actions on holds.
  Replayed on Guestbook's recorded failures: the two 401s retry with a fresh key, and the change
  is not held at implement.
- **PR 2, e2e before deploy** (§5; ~1.5 days): the `preview` pack field, `preview-e2e` station
  type with its contract entry and collector, both packs' `make preview`, skill guidance, and the
  two lanes. Measured by the evaluation harness (ADR-0025): loops to deliver and cost per
  delivered change on the Next.js cases.

## Consequences
- A failure the developer can't fix no longer reaches the developer. Guestbook's two expired keys
  would have cost two quick retries instead of two developer calls and a hold.
- Most e2e failures are found in seconds inside the sandbox, not after a build and a deploy.
- Some infra problems now wait up to ~7 minutes in backoff before a person hears about them.
  That's the price of not paging anyone for a blip; the hold says what was tried.
- A misclassified work failure would be retried instead of fixed. Unknown errors stay `work` and
  the infra rules are narrow and tested, so this is limited to the patterns in the table.
- Rollback to the last good release, an accepted/closed state for delivered changes, and
  resilience inside generated apps (timeouts, retries, graceful shutdown, reliability signals) are
  not in this ADR. Rollback waits until a cluster target serves real users; app resilience goes
  with the next pack release.

## Open questions
Decided with the defaults for PR 1 (2026-10-21), each a setting under `budgets:`:
1. Retry defaults: 3 retries, with full jitter under 20 s, 90 s and 300 s (at most ~7 minutes).
2. Agent call limit: 45 minutes of agent time, the same for every role.
3. A resumed `infra` hold does not run the provider's `check()` first: the readiness pills already
   show it, and the step's own retries test the same thing.

## Best practices
Mapped to [practices.md](../practices.md).

| Practice | How this decision meets it |
|---|---|
| §2 DORA: change failure rate, cost per delivered change | Infra failures stop counting as the product's failures and stop spending developer calls; e2e failures are found before a build. |
| §2 DORA: time to restore | An infra blip recovers by itself in minutes; a decision hold names the one action that unblocks it. |
| §3 OWASP agentic: cascading failures | One failed dependency no longer cascades into fix loops and an attempt-budget hold; retries are capped and every one is recorded. |
| §3 OWASP agentic: human-agent trust | A person is asked only for what needs a person, with the action named; a resume can't loop on an undone action. |
| §5 Agent engineering: trace every agent call | Retries, stalls and time limits are events on the agent call records. |
| §7 The factory itself: reliability | Classified failures, retries with backoff, jitter and a cap, and time limits that hold inside a station. |
