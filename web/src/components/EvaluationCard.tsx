import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Play } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";

type Eval = components["schemas"]["EvalRun"];
type Summary = components["schemas"]["EvalSummary"];

const pct = (x: number | null | undefined) =>
  x == null ? "—" : `${Math.round(100 * x)}%`;
const usd = (x: number | null | undefined) =>
  x == null ? "—" : `$${x.toFixed(2)}`;
const secs = (x: number | null | undefined) =>
  x == null ? "—" : x < 120 ? `${Math.round(x)}s` : `${Math.round(x / 60)}m`;

const METRICS: [keyof Summary, string, (v: never) => string][] = [
  ["pass_rate", "pass rate", pct as (v: never) => string],
  ["autonomy", "autonomy", pct as (v: never) => string],
  ["level3_share", "Level 3", pct as (v: never) => string],
  ["verified_live_share", "verified live", pct as (v: never) => string],
  ["cost_per_delivery_usd", "cost / delivery", usd as (v: never) => string],
  [
    "fix_loops_per_case",
    "fix loops / case",
    ((v: number) => String(v)) as (v: never) => string,
  ],
  ["lead_time_median_s", "median lead time", secs as (v: never) => string],
];

/** Evaluations of this workflow's versions on its fixed suite (ADR-0025): candidate
 * against baseline, the verdict, and every case. Admins start or cancel one. */
export default function EvaluationCard({
  workflowId,
  isAdmin,
  versions,
  suiteSize,
  gate,
}: {
  workflowId: string;
  isAdmin: boolean;
  versions: number[];
  suiteSize: number;
  gate: string;
}) {
  const qc = useQueryClient();
  const path = { workflow_id: workflowId };
  const q = useQuery({
    queryKey: ["evals", workflowId],
    queryFn: () =>
      unwrap(
        api.GET("/api/workflows/{workflow_id}/evals", { params: { path } }),
      ),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((e) => e.status === "running")
        ? 3000
        : 30_000,
  });
  const newest = versions.length ? Math.max(...versions) : 1;
  const [version, setVersion] = useState<number | null>(null);
  const refresh = () =>
    ["evals", "wf-versions", "workflow", "workflows"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] }),
    );
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/workflows/{workflow_id}/evals", {
          params: { path },
          body: { version: version ?? newest },
        }),
      ),
    onSuccess: () => {
      toast.success("Evaluation started");
      refresh();
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/workflows/{workflow_id}/evals/{eval_id}/cancel", {
          params: { path: { ...path, eval_id: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const list = q.data ?? [];

  return (
    <section className="card" data-testid="evaluations">
      <div className="row spread">
        <h3 className="flex items-center gap-2">
          <FlaskConical className="size-4 text-primary" /> Evaluation
          <span className="pill muted">gate: {gate}</span>
          <span className="pill muted">{suiteSize} cases</span>
        </h3>
        {isAdmin && suiteSize > 0 && (
          <div className="row">
            <select
              aria-label="Version to evaluate"
              value={version ?? newest}
              onChange={(e) => setVersion(Number(e.target.value))}
            >
              {[...versions]
                .sort((a, b) => b - a)
                .map((v) => (
                  <option key={v} value={v}>
                    v{v}
                  </option>
                ))}
            </select>
            <Button
              size="sm"
              onClick={() => start.mutate()}
              disabled={
                start.isPending || list.some((e) => e.status === "running")
              }
            >
              <Play className="size-3" /> Run evaluation
            </Button>
          </div>
        )}
      </div>
      <p className="muted small">
        Every new version is measured on the same fixed orders as the active
        version: pass rate, autonomy, Level 3, requirements verified live, cost
        and fix loops. With the gate on <code>block</code>, a published version
        stays a candidate until it shows no regression, then activates. Each
        case is a full build, so an evaluation costs about {suiteSize} order
        {suiteSize === 1 ? "" : "s"} per version measured.
      </p>
      <Problems error={start.error ?? cancel.error ?? null} />
      {list.length === 0 && (
        <p className="muted small m-0">
          {suiteSize
            ? "No evaluations yet."
            : "This version has no evaluation cases: add them in Edit workflow."}
        </p>
      )}
      <div className="grid gap-3">
        {list.slice(0, 5).map((e, i) => (
          <EvalRow
            key={e.id}
            e={e}
            open={i === 0}
            onCancel={isAdmin ? () => cancel.mutate(e.id) : undefined}
          />
        ))}
      </div>
    </section>
  );
}

function EvalRow({
  e,
  open,
  onCancel,
}: {
  e: Eval;
  open: boolean;
  onCancel?: () => void;
}) {
  const v = e.verdict;
  const base = e.baseline;
  const done =
    (e.candidate.results ?? []).filter((r) => r.final).length +
    (base?.results ?? []).filter((r) => r.final).length;
  const total =
    (e.candidate.results ?? []).length + (base?.results?.length ?? 0);
  return (
    <details open={open} className="rounded-lg border p-3" data-testid="eval">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <strong>
          v{e.candidate.version}
          {base ? ` vs v${base.version}` : ""}
        </strong>
        {e.status === "running" ? (
          <span className="pill info">
            running · {done}/{total} cases
          </span>
        ) : e.status === "cancelled" ? (
          <span className="pill muted">cancelled</span>
        ) : v?.passed ? (
          <span className="pill ok">no regression</span>
        ) : (
          <span className="pill bad">regression</span>
        )}
        {e.activated && (
          <span className="pill ok">activated v{e.candidate.version}</span>
        )}
        {e.override && (
          <span className="pill warn" title={e.override.reason}>
            activated anyway by {e.override.by}
          </span>
        )}
        <span className="muted small">
          {e.trigger} · {e.started_by} · {ago(e.created_at)}
          {e.baseline_from ? " · baseline from an earlier evaluation" : ""}
        </span>
        {e.status === "running" && onCancel && (
          <button className="tab" onClick={onCancel}>
            Cancel evaluation
          </button>
        )}
      </summary>
      <div className="mt-3 grid gap-3 text-sm">
        {v &&
          ((v.regressions ?? []).length > 0 ||
            (v.warnings ?? []).length > 0) && (
            <ul className="m-0 pl-5">
              {(v.regressions ?? []).map((r) => (
                <li key={r} className="error">
                  {r}
                </li>
              ))}
              {(v.warnings ?? []).map((w) => (
                <li key={w} className="muted">
                  {w}
                </li>
              ))}
            </ul>
          )}
        {e.candidate.summary && (
          <table className="wide">
            <thead>
              <tr>
                <td>metric</td>
                <td>v{e.candidate.version}</td>
                {base && <td>v{base.version}</td>}
              </tr>
            </thead>
            <tbody>
              {METRICS.map(([k, label, fmt]) => (
                <tr key={k}>
                  <td>{label}</td>
                  <td className="tabular-nums">
                    {fmt(e.candidate.summary![k] as never)}
                  </td>
                  {base && (
                    <td className="tabular-nums">
                      {base.summary ? fmt(base.summary[k] as never) : "—"}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <table className="wide">
          <thead>
            <tr>
              <td>version</td>
              <td>case</td>
              <td>result</td>
              <td>cost</td>
              <td>loops</td>
              <td>level</td>
              <td>verified</td>
            </tr>
          </thead>
          <tbody>
            {[e.candidate, ...(base ? [base] : [])].flatMap((side) =>
              (side.results ?? []).map((r) => (
                <tr key={`${side.version}-${r.case_id}`} title={r.note ?? ""}>
                  <td>v{side.version}</td>
                  <td>{r.title}</td>
                  <td
                    className={cn(
                      r.final && r.status !== "delivered" && "text-bad",
                    )}
                  >
                    {r.status}
                    {r.unplanned_touch ? " · needed answers" : ""}
                  </td>
                  <td className="tabular-nums">{usd(r.cost_usd)}</td>
                  <td className="tabular-nums">{r.fix_loops}</td>
                  <td className="tabular-nums">{r.readiness_level ?? "—"}</td>
                  <td className="tabular-nums">
                    {r.requirements
                      ? `${r.requirements_verified}/${r.requirements}`
                      : "—"}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
    </details>
  );
}
