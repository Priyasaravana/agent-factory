import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CircleCheck,
  CircleDashed,
  CircleX,
  DatabaseBackup,
  HeartPulse,
  RefreshCw,
  LifeBuoy,
  RotateCcw,
  Siren,
  Target,
  UserRound,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import { ago } from "../lib/time";
import { cn } from "../lib/utils";
import Problems from "./Problems";
import { Button } from "./ui/button";
import { Panel, type PanelTone } from "./ui/panel";

type View = components["schemas"]["OperationsView"];
type Record_ = components["schemas"]["OperationRecord"];
type Response_ = components["schemas"]["IncidentResponse"];
type WorkItem_ = components["schemas"]["WorkItem"];

const STATE: Record<string, { label: string; tone: string }> = {
  ok: { label: "Within target", tone: "ok" },
  breached: { label: "Outside target", tone: "bad" },
  unknown: { label: "Not enough readings yet", tone: "muted" },
  off: { label: "Not watched", tone: "muted" },
};
const KIND: Record<string, string> = {
  backup: "Backup",
  restore: "Restore drill",
  upkeep: "Upkeep check",
  alert: "Alert",
  recovered: "Recovered",
  retire: "Retirement",
  owner: "Owner",
  respond: "Response",
  restored: "Restored",
  resolved: "Resolved",
};
const STEP: Record<string, string> = {
  reapply: "Re-apply the recorded release",
  restart: "Restart the app",
};
const STAGES = ["open", "mitigating", "restored", "resolved"] as const;
const STAGE_LABEL: Record<string, string> = {
  open: "Open",
  mitigating: "Restoring",
  restored: "Restored",
  resolved: "Resolved",
};

function mins(s?: number | null) {
  if (s === null || s === undefined) return "–";
  return s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`;
}

function pct(n?: number | null, digits = 1) {
  return n === null || n === undefined ? "–" : `${(n * 100).toFixed(digits)}%`;
}

/** Run and maintain (roadmap J4): what the factory does for a delivered product after delivery.
 * Its owner, its availability against its target (read by the Operate watch), open incidents, its
 * data's backups and the restore drill that proves them, upkeep, and a log of what was done. Every
 * verdict comes from the engine (`GET /operations`); the buttons ask it to do one step now. */
export default function Operations({ productId, canSteer }: { productId: string; canSteer: boolean }) {
  const qc = useQueryClient();
  const path = { params: { path: { product_id: productId } } };
  const key = ["operations", productId];
  const q = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/products/{product_id}/operations", path)),
    refetchInterval: 15_000,
  });
  const refresh = () => {
    for (const k of [key, ["ready-for"], ["next-iteration", productId], ["work-items", productId], ["attention"]])
      qc.invalidateQueries({ queryKey: k });
  };
  const step = useMutation({
    mutationFn: async (what: "backup" | "restore-drill" | "upkeep" | "health-check") => {
      if (what === "backup") return unwrap(api.POST("/api/products/{product_id}/backup", path));
      if (what === "restore-drill") return unwrap(api.POST("/api/products/{product_id}/restore-drill", path));
      if (what === "upkeep") return unwrap(api.POST("/api/products/{product_id}/upkeep", path));
      await unwrap(api.POST("/api/products/{product_id}/health-check", path));
      return null;
    },
    onSuccess: (rec) => {
      refresh();
      if (rec) (rec.ok ? toast.success : toast.error)(rec.summary);
      else toast.success("Health read");
    },
  });
  const [editing, setEditing] = useState(false);
  const [target, setTarget] = useState("");
  const tighten = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/products/{product_id}/availability", {
          ...path,
          body: { success: Number(target) / 100 },
        }),
      ),
    onSuccess: (v) => {
      qc.setQueryData(key, v);
      refresh();
      setEditing(false);
      toast.success(`Target: ${v.availability.target}`);
    },
  });

  const respondNow = useMutation({
    mutationFn: () => unwrap(api.POST("/api/products/{product_id}/respond", path)),
    onSuccess: (view) => {
      qc.setQueryData(key, view);
      refresh();
      const r = view.responses?.[0];
      toast.success(r?.tried?.length ? `${STEP[r.tried[r.tried.length - 1]] ?? "Step"}: done` : "Response step run");
    },
  });

  const v = q.data as View | undefined;
  if (!v) return null;
  const a = v.availability;
  const state = STATE[a.state] ?? STATE.off;
  const incidents = v.incidents ?? [];
  const tone: PanelTone = incidents.length ? "bad" : a.state === "ok" && v.restore_proven ? "ok" : "default";
  const busy = step.isPending ? step.variables : null;

  return (
    <Panel
      id="operations"
      data-testid="operations"
      defaultOpen={incidents.length > 0}
      attention={incidents.length > 0}
      tone={tone}
      icon={incidents.length ? <Siren className="text-bad" /> : <Activity className="text-primary" />}
      title="Running it"
      summary={
        <>
          {v.live ? (
            <span className={cn("pill", state.tone)} data-testid="operations-state">
              {state.label}
            </span>
          ) : (
            <span className="pill muted">{v.retirement ? "retired" : "not running"}</span>
          )}
          {incidents.length > 0 && (
            <span className="pill bad">
              {incidents.length} open incident{incidents.length > 1 ? "s" : ""}
            </span>
          )}
          {v.live && v.data_declared && (
            <span className={cn("pill", v.restore_proven ? "ok" : "warn")}>
              {v.restore_proven ? "restore proven" : "restore not proven"}
            </span>
          )}
          <span className="text-muted-foreground">owner {v.owner ?? "nobody"}</span>
        </>
      }
    >
      <div className="grid gap-4">
        {!v.live && <p className="note m-0 text-sm">{v.why_not}</p>}

        <div className="grid gap-3 md:grid-cols-3">
          <Block icon={<HeartPulse className="size-4" />} title="Availability">
            <Line label="Target">
              {a.target ?? "none declared"}
              {a.target && (
                <span className="text-muted-foreground">
                  {" "}
                  · {a.chosen_by === "product" ? "set for this product" : "the stack's default"}
                </span>
              )}
            </Line>
            {v.live && (
              <>
                <Line label="Measured">
                  {a.samples ? (
                    <>
                      {pct(a.measured)} of {a.samples} checks
                      {a.error_rate !== null && a.error_rate !== undefined && (
                        <span className="text-muted-foreground"> · {pct(a.error_rate)} server errors</span>
                      )}
                    </>
                  ) : (
                    <span className="text-muted-foreground">no readings yet</span>
                  )}
                </Line>
                <Line label="Watch">
                  {a.watching ? (
                    <span className="text-ok">running{a.last_at ? `, read ${ago(a.last_at)}` : ""}</span>
                  ) : (
                    <span className="text-warn">
                      not reading it
                      {a.last_at ? ` (last ${ago(a.last_at)})` : ""}
                    </span>
                  )}
                </Line>
                {a.reasons?.map((r) => (
                  <p key={r} className="m-0 text-xs text-bad">
                    {r}
                  </p>
                ))}
              </>
            )}
            {canSteer && v.live && (
              <div className="mt-1 flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busy}
                  onClick={() => step.mutate("health-check")}
                  data-testid="health-check"
                >
                  <RefreshCw className={cn(busy === "health-check" && "animate-spin")} /> Read now
                </Button>
                {a.target && !editing && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setTarget(((a.success ?? 0.99) * 100).toFixed(2));
                      setEditing(true);
                    }}
                  >
                    <Target /> Tighten target
                  </Button>
                )}
              </div>
            )}
            {editing && (
              <form
                className="grid gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  tighten.mutate();
                }}
              >
                <label className="grid gap-1 text-sm">
                  <span className="font-medium">Health checks that must answer (%)</span>
                  <span className="text-xs text-muted-foreground">
                    At least the stack's {pct(a.floor, 2)}: a product may tighten its target, never loosen it.
                  </span>
                  <input
                    type="number"
                    step="0.01"
                    min={(a.floor ?? 0.5) * 100}
                    max={99.99}
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                    autoFocus
                  />
                </label>
                <span className="flex gap-2">
                  <Button size="sm" disabled={tighten.isPending}>
                    Save
                  </Button>
                  <Button size="sm" type="button" variant="outline" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                </span>
                <Problems error={tighten.error} />
              </form>
            )}
          </Block>

          <Block icon={<DatabaseBackup className="size-4" />} title="Data">
            {!v.data_declared ? (
              <p className="m-0 text-sm text-muted-foreground">The product's stack declares no data to back up.</p>
            ) : (
              <>
                <Line label="Last backup">
                  <RecordLine rec={v.last_backup} none="none yet" />
                </Line>
                <Line label="Restore">
                  {v.last_restore ? (
                    <RecordLine rec={v.last_restore} none="" />
                  ) : (
                    <span className="text-warn">never tested</span>
                  )}
                </Line>
                {v.restore_overdue && v.restore_proven && (
                  <p className="m-0 text-xs text-warn">A drill is due: the last proven restore is older than a week.</p>
                )}
                <p className="m-0 text-xs text-muted-foreground">
                  {v.backups} backup{v.backups === 1 ? "" : "s"} kept. A backup counts once a drill restored it away
                  from production and the data matched.
                </p>
                {canSteer && v.live && (
                  <div className="mt-1 flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" disabled={!!busy} onClick={() => step.mutate("backup")}>
                      <DatabaseBackup className={cn(busy === "backup" && "animate-pulse")} /> Back up now
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!!busy || v.backups === 0}
                      onClick={() => step.mutate("restore-drill")}
                      data-testid="restore-drill"
                    >
                      <RotateCcw className={cn(busy === "restore-drill" && "animate-spin")} /> Test a restore
                    </Button>
                  </div>
                )}
              </>
            )}
          </Block>

          <Block icon={<RefreshCw className="size-4" />} title="Upkeep">
            <Line label="Last check">
              <RecordLine rec={v.last_upkeep} none={v.upkeep_declared ? "none yet" : "not declared by the stack"} />
            </Line>
            <Line label="Behind">
              {(v.upkeep_items ?? []).length ? (
                <span className="text-warn">{v.upkeep_items![0].title}</span>
              ) : (
                <span className="text-muted-foreground">nothing open</span>
              )}
            </Line>
            <p className="m-0 text-xs text-muted-foreground">
              The live image is scanned again and dependencies compared with their newest releases. What is found waits
              on the Next iteration card.
            </p>
            {canSteer && v.live && (
              <div className="mt-1">
                <Button size="sm" variant="outline" disabled={!!busy} onClick={() => step.mutate("upkeep")}>
                  <RefreshCw className={cn(busy === "upkeep" && "animate-spin")} /> Check now
                </Button>
              </div>
            )}
          </Block>
        </div>
        <Problems error={step.error} />

        {incidents.map((i) => (
          <IncidentCard
            key={i.id}
            item={i}
            response={(v.responses ?? []).find((r) => r.incident === i.id)}
            mode={v.respond}
            canSteer={canSteer && v.live}
            busy={respondNow.isPending}
            onRespond={() => respondNow.mutate()}
          />
        ))}
        <Problems error={respondNow.error} />

        {(v.records ?? []).length > 0 && (
          <div className="grid gap-1">
            <h3 className="m-0 text-sm font-semibold">What the factory did</h3>
            <ul className="m-0 grid max-h-64 list-none gap-1 overflow-auto p-0 text-sm" data-testid="operations-log">
              {(v.records ?? []).slice(0, 20).map((r) => (
                <li key={r.id} className="flex items-start gap-2">
                  {r.ok ? (
                    <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />
                  ) : (
                    <CircleX className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
                  )}
                  <span className="min-w-0 flex-1 break-words">
                    <strong>{KIND[r.kind] ?? r.kind}</strong> · {r.summary}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {r.by === "factory" ? "schedule" : r.by} · {ago(r.at)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex items-center gap-2 border-t pt-3 text-sm text-muted-foreground">
          <UserRound className="size-4" aria-hidden />
          Incidents, upkeep and retirement go to <strong className="text-foreground">{v.owner ?? "nobody yet"}</strong>
          {v.owner && !v.owner_named && " (its creator; an admin can name an owner under Ready for)"}
        </div>
      </div>
    </Panel>
  );
}

/** One open incident and the engine's response to it (ADR-0049 §4, slice 0): where it is on the way to
 * resolved, what was tried, why it is held, the DORA timings, and the step the owner can run now. */
function IncidentCard({
  item,
  response: r,
  mode,
  canSteer,
  busy,
  onRespond,
}: {
  item: WorkItem_;
  response?: Response_;
  mode: View["respond"];
  canSteer: boolean;
  busy: boolean;
  onRespond: () => void;
}) {
  const stage = r ? (r.state === "reopened" ? "mitigating" : r.state) : "open";
  const at = STAGES.indexOf(stage as (typeof STAGES)[number]);
  const offer = r?.proposed ?? "";
  return (
    <div className="grid gap-2 rounded-lg border border-bad/40 bg-bad/5 p-3 text-sm" data-testid="incident">
      <span className="flex flex-wrap items-center gap-2 font-medium">
        <Siren className="size-4 text-bad" aria-hidden />
        {item.title}
        <span className="pill muted">{item.status === "in_progress" ? "fix in progress" : "open"}</span>
        {r && r.reopened > 0 && <span className="pill warn">came back {r.reopened}×</span>}
      </span>
      {r && mode.mode !== "off" && (
        <ol className="m-0 flex list-none flex-wrap items-center gap-1 p-0 text-xs" aria-label="Incident state">
          {STAGES.map((s, n) => (
            <li key={s} className="flex items-center gap-1">
              <span
                className={cn(
                  "rounded-full border px-2 py-0.5",
                  n < at && "border-ok/40 text-ok",
                  n === at && "border-primary bg-primary/10 font-semibold text-foreground",
                  n > at && "text-muted-foreground",
                )}
              >
                {STAGE_LABEL[s]}
              </span>
              {n < STAGES.length - 1 && <span className="text-muted-foreground">→</span>}
            </li>
          ))}
        </ol>
      )}
      <p className="m-0 whitespace-pre-line text-muted-foreground">{item.detail}</p>
      {r && (
        <div className="grid gap-1 rounded-md border bg-background/60 p-2">
          <Line label="Tried">
            {r.tried?.length ? (
              r.tried.map((t) => STEP[t] ?? t).join(" → ")
            ) : (
              <span className="text-muted-foreground">nothing yet</span>
            )}
          </Line>
          {r.held && (
            <Line label="Held">
              <span className="text-warn">{r.held}</span>
            </Line>
          )}
          {r.proposed && (
            <Line label="Proposed">
              {STEP[r.proposed] ?? r.proposed}
              {mode.why && <span className="text-muted-foreground"> · {mode.why}</span>}
            </Line>
          )}
          {r.classification && <Line label="Cause">{r.classification}</Line>}
          <Line label="Timings">
            detected in {mins(r.time_to_detect_s)}
            {r.restored_at && <> · restored in {mins(r.time_to_restore_s)}</>}
            <span className="text-muted-foreground"> · from the first failed reading</span>
          </Line>
          {r.caused_by && (
            <Line label="Counts against">
              change <code>{r.caused_by}</code>{" "}
              <span className="text-muted-foreground">(it began within a day of its release)</span>
            </Line>
          )}
          {r.state === "restored" && (
            <p className="m-0 text-xs text-muted-foreground">
              Service is back without a change. It is resolved once it stays healthy for a day; if the same breach
              returns first, the factory takes the next step.
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {canSteer && offer && (
          <Button size="sm" onClick={onRespond} disabled={busy} data-testid="respond-now">
            <LifeBuoy className={cn(busy && "animate-spin")} /> {STEP[offer] ?? "Run the proposed step"} now
          </Button>
        )}
        {item.status === "open" && (
          <span className="text-xs text-muted-foreground">
            The root cause is fixed by a change: tick it on the Next iteration card.
          </span>
        )}
      </div>
    </div>
  );
}

function Block({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="grid content-start gap-1.5 rounded-lg border p-3">
      <h3 className="m-0 flex items-center gap-2 text-sm font-semibold">
        <span className="text-muted-foreground">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_minmax(0,1fr)] gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

function RecordLine({ rec, none }: { rec?: Record_ | null; none: string }) {
  if (!rec) return <span className="text-muted-foreground">{none}</span>;
  return (
    <span className={rec.ok ? "" : "text-bad"} title={rec.summary}>
      {rec.ok ? (
        <CircleCheck className="mr-1 inline size-3.5 text-ok" />
      ) : (
        <CircleDashed className="mr-1 inline size-3.5" />
      )}
      {rec.ok ? "ok" : "failed"} · {ago(rec.at)}
    </span>
  );
}
