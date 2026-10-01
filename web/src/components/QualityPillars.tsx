import { Tip } from "./ui/tooltip";

/** The ten quality pillars (ADR-0024), in model order, with the question each answers. */
export const PILLAR_QUESTIONS: Record<string, string> = {
  security:
    "Is it protected, least-privilege, auditable, with a trusted supply chain?",
  reliability:
    "Does it keep working, or degrade gracefully, when parts fail? Can it be restored?",
  performance:
    "Does it meet latency and throughput needs, and scale with load?",
  operability: "Can operators see, deploy, diagnose and recover it?",
  cost: "Is the cost to build and run visible and justified?",
  interoperability:
    "Does it integrate through documented, standard interfaces?",
  usability:
    "Can its users learn and use it, including users with accessibility needs?",
  maintainability: "Is it cheap and safe to change?",
  portability: "Can it be installed and moved across environments?",
  compliance:
    "Can we prove what was built, from what, and that it meets its requirements?",
};

export type PillarRow = {
  id: string;
  title: string;
  passed: number;
  applicable: number;
  /** distinct signals tagged with the pillar; 0 = uncovered */
  signals?: number;
  note?: string;
};

/** One row per pillar: a single-hue meter of passed / applicable signals.
 * A pillar with no signals says "uncovered" and never shows a full bar. */
export default function PillarBars({ rows }: { rows: PillarRow[] }) {
  return (
    <ul
      className="m-0 grid list-none gap-1.5 p-0 text-sm"
      data-testid="pillars"
    >
      {rows.map((p) => {
        const uncovered = (p.signals ?? p.applicable) === 0;
        const pct = p.applicable ? p.passed / p.applicable : 0;
        return (
          <Tip key={p.id} side="top" label={PILLAR_QUESTIONS[p.id] ?? p.title}>
            <li
              className="grid grid-cols-[minmax(0,13rem)_minmax(0,1fr)_7rem] items-center gap-3"
              data-pillar={p.id}
            >
              <span className="truncate">{p.title}</span>
              {uncovered ? (
                <span className="text-xs italic text-muted-foreground">
                  uncovered: no signals yet
                </span>
              ) : (
                <span
                  className="h-2 w-full rounded-[4px] bg-muted"
                  role="meter"
                  aria-valuemin={0}
                  aria-valuemax={p.applicable}
                  aria-valuenow={p.passed}
                  aria-label={`${p.title}: ${p.passed} of ${p.applicable}`}
                >
                  <span
                    className="block h-full rounded-[4px]"
                    style={{
                      width: `${Math.round(100 * pct)}%`,
                      background: "var(--series-1)",
                    }}
                  />
                </span>
              )}
              <span className="text-right tabular-nums text-muted-foreground">
                {uncovered
                  ? "—"
                  : p.applicable
                    ? `${p.passed}/${p.applicable} · ${Math.round(100 * pct)}%`
                    : "not assessed"}
              </span>
            </li>
          </Tip>
        );
      })}
    </ul>
  );
}
