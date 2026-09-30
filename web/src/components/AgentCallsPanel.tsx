import { useQuery } from "@tanstack/react-query";
import { Bot, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { duration } from "../pages/OutcomesPage";
import { cn } from "../lib/utils";

type Entry = {
  type?: string;
  t?: number;
  text?: string;
  tool?: string;
  input?: string;
  content?: string;
  is_error?: boolean;
  reason?: string;
  note?: string;
  turns?: number;
  cost_usd?: number;
};

/** Every agent call of a run (ADR-0022): who ran, on which model, how long, what it
 * cost, which tools it used and what the guardrails refused, with its transcript. */
export default function AgentCallsPanel({
  runId,
  runStatus,
}: {
  runId: string;
  runStatus: string;
}) {
  const q = useQuery({
    queryKey: ["calls", runId, runStatus],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}/calls", {
          params: { path: { run_id: runId } },
        }),
      ),
  });
  const [open, setOpen] = useState<string | null>(null);
  const calls = q.data?.calls ?? [];
  const denials = q.data?.denials ?? [];
  if (!calls.length && !denials.length) return null;
  const cost = calls.reduce((n, c) => n + (c.cost_usd ?? 0), 0);

  return (
    <details
      className="rounded-xl border bg-card p-4"
      data-testid="agent-calls"
    >
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <Bot className="size-4 text-primary" />
        <span className="font-semibold">Agent calls</span>
        <span className="pill muted">{calls.length} calls</span>
        <span className="pill muted">${cost.toFixed(2)}</span>
        {denials.length > 0 && (
          <span className="pill bad" title="actions the guardrails refused">
            <ShieldAlert className="size-3" /> {denials.length} denied
          </span>
        )}
      </summary>
      <div className="mt-3 grid gap-3">
        {denials.length > 0 && (
          <div className="grid gap-1 text-sm" data-testid="denials">
            <strong>Refused by guardrails</strong>
            {denials.map((d, i) => (
              <div key={i} className="flex flex-wrap items-baseline gap-2">
                <span className="pill bad">{d.role}</span>
                <span className="font-mono text-xs">
                  {d.tool}: {d.input}
                </span>
                <span className="text-muted-foreground">{d.reason}</span>
              </div>
            ))}
          </div>
        )}
        <table className="wide">
          <thead>
            <tr>
              <td>station</td>
              <td>agent</td>
              <td>model</td>
              <td>turns</td>
              <td>time</td>
              <td>cost</td>
              <td>tools</td>
              <td />
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr
                key={c.transcript ?? `${c.station}-${c.at}`}
                className={cn(!c.ok && "text-bad")}
              >
                <td className="font-mono text-xs">{c.station}</td>
                <td>{c.role}</td>
                <td className="text-xs">{c.model}</td>
                <td className="tabular-nums">{c.turns}</td>
                <td className="tabular-nums">{duration(c.duration_s)}</td>
                <td className="tabular-nums">
                  ${(c.cost_usd ?? 0).toFixed(2)}
                </td>
                <td className="text-xs" title={JSON.stringify(c.tools)}>
                  {Object.entries(c.tools ?? {})
                    .slice(0, 3)
                    .map(([t, n]) => `${t} ${n}`)
                    .join(" · ") || "—"}
                  {(c.denied ?? 0) > 0 && (
                    <span className="pill bad ml-1">{c.denied} denied</span>
                  )}
                </td>
                <td>
                  {c.transcript && (
                    <button
                      className="tab"
                      onClick={() =>
                        setOpen(open === c.transcript ? null : c.transcript!)
                      }
                    >
                      {open === c.transcript ? "hide" : "transcript"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {open && <Transcript runId={runId} name={open} />}
      </div>
    </details>
  );
}

function Transcript({ runId, name }: { runId: string; name: string }) {
  const q = useQuery({
    queryKey: ["transcript", runId, name],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}/calls/{name}/transcript", {
          params: { path: { run_id: runId, name } },
        }),
      ) as Promise<Entry[]>,
  });
  if (!q.data)
    return <div className="h-24 animate-pulse rounded-lg bg-muted" />;
  return (
    <div
      className="grid max-h-[32rem] gap-1 overflow-auto rounded-lg border p-2 text-xs"
      data-testid="transcript"
    >
      <div className="text-muted-foreground">
        {name} · secrets redacted · long outputs keep their head and tail
      </div>
      {q.data.map((e, i) => (
        <div
          key={i}
          className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-2 border-t py-1"
        >
          <span className="tabular-nums text-muted-foreground">
            {e.t != null ? `${e.t}s` : ""}
          </span>
          <div className="min-w-0">
            {e.type === "text" && (
              <div className="whitespace-pre-wrap">{e.text}</div>
            )}
            {e.type === "tool_use" && (
              <div>
                <span className="pill info">{e.tool}</span>{" "}
                <code className="break-all">{e.input}</code>
              </div>
            )}
            {e.type === "tool_result" && (
              <pre
                className={cn(
                  "pre m-0 max-h-48 overflow-auto whitespace-pre-wrap",
                  e.is_error && "text-bad",
                )}
              >
                {e.content || "(no output)"}
              </pre>
            )}
            {e.type === "denied" && (
              <div className="text-bad">
                <ShieldAlert className="inline size-3" /> denied {e.tool}{" "}
                <code>{e.input}</code>: {e.reason}
              </div>
            )}
            {e.type === "result" && (
              <div className="text-muted-foreground">
                finished: {e.turns} turns, ${(e.cost_usd ?? 0).toFixed(2)}
              </div>
            )}
            {e.type === "truncated" && (
              <div className="text-muted-foreground">{e.note}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
