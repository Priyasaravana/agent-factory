import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import AgentCard from "../components/AgentCard";
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
          <div className="row">
            {!l.active && (
              <button onClick={() => activate.mutate(l.version)} disabled={activate.isPending}>
                Make v{l.version} active
              </button>
            )}
            <Link to="/line/edit">
              <button>Edit line</button>
            </Link>
          </div>
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
            <AgentCard key={a.spec.id} a={a} />
          ))}
        </div>
        {(l.docs?.length ?? 0) > 0 && (
          <>
            <h3>Reference documents</h3>
            {l.docs!.map((d) => (
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
            Edit the line to create a new version, or import one with <code>agent-factory line import</code>.
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
