import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";

const ORDER = ["critical", "high", "medium", "low", "info"] as const;
const TONE: Record<string, string> = { critical: "bad", high: "bad", medium: "warn", low: "muted", info: "muted" };

/** What each tool station found on this change (ADR-0034): normalised findings, the
 * policy that judged them, and whether they blocked. */
export default function ToolsPanel({ changeId, runStatus }: { changeId: string; runStatus: string }) {
  const q = useQuery({
    queryKey: ["tools", changeId, runStatus],
    queryFn: () =>
      unwrap(api.GET("/api/changes/{change_id}/tools", { params: { path: { change_id: changeId } } })),
  });
  const reports = q.data ?? [];
  if (!reports.length) return null;
  const total = reports.reduce(
    (n, r) => n + ORDER.reduce((m, s) => m + (r.counts?.[s] ?? 0), 0),
    0,
  );
  const failing = reports.filter((r) => !r.passed).length;

  return (
    <details className="rounded-xl border bg-card p-4" data-testid="tools">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <ShieldCheck className="size-4 text-primary" />
        <span className="font-medium">Tool findings</span>
        <span className="pill muted">
          {reports.length} tools · {total} findings
        </span>
        {failing > 0 && <span className="pill bad">{failing} blocking</span>}
      </summary>
      <div className="mt-3 grid gap-3">
        {reports.map((r) => {
          const counts = ORDER.filter((s) => (r.counts?.[s] ?? 0) > 0);
          const findings = r.findings ?? [];
          return (
            <div key={r.station} className="grid gap-1.5 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{r.title}</span>
                {r.simulated ? (
                  <span className="pill muted">simulated</span>
                ) : (
                  <span className={cn("pill", r.passed ? "ok" : "bad")}>
                    {r.passed ? "passed" : `${r.blocking} blocking`}
                  </span>
                )}
                {counts.map((s) => (
                  <span key={s} className={`pill ${TONE[s]}`}>
                    {r.counts?.[s]} {s}
                  </span>
                ))}
                {!counts.length && !r.simulated && <span className="text-xs text-muted-foreground">no findings</span>}
                <span className="ml-auto text-xs text-muted-foreground">
                  {r.fail_on === "none" ? "reports only" : `fails on ${r.fail_on} and above`}
                  {r.scope === "changed" ? " · changed files only" : ""}
                </span>
              </div>
              {(r.outside ?? 0) > 0 && (
                <p className="m-0 text-xs text-muted-foreground">
                  {r.outside} more in files this change didn't touch (not counted).
                </p>
              )}
              {findings.length > 0 && (
                <ul className="m-0 grid list-none gap-1 p-0 text-sm">
                  {findings.slice(0, 20).map((f, i) => (
                    <li key={i} className="flex flex-wrap items-baseline gap-2">
                      <span className={`pill ${TONE[f.severity] ?? "muted"}`}>{f.severity}</span>
                      <span>{f.title}</span>
                      <code className="text-xs">
                        {f.file}
                        {f.line ? `:${f.line}` : ""}
                      </code>
                      <span className="text-xs text-muted-foreground">{f.rule}</span>
                    </li>
                  ))}
                  {findings.length > 20 && (
                    <li className="text-xs text-muted-foreground">
                      … and {findings.length - 20} more in the evidence
                    </li>
                  )}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </details>
  );
}
