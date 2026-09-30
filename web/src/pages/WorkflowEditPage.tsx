import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, unwrap, type AgentSpec, type DraftView, type RefDoc } from "../api/client";
import AgentCard from "../components/AgentCard";
import AgentEditor from "../components/AgentEditor";
import LaneBuilder, { type LaneOps } from "../components/LaneBuilder";

type Editing = { kind: "agent"; spec?: AgentSpec } | { kind: "doc"; doc?: RefDoc } | null;

export default function WorkflowEditPage() {
  const { workflowId = "" } = useParams();
  const wid = { workflow_id: workflowId };
  const qc = useQueryClient();
  const nav = useNavigate();
  const draft = useQuery({
    queryKey: ["draft", workflowId],
    queryFn: () => unwrap(api.GET("/api/workflows/{workflow_id}/draft", { params: { path: wid } })),
  });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => unwrap(api.GET("/api/workflow-templates")) });
  const [template, setTemplate] = useState("");
  const [gh, setGh] = useState({ repo: "", path: "", ref: "main" });
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => unwrap(api.GET("/api/catalog")) });
  const [editing, setEditing] = useState<Editing>(null);
  const [note, setNote] = useState("");
  const [dupFrom, setDupFrom] = useState<string | null>(null);
  const [dupId, setDupId] = useState("");

  const apply = (d: DraftView) => qc.setQueryData(["draft", workflowId], d);
  const m = useMutation({
    mutationFn: (fn: () => Promise<DraftView>) => fn(),
    onSuccess: (d) => {
      apply(d);
      setEditing(null);
      setDupFrom(null);
    },
  });
  const publish = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/workflows/{workflow_id}/draft/publish", { params: { path: wid }, body: { note } })),
    onSuccess: () => {
      ["draft", "workflow", "wf-versions", "workflows", "config"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      );
      nav(`/workflows/${workflowId}`);
    },
  });

  if (!draft.data || !catalog.data) return <p className="muted">Loading…</p>;
  const d = draft.data;
  const st = (station_id: string) => ({ params: { path: { ...wid, station_id } } });
  const laneOps: LaneOps = {
    busy: m.isPending,
    add: ({ position, ...body }) =>
      m.mutate(() =>
        unwrap(
          api.POST("/api/workflows/{workflow_id}/draft/stations", {
            params: { path: wid },
            body: { ...body, only_on_fail: body.only_on_fail ?? false, position },
          }),
        ),
      ),
    remove: (id) => m.mutate(() => unwrap(api.DELETE("/api/workflows/{workflow_id}/draft/stations/{station_id}", st(id)))),
    reorder: (order) => {
      // optimistic: show the new order at once, the server answer replaces it
      apply({ ...d, stations: order.map((id) => d.stations.find((s) => s.id === id)!) });
      m.mutate(() =>
        unwrap(api.PUT("/api/workflows/{workflow_id}/draft/stations/order", { params: { path: wid }, body: { order } })),
      );
    },
    update: (id, patch) =>
      m.mutate(() =>
        unwrap(api.PATCH("/api/workflows/{workflow_id}/draft/stations/{station_id}", { ...st(id), body: patch })),
      ),
  };

  return (
    <div className="stack">
      <section className="card">
        <div className="row spread">
          <h2>
            Edit workflow <code>{workflowId}</code> <span className="pill muted">draft from v{d.base_version}</span>{" "}
            {d.dirty ? <span className="pill warn">unpublished changes</span> : <span className="pill ok">no changes</span>}
          </h2>
          <Link to={`/workflows/${workflowId}`}>← back to the workflow</Link>
        </div>
        {d.stale && (
          <p className="note">
            The active workflow is now v{d.active_version}. Publishing this draft will replace it; discard to start from v
            {d.active_version}.
          </p>
        )}
        {Object.keys(d.skill_updates ?? {}).length > 0 && (
          <p className="note">
            Updated imported skills will be pinned when you publish:{" "}
            {Object.entries(d.skill_updates!).map(([n, sha]) => (
              <code key={n}>
                {n}@{sha.slice(0, 7)}{" "}
              </code>
            ))}
          </p>
        )}
        {d.problems.length > 0 && (
          <div className="problems">
            <strong>Fix before publishing:</strong>
            <ul>{d.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        )}
        {(d.warnings ?? []).length > 0 && (
          <details className="warnings">
            <summary>{d.warnings!.length} suggestion(s) — publishing is allowed</summary>
            <ul>{d.warnings!.map((w) => <li key={w}>{w}</li>)}</ul>
          </details>
        )}
        <div className="row">
          <input
            className="grow"
            placeholder="What changed? e.g. stricter acceptance agent"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button
            disabled={(!d.dirty && Object.keys(d.skill_updates ?? {}).length === 0) || d.problems.length > 0 || note.trim().length < 3 || publish.isPending}
            onClick={() => publish.mutate()}
          >
            Publish as v{Math.max(d.active_version, d.base_version) + 1}
          </button>
          <button
            className="secondary"
            disabled={!d.dirty}
            onClick={() => m.mutate(() => unwrap(api.DELETE("/api/workflows/{workflow_id}/draft", { params: { path: wid } })))}
          >
            Discard draft
          </button>
        </div>
        {publish.error && <p className="error">{publish.error.message}</p>}
        <p className="muted small">
          Changes are saved to the draft as you go. Publishing validates the workflow (including each station's role and
          tool needs) and makes it the active version for new runs; runs in flight keep their version.
        </p>
      </section>

      <section className="card" data-testid="spec-review-setting">
        <h3>Spec review gate</h3>
        <p className="muted small">
          Pause each run after design until a person approves the specification (product spec, technical design,
          numbered requirements and acceptance scenarios). Reviewers can approve, request changes or edit the spec.
        </p>
        <div className="flex flex-wrap gap-1 rounded-lg border bg-card p-1 w-fit" role="radiogroup" aria-label="Spec review">
          {(
            [
              ["off", "Off", "never pause (fully automatic)"],
              ["first", "First iteration", "new orders pause; feedback iterations continue"],
              ["always", "Every iteration", "every run pauses after design"],
            ] as const
          ).map(([value, label, hint]) => (
            <button
              key={value}
              role="radio"
              aria-checked={d.spec_review === value}
              title={hint}
              className={`tab ${d.spec_review === value ? "active" : ""}`}
              disabled={m.isPending}
              onClick={() =>
                m.mutate(() =>
                  unwrap(
                    api.PATCH("/api/workflows/{workflow_id}/draft/settings", {
                      params: { path: wid },
                      body: { spec_review: value },
                    }),
                  ),
                )
              }
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      <section className="card" data-testid="learn-setting">
        <h3>Learn from runs</h3>
        <p className="muted small">
          After a run that needed help, suggest short lessons for the agent that caused it. Suggestions appear on the
          workflow page for an admin to accept into this draft or reject; nothing changes until you publish.
        </p>
        <label className="flex w-fit items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={d.learn_from_runs ?? true}
            disabled={m.isPending}
            onChange={(e) =>
              m.mutate(() =>
                unwrap(
                  api.PATCH("/api/workflows/{workflow_id}/draft/settings", {
                    params: { path: wid },
                    body: { learn_from_runs: e.target.checked },
                  }),
                ),
              )
            }
          />
          Suggest learnings after runs that needed help
        </label>
      </section>

      <section className="card">
        <h3>Start from a template</h3>
        <p className="muted small">
          Replaces this draft with a template. Nothing changes for orders until you publish. Current draft template:{" "}
          <code>{d.template}</code>
        </p>
        <div className="row">
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">built-in template…</option>
            {templates.data?.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name} — {t.stations.length} stations
              </option>
            ))}
          </select>
          <button
            className="secondary"
            disabled={!template || m.isPending}
            onClick={() =>
              m.mutate(() =>
                unwrap(
                  api.POST("/api/workflows/{workflow_id}/draft/from-template", {
                    params: { path: wid },
                    body: { template },
                  }),
                ),
              )
            }
          >
            Use template
          </button>
        </div>
        <div className="row">
          <input placeholder="owner/repo" value={gh.repo} onChange={(e) => setGh({ ...gh, repo: e.target.value })} />
          <input
            className="grow"
            placeholder="path/to/folder (contains workflow.yaml)"
            value={gh.path}
            onChange={(e) => setGh({ ...gh, path: e.target.value })}
          />
          <input placeholder="ref" value={gh.ref} onChange={(e) => setGh({ ...gh, ref: e.target.value })} />
          <button
            className="secondary"
            disabled={!gh.repo || m.isPending}
            onClick={() =>
              m.mutate(() =>
                unwrap(
                  api.POST("/api/workflows/{workflow_id}/draft/from-github", { params: { path: wid }, body: gh }),
                ),
              )
            }
          >
            Import from GitHub
          </button>
        </div>
        <p className="muted small">
          GitHub imports are pinned to the exact commit. Private repos use <code>SKILLS_GITHUB_TOKEN</code> from{" "}
          <code>.env</code>.
        </p>
      </section>

      <section className="card">
        <h3>Lane</h3>
        <LaneBuilder draft={d} handlers={catalog.data.handlers} ops={laneOps} />
        {m.error && <p className="error">{m.error.message}</p>}
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
                  api.PUT("/api/workflows/{workflow_id}/draft/agents/{agent_id}", {
                    params: { path: { ...wid, agent_id: spec.id } },
                    body: spec,
                  }),
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
                              api.POST("/api/workflows/{workflow_id}/draft/agents/{agent_id}/duplicate", {
                                params: { path: { ...wid, agent_id: a.spec.id } },
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
                        unwrap(api.DELETE("/api/workflows/{workflow_id}/draft/agents/{agent_id}", {
                            params: { path: { ...wid, agent_id: a.spec.id } },
                          })),
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
                unwrap(api.PUT("/api/workflows/{workflow_id}/draft/docs/{doc_id}", {
                    params: { path: { ...wid, doc_id: doc.id } },
                    body: doc,
                  })),
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
                    m.mutate(() => unwrap(api.DELETE("/api/workflows/{workflow_id}/draft/docs/{doc_id}", {
                      params: { path: { ...wid, doc_id: doc.id } },
                    })))
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
