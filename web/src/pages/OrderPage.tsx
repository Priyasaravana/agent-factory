import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight, GitBranch, Globe, Layers } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import RunPanel from "../components/RunPanel";
import StatusPill from "../components/StatusPill";
import { Card, CardContent } from "../components/ui/card";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";

export default function OrderPage() {
  const { orderId = "" } = useParams();
  const detail = useQuery({
    queryKey: ["order", orderId],
    queryFn: () => unwrap(api.GET("/api/orders/{order_id}", { params: { path: { order_id: orderId } } })),
    refetchInterval: 4_000,
  });
  const [selected, setSelected] = useState<string | null>(null);

  if (detail.isLoading) return <div className="h-40 animate-pulse rounded-xl bg-muted" />;
  if (detail.error) return <p className="error">{detail.error.message}</p>;
  const { order, runs, feedback } = detail.data!;
  const runId = selected ?? order.latest_run_id ?? runs[0]?.id;

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5">
      <Link to="/" className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground">
        <ArrowLeft className="size-4" /> Orders
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="m-0 text-2xl font-semibold tracking-tight">{order.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {order.product_slug} · created {ago(order.created_at)}
            {order.created_by ? ` by ${order.created_by}` : ""}
          </p>
        </div>
        {order.latest_status && <StatusPill status={order.latest_status} />}
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Fact icon={<Layers />} label="Product line">
          {order.product_line}
        </Fact>
        <Fact icon={<Globe />} label="App">
          {order.app_url ? (
            <a href={order.app_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5">
              {order.app_url.replace(/^https?:\/\//, "")} <ArrowUpRight className="size-3" />
            </a>
          ) : (
            <span className="text-muted-foreground">will be served on :{order.host_port}</span>
          )}
        </Fact>
        <Fact icon={<GitBranch />} label="Repository">
          {order.repo_url ? (
            <a href={order.repo_url} target="_blank" rel="noreferrer" className="truncate">
              {order.repo_url.replace(/^https:\/\/github\.com\//, "")}
            </a>
          ) : (
            <span className="text-muted-foreground">local only</span>
          )}
        </Fact>
      </div>

      <Card>
        <CardContent className="grid gap-2 pt-4">
          <details>
            <summary className="font-medium">Requirements</summary>
            <pre className="pre mt-2">{order.requirements}</pre>
          </details>
          {feedback.length > 0 && (
            <details>
              <summary className="font-medium">Feedback history ({feedback.length})</summary>
              <ol className="mt-2 grid gap-2 pl-5 text-sm">
                {feedback.map((f) => (
                  <li key={f.id}>{f.text}</li>
                ))}
              </ol>
            </details>
          )}
        </CardContent>
      </Card>

      {runs.length > 1 && (
        <div className="flex w-fit flex-wrap gap-1 rounded-lg border bg-card p-1" role="tablist" aria-label="Iterations">
          {runs.map((r) => (
            <button
              key={r.id}
              role="tab"
              aria-selected={r.id === runId}
              className={cn("tab", r.id === runId && "active")}
              onClick={() => setSelected(r.id)}
            >
              iteration {r.iteration}
            </button>
          ))}
        </div>
      )}
      {runId && <RunPanel key={runId} runId={runId} orderId={order.id} />}
    </div>
  );
}

function Fact({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start gap-3 rounded-xl border bg-card px-4 py-3 shadow-sm">
      <span className="mt-0.5 text-muted-foreground [&_svg]:size-4">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs font-medium text-muted-foreground">{label}</div>
        <div className="truncate text-sm">{children}</div>
      </div>
    </div>
  );
}
