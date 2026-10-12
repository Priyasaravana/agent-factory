import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";

/** The products an org workflow is assigned to (ADR-0039). Unassigned products fall back to the
 * factory default lane at once. */
export default function AssignmentsCard({
  workflowId,
  worksOn,
  assigned,
  isAdmin,
}: {
  workflowId: string;
  worksOn: string;
  assigned: string[];
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const products = useQuery({ queryKey: ["products"], queryFn: () => unwrap(api.GET("/api/products")) });
  const [pick, setPick] = useState("");
  const done = () => ["workflow", "workflows"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const path = { params: { path: { workflow_id: workflowId } } };
  const assign = useMutation({
    mutationFn: (product_id: string) =>
      unwrap(api.POST("/api/workflows/{workflow_id}/assign", { ...path, body: { product_id } })),
    onSuccess: () => {
      setPick("");
      done();
    },
  });
  const unassign = useMutation({
    mutationFn: (product_id: string) =>
      unwrap(api.POST("/api/workflows/{workflow_id}/unassign", { ...path, body: { product_id } })),
    onSuccess: done,
  });
  const fits = (products.data ?? []).filter(
    (p) => (p.target === "repo" ? "repo" : "factory") === worksOn,
  );
  const mine = fits.filter((p) => assigned.includes(p.slug));
  const others = fits.filter((p) => !assigned.includes(p.slug));
  const error = assign.error ?? unassign.error;
  return (
    <section className="card" data-testid="assignments">
      <h3>Products using this workflow</h3>
      {mine.length === 0 ? (
        <p className="muted small">No products yet: every product runs the factory default lane.</p>
      ) : (
        <ul className="list-none p-0">
          {mine.map((p) => (
            <li key={p.id} className="row">
              <strong>{p.title}</strong> <code className="small">{p.slug}</code>
              {isAdmin && (
                <button className="secondary" onClick={() => unassign.mutate(p.id)} disabled={unassign.isPending}>
                  Back to factory default
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {isAdmin && others.length > 0 && (
        <div className="row">
          <select aria-label="Product to assign" value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Assign to a product…</option>
            {others.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title} ({p.slug})
              </option>
            ))}
          </select>
          <button disabled={!pick || assign.isPending} onClick={() => assign.mutate(pick)}>
            Assign
          </button>
        </div>
      )}
      {error && <p className="error">{error.message}</p>}
    </section>
  );
}
