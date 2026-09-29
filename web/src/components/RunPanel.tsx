import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CircleDollarSign, MessageSquareText, Play, RefreshCw, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ACTIVE, api, unwrap, type FactoryEvent } from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import StationStrip from "./StationStrip";
import StatusPill from "./StatusPill";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";

export default function RunPanel({
  runId,
  orderId,
  archived = false,
}: {
  runId: string;
  orderId: string;
  archived?: boolean;
}) {
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

  if (!run.data) return <div className="h-64 animate-pulse rounded-xl bg-muted" />;
  const { run: r, stations } = run.data;

  return (
    <Card>
      <CardHeader className="border-b pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>Iteration {r.iteration}</CardTitle>
          <StatusPill status={r.status} />
          <span className="pill muted" title="Workflow version this run is pinned to">
            {r.workflow_id ?? "workflow"} v{r.workflow_version}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
            <CircleDollarSign className="size-3.5" />${r.cost_usd.toFixed(2)}
            <RefreshCw className="ml-2 size-3.5" />
            {r.loops} {r.loops === 1 ? "loop" : "loops"}
          </span>
          {["held", "interrupted", "paused_limits"].includes(r.status) && (
            <Button size="sm" onClick={() => resume.mutate()} disabled={resume.isPending}>
              <Play /> Resume
            </Button>
          )}
          {ACTIVE.has(r.status) && (
            <Button size="sm" variant="outline" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              <Ban /> Cancel
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 pt-4">
        {r.change_request && (
          <p className="note m-0 flex items-start gap-2">
            <MessageSquareText className="mt-0.5 size-4 shrink-0 text-info" />
            <span>
              <span className="font-medium">Change request:</span> {r.change_request}
            </span>
          </p>
        )}
        <Problems error={resume.error} />
        <StationStrip stations={stations} />
        {r.summary && (
          <p className={cn("m-0 text-sm", r.status === "held" ? "error" : "text-muted-foreground")}>{r.summary}</p>
        )}
        {r.status === "held" && r.last_failure && (
          <details open>
            <summary className="font-medium">Evidence</summary>
            <pre className="pre mt-2">{r.last_failure}</pre>
          </details>
        )}
        {r.status === "needs_input" && <Questions runId={runId} questions={r.questions ?? []} onDone={refresh} />}
        {r.status === "awaiting_feedback" && !archived && <FeedbackForm orderId={orderId} onDone={refresh} />}
        <EventLog events={events} live={ACTIVE.has(r.status)} />
      </CardContent>
    </Card>
  );
}

function useRunEvents(runId: string): FactoryEvent[] {
  const [events, setEvents] = useState<FactoryEvent[]>([]);
  useEffect(() => {
    setEvents([]);
    let last = 0;
    let source: EventSource | null = null;
    let closed = false;
    const add = (batch: FactoryEvent[]) => {
      if (!batch.length) return;
      last = Math.max(last, ...batch.map((e) => e.id));
      setEvents((prev) => {
        const seen = new Set(prev.map((p) => p.id));
        const fresh = batch.filter((e) => !seen.has(e.id));
        return fresh.length ? [...prev, ...fresh].sort((a, b) => a.id - b.id) : prev;
      });
    };
    // Fallback when the live stream can't get through (a proxy, or too many open
    // tabs): fetch whatever was missed over plain HTTP.
    const catchUp = async () => {
      try {
        const res = await api.GET("/api/runs/{run_id}/events", {
          params: { path: { run_id: runId }, query: { after: last } },
        });
        if (res.data && !closed) add(res.data);
      } catch {
        /* the next reconnect tries again */
      }
    };
    const connect = () => {
      source = new EventSource(`/api/runs/${runId}/stream?after=${last}`);
      source.addEventListener("event", (m) => add([JSON.parse((m as MessageEvent).data) as FactoryEvent]));
      // The server closes the stream when the run parks; reconnect slowly to catch resumes.
      source.onerror = () => {
        source?.close();
        void catchUp();
        if (!closed) setTimeout(connect, 5_000);
      };
    };
    void catchUp();
    connect();
    return () => {
      closed = true;
      source?.close();
    };
  }, [runId]);
  return events;
}

function EventLog({ events, live }: { events: FactoryEvent[]; live: boolean }) {
  const [filter, setFilter] = useState<"all" | "decisions" | "stations">("all");
  const bottom = useRef<HTMLDivElement>(null);
  // Block body on purpose: newer Chrome returns a Promise from scrollIntoView, and
  // React would call a returned value as the effect's cleanup (blank page).
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [events.length]);
  const shown = events.filter((e) =>
    filter === "all" ? true : filter === "decisions" ? e.kind === "decision" : e.kind.startsWith("station"),
  );
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h4 className="m-0 flex items-center gap-2">
          Activity
          {live && (
            <span className="relative flex size-2" title="Live">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-ok opacity-60" />
              <span className="relative inline-flex size-2 rounded-full bg-ok" />
            </span>
          )}
        </h4>
        <span className="text-xs text-muted-foreground">{events.length} events</span>
        <div className="ml-auto flex gap-1 rounded-lg border bg-card p-0.5">
          {(["all", "decisions", "stations"] as const).map((f) => (
            <button key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
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
        {shown.length === 0 && <p className="m-0 p-3 text-muted-foreground">Nothing yet.</p>}
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
      className="rounded-xl border border-warn/50 bg-warn/5 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit.mutate();
      }}
    >
      <h4 className="m-0">Intake needs a decision from you</h4>
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
      <Button className="w-fit" disabled={submit.isPending}>
        <Send /> Send answers
      </Button>
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
      toast.success("Feedback sent", { description: "The next iteration is starting." });
      onDone();
    },
  });
  return (
    <form
      className="rounded-xl border border-ok/40 bg-ok/5 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        send.mutate();
      }}
    >
      <h4 className="m-0">Try the app, then tell the factory what to change</h4>
      <textarea className="font-sans text-sm" placeholder="e.g. add pagination to the list endpoint…" rows={4} value={text} onChange={(e) => setText(e.target.value)} required minLength={3} />
      <Button className="w-fit" disabled={send.isPending}>
        <Send /> Send feedback &amp; start next iteration
      </Button>
      <Problems error={send.error} />
    </form>
  );
}
