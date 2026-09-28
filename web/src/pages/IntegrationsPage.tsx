import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";

export default function IntegrationsPage() {
  const q = useQuery({
    queryKey: ["integrations"],
    queryFn: () => unwrap(api.GET("/api/integrations")),
  });
  if (q.error) return <p className="error">{q.error.message}</p>;
  if (!q.data) return <p className="muted">Checking integrations…</p>;
  const { integrations, environments } = q.data;
  return (
    <div className="stack">
      <section className="card">
        <div className="row spread">
          <h2>Integrations</h2>
          <button
            className="secondary"
            onClick={() => q.refetch()}
            disabled={q.isFetching}
          >
            {q.isFetching ? "Checking…" : "Re-check"}
          </button>
        </div>
        <p className="muted">
          External systems the delivery steps run through. Each one is checked
          live. Configured in <code>.agent-factory/config.yaml</code>;
          credentials are never stored there.
        </p>
        <table className="wide">
          <thead>
            <tr className="small muted">
              <td>integration</td>
              <td>provider</td>
              <td>capabilities</td>
              <td>used by</td>
              <td>readiness</td>
            </tr>
          </thead>
          <tbody>
            {integrations.map((i) => (
              <tr key={i.id} data-testid={`integration-${i.id}`}>
                <td>
                  <strong>{i.id}</strong>
                  {Object.keys(i.settings ?? {}).length > 0 && (
                    <div className="small muted">
                      {Object.entries(i.settings ?? {})
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(" · ")}
                    </div>
                  )}
                </td>
                <td>{i.provider}</td>
                <td>{i.capabilities.join(", ")}</td>
                <td>
                  {(i.used_by ?? []).join(", ") || (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <span
                    className={`pill ${i.readiness.state === "ready" ? "ok" : i.readiness.state === "failed" ? "bad" : "warn"}`}
                  >
                    {i.readiness.state}
                  </span>
                  {(i.readiness.reasons ?? []).map((r) => (
                    <div key={r} className="small muted">
                      {r}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h3>Environments</h3>
        <table className="wide">
          <thead>
            <tr className="small muted">
              <td>environment</td>
              <td>registry</td>
              <td>scan</td>
              <td>deploy</td>
              <td>publish</td>
              <td>product lines</td>
            </tr>
          </thead>
          <tbody>
            {environments.map((e) => (
              <tr key={e.name}>
                <td>
                  <strong>{e.name}</strong>
                </td>
                {["registry", "scan", "deploy", "publish"].map((c) => (
                  <td key={c}>
                    <code>{e.bindings[c]}</code>
                  </td>
                ))}
                <td>
                  {(e.product_lines ?? []).join(", ") || (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
