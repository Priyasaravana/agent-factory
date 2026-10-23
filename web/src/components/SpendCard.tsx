import { CircleDollarSign } from "lucide-react";
import type { components } from "../api/schema";

type Spend = components["schemas"]["ProductSpend"];

const usd = (n: number) => `$${n.toFixed(2)}`;
// one hue, lighter as the lifecycle moves on: the legend carries the names, never colour alone
const SHADE = [1, 0.8, 0.62, 0.46, 0.34, 0.24, 0.16, 0.1];

/** What the product has cost so far, across all its changes, split by phase (model spend:
 * tests, scans, builds and deploys run locally at no extra cost). */
export default function SpendCard({ spend }: { spend?: Spend }) {
  const phases = (spend?.phases ?? []).filter((p) => p.cost_usd >= 0.005);
  const total = spend?.total_usd ?? 0;
  const changes = spend?.changes ?? 0;
  return (
    <div
      className="flex min-w-0 items-start gap-3 rounded-xl border bg-card px-4 py-3 shadow-sm"
      data-testid="product-spend"
    >
      <span className="mt-0.5 text-muted-foreground [&_svg]:size-4">
        <CircleDollarSign />
      </span>
      <div className="grid min-w-0 flex-1 gap-1.5">
        <div className="text-xs font-medium text-muted-foreground">
          Spend so far
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tabular-nums leading-none">
            {usd(total)}
          </span>
          <span className="text-xs text-muted-foreground">
            {changes} change{changes === 1 ? "" : "s"}
          </span>
        </div>
        {total > 0 && phases.length > 0 && (
          <>
            <div
              className="flex h-1.5 w-full gap-px overflow-hidden rounded-full bg-muted"
              role="img"
              aria-label={phases
                .map((p) => `${p.title} ${usd(p.cost_usd)}`)
                .join(", ")}
            >
              {phases.map((p, i) => (
                <span
                  key={p.phase}
                  className="h-full bg-primary"
                  style={{
                    width: `${(p.cost_usd / total) * 100}%`,
                    opacity: SHADE[i] ?? 0.1,
                  }}
                  title={`${p.title}: ${usd(p.cost_usd)}`}
                />
              ))}
            </div>
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                By phase
              </summary>
              <table className="mt-1 w-full text-xs tabular-nums">
                <tbody>
                  {phases.map((p, i) => (
                    <tr
                      key={p.phase}
                      title={(p.stations ?? [])
                        .map((s) => `${s.station} ${usd(s.cost_usd)}`)
                        .join(" · ")}
                    >
                      <td className="py-0.5 pr-2">
                        <span
                          className="mr-1.5 inline-block size-2 rounded-sm bg-primary align-middle"
                          style={{ opacity: SHADE[i] ?? 0.1 }}
                        />
                        {p.title}
                      </td>
                      <td className="py-0.5 text-right">{usd(p.cost_usd)}</td>
                      <td className="w-10 py-0.5 text-right text-muted-foreground">
                        {Math.round((p.cost_usd / total) * 100)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="m-0 mt-1 text-muted-foreground">
                Model spend. Tests, scans, builds and deploys run on this
                machine at no extra cost.
              </p>
            </details>
          </>
        )}
      </div>
    </div>
  );
}
