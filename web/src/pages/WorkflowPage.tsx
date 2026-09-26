import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import AgentCard from "../components/AgentCard";
import StationStrip from "../components/StationStrip";

export default function WorkflowPage() {
  const { workflowId = "" } = useParams();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const p = { workflow_id: workflowId };
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => unwrap(api.GET("/api/config")) });
  const versions = useQuery({
    queryKey: ["wf-versions", workflowId],
    queryFn: () => unwrap(api.GET("/api/workflows/{workflow_id}/versions", { params: { path: p } })),
  });
  const wf = useQuery({
    queryKey: ["workflow", workflowId, selected],
    queryFn: () =>
      selected
        ? unwrap(
            api.GET("/api/workflows/{workflow_id}/versions/{version}", {
              params: { path: { ...p, version: selected } },
            }),
          )
        : unwrap(api.GET("/api/workflows/{workflow_id}", { params: { path: p } })),
  });
  const activate = useMutation({
    mutationFn: (version: number) =>
      unwrap(
        api.POST("/api/workflows/{workflow_id}/versions/{version}/activate", {
          params: { path: { ...p, version } },
        }),
      ),
    onSuccess: () => {
      ["workflow", "wf-versions", "workflows", "config"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    },
  });

  if (wf.error) return <p className="error">{wf.error.message}</p>;
  if (!wf.data || !cfg.data) return <p className="muted">Loading…</p>;
  const w = wf.data;
  const c = cfg.data;
  return (
    <div className="stack">
      <section className="card">
        <div className="row spread">
          <h2>
            <Link to="/workflows">Workflows</Link> / {workflowId} <span className="pill info">v{w.version}</span>{" "}
            {w.active ? <span className="pill ok">active</span> : <span className="pill muted">inactive</span>}
          </h2>
          <div className="row">
            {!w.active && (
              <button onClick={() => activate.mutate(w.version)} disabled={activate.isPending}>
                Make v{w.version} active
              </button>
            )}
            <Link to={`/workflows/${workflowId}/edit`}>
              <button>Edit workflow</button>
            </Link>
          </div>
        </div>
        <p className="muted">
          {w.description} · template <code>{w.template}</code> · {w.note}
        </p>
        {w.template_update_available && (
          <p className="note">The shipped template has changed since this workflow was seeded.</p>
        )}
        <StationStrip stations={w.stations} />
        {(w.warnings?.length ?? 0) > 0 && (
          <details className="warnings">
            <summary>{w.warnings!.length} suggestion(s)</summary>
            <ul>{w.warnings!.map((x) => <li key={x}>{x}</li>)}</ul>
          </details>
        )}
        <p className="muted small">
          Orders on the <code>{workflowId}</code> product line run the active version. Runs in flight keep the version
          they started with.
        </p>
      </section>

      <section className="card">
        <h3>Agents in this version</h3>
        <div className="grid2">
          {w.agents.map((a) => (
            <AgentCard key={a.spec.id} a={a} />
          ))}
        </div>
        {(w.docs?.length ?? 0) > 0 && (
          <>
            <h3>Reference documents</h3>
            {w.docs!.map((d) => (
              <details key={d.id}>
                <summary>
                  {d.title} <span className="muted small">({d.id})</span>
                </summary>
                <pre className="pre">{d.content}</pre>
              </details>
            ))}
          </>
        )}
      </section>

      <section className="grid2">
        <div className="card">
          <h3>Versions</h3>
          <table>
            <tbody>
              {versions.data?.map((v) => (
                <tr key={v.version}>
                  <td>
                    <button
                      className={`tab ${v.version === w.version ? "active" : ""}`}
                      onClick={() => setSelected(v.version)}
                    >
                      v{v.version}
                    </button>
                  </td>
                  <td>{v.active && <span className="pill ok">active</span>}</td>
                  <td className="small muted">{new Date(v.created_at).toLocaleString()}</td>
                  <td className="small">{v.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Action policies</h3>
          <table>
            <tbody>
              {Object.entries(c.policies).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td>
                    <span className={`pill ${v === "auto" ? "ok" : v === "off" ? "muted" : "warn"}`}>{v}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Human gates</h3>
          <ul>{c.gates.map((g) => <li key={g}>{g}</li>)}</ul>
        </div>
      </section>
    </div>
  );
}
