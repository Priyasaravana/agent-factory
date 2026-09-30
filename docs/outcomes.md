# Outcomes: what the numbers mean

The **Outcomes** page (and `GET /api/outcomes?days=7|30|90`) answers four questions:
- Is the factory delivering?
- How autonomously?
- At what cost?
- Who is it waiting on right now?

Every number has one definition, written here and shown next to it in the UI.
Decision record: [ADR-0019](adr/0019-outcome-metrics.md).

An **iteration** is one run: the first build of an order, or one round of
feedback. **Delivered** means the run reached *delivered, awaiting feedback*:
- deployed;
- verified by the hidden scenarios;
- the feedback gate is open.

## Delivery (after DORA's four keys)

| Metric | Definition | Why this way |
|---|---|---|
| **Deliveries** | Iterations delivered in the window, and that number per week. | DORA's deployment frequency. Small, frequent iterations are the goal. |
| **Lead time** | From the moment an iteration was requested (order submitted or feedback sent) until it was delivered. Median and p90. | Includes time spent waiting on people. That's what the requester experiences, and "Where the time goes" shows how much of it was waiting. |
| **Change failure rate** | Of the iterations that finished in the window (delivered or failed; cancelled ones excluded), the share that **failed** or was **held** and needed a person to rescue it. | Held means the factory could not finish on its own evidence, so for us it counts as a failed change even when a person later saved it. Cancelling is a human decision, not a failure. |
| **Recovery time** | For delivered iterations that were held: median time from first held to delivered. | DORA's time to restore, applied to the factory's own failures. |

## Autonomy

| Metric | Definition |
|---|---|
| **Autonomy** | Share of deliveries that needed **no unplanned human touch** in their whole life. |
| **Unplanned touches** | A person answered blocking intake questions, **rescued** a held run, or **resumed** a run after the factory restarted. |
| **Planned touches** | Spec reviews at the spec review gate, and feedback. These are the human checkpoints the workflow asks for, so they never count against autonomy. |

**Fix loops don't count against autonomy.** A fix loop is agents repairing their
own work with evidence, which *is* autonomy working. It shows up as effort
(**fix loops per delivery**) instead.

## Cost and effort

| Metric | Definition |
|---|---|
| **Cost per delivered change** | All model spend of iterations started in the window, **failed ones included**, divided by deliveries. Failed attempts are part of what a delivered change really costs. |
| **Fix loops per delivery** | Average repair loops of delivered iterations. |

## Where the time goes

Each run's time in the window is split by who it was waiting on:

| Bucket | Run states |
|---|---|
| **Agents working** | queued, running |
| **Waiting on a person** | needs answers, held, spec ready for review, interrupted |
| **Waiting on the system** | paused for the model usage window |

Time after delivery is not counted: the run is done, and the next iteration is a
new run.

Timing comes from the **status transition log**, which every run writes from
this version on. Older runs still count for deliveries (their "delivered" event
gives the time), for cost and for quality, but not for the time split. The page
says how many runs have a timeline.

## Waiting on a person now

Every run that can't continue until someone acts:
- what they need to do;
- **who** should do it: the order's creator, or "an admin" when unknown;
- how long it has waited.

Longest wait first. The usage-window pause is listed last, with "system" as its
owner, since nobody needs to act.

## Agent effort by station

Every agent call in the window, grouped by station (ADR-0022): the number of
calls, their total **cost**, the **median turns** and **median time** per call,
the number of **tool calls**, and **denied** actions (tool calls a guardrail
refused). The retro's own calls appear as the `retro` station.
**Guardrail denials** counts every refused action in the window.

Use it to see where model spend goes, which station is slow, and which agent
keeps attempting things it must not. Open a run's **Agent calls** panel to read
the transcripts.

## Quality of what is live

| Metric | Definition |
|---|---|
| **Live apps at Level 3** | Apps (not archived) whose latest delivery scored agent-readiness Level 3. |
| **Requirements verified live** | Of those apps' numbered requirements, how many were checked on the running app by hidden scenarios that all passed (ADR-0017). A requirement no hidden scenario covers is not counted as verified. |

## Using it
- **Before and after a workflow change:** compare the **By workflow** table
  across windows. The evaluation harness (next on the roadmap) will make this a
  publish gate.
- **When autonomy drops:** the touches line under "Where the time goes" says
  which kind of help was needed. Questions point to intake prompts; rescues
  point to templates, skills or budgets.
- **When waiting on a person dominates:** that's the bottleneck, not the agents.
  Consider turning the spec review gate from `always` to `first`, or answering
  sooner.
