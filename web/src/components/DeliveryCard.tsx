import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, unwrap, type WorkflowView } from "../api/client";

const WHAT: Record<string, string> = {
  registry: "where the built image goes",
  scan: "vulnerability scan of the image",
  deploy: "where the app runs and is verified",
  publish: "where the product repo is pushed",
};

/** Where this product line's delivery steps run, with each integration's readiness. */
export default function DeliveryCard({ w }: { w: WorkflowView }) {
  const delivery = useQuery({
    queryKey: ["integrations"],
    queryFn: () => unwrap(api.GET("/api/integrations")),
  });
  const ready = new Map(
    delivery.data?.integrations.map((i) => [i.id, i.readiness]) ?? [],
  );
  return (
    <section className="card">
      <div className="row spread">
        <h3>
          Delivery environment <code>{w.environment}</code>
        </h3>
        <Link to="/integrations">All integrations →</Link>
      </div>
      <table className="wide">
        <tbody>
          {(w.delivery ?? []).map((b) => {
            const r = ready.get(b.integration);
            return (
              <tr key={b.capability}>
                <td>
                  <strong>{b.capability}</strong>
                  <div className="small muted">{WHAT[b.capability]}</div>
                </td>
                <td>
                  <code>{b.integration}</code>{" "}
                  <span className="small muted">({b.provider})</span>
                </td>
                <td>
                  {r ? (
                    <span
                      className={`pill ${r.state === "ready" ? "ok" : r.state === "failed" ? "bad" : "warn"}`}
                      title={(r.reasons ?? []).join("; ")}
                    >
                      {r.state}
                    </span>
                  ) : (
                    <span className="muted small">checking…</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted small">
        Set in <code>.agent-factory/config.yaml</code> (
        <code>product_lines.{w.workflow_id}.environment</code>,{" "}
        <code>environments</code>, <code>integrations</code>). Credentials are
        never stored there.
      </p>
    </section>
  );
}
