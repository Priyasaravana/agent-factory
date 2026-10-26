import { useQuery } from "@tanstack/react-query";
import { Images } from "lucide-react";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { cn } from "../lib/utils";
import { Panel } from "./ui/panel";

const RUN_LABEL: Record<string, string> = { preview: "Before deploy (preview)", live: "Live app" };

/** "home-phone-dark.png" → route home, size phone, scheme dark. */
function parts(name: string) {
  const m = /^(.*)-(desktop|phone)-(light|dark)\.png$/.exec(name);
  return m ? { route: m[1], size: m[2], scheme: m[3] } : { route: name.replace(/\.png$/, ""), size: "", scheme: "" };
}

/** Route screenshots of this change, light and dark, desktop and phone, and the reviewer's scores
 * against the pack's UI rubric (ADR-0044 §4). The scores are reported only: they never fail a change. */
export default function ScreenshotsPanel({ changeId, runStatus }: { changeId: string; runStatus: string }) {
  const q = useQuery({
    queryKey: ["screenshots", changeId, runStatus],
    queryFn: () =>
      unwrap(api.GET("/api/changes/{change_id}/screenshots", { params: { path: { change_id: changeId } } })),
  });
  const runs = (q.data?.runs ?? []).map((r) => ({ run: r.run, files: r.files ?? [] }));
  const raw = q.data?.review;
  const review = raw
    ? {
        ...raw,
        criteria: raw.criteria ?? [],
        scores: raw.scores ?? {},
        why: raw.why ?? {},
        findings: raw.findings ?? [],
        problems: raw.problems ?? [],
      }
    : null;
  const [run, setRun] = useState<string | null>(null);
  if (!runs.length && !review) return null;
  const current = runs.find((r) => r.run === run) ?? runs[runs.length - 1];
  const routes = new Map<string, string[]>();
  for (const f of current?.files ?? []) {
    const r = parts(f).route;
    routes.set(r, [...(routes.get(r) ?? []), f]);
  }
  const total = runs.reduce((n, r) => n + r.files.length, 0);
  return (
    <Panel
      id="screenshots"
      data-testid="screenshots"
      icon={<Images className="text-primary" />}
      title="Look and feel"
      summary={
        <>
          <span className="pill muted">{total} screenshots</span>
          {review && review.mean != null && (
            <span className={cn("pill", review.mean >= 4 ? "ok" : review.mean >= 3 ? "info" : "warn")}>
              UI {review.mean}/5 · reported only
            </span>
          )}
        </>
      }
    >
      {review && (
        <div className="grid gap-2" data-testid="ui-review">
          {review.summary && <p className="m-0 text-sm">{review.summary}</p>}
          <ul className="m-0 grid list-none gap-1.5 p-0 sm:grid-cols-2">
            {(review.criteria.length ? review.criteria : Object.keys(review.scores).map((id) => ({ id, title: id }))).map(
              (c) => {
                const s = review.scores[c.id];
                return (
                  <li key={c.id} className="grid gap-0.5 rounded-lg border p-2 text-sm">
                    <span className="flex items-center gap-2">
                      <span className="font-medium">{c.title}</span>
                      <span className="ml-auto tabular-nums text-muted-foreground">{s ?? "–"}/5</span>
                    </span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                      <span
                        className={cn("block h-full", (s ?? 0) >= 4 ? "bg-ok" : (s ?? 0) >= 3 ? "bg-info" : "bg-warn")}
                        style={{ width: `${((s ?? 0) / 5) * 100}%` }}
                      />
                    </span>
                    {review.why[c.id] && <span className="text-xs text-muted-foreground">{review.why[c.id]}</span>}
                  </li>
                );
              },
            )}
          </ul>
          {review.findings.length > 0 && (
            <ul className="m-0 grid gap-1 pl-5 text-sm">
              {review.findings.map((f, i) => (
                <li key={i}>
                  {f.screenshot && <code className="mr-1 text-xs text-muted-foreground">{f.screenshot}</code>}
                  {f.issue}
                </li>
              ))}
            </ul>
          )}
          {!review.complete && (
            <p className="note m-0 text-sm">The reviewer's report was incomplete: {review.problems.join("; ")}</p>
          )}
        </div>
      )}
      {runs.length > 1 && (
        <div className="flex w-fit gap-1 rounded-lg border bg-card p-0.5" role="tablist" aria-label="Screenshot run">
          {runs.map((r) => (
            <button
              key={r.run}
              role="tab"
              aria-selected={r.run === current?.run}
              className={cn("tab", r.run === current?.run && "active")}
              onClick={() => setRun(r.run)}
            >
              {RUN_LABEL[r.run] ?? r.run}
            </button>
          ))}
        </div>
      )}
      {[...routes.entries()].map(([route, files]) => (
        <div key={route} className="grid gap-2">
          <span className="text-sm font-medium">/{route === "home" ? "" : route}</span>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {files.map((f) => {
              const p = parts(f);
              const url = `/api/changes/${changeId}/screenshots/${current!.run}/${f}`;
              return (
                <a
                  key={f}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="group grid gap-1 rounded-lg border bg-background/60 p-1.5 no-underline hover:no-underline"
                  title={f}
                >
                  <img
                    src={url}
                    alt={`${route} at ${p.size} width, ${p.scheme} mode`}
                    loading="lazy"
                    className="h-40 w-full rounded object-cover object-top group-hover:opacity-90"
                  />
                  <span className="text-[11px] text-muted-foreground">
                    {p.size} · {p.scheme}
                  </span>
                </a>
              );
            })}
          </div>
        </div>
      ))}
    </Panel>
  );
}
