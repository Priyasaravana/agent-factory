import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import RunPanel from "../components/RunPanel";
import StatusPill from "../components/StatusPill";

export default function OrderPage() {
  const { orderId = "" } = useParams();
  const detail = useQuery({
    queryKey: ["order", orderId],
    queryFn: () => unwrap(api.GET("/api/orders/{order_id}", { params: { path: { order_id: orderId } } })),
    refetchInterval: 4_000,
  });
  const [selected, setSelected] = useState<string | null>(null);

  if (detail.isLoading) return <p className="muted">Loading…</p>;
  if (detail.error) return <p className="error">{detail.error.message}</p>;
  const { order, runs, feedback } = detail.data!;
  const runId = selected ?? order.latest_run_id ?? runs[0]?.id;

  return (
    <div className="stack">
      <section className="card">
        <div className="row spread">
          <h2>{order.title}</h2>
          {order.latest_status && <StatusPill status={order.latest_status} />}
        </div>
        <div className="row small muted">
          <span>line: {order.product_line}</span>
          <span>repo: {order.repo_url ? <a href={order.repo_url}>{order.repo_url}</a> : "local only"}</span>
          <span>
            app:{" "}
            {order.app_url ? (
              <a href={order.app_url} target="_blank" rel="noreferrer">
                {order.app_url}
              </a>
            ) : (
              `will be served on :${order.host_port}`
            )}
          </span>
        </div>
        <details>
          <summary>Requirements</summary>
          <pre className="pre">{order.requirements}</pre>
        </details>
        {feedback.length > 0 && (
          <details>
            <summary>Feedback history ({feedback.length})</summary>
            <ul>
              {feedback.map((f) => (
                <li key={f.id}>{f.text}</li>
              ))}
            </ul>
          </details>
        )}
        <div className="row">
          {runs.map((r) => (
            <button
              key={r.id}
              className={`tab ${r.id === runId ? "active" : ""}`}
              onClick={() => setSelected(r.id)}
            >
              iteration {r.iteration}
            </button>
          ))}
        </div>
      </section>
      {runId && <RunPanel key={runId} runId={runId} orderId={order.id} />}
    </div>
  );
}
