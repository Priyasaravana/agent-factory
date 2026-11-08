import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  ChevronsDownUp,
  ChevronsUpDown,
  CircleDollarSign,
  HelpCircle,
  Layers3,
  MessageSquareText,
  Play,
  RefreshCw,
  Send,
  Shuffle,
  Sparkles,
  OctagonAlert,
} from "lucide-react";
import { useEffect, useState } from "react";
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
import ProvidedArtifacts from "./ProvidedArtifacts";
import PullRequestStatus from "./PullRequestStatus";
import AgentCallsPanel from "./AgentCallsPanel";
import AssessmentPanel from "./AssessmentPanel";
import ChangeOutcome from "./ChangeOutcome";
import ChangeVerdict from "./ChangeVerdict";
import EventLog from "./EventLog";
import ToolsPanel from "./ToolsPanel";
import ScreenshotsPanel from "./ScreenshotsPanel";
import ChangeRiskPanel from "./ChangeRiskPanel";
import EvidencePanel from "./EvidencePanel";
import PillarBars, { type PillarRow } from "./QualityPillars";
import DecisionsPanel from "./DecisionsPanel";
import SpecPanel from "./SpecPanel";
import SchemaHold from "./SchemaHold";
import IterationSummary from "./IterationSummary";
import { buildsOf } from "./StackCards";
import StationStrip from "./StationStrip";
import StatusPill from "./StatusPill";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Panel, setAllPanels } from "./ui/panel";

/** Stopped short of done, waiting for a person or the clock: the hold panel explains and resumes. */
const STOPPED = new Set(["held", "interrupted", "paused_limits"]);

export default function ChangePanel({
  changeId,
  productId,
  archived = false,
  target = "new",
  canSteer = true,
  simple = false,
}: {
  changeId: string;
  productId: string;
  archived?: boolean;
  target?: string;
  /** the product's creator or an admin: may accept or close it (the engine checks it too) */
  canSteer?: boolean;
  /** the product page's overview: what a person acts on; the details live in the advanced view */
  simple?: boolean;
}) {
  const qc = useQueryClient();
  const change = useQuery({
    queryKey: ["change", changeId],
    queryFn: () =>
      unwrap(
        api.GET("/api/changes/{change_id}", {
          params: { path: { change_id: changeId } },
        }),
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
          {r.outcome && (
            <span className={cn("pill", ["accepted", "merged"].includes(r.outcome.state) ? "ok" : "muted")}>
              {r.outcome.state}
            </span>
          )}
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
          <PullRequestStatus change={r} />
          <a
            className="pill muted no-underline"
            href={`/workflows/${r.workflow_id ?? ""}`}
            title="The lane this change runs on, and the version it is pinned to (a later restore or publish doesn't change it)"
            data-testid="change-lane"
          >
            Lane: {r.workflow_id ?? "workflow"} v{r.workflow_version}
          </a>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
            <span
              className="inline-flex items-center gap-1"
              title={
                r.gateway_spend_usd
                  ? "Model spend on two meters: the agent CLI's own price, and the model gateway's (its per-call cap counts on the gateway's)"
                  : "Model spend, as the agent CLI prices it"
              }
            >
              <CircleDollarSign className="size-3.5" />${r.cost_usd.toFixed(2)}
              {r.gateway_spend_usd ? (
                <span>· gateway ${r.gateway_spend_usd.toFixed(2)}</span>
              ) : null}
            </span>
            <RefreshCw className="ml-2 size-3.5" />
            {r.loops} {r.loops === 1 ? "loop" : "loops"}
          </span>
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
          <span className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setAllPanels(true)} title="Open every panel">
              <ChevronsUpDown /> <span className="hidden sm:inline">Expand all</span>
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setAllPanels(false)} title="Close every panel">
              <ChevronsDownUp /> <span className="hidden sm:inline">Collapse all</span>
            </Button>
          </span>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-[minmax(0,1fr)] gap-4 pt-4">
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
        {!STOPPED.has(r.status) && <Problems error={resume.error} />}
        <StationStrip
          stations={stations.filter((s) => s.state !== "skipped")}
        />
        {stations.some((s) => s.state === "skipped") && (
          <details
            className="text-xs text-muted-foreground"
            data-testid="skipped-stations"
          >
            <summary>
              {stations.filter((s) => s.state === "skipped").length} stations
              skipped in this change
            </summary>
            <ul className="mt-1 list-none p-0">
              {stations
                .filter((s) => s.state === "skipped")
                .map((s) => (
                  <li key={s.id}>
                    {s.label || s.id}: {s.skip_reason}
                  </li>
                ))}
            </ul>
          </details>
        )}
        <ChangeOutcome changeId={changeId} runStatus={r.status} />
        <ChangeVerdict change={r} productId={productId} canChange={canSteer && !archived} />
        {r.kind !== "assess" && r.status === "awaiting_feedback" && (
          <IterationSummary
            changeId={changeId}
            productId={productId}
            runStatus={r.status}
            iteration={r.iteration}
          />
        )}
        {r.kind === "assess" ? (
          <AssessmentPanel
            changeId={changeId}
            runStatus={r.status}
            productId={productId}
            canRequest={r.status === "awaiting_feedback" && !archived}
            onRequested={refresh}
          />
        ) : (
          <>
            {!simple && <DecisionsPanel changeId={changeId} runStatus={r.status} />}
            {!simple && <SpecPanel changeId={changeId} runStatus={r.status} />}
          </>
        )}
        {risk && (
          <ChangeRiskPanel
            changeId={changeId}
            productId={productId}
            risk={risk}
          />
        )}
        {r.summary && !STOPPED.has(r.status) && (
          <p className="m-0 text-sm text-muted-foreground">{r.summary}</p>
        )}
        {r.status === "running" && r.validation && !r.validation.removed_at && (
          <p className="m-0 text-sm text-muted-foreground" data-testid="validation-env">
            Testing in a temporary environment; production is untouched until promote:{" "}
            {r.validation.url ? (
              <a
                href={r.validation.url}
                target="_blank"
                rel="noreferrer"
                className="text-foreground underline underline-offset-2"
              >
                {r.validation.url}
              </a>
            ) : (
              <code>{r.validation.helm_release}</code>
            )}
          </p>
        )}
        {STOPPED.has(r.status) && (
          <Panel
            id="hold"
            tone="attention"
            attention
            data-testid="hold-panel"
            icon={<OctagonAlert className="text-warn" />}
            title={
              r.status === "held"
                ? r.hold_class === "infra"
                  ? "Held: the factory failed here"
                  : "Held: the change needs a person"
                : r.status === "interrupted"
                  ? "Interrupted: the factory restarted"
                  : "Paused for the model usage window"
            }
            summary={r.summary ? <span className="text-foreground">{r.summary}</span> : undefined}
            right={
              r.status !== "paused_limits" && (
                <Button size="sm" onClick={() => resume.mutate()} disabled={resume.isPending}>
                  <Play /> Resume
                </Button>
              )
            }
          >
            {r.status === "held" && r.hold_class === "infra" && (
              <p className="note m-0" data-testid="hold-infra">
                The factory failed here, not the product: retries didn't cure it,
                and nothing was sent to the developer. Check what the hold names,
                then resume.
              </p>
            )}
            {r.status === "held" &&
              (r.required_action?.kind === "approve_schema" ||
                r.required_action?.kind === "schema_in_place" ||
                r.required_action?.kind === "colors_in_place") && (
                <SchemaHold change={r} productId={productId} onDone={refresh} />
              )}
            {r.status === "held" && r.required_action?.kind === "lane_without_promote" && (
              <div className="note m-0 grid gap-1" data-testid="lane-without-promote">
                <p className="m-0">
                  <span className="font-medium">Production was not touched.</span> {r.required_action.text}
                </p>
                <p className="m-0 flex flex-wrap gap-x-4">
                  <a href={`/workflows/${r.workflow_id ?? ""}`}>Open the lane</a>
                  <a href={`/products/${productId}#releases`}>Open Releases</a>
                </p>
              </div>
            )}
            {r.status === "held" && r.required_action && r.required_action.kind === "accept_risk" && (
              <p className="note m-0" data-testid="required-action">
                <span className="font-medium">Before you resume:</span>{" "}
                {r.required_action.text}
              </p>
            )}
            {r.status === "held" && r.last_failure ? (
              <div className="grid gap-1">
                <span className="text-sm font-medium">Evidence</span>
                <pre className="pre m-0">{r.last_failure}</pre>
              </div>
            ) : (
              <p className="m-0 text-sm text-muted-foreground">
                {r.status === "paused_limits"
                  ? "Nothing to do: the change resumes by itself when the window resets."
                  : "Resume to carry on from the station it stopped at."}
              </p>
            )}
            <Problems error={resume.error} />
          </Panel>
        )}
        {r.status === "needs_input" && (
          <Panel
            id="questions"
            tone="attention"
            attention
            icon={<HelpCircle className="text-warn" />}
            title="Intake needs a decision from you"
            summary={`${(r.questions ?? []).length} question${(r.questions ?? []).length === 1 ? "" : "s"}`}
          >
            <Questions
              changeId={changeId}
              questions={r.questions ?? []}
              switchTo={r.switch_to ?? []}
              onDone={refresh}
            />
          </Panel>
        )}
        {/* a factory product picks its next iteration on the "Next iteration" card at the top of the page */}
        {r.status === "awaiting_feedback" && !archived && target === "repo" && (
          <Panel
            id="feedback"
            defaultOpen
            tone="ok"
            icon={<Sparkles className="text-ok" />}
            title="Ask for a change: the factory opens a pull request, and you merge it"
            summary="Feature, bug fix or upkeep: the next iteration starts when you send it"
          >
            <FeedbackForm productId={productId} onDone={refresh} repo />
          </Panel>
        )}
        {r.status === "awaiting_feedback" && !archived && target === "repo" && (
          <AssessAgain productId={productId} onDone={refresh} />
        )}
        {simple ? (
          <p className="m-0 text-sm text-muted-foreground" data-testid="more-in-advanced">
            Decisions, the spec, quality pillars, screenshots, tools, agent calls and evidence are in the{" "}
            <a href="?view=advanced#change">Advanced</a> view.
          </p>
        ) : (
          <>
            <RunPillars events={events} />
            <ScreenshotsPanel changeId={changeId} runStatus={r.status} />
            <ToolsPanel changeId={changeId} runStatus={r.status} />
            <AgentCallsPanel changeId={changeId} runStatus={r.status} />
            <EvidencePanel changeId={changeId} runStatus={r.status} />
          </>
        )}
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

function Questions({
  changeId,
  questions,
  switchTo,
  onDone,
}: {
  changeId: string;
  questions: string[];
  switchTo: string[];
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
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit.mutate();
      }}
    >
      {switchTo.length > 0 && (
        <SwitchStack changeId={changeId} switchTo={switchTo} onDone={onDone} />
      )}
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

/** A stack conflict before the first build: build on a stack that does what was asked (ADR-0041 §2). */
function SwitchStack({
  changeId,
  switchTo,
  onDone,
}: {
  changeId: string;
  switchTo: string[];
  onDone: () => void;
}) {
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => unwrap(api.GET("/api/config")),
  });
  const cards = new Map(
    (config.data?.stacks ?? []).map((c) => [c.blueprint, c]),
  );
  const send = useMutation({
    mutationFn: (blueprint: string) =>
      unwrap(
        api.POST("/api/changes/{change_id}/switch-stack", {
          params: { path: { change_id: changeId } },
          body: { blueprint },
        }),
      ),
    onSuccess: (_d, blueprint) => {
      toast.success("Stack switched", {
        description: `${cards.get(blueprint)?.title ?? blueprint}: intake runs again on the new stack.`,
      });
      onDone();
    },
  });
  return (
    <div className="grid gap-2 rounded-lg border bg-card p-3">
      <p className="m-0 text-sm">
        <span className="font-medium">Nothing has been built yet,</span>{" "}
        <span className="text-muted-foreground">
          so this product can switch to a stack that builds what was asked:
        </span>
      </p>
      <div className="grid gap-2">
        {switchTo.map((bp) => {
          const c = cards.get(bp);
          const { label, icon: Icon } = c
            ? buildsOf(c)
            : { label: "", icon: Shuffle };
          return (
            <div key={bp} className="flex flex-wrap items-center gap-3">
              <span
                aria-hidden
                className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"
              >
                <Icon className="size-4" />
              </span>
              <span className="grid min-w-0 flex-1">
                <span className="text-sm font-semibold">
                  {c?.title ?? bp}{" "}
                  {label && (
                    <span className="font-normal text-muted-foreground">
                      · {label}
                    </span>
                  )}
                </span>
                {c?.summary && (
                  <span className="text-xs text-muted-foreground">
                    {c.summary}
                  </span>
                )}
              </span>
              <Button
                type="button"
                variant="outline"
                disabled={send.isPending}
                onClick={() => send.mutate(bp)}
              >
                <Shuffle /> Switch to {c?.title ?? bp}
              </Button>
            </div>
          );
        })}
      </div>
      <Problems error={send.error} />
      <p className="m-0 text-xs text-muted-foreground">
        Or answer below to keep the current stack.
      </p>
    </div>
  );
}

/** An existing repo is assessed again on its latest commit (ADR-0031). */
function AssessAgain({
  productId,
  onDone,
}: {
  productId: string;
  onDone: () => void;
}) {
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: { text: "Assess the latest commit again", kind: "assess" },
        }),
      ),
    onSuccess: () => {
      toast.success("Assessment started", {
        description: "The latest commit is fetched read-only.",
      });
      onDone();
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border p-4">
      <p className="m-0 flex-1 text-sm text-muted-foreground">
        After the team merges changes, assess the repo again to see where it
        stands.
      </p>
      <Button
        variant="outline"
        disabled={send.isPending}
        onClick={() => send.mutate()}
      >
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
  // entry artifacts from your own tools, when this product's lane starts after the phases that make them
  const [provided, setProvided] = useState<Record<
    string,
    Record<string, unknown>
  > | null>({});
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: { text, kind, provided: provided ?? {} },
        }),
      ),
    onSuccess: () => {
      setText("");
      toast.success(`${ITERATION_KINDS[kind].title} requested`, {
        description: repo
          ? "The factory will open a pull request."
          : "The next iteration is starting.",
      });
      onDone();
    },
  });
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        send.mutate();
      }}
    >
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
        {repo && kind === "feature"
          ? "Add or change behaviour, with tests that prove it."
          : ITERATION_KINDS[kind].hint}
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
      <ProvidedArtifacts
        productId={productId}
        kind={kind}
        onChange={setProvided}
      />
      <Button className="w-fit" disabled={send.isPending || provided === null}>
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
    <Panel
      id="pillars"
      data-testid="change-pillars"
      icon={<Layers3 className="text-primary" />}
      title="Quality pillars"
      summary={
        <>
          <span className={cn("pill", (card.level ?? 0) >= 3 ? "ok" : "warn")}>Level {card.level}</span>
          {uncovered > 0 && <span className="pill muted">{uncovered} uncovered</span>}
        </>
      }
    >
      <PillarBars rows={card.pillars} />
    </Panel>
  );
}
