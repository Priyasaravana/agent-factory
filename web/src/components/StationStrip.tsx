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

/** The run's stations as a horizontal timeline (wraps on narrow screens). */
export default function StationStrip({ stations }: { stations: StationView[] }) {
  return (
    <ol className="strip-timeline strip my-3 flex list-none flex-wrap gap-y-3 p-0" aria-label="Stations">
      {stations.map((s, i) => {
        const st = STATE[s.state] ?? STATE.pending;
        return (
          <li
            key={s.id}
            className={`station-step st-${s.state} group flex min-w-32 flex-1 items-start`}
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
                {i < stations.length - 1 && (
                  <span
                    className={cn(
                      "mx-2 h-px flex-1",
                      s.state === "passed" ? "bg-ok/60" : "bg-border",
                    )}
                  />
                )}
              </div>
              <div className="pr-3 leading-tight">
                <div className={cn("text-sm font-medium", s.state === "pending" && "text-muted-foreground")}>{s.id}</div>
                <div className="text-[11px] text-muted-foreground">
                  {s.kind === "agent" ? "agent" : "check"}
                  {s.repair ? " · repair" : ""}
                  {s.attempts > 1 ? ` · ×${s.attempts}` : ""}
                </div>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
