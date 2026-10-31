import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, GitPullRequest, ListPlus, RotateCcw, ShieldCheck, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel } from "./ui/panel";

type Item = components["schemas"]["WorkItem"];
type Tab = "open" | "in_progress" | "fixed" | "closed";

const SEV: Record<string, string> = { critical: "bad", high: "bad", medium: "warn", low: "muted", info: "muted" };
const KIND: Record<string, string> = {
  issue: "issue",
  risk: "risk",
  test_gap: "test gap",
  recommendation: "recommendation",
  coverage_gap: "coverage gap",
};
const TABS: [Tab, string, (i: Item) => boolean][] = [
  ["open", "Open", (i) => i.status === "open"],
  ["in_progress", "In progress", (i) => i.status === "in_progress"],
  ["fixed", "Fixed", (i) => i.status === "fixed"],
  ["closed", "Won't do / accepted", (i) => i.status === "wont_do" || i.status === "accepted_risk"],
];

/** The product's work ledger (ADR-0040): everything found across iterations, what is being
 * addressed, what a later assessment verified as fixed, and what was decided against. */
export default function WorkLedger({
  productId,
  canChange,
  onChanged,
  attention = false,
  nextIteration,
  onAddToNext,
}: {
  productId: string;
  canChange: boolean;
  onChanged?: () => void;
  /** a held change waits on a decision here (accept a risk): open and highlight the panel */
  attention?: boolean;
  /** a factory product: items go to the "Next iteration" card (the one place an iteration starts)
   * instead of starting a change from here; `nextIteration` holds what is already added there */
  nextIteration?: Set<string>;
  onAddToNext?: (ids: string[]) => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["work-items", productId],
    queryFn: () =>
      unwrap(api.GET("/api/products/{product_id}/work-items", { params: { path: { product_id: productId } } })),
    refetchInterval: 8_000,
  });
  const [tab, setTab] = useState<Tab>("open");
  const [kind, setKind] = useState("");
  const [sev, setSev] = useState("");
  const [iteration, setIteration] = useState("");
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [mode, setMode] = useState<"" | "change" | "wont_do" | "accepted_risk">("");
  const [note, setNote] = useState("");
  const [changeKind, setChangeKind] = useState<"bug" | "upkeep" | "feature">("upkeep");
  const [until, setUntil] = useState("");

  const items = q.data ?? [];
  const iterations = useMemo(
    () => [...new Set(items.flatMap((i) => i.seen_in ?? []))].sort((a, b) => a - b),
    [items],
  );
  const filter = TABS.find((t) => t[0] === tab)![2];
  const s = search.trim().toLowerCase();
  const shown = items.filter(
    (i) =>
      filter(i) &&
      (!kind || i.kind === kind) &&
      (!sev || i.severity === sev) &&
      (!iteration || (i.seen_in ?? []).includes(Number(iteration))) &&
      (!s || `${i.title} ${i.detail} ${(i.at ?? []).join(" ")}`.toLowerCase().includes(s)),
  );
  const chosen = items.filter((i) => picked.has(i.id));
  const done = () => {
    setPicked(new Set());
    setMode("");
    setNote("");
    qc.invalidateQueries({ queryKey: ["work-items", productId] });
    onChanged?.();
  };
  const makeChange = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/feedback", {
          params: { path: { product_id: productId } },
          body: {
            text: note.trim() || `Address ${chosen.length} work item(s)`,
            kind: changeKind,
            items: chosen.map((i) => i.id),
          },
        }),
      ),
    onSuccess: () => {
      toast.success("Change requested", { description: `${chosen.length} item(s) are now in progress.` });
      done();
    },
  });
  const decide = useMutation({
    mutationFn: (decision: "wont_do" | "accepted_risk" | "reopen") =>
      unwrap(
        api.POST("/api/products/{product_id}/work-items/decide", {
          params: { path: { product_id: productId } },
          body: {
            items: chosen.map((i) => i.id),
            decision,
            reason: note.trim(),
            until: decision === "accepted_risk" && until ? new Date(until).toISOString() : null,
          },
        }),
      ),
    onSuccess: done,
  });

  if (q.isLoading) return <div className="h-24 animate-pulse rounded-xl bg-muted" />;
  if (!items.length) return null;
  const counts = Object.fromEntries(TABS.map(([t, , f]) => [t, items.filter(f).length]));
  const pickable = tab === "open" || tab === "closed" || tab === "fixed";
  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };
  const startChange = () => {
    const kinds = chosen.map((i) => i.change_kind);
    setChangeKind((kinds.includes("bug") ? "bug" : kinds.includes("feature") ? "feature" : "upkeep") as never);
    setMode("change");
  };

  return (
    <Panel
      id="work-ledger"
      tone={attention ? "attention" : "default"}
      attention={attention}
      data-testid="work-ledger"
      aria-label="Work items"
      icon={<ClipboardList className="text-primary" />}
      title="Work items"
      summary={
        <>
          <span className={cn("pill", counts.open ? "warn" : "muted")}>{counts.open} open</span>
          {counts.in_progress > 0 && <span className="pill info">{counts.in_progress} in progress</span>}
          <span className="pill muted">{counts.fixed} fixed</span>
          <span>everything found across iterations, and what became of it</span>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-lg border bg-card p-0.5" role="tablist" aria-label="Status">
          {TABS.map(([t, label]) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={cn("tab", tab === t && "active")}
              onClick={() => {
                setTab(t);
                setPicked(new Set());
                setMode("");
              }}
            >
              {label} {counts[t]}
            </button>
          ))}
        </div>
        <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)} className="w-auto py-1 text-sm">
          <option value="">all kinds</option>
          {Object.entries(KIND).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select aria-label="Severity" value={sev} onChange={(e) => setSev(e.target.value)} className="w-auto py-1 text-sm">
          <option value="">all severities</option>
          {["critical", "high", "medium", "low", "info"].map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <select
          aria-label="Iteration"
          value={iteration}
          onChange={(e) => setIteration(e.target.value)}
          className="w-auto py-1 text-sm"
        >
          <option value="">all iterations</option>
          {iterations.map((n) => (
            <option key={n} value={n}>
              seen in iteration {n}
            </option>
          ))}
        </select>
        <input
          type="search"
          aria-label="Search work items"
          placeholder="Search…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8 w-48 text-sm"
        />
      </div>

      {pickable && chosen.length > 0 && (
        <div className="grid gap-2 rounded-lg border bg-muted/40 p-3 text-sm" data-testid="ledger-actions">
          <div className="flex flex-wrap items-center gap-2">
            <strong>{chosen.length} selected</strong>
            {tab === "open" && (
              <>
                {onAddToNext ? (
                  <Button
                    size="sm"
                    data-testid="add-to-next"
                    onClick={() => {
                      onAddToNext(chosen.map((i) => i.id));
                      toast.success(`Added to the next iteration`, {
                        description: `${chosen.length} item(s) are ticked on the Next iteration card.`,
                      });
                      setPicked(new Set());
                      setMode("");
                      document.getElementById("next-iteration")?.scrollIntoView({ behavior: "smooth", block: "start" });
                    }}
                  >
                    <ListPlus /> Add to next iteration
                  </Button>
                ) : (
                  <Button size="sm" disabled={!canChange} onClick={startChange}>
                    <GitPullRequest /> Make a change
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={() => setMode("wont_do")}>
                  <X /> Won't do
                </Button>
                {chosen.some((i) => i.kind === "risk" || i.kind === "issue") && (
                  <Button size="sm" variant="outline" onClick={() => setMode("accepted_risk")}>
                    <ShieldCheck /> Accept risk
                  </Button>
                )}
              </>
            )}
            {tab !== "open" && (
              <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate("reopen")}>
                <RotateCcw /> Reopen
              </Button>
            )}
            <button className="tab ml-auto" onClick={() => setPicked(new Set())}>
              clear
            </button>
          </div>
          {!canChange && tab === "open" && !onAddToNext && (
            <span className="text-xs text-muted-foreground">A change can start once the current iteration stops.</span>
          )}
          {mode === "change" && (
            <div className="grid gap-2">
              <label className="grid gap-1">
                What to tell the developer (optional; the items are listed for it)
                <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  aria-label="Change kind"
                  value={changeKind}
                  onChange={(e) => setChangeKind(e.target.value as never)}
                  className="w-auto py-1"
                >
                  <option value="bug">bug fix</option>
                  <option value="upkeep">upkeep</option>
                  <option value="feature">feature</option>
                </select>
                <Button size="sm" disabled={makeChange.isPending} onClick={() => makeChange.mutate()}>
                  Start the change
                </Button>
              </div>
            </div>
          )}
          {(mode === "wont_do" || mode === "accepted_risk") && (
            <div className="grid gap-2">
              <label className="grid gap-1">
                Why (kept with the items for audit)
                <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="flex flex-wrap items-center gap-2">
                {mode === "accepted_risk" && (
                  <label className="flex items-center gap-2">
                    until <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
                  </label>
                )}
                <Button
                  size="sm"
                  disabled={note.trim().length < 10 || decide.isPending}
                  onClick={() => decide.mutate(mode)}
                >
                  {mode === "wont_do" ? "Mark won't do" : "Accept the risk"}
                </Button>
              </div>
            </div>
          )}
          <Problems error={makeChange.error ?? decide.error} />
        </div>
      )}

      {shown.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">Nothing here.</p>
      ) : (
        <ul className="m-0 grid list-none gap-2 p-0 text-sm">
          {shown.map((i) => (
            <li key={i.id} className="grid gap-1 rounded-lg border p-2" data-testid={`work-item-${i.id}`}>
              <div className="flex flex-wrap items-center gap-2">
                {pickable && (
                  <input
                    type="checkbox"
                    aria-label={`Select ${i.title}`}
                    checked={picked.has(i.id)}
                    onChange={() => toggle(i.id)}
                  />
                )}
                <span className={`pill ${SEV[i.severity ?? "medium"] ?? "muted"}`}>{i.severity}</span>
                <span className="pill muted">{KIND[i.kind]}</span>
                {nextIteration?.has(i.id) && i.status === "open" && (
                  <span className="pill info">in next iteration</span>
                )}
                <span
                  role="button"
                  tabIndex={0}
                  aria-expanded={open === i.id}
                  className="cursor-pointer font-medium hover:underline"
                  onClick={() => setOpen(open === i.id ? null : i.id)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen(open === i.id ? null : i.id)}
                >
                  {i.title}
                </span>
                <span className="ml-auto flex flex-wrap gap-1 text-xs text-muted-foreground">
                  {(i.seen_in ?? []).map((n) => (
                    <span key={n} className="pill muted" title={`seen in iteration ${n}`}>
                      it {n}
                    </span>
                  ))}
                </span>
              </div>
              {i.status === "in_progress" && (
                <span className="text-xs text-muted-foreground">
                  being addressed by change {(i.claimed_by ?? []).join(", ")}: the next assessment verifies it
                </span>
              )}
              {(i.status === "wont_do" || i.status === "accepted_risk") && (
                <span className="text-xs text-muted-foreground">
                  {i.status === "wont_do" ? "won't do" : "risk accepted"}
                  {i.accepted_until ? ` until ${new Date(i.accepted_until).toLocaleDateString()}` : ""} by{" "}
                  {i.decision_by ?? "?"}: {i.decision_reason}
                </span>
              )}
              {open === i.id && (
                <div className="grid gap-1 pl-6 text-muted-foreground">
                  {i.detail && <span>{i.detail}</span>}
                  {(i.at ?? []).length > 0 && (
                    <span className="flex flex-wrap gap-1">
                      {(i.at ?? []).slice(0, 8).map((a) => (
                        <code key={a} className="text-xs">
                          {a}
                        </code>
                      ))}
                    </span>
                  )}
                  {(i.sources ?? []).length > 0 && (
                    <span className="text-xs">
                      found by {(i.sources ?? []).map((s) => `${s.station} via ${s.via}`).join("; ")}
                    </span>
                  )}
                  <ol className="m-0 pl-5 text-xs">
                    {(i.history ?? []).map((h, n) => (
                      <li key={n}>
                        {new Date(h.at).toLocaleString()} · {h.event}
                        {h.iteration ? ` (iteration ${h.iteration})` : ""}
                        {h.note ? `: ${h.note}` : ""}
                        {h.by ? ` · ${h.by}` : ""}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
