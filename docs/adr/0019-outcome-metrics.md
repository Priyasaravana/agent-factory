# ADR-0019: Outcome metrics from a status transition log

**Status:** accepted · 2026-09-30 · definitions: [outcomes.md](../outcomes.md)

## Context
The factory recorded rich evidence per run: events, readiness scorecards,
traceability, cost. But nobody could answer the questions that decide whether it
is worth running:
- Is it delivering?
- How often does it need rescuing?
- What does a change cost?
- Who is it waiting on?

The maturity model and DORA both say to measure outcomes, not activity. Warp's
software-factory write-up found that people, not agents, were their bottleneck.
We had no way to see that.

Time-based metrics need exact times of status changes. Only some changes left a
trace, as free-text event messages.

## Decision
1. **A status transition log.**
   - `run_transitions(run_id, ts, from_status, to_status)` is written by the
     state store's `create_run` and `save_run`: the one place every run is saved.
   - No call site can forget to record a change, and nothing else changes.
   - It is append-only, and future metrics can use it too.
2. **Metrics are a pure function of stored facts.**
   - `outcomes.gather()` reads the store; `outcomes.compute()` is pure.
   - Every definition is pinned by exact-value tests in `test_outcomes.py`.
3. **Definitions favour honesty over flattering numbers.** They are written in
   `docs/outcomes.md` and shown next to each number in the UI.
   - **Lead time** includes time spent waiting on people.
   - **Change failure rate** counts held-then-rescued iterations as failures.
   - **Cost per delivered change** includes the spend of failed runs.
   - **Autonomy** counts only *unplanned* touches (answers, rescues, restarts).
     Planned checkpoints (the spec review gate, feedback) never reduce it.
   - **Fix loops** are agents repairing their own work, so they are reported as
     effort, not as lost autonomy.
4. **Who is the factory waiting on?**
   - Run time is split into agents working / waiting on a person / waiting on
     the system.
   - A board lists every run that needs someone to act: what to do, who (the
     order's creator), and for how long.
5. **API first:** `GET /api/outcomes?days=1..365`. The Outcomes page, the CI
   smoke test and any future automation (e.g. a weekly digest) use the same
   endpoint.

## Consequences
- **History before this version has no timeline.** It still counts for
  deliveries (from the "delivered" event), cost and quality, but not for the
  time split. The page says how many runs have a timeline.
- **Computation reads all runs.** That is fine at the factory's current scale.
  Postgres (`docs/scaling.md`) would move it to SQL aggregates behind the same
  endpoint.
- **This is the baseline for the evaluation harness.** It will gate a workflow
  publish on these metrics not regressing on a fixed set of orders.
