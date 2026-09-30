import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GraduationCap, X } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { ago } from "../lib/time";
import Problems from "./Problems";
import { Button } from "./ui/button";

/** Lessons the retro suggested after runs that needed help (ADR-0021). Everyone can
 * read them; an admin accepts one into the workflow draft or rejects it. Nothing
 * changes what agents are taught until the draft is published. */
export default function LearningsCard({
  workflowId,
  isAdmin,
}: {
  workflowId: string;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const path = { workflow_id: workflowId };
  const q = useQuery({
    queryKey: ["learnings", workflowId],
    queryFn: () =>
      unwrap(
        api.GET("/api/workflows/{workflow_id}/learnings", { params: { path } }),
      ),
    refetchInterval: 30_000,
  });
  const decide = useMutation({
    mutationFn: ({ id, how }: { id: string; how: "accept" | "reject" }) =>
      unwrap(
        how === "accept"
          ? api.POST(
              "/api/workflows/{workflow_id}/learnings/{proposal_id}/accept",
              {
                params: { path: { ...path, proposal_id: id } },
              },
            )
          : api.POST(
              "/api/workflows/{workflow_id}/learnings/{proposal_id}/reject",
              {
                params: { path: { ...path, proposal_id: id } },
              },
            ),
      ),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["learnings", workflowId] });
      qc.invalidateQueries({ queryKey: ["draft", workflowId] });
      toast.success(
        p.status === "accepted"
          ? `Added to ${p.agent}'s learnings in the draft. Publish the draft to use it.`
          : "Rejected",
      );
    },
  });
  const items = q.data ?? [];
  if (!q.data) return null;

  return (
    <section className="card" data-testid="learnings">
      <div className="row spread">
        <h3 className="flex items-center gap-2">
          <GraduationCap className="size-4 text-primary" /> Suggested learnings
        </h3>
        <span className={`pill ${items.length ? "info" : "muted"}`}>
          {items.length ? `${items.length} to review` : "none"}
        </span>
      </div>
      <p className="muted small">
        After a run that needed help (a fix loop, a hold, blocking questions)
        the factory suggests short lessons for the agent whose work caused it.
        Accepted lessons go into the workflow <strong>draft</strong>; they reach
        agents only when an admin publishes it
        {isAdmin ? (
          <>
            {" "}
            (<Link to={`/workflows/${workflowId}/edit`}>Edit workflow</Link>)
          </>
        ) : null}
        .
      </p>
      {items.length === 0 ? (
        <p className="muted small m-0">Nothing to review.</p>
      ) : (
        <ul className="m-0 grid list-none gap-3 p-0">
          {items.map((p) => (
            <li
              key={p.id}
              className="grid gap-1 rounded-lg border p-3"
              data-testid={`learning-${p.id}`}
            >
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="pill info">{p.agent}</span>
                from <Link to={`/orders/${p.order_id}`}>a run</Link> on v
                {p.workflow_version} · {ago(p.created_at)}
              </div>
              <div className="font-medium">{p.lesson}</div>
              <div className="text-sm text-muted-foreground">Why: {p.why}</div>
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  Evidence
                </summary>
                <pre className="pre mt-1 whitespace-pre-wrap">{p.evidence}</pre>
              </details>
              {isAdmin && (
                <div className="flex gap-2 pt-1">
                  <Button
                    size="sm"
                    onClick={() => decide.mutate({ id: p.id, how: "accept" })}
                    disabled={decide.isPending}
                  >
                    <Check /> Accept into draft
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => decide.mutate({ id: p.id, how: "reject" })}
                    disabled={decide.isPending}
                  >
                    <X /> Reject
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <Problems error={decide.error ?? q.error ?? null} />
    </section>
  );
}
