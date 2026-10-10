import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CircleDollarSign,
  MessageSquareText,
  Play,
  RefreshCw,
  Send,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  ACTIVE,
  KIND_LABEL,
  api,
  unwrap,
  type ChangeKind,
  type FactoryEvent,
} from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import AgentCallsPanel from "./AgentCallsPanel";
import AssessmentPanel from "./AssessmentPanel";
import ChangeRiskPanel from "./ChangeRiskPanel";
import EvidencePanel from "./EvidencePanel";
import PillarBars, { type PillarRow } from "./QualityPillars";
import SpecPanel from "./SpecPanel";
import StationStrip from "./StationStrip";
import StatusPill from "./StatusPill";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";

export default function ChangePanel({
  changeId,
  productId,
  archived = false,
  target = "new",
}: {
  changeId: string;
  productId: string;
  archived?: boolean;
  target?: string;
}) {
  const qc = useQueryClient();
  const change = useQuery({
    queryKey: ["change", changeId],
    queryFn: () =>
      unwrap(
        api.GET("/api/changes/{change_id}", { params: { path: { change_id: changeId } } }),
      ),
    refetchInterval: (q) =>
      q.state.data && ACTIVE.has(q.state.data.change.status) ? 2_000 : 8_000,
  });
  const events = useRunEvents(changeId);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["change", changeId] });
    qc.invalidateQueries({ queryKey: ["product", productId] });
  };
  const resume = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/resume", {
          params: { path: { change_id: changeId } },
        }),
      ),
    onSuccess: refresh,
  });
  const cancel = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/cancel", {
          params: { path: { change_id: changeId } },
        }),
      ),
    onSuccess: refresh,
  });

  // the product header (status, archive) follows the change: refresh it whenever the change changes state
  const status = change.data?.change.status;
  useEffect(() => {
    if (status) qc.invalidateQueries({ queryKey: ["product", productId] });
  }, [status, productId, qc]);

  if (!change.data)
    return <div className="h-64 animate-pulse rounded-xl bg-muted" />;
  const { change: r, stations, risk } = change.data;

  return (
    <Card>
      <CardHeader className="border-b pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>Iteration {r.iteration}</CardTitle>
          <StatusPill status={r.status} />
          <span
            className={cn("pill", r.kind === "bug" ? "warn" : "info")}
            title={
              r.requested_by
                ? `Asked by ${r.requested_by} (${r.source})`
                : "Kind of change"
            }
          >
            {KIND_LABEL[r.kind ?? "new"]}
          </span>
          {r.pr_url && (
            <a className="pill ok" href={r.pr_url} target="_blank" rel="noreferrer">
              pull request ↗
            </a>
          )}
          <span
            className="pill muted"
            title="Workflow version this change is pinned to"
          >
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
            <Button
              size="sm"
              onClick={() => resume.mutate()}
              disabled={resume.isPending}
            >
              <Play /> Resume
            </Button>
          )}
          {ACTIVE.has(r.status) && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
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
              <span className="font-medium">
                {r.kind === "bug"
                  ? "Bug report"
                  : r.kind === "upkeep"
                    ? "Upkeep"
                    : "Change request"}
                :
              </span>{" "}
              {r.change_request}
            </span>
          </p>
        )}
        <Problems error={resume.error} />
        <StationStrip stations={stations} />
        {r.kind === "assess" ? (
          <AssessmentPanel
            changeId={changeId}
            runStatus={r.status}
            productId={productId}
            canRequest={r.status === "awaiting_feedback" && !archived}
            onRequested={refresh}
          />
        ) : (
          <SpecPanel changeId={changeId} runStatus={r.status} />
        )}
        {risk && <ChangeRiskPanel changeId={changeId} productId={productId} risk={risk} />}
        {r.summary && (
          <p
            className={cn(
              "m-0 text-sm",
              r.status === "held" ? "error" : "text-muted-foreground",
            )}
          >
            {r.summary}
          </p>
        )}
        {r.status === "held" && r.last_failure && (
          <details open>
            <summary className="font-medium">Evidence</summary>
            <pre className="pre mt-2">{r.last_failure}</pre>
          </details>
        )}
        {r.status === "needs_input" && (
          <Questions
            changeId={changeId}
            questions={r.questions ?? []}
            onDone={refresh}
          />
        )}
        {r.status === "awaiting_feedback" && !archived && (
          <FeedbackForm productId={productId} onDone={refresh} repo={target === "repo"} />
        )}
        {r.status === "awaiting_feedback" && !archived && target === "repo" && (
          <AssessAgain productId={productId} onDone={refresh} />
        )}
        <RunPillars events={events} />
        <AgentCallsPanel changeId={changeId} runStatus={r.status} />
        <EvidencePanel changeId={changeId} runStatus={r.status} />
        <EventLog events={events} live={ACTIVE.has(r.status)} />
      </CardContent>
    </Card>
  );
}

function useRunEvents(changeId: string): FactoryEvent[] {
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
        return fresh.length
          ? [...prev, ...fresh].sort((a, b) => a.id - b.id)
          : prev;
      });
    };
    // Fallback when the live stream can't get through (a proxy, or too many open
    // tabs): fetch whatever was missed over plain HTTP.
    const catchUp = async () => {
      try {
        const res = await api.GET("/api/changes/{change_id}/events", {
          params: { path: { change_id: changeId }, query: { after: last } },
        });
        if (res.data && !closed) add(res.data);
      } catch {
        /* the next reconnect tries again */
      }
    };
    const connect = () => {
      source = new EventSource(`/api/changes/${changeId}/stream?after=${last}`);
      source.addEventListener("event", (m) =>
        add([JSON.parse((m as MessageEvent).data) as FactoryEvent]),
      );
      // The server closes the stream when the change parks; reconnect slowly to catch resumes.
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
  }, [changeId]);
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
    filter === "all"
      ? true
      : filter === "decisions"
        ? e.kind === "decision"
        : e.kind.startsWith("station"),
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
        <span className="text-xs text-muted-foreground">
          {events.length} events
        </span>
        <div className="ml-auto flex gap-1 rounded-lg border bg-card p-0.5">
          {(["all", "decisions", "stations"] as const).map((f) => (
            <button
              key={f}
              className={`tab ${filter === f ? "active" : ""}`}
              onClick={() => setFilter(f)}
            >
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
        {shown.length === 0 && (
          <p className="m-0 p-3 text-muted-foreground">Nothing yet.</p>
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
}

function Questions({
  changeId,
  questions,
  onDone,
}: {
  changeId: string;
  questions: string[];
  onDone: () => void;
}) {
  const [answers, setAnswers] = useState<string[]>(questions.map(() => ""));
  const submit = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/answers", {
          params: { path: { change_id: changeId } },
          body: { answers },
        }),
      ),
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
            onChange={(e) =>
              setAnswers(answers.map((a, j) => (j === i ? e.target.value : a)))
            }
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

/** An existing repo is assessed again on its latest commit (ADR-0031). */
function AssessAgain({ productId, onDone }: { productId: string; onDone: () => void }) {
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: { text: "Assess the latest commit again", kind: "assess" },
        }),
      ),
    onSuccess: () => {
      toast.success("Assessment started", { description: "The latest commit is fetched read-only." });
      onDone();
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border p-4">
      <p className="m-0 flex-1 text-sm text-muted-foreground">
        After the team merges changes, assess the repo again to see where it stands.
      </p>
      <Button variant="outline" disabled={send.isPending} onClick={() => send.mutate()}>
        <RefreshCw /> Assess again
      </Button>
      <Problems error={send.error} />
    </div>
  );
}

type IterationKind = Extract<ChangeKind, "feature" | "bug" | "upkeep">;

/** Kinds of change a delivered product can get (ADR-0030). */
const ITERATION_KINDS: Record<
  IterationKind,
  { title: string; hint: string; placeholder: string }
> = {
  feature: {
    title: "Feature",
    hint: "Add or change behaviour; requirements are updated.",
    placeholder: "e.g. add pagination to the list endpoint…",
  },
  bug: {
    title: "Bug fix",
    hint: "Something doesn't work as a requirement says: the factory reproduces it with a failing test, then fixes it.",
    placeholder: "e.g. tags with spaces are dropped when saving a bookmark…",
  },
  upkeep: {
    title: "Upkeep",
    hint: "Dependencies, tooling, docs or refactoring; behaviour stays the same.",
    placeholder: "e.g. update dependencies to their latest minor versions…",
  },
};

function FeedbackForm({
  productId,
  onDone,
  repo = false,
}: {
  productId: string;
  onDone: () => void;
  repo?: boolean;
}) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<IterationKind>("feature");
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: { text, kind },
        }),
      ),
    onSuccess: () => {
      setText("");
      toast.success(`${ITERATION_KINDS[kind].title} requested`, {
        description: repo ? "The factory will open a pull request." : "The next iteration is starting.",
      });
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
      <h4 className="m-0">
        {repo
          ? "Ask for a change: the factory opens a pull request, and you merge it"
          : "Try the app, then tell the factory what to change"}
      </h4>
      <div
        className="flex w-fit flex-wrap gap-1 rounded-lg border bg-card p-1"
        role="radiogroup"
        aria-label="Kind of change"
      >
        {(Object.keys(ITERATION_KINDS) as IterationKind[]).map((k) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={k === kind}
            className={cn("tab", k === kind && "active")}
            onClick={() => setKind(k)}
            title={ITERATION_KINDS[k].hint}
          >
            {ITERATION_KINDS[k].title}
          </button>
        ))}
      </div>
      <p className="m-0 text-xs text-muted-foreground">
        {repo && kind === "feature" ? "Add or change behaviour, with tests that prove it." : ITERATION_KINDS[kind].hint}
      </p>
      <textarea
        className="font-sans text-sm"
        placeholder={ITERATION_KINDS[kind].placeholder}
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        required
        minLength={3}
      />
      <Button className="w-fit" disabled={send.isPending}>
        <Send /> Request {ITERATION_KINDS[kind].title.toLowerCase()}
        {repo ? " as a pull request" : " & start next iteration"}
      </Button>
      <Problems error={send.error} />
    </form>
  );
}

/** The latest readiness scorecard of this change, by quality pillar (ADR-0024). */
function RunPillars({ events }: { events: FactoryEvent[] }) {
  const card = [...events]
    .reverse()
    .map(
      (e) =>
        (e.data as { readiness?: { level?: number; pillars?: PillarRow[] } })
          .readiness,
    )
    .find((x) => x?.pillars);
  if (!card?.pillars) return null;
  const uncovered = card.pillars.filter((p) => !p.applicable).length;
  return (
    <details
      className="rounded-xl border bg-card p-4"
      data-testid="change-pillars"
    >
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <span className="font-semibold">Quality pillars</span>
        <span className="pill muted">Level {card.level}</span>
        {uncovered > 0 && (
          <span className="pill muted">{uncovered} uncovered</span>
        )}
      </summary>
      <div className="mt-3">
        <PillarBars rows={card.pillars} />
      </div>
    </details>
  );
}
