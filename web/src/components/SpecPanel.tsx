import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  FileCheck,
  MessageSquareWarning,
  Pencil,
  Send,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";

type Tab = "requirements" | "product" | "technical" | "api" | "trace";

/** The change's specification: what the factory agreed to build, and how each
 * requirement is traced to scenarios, tests and live acceptance. When the spec
 * review gate is on, this is also where a person approves or sends it back. */
export default function SpecPanel({
  changeId,
  runStatus,
}: {
  changeId: string;
  runStatus: string;
}) {
  const qc = useQueryClient();
  const reviewing = runStatus === "awaiting_approval";
  const spec = useQuery({
    queryKey: ["spec", changeId, runStatus],
    queryFn: () =>
      unwrap(
        api.GET("/api/changes/{change_id}/spec", {
          params: { path: { change_id: changeId } },
        }),
      ),
  });
  const [tab, setTab] = useState<Tab>(reviewing ? "requirements" : "trace");
  // a change that reaches the gate while the panel is open should land on what to review
  useEffect(() => {
    if (reviewing) setTab("requirements");
  }, [reviewing]);
  const [comment, setComment] = useState("");
  const [asking, setAsking] = useState(false);
  const [editing, setEditing] = useState<{
    product: string;
    technical: string;
  } | null>(null);
  const done = (msg: string) => {
    qc.invalidateQueries({ queryKey: ["change", changeId] });
    qc.invalidateQueries({ queryKey: ["spec", changeId] });
    qc.invalidateQueries({ queryKey: ["product"] });
    toast.success(msg);
  };
  const path = { params: { path: { change_id: changeId } } };
  const approve = useMutation({
    mutationFn: () => unwrap(api.POST("/api/changes/{change_id}/spec/approve", path)),
    onSuccess: () => done("Spec approved — building"),
  });
  const changes = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/changes/{change_id}/spec/changes", {
          ...path,
          body: { comment },
        }),
      ),
    onSuccess: () => {
      setComment("");
      setAsking(false);
      done("Sent back to intake and design with your notes");
    },
  });
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.PUT("/api/changes/{change_id}/spec", { ...path, body: editing! })),
    onSuccess: (s) => {
      qc.setQueryData(["spec", changeId, runStatus], s);
      setEditing(null);
      toast.success("Spec saved");
    },
  });

  const raw = spec.data;
  if (!raw) return null;
  // optional arrays in the contract: normalise once
  const s = {
    ...raw,
    requirements: raw.requirements ?? [],
    acceptance: (raw.acceptance ?? []).map((a) => ({
      ...a,
      covers: a.covers ?? [],
    })),
    review_notes: raw.review_notes ?? [],
    traceability: (raw.traceability ?? []).map((r) => ({
      ...r,
      scenarios: r.scenarios ?? [],
      tests: r.tests ?? 0,
      holdout: (r.holdout ?? []) as {
        scenario?: unknown;
        passed?: boolean | null;
      }[],
    })),
  };
  if (!s.requirements.length && !reviewing) return null;
  const review = raw.review
    ? {
        ...raw.review,
        requirements: raw.review.requirements ?? [],
        findings: raw.review.findings ?? [],
      }
    : null;
  const reviewed = new Map(review?.requirements.map((r) => [r.id, r]) ?? []);
  const ch = s.changes ?? {};
  const changeChips = [
    ...(ch.added ?? []).map((i) => ["ok", `+${i}`]),
    ...(ch.changed ?? []).map((i) => ["warn", `~${i}`]),
    ...(ch.removed ?? []).map((i) => ["bad", `−${i}`]),
  ];
  const verified = s.traceability.filter(
    (r) => r.holdout.length && r.holdout.every((h) => h.passed),
  ).length;

  return (
    <section
      className={cn(
        "grid gap-3 rounded-xl border p-4",
        reviewing ? "border-warn/60 bg-warn/5" : "bg-card",
      )}
      data-testid="spec-panel"
      aria-label="Specification"
    >
      <div className="flex flex-wrap items-center gap-2">
        <FileCheck className="size-4 text-primary" />
        <h4 className="m-0">
          {reviewing ? "Review the specification" : "Specification"}
        </h4>
        <span className="pill muted">{s.requirements.length} requirements</span>
        <span className="pill muted">
          {s.acceptance.length} acceptance · {s.holdout_count} hidden scenarios
        </span>
        {changeChips.map(([tone, t]) => (
          <span
            key={t}
            className={`pill ${tone}`}
            title="requirement changes in this iteration"
          >
            {t}
          </span>
        ))}
        {s.approved_by && (
          <span className="pill ok">approved by {s.approved_by}</span>
        )}
        {review && (
          <span
            className={`pill ${review.passed ? "ok" : "bad"}`}
            title="latest spec review of the change (ADR-0020)"
          >
            reviewed {review.implemented}/{review.total}
          </span>
        )}
        {s.traceability.some((r) => r.holdout.length) && (
          <span className="pill info">
            {verified}/{s.traceability.length} verified live
          </span>
        )}
        <div
          className="ml-auto flex gap-1 rounded-lg border bg-card p-0.5"
          role="tablist"
        >
          {(
            [
              ["requirements", "Requirements"],
              ["product", "Product spec"],
              ["technical", "Technical design"],
              ["api", "API"],
              ["trace", "Traceability"],
            ] as const
          ).map(([t, label]) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`tab ${tab === t ? "active" : ""}`}
              onClick={() => setTab(t)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {s.review_notes.length > 0 && (
        <div className="note m-0 text-sm">
          <strong>Review notes addressed in this spec:</strong>
          <ul className="m-0 pl-5">
            {s.review_notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}

      {tab === "requirements" && (
        <table className="wide">
          <thead>
            <tr>
              <td>id</td>
              <td>requirement</td>
              <td>acceptance scenarios</td>
            </tr>
          </thead>
          <tbody>
            {s.requirements.map((r) => (
              <tr key={r.id} data-testid={`req-${r.id}`}>
                <td className="font-mono text-xs">{r.id}</td>
                <td>
                  <div className="font-medium">{r.title}</div>
                  {r.detail && (
                    <div className="text-xs text-muted-foreground">
                      {r.detail}
                    </div>
                  )}
                </td>
                <td className="text-xs">
                  {s.acceptance
                    .filter((a) => a.covers.includes(r.id))
                    .map((a) => (
                      <div
                        key={a.id}
                        title={`Given ${a.given} · When ${a.when} · Then ${a.then}`}
                      >
                        <span className="font-mono">{a.id}</span> {a.when} →{" "}
                        {a.then}
                      </div>
                    ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === "product" &&
        (editing ? (
          <textarea
            aria-label="Product spec"
            rows={18}
            value={editing.product}
            onChange={(e) =>
              setEditing({ ...editing, product: e.target.value })
            }
          />
        ) : (
          <div className="pre font-sans text-sm">
            {s.product || "not written yet"}
          </div>
        ))}
      {tab === "technical" &&
        (editing ? (
          <textarea
            aria-label="Technical design"
            rows={18}
            value={editing.technical}
            onChange={(e) =>
              setEditing({ ...editing, technical: e.target.value })
            }
          />
        ) : (
          <div className="pre font-sans text-sm">
            {s.technical || "not written yet"}
          </div>
        ))}
      {tab === "api" && (
        <pre className="pre">{s.openapi || "not written yet"}</pre>
      )}
      {tab === "trace" && (
        <table className="wide" data-testid="traceability">
          <thead>
            <tr>
              <td>id</td>
              <td>requirement</td>
              <td>scenarios</td>
              <td>tagged tests</td>
              {review && <td>review</td>}
              <td>live (hidden scenarios)</td>
            </tr>
          </thead>
          <tbody>
            {s.traceability.map((r) => (
              <tr key={r.id}>
                <td className="font-mono text-xs">{r.id}</td>
                <td>{r.title}</td>
                <td className="font-mono text-xs">
                  {r.scenarios.join(", ") || (
                    <span className="text-bad">none</span>
                  )}
                </td>
                <td>
                  {r.tests ? r.tests : <span className="text-bad">0</span>}
                </td>
                {review && (
                  <td className="text-xs">
                    {reviewed.get(r.id) ? (
                      <span
                        className={`pill ${reviewed.get(r.id)!.status === "implemented" ? "ok" : "bad"}`}
                        title={reviewed.get(r.id)!.where}
                      >
                        {reviewed.get(r.id)!.status}
                      </span>
                    ) : (
                      <span className="muted">not reviewed</span>
                    )}
                  </td>
                )}
                <td className="text-xs">
                  {r.holdout.length === 0 &&
                    (r.no_live_check ? (
                      <span className="muted" title="intake's reason">
                        no live check: {r.no_live_check}
                      </span>
                    ) : (
                      <span className="muted">
                        not covered by hidden scenarios
                      </span>
                    ))}
                  {r.holdout.map((h) => (
                    <span
                      key={String(h.scenario)}
                      className={`pill mr-1 ${h.passed === true ? "ok" : h.passed === false ? "bad" : "muted"}`}
                    >
                      {String(h.scenario)}{" "}
                      {h.passed === true
                        ? "passed"
                        : h.passed === false
                          ? "failed"
                          : "pending"}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === "trace" && review && review.findings.length > 0 && (
        <div className="grid gap-1 text-sm" data-testid="review-findings">
          <strong>Review findings</strong>
          <ul className="m-0 grid list-none gap-1 p-0">
            {review.findings.map((f, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-2">
                <span
                  className={`pill ${f.severity === "minor" ? "muted" : "bad"}`}
                >
                  {f.severity}
                </span>
                {f.requirement && (
                  <span className="font-mono text-xs">{f.requirement}</span>
                )}
                <span>{f.message}</span>
                {f.file && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {f.file}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {reviewing && (
        <div className="grid gap-2 border-t pt-3">
          {editing ? (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => save.mutate()} disabled={save.isPending}>
                <Check /> Save edits
              </Button>
              <Button variant="outline" onClick={() => setEditing(null)}>
                <X /> Cancel
              </Button>
              <span className="self-center text-xs text-muted-foreground">
                Edit the product spec and technical design here; to change
                requirements, request changes so scenarios stay consistent.
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => approve.mutate()}
                disabled={approve.isPending}
              >
                <Check /> Approve &amp; build
              </Button>
              <Button variant="outline" onClick={() => setAsking((v) => !v)}>
                <MessageSquareWarning /> Request changes
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setEditing({ product: s.product, technical: s.technical });
                  setTab("product");
                }}
              >
                <Pencil /> Edit
              </Button>
            </div>
          )}
          {asking && !editing && (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                changes.mutate();
              }}
            >
              <textarea
                aria-label="What should change"
                rows={3}
                className="font-sans text-sm"
                placeholder="e.g. Tags must be case-insensitive; add pagination to the list endpoint"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                required
                minLength={3}
              />
              <Button className="w-fit" disabled={changes.isPending}>
                <Send /> Send back to intake &amp; design
              </Button>
            </form>
          )}
          <Problems
            error={approve.error ?? changes.error ?? save.error ?? null}
          />
        </div>
      )}
    </section>
  );
}
