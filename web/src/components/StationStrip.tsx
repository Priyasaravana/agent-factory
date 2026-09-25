import type { StationView } from "../api/client";

export default function StationStrip({ stations }: { stations: StationView[] }) {
  return (
    <ol className="strip">
      {stations.map((s) => (
        <li key={s.id} className={`station st-${s.state} ${s.repair ? "repair" : ""}`} title={s.role ?? "deterministic check"}>
          <span className="name">{s.id}</span>
          <span className="meta">
            {s.kind === "agent" ? "agent" : "check"}
            {s.attempts > 1 ? ` · ×${s.attempts}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
