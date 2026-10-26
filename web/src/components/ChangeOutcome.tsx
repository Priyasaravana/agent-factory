import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { api, unwrap } from "../api/client";

const RESULT: Record<string, string> = { fixed: "ok", "still-present": "bad", "not verified yet": "muted" };

/** What a change was asked, what it did and changed, what it gathered, and what a later
 * assessment verified (ADR-0040). */
import { Panel } from "./ui/panel";

export default function ChangeOutcome({ changeId, runStatus }: { changeId: string; runStatus: string }) {
  const stopped = ["awaiting_feedback", "failed", "cancelled", "held"].includes(runStatus);
  const q = useQuery({
    queryKey: ["outcome", changeId, runStatus],
    queryFn: () =>
      unwrap(api.GET("/api/changes/{change_id}/outcome", { params: { path: { change_id: changeId } } })),
    enabled: stopped,
  });
  if (!stopped || !q.data) return null;
  const o = q.data;
  const found = o.found ?? {};
  const isAssess = o.kind === "assess";
  return (
    <Panel
      id="outcome"
      data-testid="change-outcome"
      aria-label="Outcome"
      bodyClassName="text-sm"
      icon={<ListChecks className="text-primary" />}
      title={`Outcome of iteration ${o.iteration}`}
      summary={o.asked ? <span className="line-clamp-1">{o.asked.split("\n")[0]}</span> : undefined}
    >
      <dl className="m-0 grid gap-2 sm:grid-cols-[8rem_1fr]">
        <dt className="font-medium">Asked</dt>
        <dd className="m-0 whitespace-pre-line text-muted-foreground">
          {(o.asked || "(the first assessment)").split("\n\nWork items to address:")[0]}
        </dd>
        {(o.items ?? []).length > 0 && (
          <>
            <dt className="font-medium">Work items</dt>
            <dd className="m-0">
              <ul className="m-0 grid list-none gap-1 p-0">
                {(o.verified ?? []).map((v) => (
                  <li key={String(v.item)}>
                    <span className={`pill ${RESULT[String(v.result)] ?? "muted"}`}>{String(v.result)}</span>{" "}
                    {String(v.title)}
                    {v.note ? <span className="text-xs text-muted-foreground"> · {String(v.note)}</span> : null}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        <dt className="font-medium">Done</dt>
        <dd className="m-0 text-muted-foreground">
          {o.done || "—"}
          {o.pr_url && (
            <>
              {" "}
              <a href={o.pr_url} target="_blank" rel="noreferrer">
                pull request
              </a>
            </>
          )}
        </dd>
        {(o.files ?? []).length > 0 && (
          <>
            <dt className="font-medium">Changed</dt>
            <dd className="m-0 flex flex-wrap gap-1">
              {(o.files ?? []).slice(0, 20).map((f) => (
                <code key={f} className="text-xs">
                  {f}
                </code>
              ))}
              {(o.files ?? []).length > 20 && <span className="text-xs">+{(o.files ?? []).length - 20} more</span>}
            </dd>
          </>
        )}
        {isAssess && Object.keys(found).length > 0 && (
          <>
            <dt className="font-medium">Found</dt>
            <dd className="m-0 text-muted-foreground">
              {found.new ?? 0} new · {found.fixed ?? 0} verified fixed · {found.reopened ?? 0} still present or back ·{" "}
              {(found.open ?? 0) + (found.in_progress ?? 0)} open in all
            </dd>
          </>
        )}
        <dt className="font-medium">Gathered</dt>
        <dd className="m-0 flex flex-wrap gap-1">
          {(o.gathered ?? []).map((g, n) => (
            <span key={n} className="pill muted" title={String(g.ref ?? "")}>
              {String(g.station)}: {String(g.artifact)}
            </span>
          ))}
          {(o.gathered ?? []).length === 0 && <span className="text-muted-foreground">—</span>}
        </dd>
      </dl>
    </Panel>
  );
}
