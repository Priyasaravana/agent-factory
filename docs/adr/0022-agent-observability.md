# ADR-0022: Agent observability: denials, per-call records and transcripts as run evidence

**Status:** accepted · 2026-10-01

## Context
The event log recorded each agent's messages and a short form of each tool
call. But four things were missing:
- **Guardrail denials left no trace.** When a hook refused a tool call (a write
  outside the worktree, `git push`, reading credentials) the agent was told "no",
  but nothing was recorded. That's an audit gap: we couldn't show that
  guardrails fired, or which agent keeps trying.
- **No per-call picture.** Cost was only totalled per run: no turns, time,
  model or tool use per station or per call. "Where do the money and time go?"
  had no answer below the run.
- **No transcripts.** Tool *results* weren't kept, so a failure could be seen
  but not understood ("it ran `make verify`", but what did it see?).
- **The learning loop was blind to behaviour** (ADR-0021). It couldn't suggest
  "stop searching for 25 turns; read AGENTS.md first", or learn from refused
  actions.

## Decision
1. **Every denial is evidence.**
   - `build_hooks` reports each refusal through an `on_deny` callback. The
     runner forwards it, and the station records a decision event
     (`guardrail denied <tool> for <role>: <reason>`, with a `denied` record).
   - Denials are listed on the run's Agent calls panel and counted on Outcomes.
2. **Every agent call gets one record** (`observe.AgentCall`, an `agent_call`
   event):
   - station, role, model, outcome and error;
   - turns, duration (time the host was awake) and cost, plus `suspended_s` if
     the host slept during the call;
   - tool calls by tool, and denials;
   - its transcript file.

   This covers every station's agent and the retro alike.
3. **Every agent call gets a transcript** at
   `artifacts/<run>/sessions/<nn>-<station>-<role>.jsonl`:
   - one JSON object per line: text, tool use with its full input, tool result,
     denial, final result;
   - long entries keep their head and tail (4,000 characters), and each file is
     capped at 2 MB with a visible marker;
   - it goes through `REDACTOR`, like every store write, so resolved secret
     values never appear.

   The hidden scenarios stay out: holdout paths remain protected for every agent
   but the verifier.
4. **API and UI.**
   - `GET /api/runs/{id}/calls` returns calls and denials.
   - `GET /api/runs/{id}/calls/{name}/transcript` returns one transcript. The
     name is matched against a strict pattern, so it can't reach other files.
   - The run page has an **Agent calls** panel with a transcript viewer.
   - Outcomes gains **Agent effort by station** (calls, cost, median turns and
     time, tool calls, denials) and a count of guardrail denials.
5. **The retro learns from behaviour.**
   - A denial alone now triggers a retro.
   - Every call's turns and tool counts are in its prompt, so it can suggest
     efficiency and safety lessons. The same vetting and admin approval still
     apply (ADR-0021).

## Consequences
- An auditor can see every refused action and every agent call with what it
  cost. A developer can read exactly what an agent saw.
- Transcripts take disk space, up to 2 MB per call. They live with the run's
  other artifacts, are included in `make backup`, and are removed with the data
  directory.
- Transcripts may contain code and command output from the generated app, so
  only the order's creator or an admin can read them. Call summaries and denials
  stay visible to every signed-in member (`tests/test_access.py`).
- Not done yet: exporting OpenTelemetry GenAI spans to an external tracing
  backend. The per-call records are the same data, so an exporter is a small,
  additive step.
