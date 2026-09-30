import { useQuery } from "@tanstack/react-query";
import { Clock, HelpCircle, UserRound } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import Problems from "../components/Problems";
import StatusPill from "../components/StatusPill";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { Tip } from "../components/ui/tooltip";
import { cn } from "../lib/utils";

type Outcomes = components["schemas"]["OutcomesView"];
type Week = components["schemas"]["WeekPoint"];

const WINDOWS = [7, 30, 90] as const;

/** 45s, 12m, 3h 20m, 2d 4h */
export function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86_400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(s / 86_400);
  const h = Math.round((s % 86_400) / 3600);
  return h ? `${d}d ${h}h` : `${d}d`;
}
const pct = (r: number | null | undefined) =>
  r == null ? "—" : `${Math.round(r * 100)}%`;
const usd = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toFixed(2)}`;

export default function OutcomesPage() {
  const [days, setDays] = useState<number>(30);
  const q = useQuery({
    queryKey: ["outcomes", days],
    queryFn: () =>
      unwrap(api.GET("/api/outcomes", { params: { query: { days } } })),
    refetchInterval: 30_000,
  });
  const o = q.data;

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="m-0 text-2xl font-semibold tracking-tight">
            Outcomes
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Is the factory delivering, how autonomously, at what cost, and who
            is it waiting on? Hover any number for its exact definition.
          </p>
        </div>
        <div
          className="flex gap-1 rounded-lg border bg-card p-0.5"
          role="tablist"
          aria-label="Time window"
        >
          {WINDOWS.map((d) => (
            <button
              key={d}
              role="tab"
              aria-selected={days === d}
              className={cn("tab", days === d && "active")}
              onClick={() => setDays(d)}
            >
              {d} days
            </button>
          ))}
        </div>
      </div>
      <Problems error={q.error ?? null} />
      {q.isLoading && (
        <div className="h-40 animate-pulse rounded-xl bg-muted" />
      )}
      {o && (
        <>
          <Kpis o={o} />
          <Waiting o={o} />
          <TimeSplit o={o} />
          <Weekly weeks={o.weekly ?? []} />
          <Effort o={o} />
          <ByWorkflow o={o} />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- KPIs --
function Kpis({ o }: { o: Outcomes }) {
  const lead = o.lead_time;
  return (
    <section
      className="grid grid-cols-[repeat(auto-fit,minmax(10.5rem,1fr))] gap-3"
      aria-label="Key outcomes"
      data-testid="kpis"
    >
      <Stat
        label="Deliveries"
        value={String(o.deliveries)}
        sub={`${o.deliveries_per_week} per week`}
        help="Iterations that reached “delivered” (running, verified by hidden scenarios, feedback gate open) in this window."
      />
      <Stat
        label="Lead time"
        value={duration(lead.median_s)}
        sub={
          lead.n
            ? `median · p90 ${duration(lead.p90_s)} · ${lead.n} deliveries`
            : "no deliveries yet"
        }
        help="From the moment an iteration was requested (order or feedback) until it was delivered, including any time spent waiting on people."
      />
      <Stat
        label="Change failure rate"
        value={pct(o.change_failure_rate)}
        sub={
          o.finished
            ? `${o.failed_or_rescued} of ${o.finished} failed or needed rescue${o.recovery_time?.n ? ` · recovery ${duration(o.recovery_time.median_s)}` : ""}`
            : "nothing finished yet"
        }
        help="Of the iterations that finished (delivered or failed; cancelled ones excluded), the share that failed or was held and needed a person to rescue it. Recovery: median time from first held to delivered."
      />
      <Stat
        label="Autonomy"
        value={pct(o.autonomy_ratio)}
        sub={
          o.deliveries
            ? `${o.unplanned_touches_per_delivery} unplanned touches per delivery`
            : "no deliveries yet"
        }
        help="Share of deliveries that needed no unplanned human help: no blocking questions, no rescue from held, no resume after a restart. Spec reviews and feedback are planned touches and do not count against autonomy."
      />
      <Stat
        label="Cost per delivered change"
        value={usd(o.cost_per_delivery_usd)}
        sub={`${usd(o.cost_total_usd)} spent in total`}
        help="All model spend of iterations started in this window (failed ones included) divided by deliveries."
      />
      <Stat
        label="Live apps at Level 3"
        value={o.products ? `${o.products_level3}/${o.products}` : "—"}
        sub={
          o.requirements_total
            ? `${o.requirements_verified_live}/${o.requirements_total} requirements verified live`
            : "no traced requirements yet"
        }
        help="Apps (not archived) whose latest delivery scored agent-readiness Level 3, and how many of their requirements were verified on the running app by hidden scenarios that all passed."
      />
    </section>
  );
}

function Stat({
  label,
  value,
  sub,
  help,
}: {
  label: string;
  value: string;
  sub: string;
  help: string;
}) {
  return (
    <div className="grid gap-1 rounded-xl border bg-card p-4">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        <Tip label={<span className="block max-w-72">{help}</span>} side="top">
          <button className="help" aria-label={`How ${label} is measured`}>
            <HelpCircle className="size-3.5" />
          </button>
        </Tip>
      </div>
      <div className="text-2xl font-semibold tabular-nums tracking-tight">
        {value}
      </div>
      <div className="text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

// ---------------------------------------------------------- waiting board --
function Waiting({ o }: { o: Outcomes }) {
  const items = o.waiting ?? [];
  const people = items.filter((w) => w.owner !== "system");
  return (
    <Card data-testid="waiting">
      <CardHeader>
        <div>
          <CardTitle>Waiting on a person now</CardTitle>
          <CardDescription>
            Runs that cannot continue until someone acts, longest wait first.
            The factory never waits silently.
          </CardDescription>
        </div>
        <span className={cn("pill", people.length ? "warn" : "ok")}>
          {people.length ? `${people.length} waiting` : "nobody"}
        </span>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">
            Nobody is blocking the factory right now.
          </p>
        ) : (
          <table className="wide">
            <thead>
              <tr>
                <td>order</td>
                <td>status</td>
                <td>what to do</td>
                <td>who</td>
                <td>waiting</td>
              </tr>
            </thead>
            <tbody>
              {items.map((w) => (
                <tr key={w.run_id}>
                  <td>
                    <Link to={`/orders/${w.order_id}`}>{w.order_title}</Link>
                    <span className="ml-1 text-xs text-muted-foreground">
                      iteration {w.iteration}
                    </span>
                  </td>
                  <td>
                    <StatusPill status={w.status} />
                  </td>
                  <td className="text-sm">{w.action}</td>
                  <td className="text-sm">
                    <span className="inline-flex items-center gap-1">
                      <UserRound className="size-3.5 text-muted-foreground" />
                      {w.owner}
                    </span>
                  </td>
                  <td className="text-sm tabular-nums">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="size-3.5 text-muted-foreground" />
                      {duration(w.waiting_s)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------- where time goes --
const BUCKETS = [
  {
    key: "agents_s",
    label: "Agents working",
    color: "var(--series-1)",
    help: "queued or running",
  },
  {
    key: "person_s",
    label: "Waiting on a person",
    color: "var(--series-2)",
    help: "questions, held, spec review, interrupted",
  },
  {
    key: "system_s",
    label: "Waiting on the system",
    color: "var(--series-3)",
    help: "paused for the model usage window",
  },
] as const;

function TimeSplit({ o }: { o: Outcomes }) {
  const s = o.time_split;
  const total = BUCKETS.reduce((n, b) => n + s[b.key], 0);
  return (
    <Card data-testid="time-split">
      <CardHeader>
        <div>
          <CardTitle>Where the time goes</CardTitle>
          <CardDescription>
            Run time in this window by who the run was waiting on. Based on{" "}
            {o.runs_with_timeline} of {o.runs_in_window} runs
            {o.runs_with_timeline < o.runs_in_window
              ? " (older runs have no timeline)"
              : ""}
            .
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        {total === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">
            No run time recorded in this window yet.
          </p>
        ) : (
          <>
            <div
              className="flex h-4 w-full gap-0.5"
              role="img"
              aria-label="Run time split"
            >
              {BUCKETS.filter((b) => s[b.key] > 0).map((b) => (
                <Tip
                  key={b.key}
                  side="top"
                  label={`${b.label}: ${duration(s[b.key])} (${Math.round((100 * s[b.key]) / total)}%)`}
                >
                  <div
                    className="h-full rounded-[4px] first:rounded-l-[4px] last:rounded-r-[4px]"
                    style={{
                      width: `${(100 * s[b.key]) / total}%`,
                      minWidth: 4,
                      background: b.color,
                    }}
                  />
                </Tip>
              ))}
            </div>
            <ul className="m-0 grid list-none gap-1 p-0 text-sm sm:grid-cols-3">
              {BUCKETS.map((b) => (
                <li key={b.key} className="flex items-baseline gap-2">
                  <span
                    className="inline-block size-2.5 shrink-0 translate-y-[1px] rounded-sm"
                    style={{ background: b.color }}
                  />
                  <span>
                    <span className="font-medium">{b.label}</span>{" "}
                    <span className="tabular-nums">
                      {duration(s[b.key])} ·{" "}
                      {Math.round((100 * s[b.key]) / total)}%
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {b.help}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            <p className="m-0 text-xs text-muted-foreground">
              Human touches in this window: {o.touches.answered_questions}{" "}
              answered questions, {o.touches.rescued} rescues,{" "}
              {o.touches.restarts} restarts (unplanned) ·{" "}
              {o.touches.spec_reviews} spec reviews (planned).
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------- weekly --
/** The element's rendered width, so SVG text and marks stay at their real pixel size. */
function useWidth<T extends HTMLElement>(
  fallback: number,
): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.max(240, Math.round(e.contentRect.width))),
    );
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function Weekly({ weeks }: { weeks: Week[] }) {
  const max = Math.max(1, ...weeks.map((w) => w.deliveries));
  const [box, W] = useWidth<HTMLDivElement>(640);
  const H = 160;
  const pad = { l: 28, r: 8, t: 8, b: 22 };
  const slot = (W - pad.l - pad.r) / Math.max(1, weeks.length);
  const bw = Math.min(28, slot * 0.5);
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const label = (w: Week) =>
    new Date(w.week_start).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  return (
    <Card data-testid="weekly">
      <CardHeader>
        <div>
          <CardTitle>Deliveries per week</CardTitle>
          <CardDescription>
            Hover a week for its cost and median lead time.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="grid gap-2">
        <div ref={box} className="w-full">
          <svg
            width={W}
            height={H}
            viewBox={`0 0 ${W} ${H}`}
            className="block"
            role="img"
            aria-label="Deliveries per week"
          >
            {[0, max].map((v) => (
              <g key={v}>
                <line
                  x1={pad.l}
                  x2={W - pad.r}
                  y1={y(v)}
                  y2={y(v)}
                  className="stroke-border"
                  strokeWidth={1}
                />
                <text
                  x={pad.l - 6}
                  y={y(v) + 4}
                  textAnchor="end"
                  className="fill-muted-foreground text-[11px]"
                >
                  {v}
                </text>
              </g>
            ))}
            {weeks.map((w, i) => {
              const cx = pad.l + slot * i + slot / 2;
              const top = y(w.deliveries);
              const h = y(0) - top;
              return (
                <Tip
                  key={w.week_start}
                  side="top"
                  label={
                    <span className="grid">
                      <strong>Week of {label(w)}</strong>
                      <span>{w.deliveries} deliveries</span>
                      <span>{usd(w.cost_usd)} spent</span>
                      <span>
                        median lead time {duration(w.lead_time_median_s)}
                      </span>
                    </span>
                  }
                >
                  <g className="cursor-default">
                    {/* hit target: the whole column, bigger than the mark */}
                    <rect
                      x={cx - slot / 2}
                      y={pad.t}
                      width={slot}
                      height={H - pad.t - pad.b}
                      fill="transparent"
                    />
                    {h > 0 && (
                      <path
                        d={`M${cx - bw / 2},${y(0)} V${top + 4} Q${cx - bw / 2},${top} ${cx - bw / 2 + 4},${top} H${cx + bw / 2 - 4} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + 4} V${y(0)} Z`}
                        fill="var(--series-1)"
                      />
                    )}
                    <text
                      x={cx}
                      y={H - 6}
                      textAnchor="middle"
                      className="fill-muted-foreground text-[11px]"
                    >
                      {label(w)}
                    </text>
                  </g>
                </Tip>
              );
            })}
          </svg>
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Show as table
          </summary>
          <table className="wide mt-2">
            <thead>
              <tr>
                <td>week of</td>
                <td>deliveries</td>
                <td>spent</td>
                <td>median lead time</td>
              </tr>
            </thead>
            <tbody>
              {weeks.map((w) => (
                <tr key={w.week_start}>
                  <td>{label(w)}</td>
                  <td className="tabular-nums">{w.deliveries}</td>
                  <td className="tabular-nums">{usd(w.cost_usd)}</td>
                  <td className="tabular-nums">
                    {duration(w.lead_time_median_s)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------ by workflow --
function ByWorkflow({ o }: { o: Outcomes }) {
  const rows = o.by_workflow ?? [];
  if (!rows.length) return null;
  return (
    <Card data-testid="by-workflow">
      <CardHeader>
        <div>
          <CardTitle>By workflow</CardTitle>
          <CardDescription>
            Compare product lines, or a workflow before and after a change.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <table className="wide">
          <thead>
            <tr>
              <td>workflow</td>
              <td>deliveries</td>
              <td>autonomy</td>
              <td>cost per delivery</td>
              <td>median lead time</td>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.workflow_id}>
                <td>
                  <Link to={`/workflows/${r.workflow_id}`}>
                    {r.workflow_id}
                  </Link>
                </td>
                <Num>{r.deliveries}</Num>
                <Num>{pct(r.autonomy_ratio)}</Num>
                <Num>{usd(r.cost_per_delivery_usd)}</Num>
                <Num>{duration(r.lead_time_median_s)}</Num>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

const Num = ({ children }: { children: ReactNode }) => (
  <td className="tabular-nums">{children}</td>
);

// ---------------------------------------------------------------- effort --
function Effort({ o }: { o: Outcomes }) {
  const rows = o.effort_by_station ?? [];
  if (!rows.length) return null;
  return (
    <Card data-testid="effort">
      <CardHeader>
        <div>
          <CardTitle>Agent effort by station</CardTitle>
          <CardDescription>
            Every agent call in this window: where the money and time go, and
            what the guardrails refused. Open a run's Agent calls for
            transcripts.
          </CardDescription>
        </div>
        <span className={cn("pill", o.guardrail_denials ? "bad" : "ok")}>
          {o.guardrail_denials
            ? `${o.guardrail_denials} guardrail denials`
            : "no guardrail denials"}
        </span>
      </CardHeader>
      <CardContent>
        <table className="wide">
          <thead>
            <tr>
              <td>station</td>
              <td>calls</td>
              <td>cost</td>
              <td>median turns</td>
              <td>median time</td>
              <td>tool calls</td>
              <td>denied</td>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.station}>
                <td className="font-mono text-xs">{r.station}</td>
                <Num>{r.calls}</Num>
                <Num>{usd(r.cost_usd)}</Num>
                <Num>{r.turns_median ?? "—"}</Num>
                <Num>{duration(r.duration_median_s)}</Num>
                <Num>{r.tool_calls}</Num>
                <Num>
                  {r.denied ? <span className="text-bad">{r.denied}</span> : 0}
                </Num>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
