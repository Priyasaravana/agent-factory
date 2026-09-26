import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap, type AgentSpec, type DraftView, type RefDoc } from "../api/client";
import AgentCard from "../components/AgentCard";
import AgentEditor from "../components/AgentEditor";

type Editing = { kind: "agent"; spec?: AgentSpec } | { kind: "doc"; doc?: RefDoc } | null;

export default function LineEditPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const draft = useQuery({ queryKey: ["draft"], queryFn: () => unwrap(api.GET("/api/line/draft")) });
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => unwrap(api.GET("/api/catalog")) });
  const [editing, setEditing] = useState<Editing>(null);
  const [note, setNote] = useState("");
  const [dupFrom, setDupFrom] = useState<string | null>(null);
  const [dupId, setDupId] = useState("");

  const apply = (d: DraftView) => qc.setQueryData(["draft"], d);
  const m = useMutation({
    mutationFn: (fn: () => Promise<DraftView>) => fn(),
    onSuccess: (d) => {
      apply(d);
      setEditing(null);
      setDupFrom(null);
    },
  });
  const publish = useMutation({
    mutationFn: () => unwrap(api.POST("/api/line/draft/publish", { body: { note } })),
    onSuccess: () => {
      ["draft", "line", "line-versions", "config"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      nav("/line");
    },
  });

  if (!draft.data || !catalog.data) return <p className="muted">Loading…</p>;
  const d = draft.data;
  const agentIds = d.agents.map((a) => a.spec.id);

  return (
    <div className="stack">
      <section className="card">
        <div className="row spread">
          <h2>
            Edit line <span className="pill muted">draft from v{d.base_version}</span>{" "}
            {d.dirty ? <span className="pill warn">unpublished changes</span> : <span className="pill ok">no changes</span>}
          </h2>
          <Link to="/line">← back to the line</Link>
        </div>
        {d.stale && (
          <p className="note">
            The active line is now v{d.active_version}. Publishing this draft will replace it; discard to start from v
            {d.active_version}.
          </p>
        )}
        {d.problems.length > 0 && (
          <div className="problems">
            <strong>Fix before publishing:</strong>
            <ul>{d.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        <div className="row">
          <input
            className="grow"
            placeholder="What changed? e.g. stricter acceptance agent"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button
            disabled={!d.dirty || d.problems.length > 0 || note.trim().length < 3 || publish.isPending}
            onClick={() => publish.mutate()}
          >
            Publish as v{Math.max(d.active_version, d.base_version) + 1}
          </button>
          <button
            className="secondary"
            disabled={!d.dirty}
            onClick={() => m.mutate(() => unwrap(api.DELETE("/api/line/draft")))}
          >
            Discard draft
          </button>
        </div>
        {publish.error && <p className="error">{publish.error.message}</p>}
        <p className="muted small">
          Changes are saved to the draft as you go. Publishing validates the line and makes it the active version for new
          runs; runs in flight keep their version.
        </p>
      </section>

      <section className="card">
        <h3>Stations → agents</h3>
        <table className="wide">
          <tbody>
            {d.stations.map((s) => (
              <tr key={s.id}>
                <td>
                  <strong>{s.id}</strong> {s.repair && <span className="pill muted">repair</span>}
                </td>
                <td className="small muted">{s.kind === "agent" ? `handler: ${s.handler}` : "deterministic check"}</td>
                <td>
                  {s.kind === "agent" ? (
                    <select
                      value={s.role ?? ""}
                      onChange={(e) => {
                        const agent = e.target.value; // read now: the controlled select re-renders before the request runs
                        m.mutate(() =>
                          unwrap(
                            api.PUT("/api/line/draft/stations/{station_id}/agent", {
                              params: { path: { station_id: s.id } },
                              body: { agent },
                            }),
                          ),
                        );
                      }}
                    >
                      {agentIds.map((id) => (
                        <option key={id} value={id}>
                          {id}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">Adding, removing and reordering stations comes in the next slice.</p>
      </section>

      <section className="card">
        <div className="row spread">
          <h3>Agents</h3>
          <button onClick={() => setEditing({ kind: "agent" })}>New agent</button>
        </div>
        {m.error && <p className="error">{m.error.message}</p>}
        {editing?.kind === "agent" && (
          <AgentEditor
            key={editing.spec?.id ?? "new"}
            initial={editing.spec}
            catalog={catalog.data}
            docs={d.docs}
            saving={m.isPending}
            error={m.error?.message}
            onCancel={() => setEditing(null)}
            onSave={(spec) =>
              m.mutate(() =>
                unwrap(
                  api.PUT("/api/line/draft/agents/{agent_id}", { params: { path: { agent_id: spec.id } }, body: spec }),
                ),
              )
            }
          />
        )}
        <div className="grid2">
          {d.agents.map((a) => (
            <AgentCard
              key={a.spec.id}
              a={a}
              actions={
                <>
                  <button className="secondary" onClick={() => setEditing({ kind: "agent", spec: a.spec })}>
                    Edit
                  </button>
                  {dupFrom === a.spec.id ? (
                    <>
                      <input value={dupId} onChange={(e) => setDupId(e.target.value)} placeholder="new-agent-id" />
                      <button
                        onClick={() =>
                          m.mutate(() =>
                            unwrap(
                              api.POST("/api/line/draft/agents/{agent_id}/duplicate", {
                                params: { path: { agent_id: a.spec.id } },
                                body: { new_id: dupId },
                              }),
                            ),
                          )
                        }
                      >
                        Copy
                      </button>
                    </>
                  ) : (
                    <button
                      className="secondary"
                      onClick={() => {
                        setDupFrom(a.spec.id);
                        setDupId(`${a.spec.id}-copy`);
                      }}
                    >
                      Duplicate
                    </button>
                  )}
                  <button
                    className="secondary"
                    disabled={a.used_by.length > 0}
                    title={a.used_by.length ? `Used by ${a.used_by.join(", ")} — assign another agent first` : "Delete"}
                    onClick={() =>
                      m.mutate(() =>
                        unwrap(api.DELETE("/api/line/draft/agents/{agent_id}", { params: { path: { agent_id: a.spec.id } } })),
                      )
                    }
                  >
                    Delete
                  </button>
                </>
              }
            />
          ))}
        </div>
      </section>

      <section className="card">
        <div className="row spread">
          <h3>Reference documents</h3>
          <button onClick={() => setEditing({ kind: "doc" })}>New document</button>
        </div>
        <p className="muted small">
          Team standards and conventions. Attach them to agents in the agent editor; max {catalog.data.max_doc_chars} characters
          each.
        </p>
        {editing?.kind === "doc" && (
          <DocEditor
            key={editing.doc?.id ?? "new"}
            initial={editing.doc}
            saving={m.isPending}
            onCancel={() => setEditing(null)}
            onSave={(doc) =>
              m.mutate(() =>
                unwrap(api.PUT("/api/line/draft/docs/{doc_id}", { params: { path: { doc_id: doc.id } }, body: doc })),
              )
            }
          />
        )}
        {d.docs.map((doc) => {
          const users = d.agents.filter((a) => (a.spec.context_docs ?? []).includes(doc.id)).map((a) => a.spec.id);
          return (
            <div key={doc.id} className="row spread doc">
              <span>
                <strong>{doc.title}</strong> <span className="muted small">({doc.id}) · used by {users.join(", ") || "none"}</span>
              </span>
              <span className="row">
                <button className="secondary" onClick={() => setEditing({ kind: "doc", doc })}>
                  Edit
                </button>
                <button
                  className="secondary"
                  disabled={users.length > 0}
                  onClick={() =>
                    m.mutate(() => unwrap(api.DELETE("/api/line/draft/docs/{doc_id}", { params: { path: { doc_id: doc.id } } })))
                  }
                >
                  Delete
                </button>
              </span>
            </div>
          );
        })}
      </section>
    </div>
  );
}

function DocEditor({
  initial,
  saving,
  onSave,
  onCancel,
}: {
  initial?: RefDoc;
  saving: boolean;
  onSave: (d: RefDoc) => void;
  onCancel: () => void;
}) {
  const [d, setD] = useState<RefDoc>(initial ?? { id: "", title: "", content: "" });
  return (
    <form
      className="panel editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(d);
      }}
    >
      <div className="grid2">
        <label>
          Id
          <input value={d.id} disabled={!!initial} onChange={(e) => setD({ ...d, id: e.target.value })} pattern="[a-z][a-z0-9-]{1,40}" required />
        </label>
        <label>
          Title
          <input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} required />
        </label>
      </div>
      <label>
        Content (Markdown)
        <textarea className="mono" rows={12} value={d.content} onChange={(e) => setD({ ...d, content: e.target.value })} required />
      </label>
      <div className="row">
        <button disabled={saving}>Save to draft</button>
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
