import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import StatusPill from "../components/StatusPill";

export default function OrdersPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const orders = useQuery({
    queryKey: ["orders"],
    queryFn: () => unwrap(api.GET("/api/orders")),
    refetchInterval: 5_000,
  });
  const config = useQuery({ queryKey: ["config"], queryFn: () => unwrap(api.GET("/api/config")) });
  const [title, setTitle] = useState("");
  const [requirements, setRequirements] = useState("");
  const [line, setLine] = useState("fastapi-service");

  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/orders", { body: { title, requirements, product_line: line } })),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["orders"] });
      nav(`/orders/${d.order.id}`);
    },
  });

  return (
    <div className="grid2">
      <section className="card">
        <h2>New order</h2>
        <p className="muted">
          Describe what you want. The factory specifies, designs, builds, tests, deploys and verifies
          it, then asks for your feedback.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Bookmarks service" required minLength={3} />
          </label>
          <label>
            Product line
            <select value={line} onChange={(e) => setLine(e.target.value)}>
              {Object.entries(config.data?.product_lines ?? { "fastapi-service": "" }).map(([k, v]) => (
                <option key={k} value={k}>
                  {k} {v ? `— ${v}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            Requirements
            <textarea
              rows={10}
              value={requirements}
              onChange={(e) => setRequirements(e.target.value)}
              placeholder="A REST service to save bookmarks and notes with tags. Filter by tag, search notes…"
              required
              minLength={10}
            />
          </label>
          <button disabled={create.isPending}>{create.isPending ? "Submitting…" : "Start the line"}</button>
          {create.error && <p className="error">{create.error.message}</p>}
        </form>
      </section>

      <section className="card">
        <h2>Orders</h2>
        {orders.isLoading && <p className="muted">Loading…</p>}
        {orders.data?.length === 0 && <p className="muted">No orders yet.</p>}
        <ul className="list">
          {orders.data?.map((o) => (
            <li key={o.id}>
              <Link to={`/orders/${o.id}`}>
                <strong>{o.title}</strong>
                <span className="muted"> · {o.product_slug}</span>
              </Link>
              {o.latest_status && <StatusPill status={o.latest_status} />}
              {o.app_url && (
                <a className="small" href={o.app_url} target="_blank" rel="noreferrer">
                  app ↗
                </a>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
