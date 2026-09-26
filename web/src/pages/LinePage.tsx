import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type AgentView } from "../api/client";
import StationStrip from "../components/StationStrip";

export default function LinePage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => unwrap(api.GET("/api/config")) });
  const versions = useQuery({ queryKey: ["line-versions"], queryFn: () => unwrap(api.GET("/api/line/versions")) });
  const line = useQuery({
    queryKey: ["line", selected],
    queryFn: () =>
      selected
        ? unwrap(api.GET("/api/line/versions/{version}", { params: { path: { version: selected } } }))
        : unwrap(api.GET("/api/line")),
  });
  const activate = useMutation({
    mutationFn: (version: number) =>
      unwrap(api.POST("/api/line/versions/{version}/activate", { params: { path: { version } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["line"] });
      qc.invalidateQueries({ queryKey: ["line-versions"] });
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });

  if (!line.data || !cfg.data) return <p className="muted">Loading…</p>;
  const l = line.data;
  const c = cfg.data;
  return (
    <div className="stack">
      <section className="card">
        <div className="row spread">
          <h2>
            {c.name}: line <span className="pill info">v{l.version}</span>{" "}
            {l.active ? <span className="pill ok">active</span> : <span className="pill muted">inactive</span>}
          </h2>
          {!l.active && (
            <button onClick={() => activate.mutate(l.version)} disabled={activate.isPending}>
              Make v{l.version} active
            </button>
          )}
        </div>
        <p className="muted">
          {l.description} · blueprint <code>{l.blueprint}</code> · {l.note}
        </p>
        {l.blueprint_update_available && (
          <p className="note">The shipped blueprint has changed since this line was seeded.</p>
        )}
        <StationStrip stations={l.stations} />
        <p className="muted small">
          Agent stations run an agent spec; checks are deterministic; dashed stations run only on failure. New runs use
          the active version; runs in flight keep the version they started with.
        </p>
      </section>

      <section className="card">
        <h3>Agents in this version</h3>
        <div className="grid2">
          {l.agents.map((a) => (
            <AgentCard key={a.id} a={a} stations={l.stations.filter((s) => s.role === a.id).map((s) => s.id)} />
          ))}
        </div>
      </section>

      <section className="grid2">
        <div className="card">
          <h3>Versions</h3>
          <table>
            <tbody>
              {versions.data?.map((v) => (
                <tr key={v.version}>
                  <td>
                    <button className={`tab ${v.version === l.version ? "active" : ""}`} onClick={() => setSelected(v.version)}>
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
          <p className="muted small">
            New versions are imported with <code>agent-factory line import</code> (UI editing comes next).
          </p>
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

function AgentCard({ a, stations }: { a: AgentView; stations: string[] }) {
  return (
    <div className="agent">
      <div className="row spread">
        <strong>{a.id}</strong>
        <span className="row">
          <span className="pill info" title={`model: ${a.model_resolved}`}>{a.model}</span>
          <span className={`pill ${a.observe_only ? "ok" : "warn"}`} title={a.effective_tools.join(", ")}>
            {a.tools}
          </span>
        </span>
      </div>
      <p className="small muted">{a.description}</p>
      <div className="small">
        <div>stations: {stations.join(", ") || "—"}</div>
        <div>tools: {a.effective_tools.join(", ")}</div>
        <div>skills: {a.skills.join(", ") || "—"}</div>
        <div>max turns: {a.max_turns}{a.produces.length ? ` · produces: ${a.produces.join(", ")}` : ""}</div>
      </div>
      <details>
        <summary className="small">prompt</summary>
        <pre className="pre">{a.prompt}</pre>
      </details>
    </div>
  );
}
