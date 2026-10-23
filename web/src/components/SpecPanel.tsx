import { useQuery } from "@tanstack/react-query";
import { FileCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";

type Tab = "requirements" | "product" | "technical" | "api" | "trace";

/** The change's specification: what the factory agreed to build, and how each
 * requirement is traced to scenarios, tests and live acceptance. At the review
 * gate, changes and approval go through "Align with the architect". */
export default function SpecPanel({
  changeId,
  runStatus,
}: {
  changeId: string;
  runStatus: string;
}) {
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
      {tab === "product" && (
        <div className="pre font-sans text-sm">
          {s.product || "not written yet"}
        </div>
      )}
      {tab === "technical" && (
        <div className="pre font-sans text-sm">
          {s.technical || "not written yet"}
        </div>
      )}
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
        <p className="m-0 border-t pt-3 text-sm text-muted-foreground">
          To change anything here, tell the architect in “Align with the
          architect” above: it updates the spec and plan, or sends a
          requirements change back to the specification. Approve there when you
          are aligned.
        </p>
      )}
    </section>
  );
}
