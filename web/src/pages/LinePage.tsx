import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import StationStrip from "../components/StationStrip";

export default function LinePage() {
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => unwrap(api.GET("/api/config")) });
  if (!cfg.data) return <p className="muted">Loading…</p>;
  const c = cfg.data;
  return (
    <div className="stack">
      <section className="card">
        <h2>{c.name}: the line</h2>
        <p className="muted">
          Defined in <code>.agent-factory/config.yaml</code>. Agent stations do judgment work; checks
          are deterministic. Repair stations run only on failure.
        </p>
        <StationStrip stations={c.stations} />
      </section>
      <section className="grid2">
        <div className="card">
          <h3>Action policies</h3>
          <table>
            <tbody>
              {Object.entries(c.policies).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td><span className={`pill ${v === "auto" ? "ok" : v === "off" ? "muted" : "warn"}`}>{v}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Human gates</h3>
          <ul>{c.gates.map((g) => <li key={g}>{g}</li>)}</ul>
          <h3>Product lines</h3>
          <ul>{Object.entries(c.product_lines).map(([k, v]) => <li key={k}><code>{k}</code> — {v}</li>)}</ul>
        </div>
      </section>
    </div>
  );
}
