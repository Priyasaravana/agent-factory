import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, Download, ScanSearch, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

type Tab = "findings" | "risks" | "tests" | "next" | "coverage" | "signals" | "packs" | "agents";

const SEV: Record<string, string> = { critical: "bad", high: "bad", medium: "warn", low: "muted", info: "muted" };

type Issue = {
  id: string;
  title: string;
  severity: string;
  category: string;
  detail: string;
  at: string[];
  places: number;
  sources: { station: string; via: string; rule: string }[];
};
type CoverageCheck = { station: string; title: string; status: string; via?: string; note?: string };
const KIND: Record<string, string> = { feature: "feature", bug: "bug fix", upkeep: "upkeep" };

/** An existing repo's assessment (ADR-0031): what the engine measured, and what the
 * assessor reported that the engine could check against the repository. */
export default function AssessmentPanel({
  changeId,
  runStatus,
  productId,
  canRequest = false,
  onRequested,
}: {
  changeId: string;
  runStatus: string;
  productId?: string;
  canRequest?: boolean;
  onRequested?: () => void;
}) {
  const ready = runStatus === "awaiting_feedback";
  // a recommendation becomes a change, delivered as a pull request (ADR-0033)
  const request = useMutation({
    mutationFn: (r: { title: string; why: string; kind: "feature" | "bug" | "upkeep" }) =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId! } },
          body: { text: `${r.title}\n\n${r.why}`, kind: r.kind },
        }),
      ),
    onSuccess: () => {
      toast.success("Change requested", { description: "The factory will open a pull request." });
      onRequested?.();
    },
  });
  const a = useQuery({
    queryKey: ["assessment", changeId],
    queryFn: () =>
      unwrap(api.GET("/api/changes/{change_id}/assessment", { params: { path: { change_id: changeId } } })),
    enabled: ready,
    retry: false,
  });
  const [tab, setTab] = useState<Tab>("findings");

  if (!ready)
    return (
      <p className="note m-0 text-sm">
        The assessment report appears here when the Report station has run. The repository is read, never changed.
      </p>
    );
  if (a.isLoading) return <div className="h-40 animate-pulse rounded-xl bg-muted" />;
  if (a.error || !a.data) return <p className="error m-0">{a.error?.message ?? "no assessment"}</p>;
  const d = a.data;
  const v = {
    ...d,
    repo: d.repo ?? {},
    signals: d.signals ?? [],
    findings: (d.findings ?? []).map((f) => ({ ...f, at: f.at ?? [], detail: f.detail ?? "" })),
    risks: d.risks ?? [],
    test_gaps: d.test_gaps ?? [],
    recommendations: d.recommendations ?? [],
    issues: (d.issues ?? []) as Issue[],
    issue_counts: d.issue_counts ?? {},
    coverage: (d.coverage ?? {}) as { complete?: boolean; gaps?: string[]; checks?: CoverageCheck[] },
    dropped: d.dropped ?? [],
    agents_md: d.agents_md ?? "",
    markdown: d.markdown ?? "",
    summary: d.summary ?? "",
    stack_summary: d.stack_summary ?? "",
    level: d.level ?? 0,
    points: d.points ?? 0,
    max_points: d.max_points ?? 0,
    stack_packs: d.stack_packs ?? [],
    pack_signals: d.pack_signals ?? [],
  };
  const failing = v.signals.filter((s) => !s.ok);
  // every station's issues when the report consolidated them (rollup), else the rules' findings
  const rolled = v.coverage.checks !== undefined;
  const high = rolled
    ? (v.issue_counts.critical ?? 0) + (v.issue_counts.high ?? 0)
    : v.findings.filter((f) => f.severity === "high").length;
  const gaps = v.coverage.gaps ?? [];
  const tabs: [Tab, string][] = [
    ["findings", rolled ? `Issues ${v.issues.length}` : `Findings ${v.findings.length}`],
    ["risks", `Risks ${v.risks.length}`],
    ["tests", `Test gaps ${v.test_gaps.length}`],
    ["next", `Next changes ${v.recommendations.length}`],
    ...(rolled ? ([["coverage", gaps.length ? "Coverage ⚠" : "Coverage"]] as [Tab, string][]) : []),
    ["signals", `Signals ${v.signals.length - failing.length}/${v.signals.length}`],
    ["packs", `Stack packs ${v.stack_packs.length}`],
    ...(v.agents_md ? ([["agents", "AGENTS.md"]] as [Tab, string][]) : []),
  ];
  const download = () => {
    const url = URL.createObjectURL(new Blob([v.markdown], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `assessment-${String(v.repo.slug ?? "repo").replace("/", "-")}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Panel
      id="assessment"
      defaultOpen
      data-testid="assessment"
      aria-label="Assessment"
      icon={<ScanSearch className="text-primary" />}
      title="Assessment"
      summary={
        <>
          <span className={cn("pill", v.level >= 3 ? "ok" : v.level >= 1 ? "warn" : "bad")}>
            readiness Level {v.level}
          </span>
          <span className="pill muted">
            {v.points}/{v.max_points} points
          </span>
          {high > 0 && <span className="pill bad">{high} critical/high</span>}
          {rolled && gaps.length > 0 && <span className="pill warn">scan coverage incomplete</span>}
        </>
      }
      right={
        <Button size="sm" variant="outline" onClick={download}>
          <Download /> Report (.md)
        </Button>
      }
    >
      <p className="m-0 text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{v.stack_summary}</span> · commit{" "}
        <code>{String(v.repo.commit ?? "").slice(0, 12)}</code> on <code>{String(v.repo.ref ?? "")}</code> ·{" "}
        {String(v.repo.files ?? 0)} files
      </p>
      {v.summary && <p className="m-0 text-sm">{v.summary}</p>}
      {rolled && gaps.length > 0 && (
        <div className="note m-0 text-sm" data-testid="coverage-gaps">
          <strong>Not every check really ran.</strong> No findings from these is not a clean result:
          <ul className="m-0 mt-1 pl-5">
            {gaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </div>
      )}

      {canRequest && (
        <p className="m-0 text-xs text-muted-foreground">
          Every issue, risk, test gap and recommendation here is tracked in <strong>Work items</strong> above,
          across iterations: select any of them to make a change, or mark them won't do.
        </p>
      )}
      <div className="flex w-fit flex-wrap gap-1 rounded-lg border bg-card p-0.5" role="tablist" aria-label="Assessment">
        {tabs.map(([t, label]) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={cn("tab", tab === t && "active")}
            onClick={() => setTab(t)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "findings" && rolled && (
        <List empty="No issues from the rules or the scans.">
          {v.issues.map((i) => (
            <li key={i.id} className="grid gap-0.5">
              <span>
                <span className="text-xs text-muted-foreground">{i.id}</span>{" "}
                <span className={`pill ${SEV[i.severity] ?? "muted"}`}>{i.severity}</span>{" "}
                <span className="font-medium">{i.title}</span>
              </span>
              {i.detail && <span className="text-muted-foreground">{i.detail}</span>}
              <Files files={i.at} />
              <span className="text-xs text-muted-foreground">
                found by{" "}
                {i.sources
                  .map((s) => `${s.station} via ${s.via}${s.rule ? ` (${s.rule})` : ""}`)
                  .join("; ")}
                {i.places > i.at.length && ` · ${i.places} places in all`}
              </span>
            </li>
          ))}
        </List>
      )}
      {tab === "findings" && !rolled && (
        <List empty="No findings from the rules.">
          {v.findings.map((f) => (
            <li key={f.rule + f.title} className="grid gap-0.5">
              <span>
                <span className={`pill ${SEV[f.severity] ?? "muted"}`}>{f.severity}</span>{" "}
                <span className="font-medium">{f.title}</span>{" "}
                <span className="text-xs text-muted-foreground">({f.area})</span>
              </span>
              <span className="text-muted-foreground">{f.detail}</span>
              <Files files={f.at} />
            </li>
          ))}
        </List>
      )}
      {tab === "risks" && (
        <List empty="The assessor reported no risks the repo supports.">
          {v.risks.map((r, i) => (
            <li key={i} className="grid gap-0.5">
              <span>
                <span className={`pill ${SEV[String(r.severity)] ?? "muted"}`}>{String(r.severity)}</span>{" "}
                {r.pillar ? <span className="pill muted">{String(r.pillar)}</span> : null}{" "}
                <span className="font-medium">{String(r.title)}</span>
              </span>
              <span className="text-muted-foreground">{String(r.detail)}</span>
              <Files files={r.files as string[]} />
            </li>
          ))}
        </List>
      )}
      {tab === "tests" && (
        <List empty="No test gaps reported.">
          {v.test_gaps.map((g, i) => (
            <li key={i} className="grid gap-0.5">
              <span className="font-medium">{String(g.area)}</span>
              <span className="text-muted-foreground">{String(g.why)}</span>
              <Files files={g.files as string[]} />
            </li>
          ))}
        </List>
      )}
      {tab === "next" && (
        <>
        <ol className="m-0 grid gap-2 pl-5 text-sm">
          {v.recommendations.map((r, i) => (
            <li key={i}>
              <span className="font-medium">{String(r.title)}</span>{" "}
              <span className="pill info">{KIND[String(r.kind)] ?? String(r.kind)}</span>{" "}
              <span className="pill muted">effort {String(r.effort)}</span>{" "}
              {r.severity ? (
                <span className={`pill ${SEV[String(r.severity)] ?? "muted"}`}>{String(r.severity)}</span>
              ) : null}{" "}
              {r.impact ? <span className="pill muted">{String(r.impact)} impact</span> : null}
              <div className="text-muted-foreground">{String(r.why)}</div>
              {r.basis ? (
                <div className="text-xs text-muted-foreground">
                  Basis: {String(r.basis)}
                  {Array.isArray(r.issues) && r.issues.length > 0 && ` · issues ${(r.issues as string[]).join(", ")}`}
                </div>
              ) : null}

            </li>
          ))}
          {v.recommendations.length === 0 && <p className="m-0 text-muted-foreground">None.</p>}
        </ol>
        <Problems error={request.error} />
        </>
      )}
      {tab === "coverage" && (
        <table className="text-sm" data-testid="coverage">
          <tbody>
            {(v.coverage.checks ?? []).map((c) => (
              <tr key={c.station}>
                <td className="font-medium">{c.title}</td>
                <td>
                  <span className={`pill ${c.status === "tool" ? "ok" : c.status === "not run" ? "bad" : "warn"}`}>
                    {c.status}
                  </span>
                </td>
                <td className="text-muted-foreground">{c.via}</td>
                <td className="text-xs text-muted-foreground">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === "signals" && (
        <ul className="m-0 grid list-none gap-1 p-0 text-sm">
          {v.signals.map((s) => (
            <li key={s.id} className="flex items-start gap-2">
              {s.ok ? (
                <Check className="mt-0.5 size-4 shrink-0 text-ok" aria-label="met" />
              ) : (
                <X className="mt-0.5 size-4 shrink-0 text-bad" aria-label="not met" />
              )}
              <span>
                {s.title} <span className="text-xs text-muted-foreground">level {s.level} · {s.pillar}</span>
                {!s.ok && s.hint && <span className="block text-xs text-muted-foreground">{s.hint}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {tab === "packs" && (
        <div className="grid gap-3 text-sm" data-testid="stack-packs">
          {v.stack_packs.length === 0 ? (
            <p className="m-0 text-muted-foreground">
              No installed stack pack matches this repository, so its changes run in generic mode.
            </p>
          ) : (
            <p className="m-0 text-xs text-muted-foreground">
              Detected from the repo's files at the root and one folder down. Each pack's skill guides changes to its
              folder; the repo's own CI, AGENTS.md and conventions win. Advisory: not part of the Level above.
            </p>
          )}
          {v.pack_signals.map((g) => (
            <div key={`${g.pack}:${g.folder}`} className="grid gap-1 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{g.title}</span>
                <span className="pill muted">
                  {g.pack}@{g.version}
                </span>
                <span className="pill info">{g.folder ? `${g.folder}/` : "repo root"}</span>
                <span className={cn("pill ml-auto", g.met === g.total ? "ok" : "warn")}>
                  golden path {g.met}/{g.total}
                </span>
              </div>
              <ul className="m-0 grid list-none gap-1 p-0">
                {(g.signals ?? []).map((s) => (
                  <li key={s.id} className="flex items-start gap-2">
                    {s.ok ? (
                      <Check className="mt-0.5 size-4 shrink-0 text-ok" aria-label="met" />
                    ) : (
                      <X className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="not met" />
                    )}
                    <span>
                      {s.title} <span className="text-xs text-muted-foreground">{s.pillar}</span>
                      {!s.ok && s.hint && <span className="block text-xs text-muted-foreground">{s.hint}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {tab === "agents" && (
        <div className="grid gap-2">
          <p className="m-0 text-xs text-muted-foreground">
            Proposed, not committed: copy it into the repository if it fits.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="w-fit"
            onClick={() => {
              void navigator.clipboard?.writeText(v.agents_md);
              toast.success("AGENTS.md copied");
            }}
          >
            <Copy /> Copy
          </Button>
          <pre className="pre">{v.agents_md}</pre>
        </div>
      )}
      {v.dropped.length > 0 && (
        <details className="text-xs text-muted-foreground">
          <summary>Left out of the assessor's report ({v.dropped.length})</summary>
          <ul className="m-0 mt-1 pl-5">
            {v.dropped.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        </details>
      )}
    </Panel>
  );
}

function List({ empty, children }: { empty: string; children: React.ReactNode[] }) {
  if (!children.length) return <p className="m-0 text-sm text-muted-foreground">{empty}</p>;
  return <ul className="m-0 grid list-none gap-3 p-0 text-sm">{children}</ul>;
}

function Files({ files }: { files?: string[] }) {
  if (!files?.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {files.slice(0, 8).map((f) => (
        <code key={f} className="text-xs">
          {f}
        </code>
      ))}
      {files.length > 8 && <span className="text-xs text-muted-foreground">+{files.length - 8} more</span>}
    </span>
  );
}
