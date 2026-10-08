import { useQuery } from "@tanstack/react-query";
import { Check, Copy, Download, ScanSearch, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

type Tab = "findings" | "risks" | "tests" | "next" | "signals" | "agents";

const SEV: Record<string, string> = { high: "bad", medium: "warn", low: "muted" };
const KIND: Record<string, string> = { feature: "feature", bug: "bug fix", upkeep: "upkeep" };

/** An existing repo's assessment (ADR-0031): what the engine measured, and what the
 * assessor reported that the engine could check against the repository. */
export default function AssessmentPanel({ changeId, runStatus }: { changeId: string; runStatus: string }) {
  const ready = runStatus === "awaiting_feedback";
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
    dropped: d.dropped ?? [],
    agents_md: d.agents_md ?? "",
    markdown: d.markdown ?? "",
    summary: d.summary ?? "",
    stack_summary: d.stack_summary ?? "",
    level: d.level ?? 0,
    points: d.points ?? 0,
    max_points: d.max_points ?? 0,
  };
  const failing = v.signals.filter((s) => !s.ok);
  const high = v.findings.filter((f) => f.severity === "high").length;
  const tabs: [Tab, string][] = [
    ["findings", `Findings ${v.findings.length}`],
    ["risks", `Risks ${v.risks.length}`],
    ["tests", `Test gaps ${v.test_gaps.length}`],
    ["next", `Next changes ${v.recommendations.length}`],
    ["signals", `Signals ${v.signals.length - failing.length}/${v.signals.length}`],
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
    <section className="grid gap-3 rounded-xl border bg-card p-4" data-testid="assessment" aria-label="Assessment">
      <div className="flex flex-wrap items-center gap-2">
        <ScanSearch className="size-4 text-primary" />
        <h4 className="m-0">Assessment</h4>
        <span className={cn("pill", v.level >= 3 ? "ok" : v.level >= 1 ? "warn" : "bad")}>
          readiness Level {v.level}
        </span>
        <span className="pill muted">
          {v.points}/{v.max_points} points
        </span>
        {high > 0 && <span className="pill bad">{high} high</span>}
        <Button size="sm" variant="outline" className="ml-auto" onClick={download}>
          <Download /> Report (.md)
        </Button>
      </div>
      <p className="m-0 text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{v.stack_summary}</span> · commit{" "}
        <code>{String(v.repo.commit ?? "").slice(0, 12)}</code> on <code>{String(v.repo.ref ?? "")}</code> ·{" "}
        {String(v.repo.files ?? 0)} files
      </p>
      {v.summary && <p className="m-0 text-sm">{v.summary}</p>}

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

      {tab === "findings" && (
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
        <ol className="m-0 grid gap-2 pl-5 text-sm">
          {v.recommendations.map((r, i) => (
            <li key={i}>
              <span className="font-medium">{String(r.title)}</span>{" "}
              <span className="pill info">{KIND[String(r.kind)] ?? String(r.kind)}</span>{" "}
              <span className="pill muted">effort {String(r.effort)}</span>
              <div className="text-muted-foreground">{String(r.why)}</div>
            </li>
          ))}
          {v.recommendations.length === 0 && <p className="m-0 text-muted-foreground">None.</p>}
        </ol>
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
    </section>
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
