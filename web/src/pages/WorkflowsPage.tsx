import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useIsAdmin } from "../auth";
import type { components } from "../api/schema";

type Summary = components["schemas"]["WorkflowSummary"];

export const WORKS_ON: Record<string, string> = { factory: "New build", repo: "Existing repo" };

/** Kinds of change a lane serves, in plain words. */
export const kindsText = (kinds: string[] | undefined) => (kinds ?? []).join(" · ");

export default function WorkflowsPage() {
  const isAdmin = useIsAdmin();
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: () => unwrap(api.GET("/api/workflows")) });
  const all = workflows.data ?? [];
  const factory = all.filter((w) => w.factory_default);
  const org = all.filter((w) => !w.factory_default);
  return (
    <div className="stack">
      <section className="card">
        <h2>Workflows</h2>
        <p className="muted">
          A workflow is a lane for one kind of work (ADR-0039). The factory default lanes are always there and
          serve every product that has no org workflow assigned. To change how some products run, copy a lane,
          edit the copy and assign it to those products. Every publish is a new version; changes in flight keep
          the version they started on.
        </p>
        <h3>Factory default lanes</h3>
        <LaneTable rows={factory} isAdmin={isAdmin} />
        <h3>Org workflows</h3>
        {org.length === 0 ? (
          <p className="muted small" data-testid="no-org-workflows">
            None yet. Copy a factory lane to make one.
          </p>
        ) : (
          <LaneTable rows={org} isAdmin={isAdmin} />
        )}
      </section>
    </div>
  );
}

function LaneTable({ rows, isAdmin }: { rows: Summary[]; isAdmin: boolean }) {
  return (
    <table className="wide">
      <thead>
        <tr className="small muted">
          <td>workflow</td>
          <td>works on · for</td>
          <td>active</td>
          <td>stations</td>
          <td>used by</td>
          <td />
        </tr>
      </thead>
      <tbody>
        {rows.map((w) => (
          <tr key={w.workflow_id} data-testid={`workflow-${w.workflow_id}`}>
            <td>
              <Link to={`/workflows/${w.workflow_id}`}>
                <strong>{w.name || w.workflow_id}</strong>
              </Link>
              <div className="small muted">
                <code>{w.workflow_id}</code>
              </div>
            </td>
            <td className="small">
              {WORKS_ON[w.works_on ?? "factory"]}
              <div className="muted">{kindsText(w.kinds)}</div>
            </td>
            <td>
              <span className="pill info">v{w.active_version}</span>{" "}
              {w.draft_dirty && <span className="pill warn">draft</span>}
            </td>
            <td>{w.stations}</td>
            <td className="small">
              {w.factory_default
                ? "products without an org workflow"
                : (w.products ?? []).length > 0
                  ? (w.products ?? []).join(", ")
                  : "no products yet"}
            </td>
            <td>{isAdmin && <RowActions w={w} />}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RowActions({ w }: { w: Summary }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [id, setId] = useState(`${w.workflow_id}-copy`);
  const copy = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/workflows/{workflow_id}/copy", {
          params: { path: { workflow_id: w.workflow_id } },
          body: { new_id: id.trim(), note: "" },
        }),
      ),
    onSuccess: (v) => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      nav(`/workflows/${v.workflow_id}/edit`);
    },
  });
  return (
    <div className="row">
      <Link to={`/workflows/${w.workflow_id}/edit`}>
        <button className="secondary">Edit</button>
      </Link>
      {!open ? (
        <button className="secondary" onClick={() => setOpen(true)}>
          Copy
        </button>
      ) : (
        <>
          <input aria-label="New workflow id" value={id} onChange={(e) => setId(e.target.value)} />
          <button disabled={copy.isPending || id.trim().length < 2} onClick={() => copy.mutate()}>
            Copy
          </button>
        </>
      )}
      {copy.error && <span className="error small">{copy.error.message}</span>}
    </div>
  );
}
