import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ACTIVE, api, unwrap, type FactoryEvent } from "../api/client";
import StationStrip from "./StationStrip";
import StatusPill from "./StatusPill";

export default function RunPanel({ runId, orderId }: { runId: string; orderId: string }) {
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.run.status) ? 2_000 : 8_000),
  });
  const events = useRunEvents(runId);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["run", runId] });
    qc.invalidateQueries({ queryKey: ["order", orderId] });
  };
  const resume = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{run_id}/resume", { params: { path: { run_id: runId } } })),
    onSuccess: refresh,
  });
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSuccess: refresh,
  });

  if (!run.data) return <p className="muted">Loading run…</p>;
  const { run: r, stations } = run.data;

  return (
    <section className="card">
      <div className="row spread">
        <h3>
          Iteration {r.iteration} <StatusPill status={r.status} />
        </h3>
        <div className="row">
          <span className="muted small">
            {r.cost_usd > 0 ? `$${r.cost_usd.toFixed(2)} · ` : ""}loops {r.loops}
          </span>
          {["held", "interrupted", "paused_limits"].includes(r.status) && (
            <button onClick={() => resume.mutate()} disabled={resume.isPending}>Resume</button>
          )}
          {ACTIVE.has(r.status) && (
            <button className="secondary" onClick={() => cancel.mutate()}>Cancel</button>
          )}
        </div>
      </div>
      {r.change_request && <p className="note">Change request: {r.change_request}</p>}
      <StationStrip stations={stations} />
      {r.summary && <p className={r.status === "held" ? "error" : "muted"}>{r.summary}</p>}
      {r.status === "held" && r.last_failure && (
        <details open>
          <summary>Evidence</summary>
          <pre className="pre">{r.last_failure}</pre>
        </details>
      )}
      {r.status === "needs_input" && <Questions runId={runId} questions={r.questions ?? []} onDone={refresh} />}
      {r.status === "awaiting_feedback" && <FeedbackForm orderId={orderId} onDone={refresh} />}
      <EventLog events={events} />
    </section>
  );
}

function useRunEvents(runId: string): FactoryEvent[] {
  const [events, setEvents] = useState<FactoryEvent[]>([]);
  useEffect(() => {
    setEvents([]);
    let last = 0;
    let source: EventSource | null = null;
    let closed = false;
    const connect = () => {
      source = new EventSource(`/api/runs/${runId}/stream?after=${last}`);
      source.addEventListener("event", (m) => {
        const ev = JSON.parse((m as MessageEvent).data) as FactoryEvent;
        last = Math.max(last, ev.id);
        setEvents((prev) => (prev.some((p) => p.id === ev.id) ? prev : [...prev, ev]));
      });
      // The server closes the stream when the run parks; reconnect slowly to catch resumes.
      source.onerror = () => {
        source?.close();
        if (!closed) setTimeout(connect, 5_000);
      };
    };
    connect();
    return () => {
      closed = true;
      source?.close();
    };
  }, [runId]);
  return events;
}

function EventLog({ events }: { events: FactoryEvent[] }) {
  const [filter, setFilter] = useState<"all" | "decisions" | "stations">("all");
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => bottom.current?.scrollIntoView({ block: "nearest" }), [events.length]);
  const shown = events.filter((e) =>
    filter === "all" ? true : filter === "decisions" ? e.kind === "decision" : e.kind.startsWith("station"),
  );
  return (
    <div>
      <div className="row">
        <h4>Activity</h4>
        {(["all", "decisions", "stations"] as const).map((f) => (
          <button key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
            {f}
          </button>
        ))}
      </div>
      <div className="log">
        {shown.map((e) => (
          <div key={e.id} className={`ev ev-${e.kind}`}>
            <span className="ts">{new Date(e.ts).toLocaleTimeString()}</span>
            <span className="st">{e.station ?? ""}</span>
            <span className="msg">{e.message}</span>
            {typeof e.data?.output === "string" && e.data.output && (
              <details>
                <summary>output</summary>
                <pre className="pre">{e.data.output as string}</pre>
              </details>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>
    </div>
  );
}

function Questions({ runId, questions, onDone }: { runId: string; questions: string[]; onDone: () => void }) {
  const [answers, setAnswers] = useState<string[]>(questions.map(() => ""));
  const submit = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/runs/{run_id}/answers", { params: { path: { run_id: runId } }, body: { answers } })),
    onSuccess: onDone,
  });
  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault();
        submit.mutate();
      }}
    >
      <h4>Intake needs a decision from you</h4>
      {questions.map((q, i) => (
        <label key={i}>
          {q}
          <input
            value={answers[i]}
            onChange={(e) => setAnswers(answers.map((a, j) => (j === i ? e.target.value : a)))}
            required
          />
        </label>
      ))}
      <button disabled={submit.isPending}>Send answers</button>
      {submit.error && <p className="error">{submit.error.message}</p>}
    </form>
  );
}

function FeedbackForm({ orderId, onDone }: { orderId: string; onDone: () => void }) {
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/orders/{order_id}/feedback", { params: { path: { order_id: orderId } }, body: { text } })),
    onSuccess: () => {
      setText("");
      onDone();
    },
  });
  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault();
        send.mutate();
      }}
    >
      <h4>Try the app, then tell the factory what to change</h4>
      <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} required minLength={3} />
      <button disabled={send.isPending}>Send feedback &amp; start next iteration</button>
      {send.error && <p className="error">{send.error.message}</p>}
    </form>
  );
}
