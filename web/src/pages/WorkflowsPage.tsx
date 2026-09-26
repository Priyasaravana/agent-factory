import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";

export default function WorkflowsPage() {
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: () => unwrap(api.GET("/api/workflows")) });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => unwrap(api.GET("/api/workflow-templates")) });
  return (
    <div className="stack">
      <section className="card">
        <h2>Workflows</h2>
        <p className="muted">
          Each product line has its own workflow: the stations an order goes through and the agents that do the work.
          Edit a workflow to change it; every publish becomes a new version.
        </p>
        <table className="wide">
          <thead>
            <tr className="small muted">
              <td>workflow (product line)</td>
              <td>active</td>
              <td>stations</td>
              <td>agents</td>
              <td>template</td>
              <td />
            </tr>
          </thead>
          <tbody>
            {workflows.data?.map((w) => (
              <tr key={w.workflow_id}>
                <td>
                  <Link to={`/workflows/${w.workflow_id}`}>
                    <strong>{w.workflow_id}</strong>
                  </Link>
                  <div className="small muted">{w.product_line}</div>
                </td>
                <td>
                  <span className="pill info">v{w.active_version}</span>{" "}
                  {w.draft_dirty && <span className="pill warn">draft</span>}
                </td>
                <td>{w.stations}</td>
                <td>{w.agents}</td>
                <td className="small">
                  <code>{w.template}</code>
                </td>
                <td>
                  <Link to={`/workflows/${w.workflow_id}/edit`}>
                    <button className="secondary">Edit</button>
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h3>Templates</h3>
        <p className="muted small">
          Start any workflow's draft from a template in its editor, or import one from a GitHub repo.
        </p>
        <div className="grid2">
          {templates.data?.map((t) => (
            <div key={t.name} className="agent">
              <strong>{t.name}</strong>
              <span className="small muted">{t.description}</span>
              <span className="small">{t.stations.join(" → ")}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
