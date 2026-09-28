import type { ReactNode } from "react";
import type { AgentView } from "../api/client";

export default function AgentCard({
  a,
  actions,
}: {
  a: AgentView;
  actions?: ReactNode;
}) {
  const s = a.spec;
  return (
    <div className="agent">
      <div className="row spread">
        <strong>{s.id}</strong>
        <span className="row">
          <span className="pill info" title={`model: ${a.model_resolved}`}>
            {s.model}
          </span>
          <span
            className={`pill ${a.observe_only ? "ok" : "warn"}`}
            title={a.effective_tools.join(", ")}
          >
            {s.tools}
          </span>
        </span>
      </div>
      <p className="small muted">{s.description}</p>
      <div className="small">
        <div>
          stations:{" "}
          {a.used_by.join(", ") || <em className="muted">not used</em>}
        </div>
        <div>tools: {a.effective_tools.join(", ")}</div>
        <div>
          skills:{" "}
          {(s.skills ?? [])
            .map((k) =>
              (s.preload_skills ?? []).includes(k) ? `${k} (preloaded)` : k,
            )
            .join(", ") || "—"}
        </div>
        <div>
          docs: {(s.context_docs ?? []).join(", ") || "—"} · earlier iterations:{" "}
          {s.previous_iterations ?? 0}
        </div>
        <div>
          max turns: {s.max_turns}
          {s.produces?.length ? ` · produces: ${s.produces.join(", ")}` : ""}
        </div>
        {s.learnings ? (
          <div>
            learnings: {s.learnings.slice(0, 120)}
            {s.learnings.length > 120 ? "…" : ""}
          </div>
        ) : null}
      </div>
      <details>
        <summary className="small">prompt</summary>
        <pre className="pre">{s.prompt}</pre>
      </details>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}
