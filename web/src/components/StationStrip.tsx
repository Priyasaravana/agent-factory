import { AlertTriangle, Check, Circle, Hourglass, Loader2, Wrench, X } from "lucide-react";
import type { ReactNode } from "react";
import type { StationView } from "../api/client";
import { cn } from "../lib/utils";

const STATE: Record<string, { icon: ReactNode; dot: string }> = {
  pending: { icon: <Circle />, dot: "bg-muted text-muted-foreground" },
  running: { icon: <Loader2 className="animate-spin" />, dot: "bg-info text-background" },
  passed: { icon: <Check />, dot: "bg-ok text-background" },
  failed: { icon: <X />, dot: "bg-warn text-background" },
  held: { icon: <AlertTriangle />, dot: "bg-bad text-background" },
  waiting: { icon: <Hourglass />, dot: "bg-warn text-background" },
};

const phaseLabel = (p: string) => p.charAt(0).toUpperCase() + p.slice(1);

/** Consecutive stations of the same DevOps phase (ADR-0028), in lane order. */
function byPhase(stations: StationView[]): { phase: string; stations: StationView[] }[] {
  const groups: { phase: string; stations: StationView[] }[] = [];
  for (const s of stations) {
    const phase = s.phase ?? "plan";
    const last = groups[groups.length - 1];
    if (last && last.phase === phase) last.stations.push(s);
    else groups.push({ phase, stations: [s] });
  }
  return groups;
}

/** The change's stations as a horizontal timeline grouped by DevOps phase (wraps on narrow screens). */
export default function StationStrip({ stations }: { stations: StationView[] }) {
  const groups = byPhase(stations);
  return (
    <ol className="strip-timeline strip my-3 flex list-none flex-wrap gap-y-4 p-0" aria-label="Stations">
      {groups.map((g, gi) => (
        <li
          key={`${g.phase}-${gi}`}
          className="flex flex-col gap-1.5"
          style={{ flex: `${g.stations.length} 1 0`, minWidth: `${g.stations.length * 8}rem` }}
        >
          <div
            className="mr-3 border-b border-border pb-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
            data-testid={`phase-${g.phase}`}
          >
            {phaseLabel(g.phase)}
          </div>
          <ol className="flex list-none p-0" aria-label={`${phaseLabel(g.phase)} stations`}>
            {g.stations.map((s) => {
              const last = gi === groups.length - 1 && s === g.stations[g.stations.length - 1];
              return <Step key={s.id} s={s} last={last} />;
            })}
          </ol>
        </li>
      ))}
    </ol>
  );
}

function Step({ s, last }: { s: StationView; last: boolean }) {
  const st = STATE[s.state] ?? STATE.pending;
  const label = s.label || s.id;
  // the id adds nothing when it is just the label in lower case (a legacy or custom id does)
  const showId = label.toLowerCase().replace(/ /g, "-") !== s.id;
  return (
    <li
      className={`station-step st-${s.state} group flex min-w-0 flex-1 items-start`}
      title={s.role ?? "deterministic check"}
    >
      <div className="flex w-full flex-col gap-1.5">
        <div className="flex items-center">
          <span
            className={cn(
              "grid size-6 shrink-0 place-items-center rounded-full [&_svg]:size-3.5",
              st.dot,
              s.state === "running" && "ring-4 ring-info/25",
              s.repair && "outline-1 outline-dashed outline-offset-2 outline-muted-foreground",
            )}
          >
            {s.repair && s.state === "pending" ? <Wrench /> : st.icon}
          </span>
          {!last && <span className={cn("mx-2 h-px flex-1", s.state === "passed" ? "bg-ok/60" : "bg-border")} />}
        </div>
        <div className="pr-3 leading-tight">
          <div className={cn("text-sm font-medium", s.state === "pending" && "text-muted-foreground")}>{label}</div>
          <div className="text-[11px] text-muted-foreground">
            {s.kind === "agent" ? "agent" : "check"}
            {s.repair ? " · repair" : ""}
            {s.attempts > 1 ? ` · ×${s.attempts}` : ""}
            {showId ? ` · ${s.id}` : ""}
          </div>
        </div>
      </div>
    </li>
  );
}
