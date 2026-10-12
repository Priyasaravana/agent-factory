import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, unwrap, type WorkflowView } from "../api/client";

const WHAT: Record<string, string> = {
  registry: "where the built image goes",
  scan: "vulnerability scan of the image",
  deploy: "where the app runs and is verified",
  publish: "where the product repo is pushed",
};

type BlueprintUse = NonNullable<WorkflowView["blueprints"]>[number];

/** The blueprints whose products run this workflow, and where each one's delivery steps run. */
export default function DeliveryCard({ w }: { w: WorkflowView }) {
  const delivery = useQuery({
    queryKey: ["integrations"],
    queryFn: () => unwrap(api.GET("/api/integrations")),
  });
  const ready = new Map(
    delivery.data?.integrations.map((i) => [i.id, i.readiness]) ?? [],
  );
  const uses = w.blueprints ?? [];
  return (
    <section className="card">
      <div className="row spread">
        <h3>Blueprints using this workflow</h3>
        <Link to="/integrations">All integrations →</Link>
      </div>
      {uses.map((u) => (
        <Binding key={u.id} u={u} ready={ready} />
      ))}
      <p className="muted small">
        Set in <code>.agent-factory/config.yaml</code> (
        <code>blueprints.&lt;id&gt;.environment</code>, <code>environments</code>,{" "}
        <code>integrations</code>). Credentials are never stored there.
      </p>
    </section>
  );
}

function Binding({
  u,
  ready,
}: {
  u: BlueprintUse;
  ready: Map<string, { state: string; reasons?: string[] | null }>;
}) {
  return (
    <div>
      <h4>
        <code>{u.id}</code> <span className="small muted">{u.description}</span>
      </h4>
      {u.target === "repo" ? (
        <p className="small muted">A team's repository: nothing is built or deployed; changes leave as pull requests.</p>
      ) : (
      <table className="wide">
        <caption className="small muted">
          delivery environment <code>{u.environment}</code>
        </caption>
        <tbody>
          {(u.delivery ?? []).map((b) => {
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
      )}
    </div>
  );
}
